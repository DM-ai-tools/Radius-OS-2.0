from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SessionCreate(BaseModel):
    client_id: UUID


class SessionOut(BaseModel):
    id: UUID
    client_id: UUID
    user_id: UUID
    active_agent_key: str | None
    is_onboarding: bool
    started_at: datetime
    ended_at: datetime | None
    archived_at: datetime | None = None

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: UUID
    session_id: UUID
    role: str
    content: str
    structured_payload: dict | None
    agent_key: str | None
    created_at: datetime
    archived_at: datetime | None = None

    model_config = {"from_attributes": True}


class ReviewRequest(BaseModel):
    action: str  # approve | edit | reject | flag_for_client
    edits: dict | None = None
    note: str | None = None


class QuestionnaireSubmit(BaseModel):
    fields: dict[str, object]
    session_id: UUID | None = None


class ManualCompetitor(BaseModel):
    name: str
    url: str


class ReadinessGateRequest(BaseModel):
    approve: bool
    note: str | None = None
