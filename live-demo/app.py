"""
Single-endpoint FastAPI demo. Lets a reviewer type any app name into the report
page and watch the research agent actually run against it live, instead of
trusting a screenshot.

Run locally:
    uvicorn live-demo.app:app --reload --port 8000

Deploy (Render/Railway/Fly): point the start command at
    uvicorn live-demo.app:app --host 0.0.0.0 --port $PORT
and set report/template.html's DEMO_API_URL to the deployed URL.
"""

import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from langchain_groq import ChatGroq

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from research.research_agent import research_app
from research.composio_tools import get_search_tools

load_dotenv()

app = FastAPI(title="Composio App-Research Agent — Live Demo")

# Wide open on purpose: this is a read-only research demo with no auth/state,
# meant to be called directly from a static report page on another origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResearchRequest(BaseModel):
    app_name: str
    website_or_hint: str = ""
    category: str = "Unknown (live demo)"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/research")
def run_research(req: ResearchRequest):
    if not req.app_name.strip():
        raise HTTPException(status_code=400, detail="app_name is required")

    llm = ChatGroq(
        model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
        temperature=0,
        max_tokens=1400,
        groq_api_key=os.environ.get("GROQ_API_KEY"),
        max_retries=0,
    )
    tools = get_search_tools()

    app_data = {
        "id": 0,
        "name": req.app_name,
        "category": req.category,
        "website_or_hint": req.website_or_hint,
    }

    result = research_app(app_data, llm, tools=tools)
    if not result:
        raise HTTPException(status_code=500, detail="Agent failed to research the app.")
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
