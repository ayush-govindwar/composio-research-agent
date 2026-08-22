"""
Blind, stratified human audit.

Design notes (why it works this way):
- Stratified, not random: 2 apps per category x 10 categories = 20, so every
  category gets checked, not whichever ones happen to get sampled.
- BLIND: the CLI shows only the app name + hint. It does NOT show what Pass 1
  or Pass 2 said. You go find the real docs yourself and type in what you find.
  Showing the agent's answer first anchors you into just confirming it.
- Diffs against BOTH pass1.jsonl and pass2.jsonl for the same app id, so we get
  a real "Pass 1 accuracy vs Pass 2 accuracy" delta on identical ground truth,
  not two different samples.
- Resumable: already-audited ids are skipped, so you can do 20 apps across
  multiple sittings.

Fields audited: auth_methods, access, api_surface_type, has_mcp, buildability.
(one_liner/blocker/evidence are free text -- not diffed automatically, but you
can eyeball them while you're in there.)

Run:
    python analysis/human_audit.py
    python analysis/human_audit.py --n-per-category 3   # bigger sample
"""

import os
import sys
import json
import random
import argparse

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

PASS1_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "pass1.jsonl")
PASS2_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "pass2.jsonl")
APPS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "apps.json")
AUDIT_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "audit_results.json")

AUDITED_FIELDS = ["auth_methods", "access", "api_surface_type", "has_mcp", "buildability"]

ACCESS_OPTIONS = ["self-serve", "gated-paid", "gated-approval", "gated-partnership", "unknown"]
BUILDABILITY_OPTIONS = ["ready", "possible-with-workaround", "blocked", "unknown"]
SURFACE_OPTIONS = ["REST", "GraphQL", "SOAP", "gRPC", "none", "unknown"]


def load_jsonl(path):
    rows = {}
    if not os.path.exists(path):
        return rows
    with open(path, "r") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                rows[row["id"]] = row
    return rows


def stratified_sample(apps, n_per_category, seed=42):
    rng = random.Random(seed)
    by_category = {}
    for app in apps:
        by_category.setdefault(app["category"], []).append(app)
    sample = []
    for cat, cat_apps in by_category.items():
        k = min(n_per_category, len(cat_apps))
        sample.extend(rng.sample(cat_apps, k))
    return sample


def prompt_choice(label, options):
    print(f"  {label} options: {options}")
    while True:
        val = input(f"  Your answer for {label}: ").strip()
        if val in options:
            return val
        print(f"  '{val}' not in {options}, try again (or type it exactly).")


def prompt_list(label):
    val = input(f"  {label} (comma-separated, e.g. 'OAuth2, API Key'): ").strip()
    return [v.strip() for v in val.split(",") if v.strip()]


def prompt_bool(label):
    while True:
        val = input(f"  {label} (y/n): ").strip().lower()
        if val in ("y", "yes"):
            return True
        if val in ("n", "no"):
            return False


def diff_field(human_val, agent_val):
    if isinstance(human_val, list) and isinstance(agent_val, list):
        return set(v.lower() for v in human_val) == set(v.lower() for v in agent_val)
    return str(human_val).strip().lower() == str(agent_val).strip().lower()


def audit_one(app, pass1_row, pass2_row):
    print("\n" + "=" * 70)
    print(f"App: {app['name']}  |  Category: {app['category']}  |  Hint: {app['website_or_hint']}")
    print("=" * 70)
    print("Go check this app's real developer docs now. Answer blind -- don't peek at pipeline output.")

    human = {}
    human["auth_methods"] = prompt_list("Auth method(s)")
    human["access"] = prompt_choice("Access tier", ACCESS_OPTIONS)
    human["api_surface_type"] = prompt_choice("API surface type", SURFACE_OPTIONS)
    human["has_mcp"] = prompt_bool("Does an MCP server exist for this app")
    human["buildability"] = prompt_choice("Buildability verdict", BUILDABILITY_OPTIONS)
    human["notes"] = input("  Any notes (optional): ").strip()

    result = {
        "id": app["id"],
        "name": app["name"],
        "category": app["category"],
        "human_answer": human,
        "pass1_answer": {f: pass1_row.get(f) for f in AUDITED_FIELDS} if pass1_row else None,
        "pass2_answer": {f: pass2_row.get(f) for f in AUDITED_FIELDS} if pass2_row else None,
        "pass1_field_match": {},
        "pass2_field_match": {},
    }

    print("\n  --- Results (revealed after your answer was locked in) ---")
    for f in AUDITED_FIELDS:
        p1_match = diff_field(human[f], pass1_row.get(f)) if pass1_row else None
        p2_match = diff_field(human[f], pass2_row.get(f)) if pass2_row else None
        result["pass1_field_match"][f] = p1_match
        result["pass2_field_match"][f] = p2_match
        p1_str = "MATCH" if p1_match else ("MISS" if p1_match is not None else "N/A")
        p2_str = "MATCH" if p2_match else ("MISS" if p2_match is not None else "N/A")
        print(f"    {f:20s} human={human[f]!r:35s} pass1={p1_str:5s} pass2={p2_str:5s}")

    return result


def compute_summary(entries):
    p1_total, p1_match, p2_total, p2_match = 0, 0, 0, 0
    for e in entries:
        for f in AUDITED_FIELDS:
            if e["pass1_field_match"].get(f) is not None:
                p1_total += 1
                p1_match += int(e["pass1_field_match"][f])
            if e["pass2_field_match"].get(f) is not None:
                p2_total += 1
                p2_match += int(e["pass2_field_match"][f])
    return {
        "n_apps_audited": len(entries),
        "n_fields_audited": len(AUDITED_FIELDS),
        "pass1_field_accuracy_pct": round(100 * p1_match / p1_total, 1) if p1_total else None,
        "pass2_field_accuracy_pct": round(100 * p2_match / p2_total, 1) if p2_total else None,
        "pass1_fields_checked": p1_total,
        "pass2_fields_checked": p2_total,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-per-category", type=int, default=2, help="Apps sampled per category (2x10=20 default).")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    with open(APPS_FILE, "r") as f:
        apps = json.load(f)
    pass1 = load_jsonl(PASS1_FILE)
    pass2 = load_jsonl(PASS2_FILE)

    if not pass1 and not pass2:
        print("No pass1.jsonl or pass2.jsonl found. Run the research/verify agents first.")
        return

    sample = stratified_sample(apps, args.n_per_category, seed=args.seed)

    existing = {"entries": []}
    if os.path.exists(AUDIT_FILE):
        with open(AUDIT_FILE, "r") as f:
            existing = json.load(f)
    audited_ids = {e["id"] for e in existing.get("entries", [])}

    print(f"Stratified sample: {len(sample)} apps across {len(set(a['category'] for a in sample))} categories.")
    print(f"Already audited: {len(audited_ids)}. Remaining this session: {len([a for a in sample if a['id'] not in audited_ids])}\n")

    for app in sample:
        if app["id"] in audited_ids:
            print(f"Skipping {app['name']} (already audited)")
            continue
        p1_row = pass1.get(app["id"])
        p2_row = pass2.get(app["id"])
        if p1_row is None and p2_row is None:
            print(f"Skipping {app['name']} -- no pipeline output for this app yet.")
            continue
        entry = audit_one(app, p1_row, p2_row)
        existing.setdefault("entries", []).append(entry)
        existing["summary"] = compute_summary(existing["entries"])
        with open(AUDIT_FILE, "w") as f:
            json.dump(existing, f, indent=2)
        print(f"\nSaved. Progress: {len(existing['entries'])}/{len(sample)}")

        cont = input("\nContinue to next app? (y/n): ").strip().lower()
        if cont not in ("y", "yes"):
            break

    print("\n" + "=" * 70)
    print("AUDIT SUMMARY")
    print(json.dumps(existing.get("summary", {}), indent=2))
    print(f"Full results: {AUDIT_FILE}")


if __name__ == "__main__":
    main()
