"""HTTP request contracts for the multi-agent application boundary."""

from pydantic import BaseModel, ConfigDict, Field


class AgentWorkflowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=20000)
    agent_codes: list[str] = Field(default_factory=list, max_length=4)
    project_id: str = Field(min_length=36, max_length=36)
    research_session_id: str | None = Field(default=None, max_length=36)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)
    external_data_acknowledged: bool = False
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)
    deadline_seconds: int = Field(default=1800, ge=60, le=7200)
