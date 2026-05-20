"""Config management API routes."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas.service import ConfigUpdate
from app.core.service_manager import ServiceManager, ServiceNotFoundError, ServiceManagerError

router = APIRouter(prefix="/services", tags=["config"])
service_manager = ServiceManager()


@router.get("/{name}/config")
async def get_service_config(name: str, session: AsyncSession = Depends(get_session)):
    try:
        config = await service_manager.get_config(session, name)
        return {"config": config}
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")


@router.put("/{name}/config")
async def update_service_config(name: str, data: ConfigUpdate, session: AsyncSession = Depends(get_session)):
    try:
        service = await service_manager.update_config(session, name, data.config)
        return {
            "message": "Config updated",
            "hot_reloaded": service.status == "running",
        }
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except ServiceManagerError as e:
        raise HTTPException(status_code=400, detail={"error": str(e), "detail": e.detail})
