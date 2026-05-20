"""Module Pydantic schemas."""
from datetime import datetime

from pydantic import BaseModel, Field


class ModuleCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$", description="Module name")
    display_name: str
    description: str | None = None
    version: str = "1.0.0"
    author: str | None = None
    code: str | None = None
    config_toml: str | None = None
    code_source: str = Field("editor", pattern=r"^(editor|upload)$")


class ModuleUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    version: str | None = None
    author: str | None = None


class ModuleCodeUpdate(BaseModel):
    code: str
    config_toml: str | None = None


class ModuleResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: str | None
    version: str
    author: str | None
    code_source: str
    service_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ModuleCodeResponse(BaseModel):
    code: str
    config_toml: str | None = None


class ModuleValidateRequest(BaseModel):
    code: str


class ModuleValidateResponse(BaseModel):
    valid: bool
    errors: list[str] = []
    module_info: dict | None = None


class ServiceModuleItem(BaseModel):
    module_id: int
    name: str
    display_name: str
    version: str
    description: str | None
    enabled: bool
    load_order: int


class ServiceModulesUpdate(BaseModel):
    modules: list[dict] = Field(..., description="List of {module_id, enabled, load_order}")


class ServiceModulesResponse(BaseModel):
    modules: list[ServiceModuleItem]
    restart_required: bool = False
