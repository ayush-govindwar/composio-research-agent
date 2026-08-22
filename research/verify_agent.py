"""
Pass 2: Verification agent.

Deliberately NOT "re-run the same search and see if the LLM agrees with itself."
Two independent checks per app, combined before the final verdict:

  A) Fresh search -- the agent searches for the app's auth/docs page from scratch,
     with no visibility into what Pass 1 concluded. This catches cases where
     Pass 1 hallucinated an answer with no real source.

  B) URL fetch -- Composio's Exa fetch tool loads the exact URL(s) Pass 1 cited
     as evidence and checks whether that page actually supports Pass 1's claims.
     This catches cases where Pass 1 cited a real but irrelevant/stale page.

The extraction step is shown both sets of notes plus Pass 1's original answer,
and must explicitly flag any field where they disagree (is_accurate_pass1=False,
corrections_made=[...]).

Run:
    python research/verify_agent.py --limit 100
"""

import os
import sys
import json
import time
import argparse

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langgraph.prebuilt import create_react_agent

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from data.schema import AppVerificationData
from research.composio_tools import get_search_tools, get_fetch_tools, composio_available

load_dotenv(override=True)

INPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "pass1.jsonl")
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "pass2.jsonl")

FRESH_SEARCH_TEMPLATE = """You are verifying a prior researcher's claims about the app: {app_name}.
Do NOT look at their answer yet -- independently search for and confirm:
1. The app's API auth method(s) (OAuth2 / API key / Basic / token / other).
2. Whether API access is self-serve, or gated behind a paid plan / approval / \
partnership / contact-sales.
3. API surface type (REST/GraphQL/SOAP/none) and roughly how broad it is.
4. Whether an MCP server exists for this app (official or community).
Search the app's real developer documentation. Cite the URLs you used.

Use ONLY the parameters defined in the search tool's own schema for this
call -- do not add fields from general knowledge of any search API. The
required field for a search is a "query" string. Do not invent "id",
"contents", or "highlights" fields.

Use at most 2 tool calls total, then STOP and write your findings.
"""

FETCH_CHECK_TEMPLATE = """The prior researcher cited these URLs as evidence for their claims \
about {app_name}: {evidence_urls}

Fetch the content of each URL using the fetch tool. For each one, state:
- Does the page actually load / exist?
- Does its content support the specific claims below, or contradict/not mention them?

Claims to check:
- auth_methods: {auth_methods}
- access: {access}
- api_surface_type: {api_surface_type}
- has_mcp: {has_mcp}

Use ONLY the parameters defined in the fetch tool's own schema for this
call -- do not add fields from general knowledge of any web-content API.
If you are unsure what field carries the URL list, check the tool's
schema rather than guessing a field name.

Use at most 2 tool calls total, then STOP and write your findings.
"""


def _invoke_with_retries(agent, messages, recursion_limit, attempts=2):
    """Run agent.invoke with a couple of retries so one malformed tool call
    doesn't sink this whole check. Returns the result or raises the last error."""
    last_err = None
    for attempt in range(attempts):
        try:
            return agent.invoke(
                {"messages": messages},
                config={"recursion_limit": recursion_limit},
            )
        except Exception as e:
            last_err = e
            print(f"    attempt {attempt + 1} failed: {e}")
            time.sleep(2)
    raise last_err


def verify_app(pass1_row: dict, llm, search_tools=None, fetch_tools=None) -> dict:
    app_name = pass1_row["name"]
    print(f"\n--- [Pass 2] Verifying {app_name} ---")

    if search_tools is None:
        search_tools = get_search_tools()
    if fetch_tools is None:
        fetch_tools = get_fetch_tools()

    if not search_tools and not fetch_tools:
        print(f"  No verification tools available -- carrying Pass 1 forward as mock stub.")
        return _mock_stub(pass1_row)

    notes_parts = []
    fresh_search_ok = False
    fetch_check_ok = False

    # Check A: fresh, independent search
    if search_tools:
        try:
            search_agent = create_react_agent(llm, search_tools)
            search_result = _invoke_with_retries(
                search_agent,
                [
                    ("system", FRESH_SEARCH_TEMPLATE.format(app_name=app_name)),
                    ("user", f"Independently verify {app_name} now. Keep the answer concise."),
                ],
                recursion_limit=10,
            )
            notes_parts.append("FRESH SEARCH FINDINGS:\n" + str(search_result["messages"][-1].content))
            fresh_search_ok = True
        except Exception as e:
            notes_parts.append(f"FRESH SEARCH FAILED: {e}")

    # Check B: fetch pass-1's cited evidence directly
    evidence = pass1_row.get("evidence", [])
    if fetch_tools and evidence:
        try:
            fetch_agent = create_react_agent(llm, fetch_tools)
            fetch_prompt = FETCH_CHECK_TEMPLATE.format(
                app_name=app_name,
                evidence_urls=evidence[:5],
                auth_methods=pass1_row.get("auth_methods", []),
                access=pass1_row.get("access", "unknown"),
                api_surface_type=pass1_row.get("api_surface_type", "unknown"),
                has_mcp=pass1_row.get("has_mcp", False),
            )
            fetch_result = _invoke_with_retries(
                fetch_agent,
                [
                    ("system", "You check whether cited URLs support claims made about them."),
                    ("user", fetch_prompt),
                ],
                recursion_limit=6,
            )
            notes_parts.append("CITATION CHECK FINDINGS:\n" + str(fetch_result["messages"][-1].content))
            fetch_check_ok = True
        except Exception as e:
            notes_parts.append(f"CITATION CHECK FAILED: {e}")
    elif not evidence:
        notes_parts.append("CITATION CHECK SKIPPED: Pass 1 provided no evidence URLs.")

    combined_notes = "\n\n".join(notes_parts)[:7000]

    if fresh_search_ok and fetch_check_ok:
        verification_source = "both"
    elif fresh_search_ok:
        verification_source = "fresh-search"
    elif fetch_check_ok:
        verification_source = "url-fetch"
    else:
        verification_source = "unverified"

    try:
        extraction_prompt = f"""
You are comparing a prior researcher's answer (Pass 1) against independent
verification notes (Pass 2) for {app_name}.

Return ONLY a small JSON verdict object -- do NOT repeat unchanged fields
from Pass 1. Do not use markdown. Do not add explanations.

{{
  "is_accurate_pass1": true | false,
  "corrections_made": ["short plain-English description of each change", ...],
  "confidence_pass2": 0,
  "verification_notes": "1-2 sentence summary of what the checks found",
  "corrected_fields": {{
    "auth_methods": ["string", ...],
    "access": "self-serve" | "gated-paid" | "gated-approval" | "gated-partnership" | "unknown",
    "api_surface_type": "REST" | "GraphQL" | "SOAP" | "gRPC" | "none" | "unknown",
    "api_surface_breadth": "narrow" | "broad" | "full-platform" | "unknown",
    "has_mcp": true | false,
    "mcp_source": "official" | "community" | "none" | "unknown",
    "buildability": "ready" | "possible-with-workaround" | "blocked" | "unknown",
    "blocker": "string",
    "one_liner": "string",
    "evidence": ["string", ...]
  }}
}}

Rules:

- "corrected_fields" should ONLY include keys whose value the verification
  notes clearly contradict vs Pass 1. Omit every key that Pass 1 already
  got right -- do not repeat them. If nothing was wrong, omit
  "corrected_fields" entirely (or make it an empty object {{}}).
- Set "is_accurate_pass1": false ONLY if you included at least one key in
  "corrected_fields". Otherwise "is_accurate_pass1": true and
  "corrections_made": [].
- Any value inside "corrected_fields" for access, api_surface_type,
  api_surface_breadth, mcp_source, or buildability MUST be one of the
  exact literal values listed above.
- "confidence_pass2" (0-100): how well corroborated the FINAL answer is by
  the verification notes. If verification_source is "unverified", use a
  low value (0-20).
- Never invent facts. Keep "verification_notes" brief.

PASS 1 SUMMARY:
- one_liner: {pass1_row.get('one_liner', '')}
- auth_methods: {pass1_row.get('auth_methods', [])}
- access: {pass1_row.get('access', 'unknown')}
- api_surface_type: {pass1_row.get('api_surface_type', 'unknown')}
- api_surface_breadth: {pass1_row.get('api_surface_breadth', 'unknown')}
- has_mcp: {pass1_row.get('has_mcp', False)}
- mcp_source: {pass1_row.get('mcp_source', 'none')}
- buildability: {pass1_row.get('buildability', 'unknown')}
- blocker: {pass1_row.get('blocker', '')}

This run's verification_source is: {verification_source}

INDEPENDENT VERIFICATION NOTES:
{combined_notes}
"""

        extractor_llm = llm.bind(response_format={"type": "json_object"})

        last_err = None
        verdict = None
        for attempt in range(2):
            try:
                response = extractor_llm.invoke(extraction_prompt)
                content = response.content

                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict):
                            text = item.get("text")
                            if text:
                                parts.append(str(text))
                        else:
                            parts.append(str(item))
                    content = "".join(parts)

                content = str(content).strip()
                if content.startswith("```"):
                    content = content.replace("```json", "", 1)
                    content = content.replace("```", "", 1).strip()

                verdict = json.loads(content)
                break
            except Exception as e:
                last_err = e
                print(f"  Extraction attempt {attempt + 1} failed: {e}")
                time.sleep(2)

        if verdict is None:
            raise last_err

        # Start from Pass 1's data and only overlay fields the verdict corrected.
        data = dict(pass1_row)
        corrected = verdict.get("corrected_fields") or {}
        for key, value in corrected.items():
            data[key] = value

        data["confidence_pass2"] = float(verdict.get("confidence_pass2", 0.0))
        data["is_accurate_pass1"] = bool(verdict.get("is_accurate_pass1", not corrected))
        data["corrections_made"] = verdict.get("corrections_made", [])
        data["verification_source"] = verification_source
        data["verification_notes"] = verdict.get("verification_notes", "")
        data["id"] = pass1_row["id"]
        data["name"] = app_name
        data["category"] = pass1_row.get("category", "Unknown")
        data["confidence_pass1"] = pass1_row.get("confidence_pass1", 0.0)
        data["is_mock"] = False

        data = AppVerificationData.model_validate(data).model_dump()
        return data

    except Exception as e:
        print(f"  ERROR verifying {app_name}: {e} -- carrying Pass 1 forward as mock stub.")
        return _mock_stub(pass1_row)


def _mock_stub(pass1_row: dict) -> dict:
    """Fallback when verification tooling/LLM fails. Carries Pass 1's values forward
    unchanged but flags is_mock=True so it's excluded from accuracy stats and shown
    honestly on the report as an unverified row."""
    stub = dict(pass1_row)
    stub["confidence_pass2"] = 0.0
    stub["is_accurate_pass1"] = True
    stub["corrections_made"] = []
    stub["verification_source"] = "unverified"
    stub["verification_notes"] = "Verification pipeline failed -- carried Pass 1 forward unchanged."
    stub["is_mock"] = True
    return stub


def main():
    parser = argparse.ArgumentParser(description="Run Pass 2 verification agent.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--sleep", type=float, default=2.0)
    args = parser.parse_args()

    if not composio_available():
        print(
            "WARNING: Composio is not configured. All rows in this run will be "
            "flagged is_mock=True and Pass 1 values carried forward unverified."
        )

    if not os.path.exists(INPUT_FILE):
        print(f"{INPUT_FILE} not found. Run research_agent.py first.")
        return

    llm = ChatGroq(
        model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        temperature=0,
        max_tokens=2500,
        groq_api_key=os.environ.get("GROQ_API_KEY"),
        max_retries=2,
    )

    pass1_rows = []
    with open(INPUT_FILE, "r") as f:
        for line in f:
            if line.strip():
                pass1_rows.append(json.loads(line))

    existing_ids = set()
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r") as f:
            for line in f:
                if line.strip():
                    try:
                        existing_ids.add(json.loads(line)["id"])
                    except Exception:
                        pass

    search_tools = get_search_tools()
    fetch_tools = get_fetch_tools()

    with open(OUTPUT_FILE, "a") as f:
        for row in pass1_rows[: args.limit]:
            if row["id"] in existing_ids:
                print(f"Skipping {row['name']} (already in {OUTPUT_FILE})")
                continue
            verified = verify_app(row, llm, search_tools=search_tools, fetch_tools=fetch_tools)
            f.write(json.dumps(verified) + "\n")
            f.flush()
            time.sleep(args.sleep)

    print(f"\nPass 2 complete. Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()