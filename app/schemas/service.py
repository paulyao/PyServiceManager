"""Service Pydantic schemas."""
import json
from datetime import datetime


from pydantic import BaseModel, Field, field_validator


class ServiceCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$", description="Service name")
    display_name: str | None = None
    description: str | None = None
    code: str | None = None
    code_source: str = Field("editor", pattern=r"^(editor|upload)$")
    python_path: str = "auto"
    auto_restart: bool = True
    requirements: list[str] = Field(default_factory=list, description="Pip package requirements")


class ServiceUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    auto_restart: bool | None = None
    python_path: str | None = None
    requirements: list[str] | None = None


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
    requirements: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None

    model_config = {"from_attributes": True}

    @field_validator("requirements", mode="before")
    @classmethod
    def parse_requirements(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


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


class RequirementsUpdate(BaseModel):
    requirements: list[str]


# ── Dependency schemas ────────────────────────────────

class ModuleDepSource(BaseModel):
    module_name: str
    module_display_name: str
    requirements: list["PkgStatusItem"]


class ServiceDepsResponse(BaseModel):
    service_requirements: list["PkgStatusItem"]
    module_requirements: list[ModuleDepSource]
    all_requirements: list["PkgStatusItem"]
    all_satisfied: bool
    missing_count: int


# Import here to avoid circular references
from app.schemas.module import PkgStatusItem  # noqa: E402

ServiceDepsResponse.model_rebuild()
