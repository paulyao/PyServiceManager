"""Service Pydantic schemas."""
from datetime import datetime


from pydantic import BaseModel, Field


class ServiceCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$", description="Service name")
    display_name: str | None = None
    description: str | None = None
    code: str | None = None
    code_source: str = Field("editor", pattern=r"^(editor|upload)$")
    python_path: str = "auto"
    auto_restart: bool = True


class ServiceUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    auto_restart: bool | None = None
    python_path: str | None = None


class ServiceResponse(BaseModel):
    id: int
    name: str
    display_name: str | None
    description: str | None
    code_source: str
    status: str
    enabled: bool
    auto_restart: bool
    python_path: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None

    model_config = {"from_attributes": True}


class ServiceStatus(BaseModel):
    name: str
    active: str  # active, inactive, failed, unknown
    enabled: bool
    pid: int | None
    uptime: str | None


class CodeUpdate(BaseModel):
    code: str


class ConfigUpdate(BaseModel):
    config: str
