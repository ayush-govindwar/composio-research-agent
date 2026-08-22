"""
Shared data schema for the Composio app-research pipeline.

AppResearchData  -> output of Pass 1 (research_agent.py)
AppVerificationData -> output of Pass 2 (verify_agent.py). Carries the original
                        fields forward plus verification metadata, so pass2.jsonl
                        is a complete, self-contained record (no need to re-join
                        against pass1.jsonl downstream).
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field

AccessType = Literal["self-serve", "gated-paid", "gated-approval", "gated-partnership", "unknown"]
BuildabilityType = Literal["ready", "possible-with-workaround", "blocked", "unknown"]
ApiSurfaceType = Literal["REST", "GraphQL", "SOAP", "gRPC", "none", "unknown"]
ApiBreadth = Literal["narrow", "broad", "full-platform", "unknown"]
McpSource = Literal["official", "community", "none", "unknown"]


class AppResearchData(BaseModel):
    id: int
    name: str
    category: str
    one_liner: str = Field(description="One sentence describing what the app does.")

    auth_methods: List[str] = Field(default_factory=list, description="e.g. ['OAuth2'], ['API Key'], ['Basic Auth']")
    access: AccessType = "unknown"

    api_surface_type: ApiSurfaceType = "unknown"
    api_surface_breadth: ApiBreadth = "unknown"
    has_mcp: bool = False
    mcp_source: McpSource = "none"

    buildability: BuildabilityType = "unknown"
    blocker: str = Field(default="", description="Main blocker if not agent-ready today. 'None' if ready.")

    evidence: List[str] = Field(default_factory=list, description="Docs/article URLs backing the answers above.")
    confidence_pass1: float = Field(default=0.0, ge=0.0, le=100.0)

    is_mock: bool = Field(default=False, description="True if this row is a fallback stub, not real research.")


class AppVerificationData(BaseModel):
    id: int
    name: str
    category: str
    one_liner: str

    auth_methods: List[str] = Field(default_factory=list)
    access: AccessType = "unknown"

    api_surface_type: ApiSurfaceType = "unknown"
    api_surface_breadth: ApiBreadth = "unknown"
    has_mcp: bool = False
    mcp_source: McpSource = "none"

    buildability: BuildabilityType = "unknown"
    blocker: str = ""

    evidence: List[str] = Field(default_factory=list)
    confidence_pass1: float = 0.0
    confidence_pass2: float = Field(default=0.0, ge=0.0, le=100.0)

    # Verification-specific fields
    is_accurate_pass1: bool = Field(default=True, description="False if pass 1 needed any correction.")
    corrections_made: List[str] = Field(default_factory=list, description="Plain-English list of what changed vs pass 1.")
    verification_source: Literal["fresh-search", "url-fetch", "both", "unverified"] = "unverified"
    verification_notes: str = ""

    is_mock: bool = Field(default=False, description="True if this row is a fallback stub, not real verification.")
