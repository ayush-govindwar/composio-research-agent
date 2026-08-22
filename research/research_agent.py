"""
Pass 1: Research agent.

For each app, searches for its developer/API docs via Composio's Exa tools,
lets a small ReAct agent gather notes, then extracts those notes into the
AppResearchData schema via structured output.

Run:
    python research/research_agent.py --limit 100
    python research/research_agent.py --limit 5 --start 0   # smoke test first
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
from data.schema import AppResearchData
from research.composio_tools import get_search_tools, composio_available

load_dotenv(override=True)

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "pass1.jsonl")
APPS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "apps.json")

SYSTEM_TEMPLATE = """You are an expert API researcher. Research the developer/API ecosystem for:
{app_name} (category: {category}). Website hint: {website}

Use the Exa search tool. Keep searches focused and use at most 5 results per search.

IMPORTANT: EXA_SEARCH `includeText` must ALWAYS contain exactly ONE
phrase with at most 5 words. For example:
["developer API authentication"]

Never use:
["developer", "api", "auth"]

EXA_SEARCH takes ONLY these parameters: query (string, required),
numResults (int, optional), type (optional), includeText (optional,
list with exactly one phrase). Do NOT include "id", "contents",
"highlights", or any other field. Every EXA_SEARCH call MUST include
a non-empty "query" string.

WRONG example (never do this): {{"id": "...", "contents": {{"highlights": true}}, "type": "instant"}}
CORRECT example: {{"query": "{app_name} developer API authentication", "type": "instant", "numResults": 5}}

Prioritize the app's real developer documentation, especially URLs containing
"developer", "api", "docs", "auth", or "pricing". Search specifically for
"{app_name} MCP server" when checking MCP support.

Find, with source URLs:
1. One-line description of the app.
2. API authentication methods: OAuth2, API key, Basic auth, token, or other.
3. API access tier: self-serve credentials, paid/gated, approval, partnership, or unknown.
4. API surface: REST, GraphQL, SOAP, gRPC, or none; and narrow/broad/full-platform.
5. Whether an official or community MCP server exists.
6. Whether it is buildable as an agent toolkit today and the single biggest blocker if not.
7. Actual documentation/article URLs used. Never fabricate URLs.

Use at most 4 tool calls total, then STOP using tools and write the
final research findings from what you have. Do not keep searching
indefinitely.

You MUST provide a concise final answer containing:
- one-line description
- authentication methods
- access tier
- API surface and breadth
- MCP availability
- buildability and blocker
- source URLs

Do not say that you need more steps.
Do not ask for clarification.
Do not return an empty response.
"""

MAX_RESEARCH_CHARS = 5000


def research_app(app_data: dict, llm, tools=None) -> dict:
    app_name = app_data["name"]
    category = app_data.get("category", "Unknown")
    website = app_data.get("website_or_hint", "")

    print(f"\n--- [Pass 1] Researching {app_name} ({website}) ---")

    if tools is None:
        tools = get_search_tools()

    if not tools:
        print(f"  No search tools available -- returning mock stub for {app_name}.")
        return _mock_stub(app_data)

    agent = create_react_agent(llm, tools)

    system_message = SYSTEM_TEMPLATE.format(
        app_name=app_name,
        category=category,
        website=website,
    )

    try:
        # ---------------------------------------------------------
        # STEP 1: Research using EXA + ReAct
        # ---------------------------------------------------------
        result = None
        last_err = None
        for attempt in range(3):
            try:
                result = agent.invoke(
                    {
                        "messages": [
                            ("system", system_message),
                            (
                                "user",
                                f"Research {app_name} now. "
                                "Use the search tool, keep searches focused, "
                                "and provide concise findings with source URLs.",
                            ),
                        ]
                    },
                    config={"recursion_limit": 30},
                )
                break
            except Exception as e:
                last_err = e
                print(f"  Attempt {attempt + 1} failed: {e}")
                time.sleep(2)

        if result is None:
            raise last_err

        print("\n--- DEBUG MESSAGES ---")
        for i, msg in enumerate(result["messages"]):
            print(f"\nMESSAGE {i}: {type(msg).__name__}")
            print("CONTENT:", msg.content)
            if hasattr(msg, "tool_calls"):
                print("TOOL CALLS:", msg.tool_calls)
        print("--- END DEBUG ---\n")

        research_notes = result["messages"][-1].content

        if isinstance(research_notes, list):
            parts = []

            for item in research_notes:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
                else:
                    parts.append(str(item))

            research_notes = "\n".join(parts)

        research_notes = str(research_notes).strip()

        # Keep the second Groq request small.
        research_notes = research_notes[:5000]

        if not research_notes:
            raise ValueError("Research agent returned empty notes.")

        # ---------------------------------------------------------
        # STEP 2: Convert notes → JSON
        # ---------------------------------------------------------
        extraction_prompt = f"""
You are a strict JSON extraction system.

Convert the research notes below into ONE valid JSON object.

Return ONLY JSON.
Do not use markdown.
Do not add explanations.

Required fields (use EXACTLY these keys):

{{
  "id": {app_data["id"]},
  "name": "{app_name}",
  "category": "{category}",
  "one_liner": "string",
  "auth_methods": ["string", ...],
  "access": "self-serve" | "gated-paid" | "gated-approval" | "gated-partnership" | "unknown",
  "api_surface_type": "REST" | "GraphQL" | "SOAP" | "gRPC" | "none" | "unknown",
  "api_surface_breadth": "narrow" | "broad" | "full-platform" | "unknown",
  "has_mcp": true | false,
  "mcp_source": "official" | "community" | "none" | "unknown",
  "buildability": "ready" | "possible-with-workaround" | "blocked" | "unknown",
  "blocker": "string, empty if none",
  "evidence": ["string", ...],
  "confidence_pass1": 0.0,
  "is_mock": false
}}

Rules:

- "access", "api_surface_type", "api_surface_breadth", "mcp_source",
  and "buildability" MUST be ONE of the exact literal values listed
  above for that field. Never use free text, never combine multiple
  values, never invent a new value. If the notes don't clearly say,
  use "unknown" (or "none" for mcp_source).
- "api_surface_type" takes only ONE value. If multiple APIs are
  mentioned (REST, SOAP, Bulk, etc.), pick the primary/most relevant
  one — usually "REST" — and mention the rest in "one_liner" or leave
  out. Do not list several types.
- Never invent facts.
- auth_methods must be an array of short strings, e.g. ["OAuth2"].
- "evidence" MUST be a flat array of STRINGS, not objects. Each
  string should be a single URL, optionally followed by " — " and a
  short description, e.g. "https://example.com/docs — auth guide".
  Do NOT return {{"url": ..., "description": ...}} objects.
- has_mcp must be true only when the notes contain evidence of an MCP server.
- confidence_pass1 must be a number between 0 and 100 (not 0 and 1).
- is_mock must be false.

Research notes:

{research_notes}
"""

        # JSON mode instead of with_structured_output().
        extractor_llm = llm.bind(
            response_format={"type": "json_object"}
        )

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

        # Defensive cleanup in case the model still returns fences.
        if content.startswith("```"):
            content = content.replace("```json", "", 1)
            content = content.replace("```", "", 1).strip()

        data = json.loads(content)

        # ---------------------------------------------------------
        # STEP 3: Validate
        # ---------------------------------------------------------
        data = AppResearchData.model_validate(data).model_dump()

        # Always trust the source app metadata.
        data["id"] = app_data["id"]
        data["name"] = app_name
        data["category"] = category
        data["is_mock"] = False

        return data

    except Exception as e:
        error_text = str(e)

        print(f"  ERROR researching {app_name}: {error_text}")

        # Don't hide the cause behind the mock record.
        return _mock_stub(app_data)


def _mock_stub(app_data: dict) -> dict:
    return {
        "id": app_data["id"],
        "name": app_data["name"],
        "category": app_data.get("category", "Unknown"),
        "one_liner": "RESEARCH FAILED -- no data collected.",
        "auth_methods": [],
        "access": "unknown",
        "api_surface_type": "unknown",
        "api_surface_breadth": "unknown",
        "has_mcp": False,
        "mcp_source": "none",
        "buildability": "unknown",
        "blocker": "Pipeline failure -- see logs, needs manual research.",
        "evidence": [],
        "confidence_pass1": 0.0,
        "is_mock": True,
    }


def main():
    parser = argparse.ArgumentParser(description="Run Pass 1 research agent over the app list.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=3.0, help="Seconds between apps.")
    args = parser.parse_args()

    if not composio_available():
        print(
            "WARNING: Composio is not configured. Rows will be flagged is_mock=True. "
            "Set COMPOSIO_API_KEY and configure Exa in Composio."
        )

    llm = ChatGroq(
        model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        groq_api_key=os.environ.get("GROQ_API_KEY"),
        max_retries=2,
        temperature=0,
        max_tokens=1500,
    )

    with open(APPS_FILE, "r") as f:
        apps = json.load(f)

    existing_ids = set()
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r") as f:
            for line in f:
                if line.strip():
                    try:
                        existing_ids.add(json.loads(line)["id"])
                    except Exception:
                        pass

    apps_to_process = apps[args.start : args.start + args.limit]
    tools = get_search_tools()

    with open(OUTPUT_FILE, "a") as f:
        for app in apps_to_process:
            if app["id"] in existing_ids:
                print(f"Skipping {app['name']} (already in {OUTPUT_FILE})")
                continue
            data = research_app(app, llm, tools=tools)
            f.write(json.dumps(data) + "\n")
            f.flush()
            time.sleep(args.sleep)

    print(f"\nPass 1 complete. Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()