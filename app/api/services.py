"""Service management API routes."""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.service import Service
from app.schemas.service import (
    ServiceCreate, ServiceUpdate, ServiceResponse, ServiceStatus, CodeUpdate, ConfigUpdate,
)
from app.core.service_manager import ServiceManager, ServiceNotFoundError, ServiceManagerError

router = APIRouter(prefix="/services", tags=["services"])
service_manager = ServiceManager()


@router.get("", response_model=list[ServiceResponse])
async def list_services(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Service).order_by(Service.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=ServiceResponse, status_code=201)
async def create_service(data: ServiceCreate, session: AsyncSession = Depends(get_session)):
    try:
        service = await service_manager.create(
            session=session,
            name=data.name,
            display_name=data.display_name,
            description=data.description,
            code_source=data.code_source,
            code=data.code,
            python_path=data.python_path,
            auto_restart=data.auto_restart,
        )
        return service
    except ServiceManagerError as e:
        raise HTTPException(status_code=400, detail={"error": str(e), "detail": e.detail})


@router.get("/{name}", response_model=ServiceResponse)
async def get_service(name: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Service).where(Service.name == name))
    service = result.scalar_one_or_none()
    if service is None:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    return service


@router.delete("/{name}", status_code=204)
async def delete_service(name: str, session: AsyncSession = Depends(get_session)):
    try:
        await service_manager.delete(session, name)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")


@router.post("/{name}/start", response_model=ServiceResponse)
async def start_service(name: str, session: AsyncSession = Depends(get_session)):
    try:
        return await service_manager.start(session, name)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        raise HTTPException(status_code=500, detail={"error": str(e), "detail": e.detail})


@router.post("/{name}/stop", response_model=ServiceResponse)
async def stop_service(name: str, session: AsyncSession = Depends(get_session)):
    try:
        return await service_manager.stop(session, name)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        raise HTTPException(status_code=500, detail={"error": str(e), "detail": e.detail})


@router.post("/{name}/restart", response_model=ServiceResponse)
async def restart_service(name: str, session: AsyncSession = Depends(get_session)):
    try:
        return await service_manager.restart(session, name)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        raise HTTPException(status_code=500, detail={"error": str(e), "detail": e.detail})


@router.post("/{name}/enable", response_model=ServiceResponse)
async def enable_service(name: str, session: AsyncSession = Depends(get_session)):
    try:
        return await service_manager.enable(session, name)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        raise HTTPException(status_code=500, detail={"error": str(e), "detail": e.detail})


@router.post("/{name}/disable", response_model=ServiceResponse)
async def disable_service(name: str, session: AsyncSession = Depends(get_session)):
    try:
        return await service_manager.disable(session, name)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        raise HTTPException(status_code=500, detail={"error": str(e), "detail": e.detail})


@router.get("/{name}/status", response_model=ServiceStatus)
async def get_service_status(name: str, session: AsyncSession = Depends(get_session)):
    try:
        status = await service_manager.get_status(session, name)
        return ServiceStatus(
            name=name,
            active=status.get("active", "unknown"),
            enabled=status.get("enabled", False),
            pid=status.get("pid"),
            uptime=None,
        )
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")


# ── Code Management ────────────────────────────────────────────

@router.get("/{name}/code")
async def get_service_code(name: str, session: AsyncSession = Depends(get_session)):
    try:
        code = await service_manager.get_code(session, name)
        return {"code": code}
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")


@router.put("/{name}/code")
async def update_service_code(name: str, data: CodeUpdate, session: AsyncSession = Depends(get_session)):
    try:
        service = await service_manager.update_code(session, name, data.code)
        return {
            "message": "Code updated",
            "status": service.status,
            "needs_restart": service.status == "running",
        }
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        raise HTTPException(status_code=400, detail={"error": str(e), "detail": e.detail})


@router.post("/{name}/upload")
async def upload_service_script(name: str, file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    if not file.filename.endswith(".py"):
        raise HTTPException(status_code=400, detail="Only .py files are allowed")

    content = (await file.read()).decode("utf-8")
    try:
        service = await service_manager.update_code(session, name, content)
        return {"message": "Script uploaded", "filename": file.filename}
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        raise HTTPException(status_code=400, detail={"error": str(e), "detail": e.detail})
