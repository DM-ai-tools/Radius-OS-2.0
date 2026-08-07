from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1)
    role_name: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RoleOut(BaseModel):
    name: str
    label: str
    description: str


class PermissionOut(BaseModel):
    agent_key: str
    label: str
    can_trigger: bool
    can_approve: bool
    implemented: bool = True


class UserOut(BaseModel):
    id: UUID
    email: str
    full_name: str
    role_name: str
    role_label: str = ""
    is_active: bool
    permissions: list[PermissionOut] = []

    model_config = {"from_attributes": True}
