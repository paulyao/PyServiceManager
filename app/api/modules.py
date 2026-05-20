"""Module management API routes."""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.module import Module
from app.models.service import Service
from app.schemas.module import (
    ModuleCreate, ModuleUpdate, ModuleResponse, ModuleCodeUpdate,
    ModuleValidateRequest, ModuleValidateResponse,
    ServiceModulesUpdate, ServiceModulesResponse, ServiceModuleItem,
)
from app.core.module_manager import ModuleManager, ModuleNotFoundError, ModuleManagerError, BuiltinModuleError
from app.utils.validation import validate_module_code

router = APIRouter(prefix="/modules", tags=["modules"])
module_manager = ModuleManager()


@router.get("", response_model=list[ModuleResponse])
async def list_modules(session: AsyncSession = Depends(get_session)):
    modules = await module_manager.list_all(session)
    result = []
    for mod in modules:
        service_count = await module_manager.get_service_count(session, mod.id)
        resp = ModuleResponse.model_validate(mod)
        resp.service_count = service_count
        result.append(resp)
    return result


@router.post("", response_model=ModuleResponse, status_code=201)
async def create_module(data: ModuleCreate, session: AsyncSession = Depends(get_session)):
    try:
        module = await module_manager.create(
            session=session,
            name=data.name,
            display_name=data.display_name,
            description=data.description,
            version=data.version,
            author=data.author,
            code_source=data.code_source,
            code=data.code,
            config_toml=data.config_toml,
        )
        resp = ModuleResponse.model_validate(module)
        resp.service_count = 0
        return resp
    except ModuleManagerError as e:
        detail_msg = f"{str(e)}: {e.detail}" if hasattr(e, 'detail') else str(e)
        raise HTTPException(status_code=400, detail=detail_msg)


# ── Validation (must be before /{name} routes) ────────────────

@router.post("/validate", response_model=ModuleValidateResponse)
async def validate_module_code_endpoint(data: ModuleValidateRequest):
    """Validate module code without creating it."""
    valid, errors, module_info = validate_module_code(data.code)
    return ModuleValidateResponse(valid=valid, errors=errors, module_info=module_info)


# ── Service Module Bindings (must be before /{name} routes) ───

@router.get("/service/{service_name}", response_model=ServiceModulesResponse)
async def get_service_modules(service_name: str, session: AsyncSession = Depends(get_session)):
    try:
        modules = await module_manager.get_service_modules(session, service_name)
        items = [ServiceModuleItem(**m) for m in modules]
        return ServiceModulesResponse(modules=items)
    except ModuleManagerError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/service/{service_name}", response_model=ServiceModulesResponse)
async def update_service_modules(service_name: str, data: ServiceModulesUpdate, session: AsyncSession = Depends(get_session)):
    try:
        result = await module_manager.update_service_modules(session, service_name, data.modules)
        modules = await module_manager.get_service_modules(session, service_name)
        items = [ServiceModuleItem(**m) for m in modules]
        return ServiceModulesResponse(
            modules=items,
            restart_required=result["restart_required"],
        )
    except ModuleManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Module CRUD ───────────────────────────────────────────────

@router.get("/{name}", response_model=ModuleResponse)
async def get_module(name: str, session: AsyncSession = Depends(get_session)):
    try:
        module = await module_manager.get(session, name)
        resp = ModuleResponse.model_validate(module)
        resp.service_count = await module_manager.get_service_count(session, module.id)
        return resp
    except ModuleNotFoundError:
        raise HTTPException(status_code=404, detail=f"Module '{name}' not found")


@router.put("/{name}", response_model=ModuleResponse)
async def update_module(name: str, data: ModuleUpdate, session: AsyncSession = Depends(get_session)):
    try:
        module = await module_manager.update(
            session=session,
            name=name,
            new_name=data.name,
            display_name=data.display_name,
            description=data.description,
            version=data.version,
            author=data.author,
        )
        resp = ModuleResponse.model_validate(module)
        resp.service_count = await module_manager.get_service_count(session, module.id)
        return resp
    except ModuleNotFoundError:
        raise HTTPException(status_code=404, detail=f"Module '{name}' not found")
    except ModuleManagerError as e:
        detail_msg = f"{str(e)}: {e.detail}" if hasattr(e, 'detail') else str(e)
        raise HTTPException(status_code=400, detail=detail_msg)


@router.delete("/{name}", status_code=204)
async def delete_module(name: str, session: AsyncSession = Depends(get_session)):
    try:
        await module_manager.delete(session, name)
    except ModuleNotFoundError:
        raise HTTPException(status_code=404, detail=f"Module '{name}' not found")
    except BuiltinModuleError as e:
        raise HTTPException(status_code=403, detail=str(e))


# ── Code Management ────────────────────────────────────────────

@router.get("/{name}/code")
async def get_module_code(name: str, session: AsyncSession = Depends(get_session)):
    try:
        return await module_manager.get_code(session, name)
    except ModuleNotFoundError:
        raise HTTPException(status_code=404, detail=f"Module '{name}' not found")


@router.put("/{name}/code")
async def update_module_code(name: str, data: ModuleCodeUpdate, session: AsyncSession = Depends(get_session)):
    try:
        module = await module_manager.get(session, name)
        service_count = await module_manager.get_service_count(session, module.id)
        await module_manager.update_code(session, name, data.code, data.config_toml)
        return {
            "message": "Module code updated",
            "affected_services": service_count,
            "restart_required": service_count > 0,
        }
    except ModuleNotFoundError:
        raise HTTPException(status_code=404, detail=f"Module '{name}' not found")
    except ModuleManagerError as e:
        detail_msg = f"{str(e)}: {e.detail}" if hasattr(e, 'detail') else str(e)
        raise HTTPException(status_code=400, detail=detail_msg)


@router.post("/{name}/upload")
async def upload_module_file(name: str, file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    if not file.filename.endswith(".py"):
        raise HTTPException(status_code=400, detail="Only .py files are allowed")

    content = (await file.read()).decode("utf-8")
    try:
        module = await module_manager.get(session, name)
        service_count = await module_manager.get_service_count(session, module.id)
        await module_manager.update_code(session, name, content)
        return {
            "message": "Module file uploaded",
            "filename": file.filename,
            "affected_services": service_count,
        }
    except ModuleNotFoundError:
        raise HTTPException(status_code=404, detail=f"Module '{name}' not found")
    except ModuleManagerError as e:
        detail_msg = f"{str(e)}: {e.detail}" if hasattr(e, 'detail') else str(e)
        raise HTTPException(status_code=400, detail=detail_msg)


# ── Module-Service Relationship ────────────────────────────────

@router.get("/{name}/services")
async def get_module_services(name: str, session: AsyncSession = Depends(get_session)):
    try:
        module = await module_manager.get(session, name)
        services = await module_manager.get_services_using_module(session, module.id)
        return {
            "services": [
                {"name": s.name, "display_name": s.display_name, "status": s.status}
                for s in services
            ]
        }
    except ModuleNotFoundError:
        raise HTTPException(status_code=404, detail=f"Module '{name}' not found")
