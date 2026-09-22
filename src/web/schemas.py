"""Pydantic schemas for the Web API."""

from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class SessionSummary(BaseModel):
    """Summary of a council session."""

    session_id: str
    run_number: int
    created_at: datetime
    idea_preview: str
    status: str  # "completed" | "failed" | "running"
    agents: List[str]
    verdict_path: Optional[str] = None
    task_verdict_path: Optional[str] = None


class SessionDetail(SessionSummary):
    """Full session detail including verdict content."""

    verdict_markdown: Optional[str] = None
    task_verdict_markdown: Optional[str] = None
    rounds: dict = Field(default_factory=dict)
    vote: Optional[dict] = None
    claims_map: Optional[dict] = None
    claims_map_text: Optional[str] = None
    meta: Optional[dict] = None
    citation_mismatches: Optional[List[str]] = None


class RunResponse(BaseModel):
    """Response after starting a council run."""

    session_id: str
    status: str = "started"


class AgentConfig(BaseModel):
    """Roster entry for the web UI (mirrors CouncilMember, CLI-agnostic)."""

    id: str
    name: str
    kind: str = "cli"  # "cli" | "openai"
    command: Optional[List[str]] = None
    model: Optional[str] = None
    available: bool
    is_default: bool = False
    enabled: bool = True


class AgentsResponse(BaseModel):
    """List of available agents."""

    agents: List[AgentConfig]


# WebSocket message types
class WSMessage(BaseModel):
    """Base WebSocket message."""

    type: str


class WSAgentStatus(WSMessage):
    """Agent status update via WebSocket."""

    type: str = "agent_status"
    agent: str
    status: str
    stage: str
    detail: str = ""
    timestamp: float


class WSStageChange(WSMessage):
    """Stage change via WebSocket."""

    type: str = "stage_change"
    stage: str


class WSProgress(WSMessage):
    """Progress update via WebSocket."""

    type: str = "progress"
    done: int
    total: int
    elapsed: str


class WSLog(WSMessage):
    """Log line via WebSocket."""

    type: str = "log"
    line: str


class WSFinished(WSMessage):
    """Council finished via WebSocket."""

    type: str = "finished"
    session_dir: str
    verdict_path: str


class WSError(WSMessage):
    """Error via WebSocket."""

    type: str = "error"
    message: str
