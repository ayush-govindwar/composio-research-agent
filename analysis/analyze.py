"""
Aggregate stats for the report: pattern clusters + real accuracy numbers.

- Reads data/pass2.jsonl as the source of truth for the table/stats.
- is_mock rows are EXCLUDED from all pattern stats but counted and disclosed
  (mock_count, mock_apps) so the report can say "N/100 rows failed and were
  excluded" instead of silently shrinking the denominator.
- Reads data/audit_results.json (if present) to report the real, human-checked
  pass1 vs pass2 field-accuracy delta, plus a handful of concrete example
  corrections for the "verification" section of the report.

Run:
    python analysis/analyze.py
"""

import json
import os
from collections import Counter

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
INPUT_FILE = os.path.join(DATA_DIR, "pass2.jsonl")
AUDIT_FILE = os.path.join(DATA_DIR, "audit_results.json")
OUTPUT_FILE = os.path.join(DATA_DIR, "stats.json")


def load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def analyze_patterns(input_file, audit_file, output_file):
    all_apps = load_jsonl(input_file)
    if not all_apps:
        print(f"No data in {input_file}. Run research_agent.py and verify_agent.py first.")
        return

    mock_apps = [a for a in all_apps if a.get("is_mock")]
    apps = [a for a in all_apps if not a.get("is_mock")]

    if not apps:
        print("All rows are is_mock=True -- nothing real to analyze yet.")
        return

    stats = {
        "total_apps": len(all_apps),
        "real_apps": len(apps),
        "mock_count": len(mock_apps),
        "mock_apps": [{"id": a["id"], "name": a["name"]} for a in mock_apps],

        "auth_methods": Counter(),
        "access_distribution": Counter(),
        "buildability_distribution": Counter(),
        "api_surface_type": Counter(),
        "blockers": Counter(),
        "has_mcp_count": 0,

        "category_stats": {},
        "avg_confidence_pass1": round(sum(a.get("confidence_pass1", 0) for a in apps) / len(apps), 1),
        "avg_confidence_pass2": round(sum(a.get("confidence_pass2", 0) for a in apps) / len(apps), 1),
        "corrections_count": sum(1 for a in apps if not a.get("is_accurate_pass1", True)),
    }

    easy_wins, needs_outreach, example_corrections = [], [], []

    for app in apps:
        cat = app.get("category", "Unknown")
        cs = stats["category_stats"].setdefault(
            cat, {"total": 0, "self_serve": 0, "gated": 0, "ready": 0, "has_mcp": 0}
        )
        cs["total"] += 1

        for auth in app.get("auth_methods", []):
            stats["auth_methods"][auth] += 1

        access = app.get("access", "unknown")
        stats["access_distribution"][access] += 1
        if access == "self-serve":
            cs["self_serve"] += 1
        elif access.startswith("gated"):
            cs["gated"] += 1

        surface = app.get("api_surface_type", "unknown")
        stats["api_surface_type"][surface] += 1

        buildability = app.get("buildability", "unknown")
        stats["buildability_distribution"][buildability] += 1
        if buildability == "ready":
            cs["ready"] += 1

        if app.get("has_mcp"):
            stats["has_mcp_count"] += 1
            cs["has_mcp"] += 1

        blocker = (app.get("blocker") or "").strip()
        if blocker and blocker.lower() not in ("none", "na", "n/a", ""):
            stats["blockers"][blocker] += 1

        if buildability == "ready" and access == "self-serve":
            easy_wins.append({"id": app["id"], "name": app["name"], "category": cat})
        if access in ("gated-approval", "gated-partnership"):
            needs_outreach.append({"id": app["id"], "name": app["name"], "category": cat})

        if not app.get("is_accurate_pass1", True) and app.get("corrections_made"):
            example_corrections.append({
                "id": app["id"],
                "name": app["name"],
                "corrections": app["corrections_made"],
                "verification_source": app.get("verification_source", "unverified"),
            })

    for key in ["auth_methods", "access_distribution", "buildability_distribution", "api_surface_type", "blockers"]:
        stats[key] = dict(stats[key].most_common(15))

    stats["easy_wins_sample"] = easy_wins[:10]
    stats["easy_wins_count"] = len(easy_wins)
    stats["needs_outreach_sample"] = needs_outreach[:10]
    stats["needs_outreach_count"] = len(needs_outreach)
    stats["example_corrections"] = example_corrections[:8]

    # Real, human-audited accuracy numbers
    stats["audit"] = _load_audit_summary(audit_file)

    with open(output_file, "w") as f:
        json.dump(stats, f, indent=2)

    print(f"Analysis complete. {len(apps)} real rows, {len(mock_apps)} mock rows excluded from stats.")
    print(f"Stats saved to {output_file}")


def _load_audit_summary(audit_file):
    if not os.path.exists(audit_file):
        return {
            "available": False,
            "note": "No human audit run yet -- run analysis/human_audit.py to generate real accuracy numbers.",
        }
    with open(audit_file, "r") as f:
        audit_data = json.load(f)
    summary = audit_data.get("summary", {})
    summary["available"] = True
    # A few concrete miss examples for the report, pulled straight from audit entries
    misses = []
    for e in audit_data.get("entries", []):
        for field, matched in e.get("pass2_field_match", {}).items():
            if matched is False:
                misses.append({
                    "name": e["name"],
                    "field": field,
                    "human_said": e["human_answer"].get(field),
                    "pass2_said": (e.get("pass2_answer") or {}).get(field),
                })
    summary["example_misses"] = misses[:8]
    return summary


if __name__ == "__main__":
    analyze_patterns(INPUT_FILE, AUDIT_FILE, OUTPUT_FILE)
