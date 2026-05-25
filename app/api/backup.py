"""Backup & Restore API routes."""
import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas.backup import (
    BackupCreateRequest, BackupItemsResponse, BackupPreviewResponse,
    RestoreOptions, RestoreResponse,
)
from app.core.backup_manager import BackupManager, BackupManagerError
from app.core.service_manager import ServiceManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backup", tags=["backup"])
backup_manager = BackupManager()
service_manager = ServiceManager()


@router.get("/items", response_model=BackupItemsResponse)
async def get_backup_items(session: AsyncSession = Depends(get_session)):
    """Get list of modules and services available for backup."""
    items = await backup_manager.get_backup_items(session)
    return BackupItemsResponse(**items)


@router.post("/create")
async def create_backup(
    data: BackupCreateRequest,
    session: AsyncSession = Depends(get_session),
):
    """Create a backup ZIP archive and return it for download."""
    try:
        buffer, filename = await backup_manager.create_backup(
            session=session,
            module_names=data.modules,
            service_names=data.services,
            include_bindings=data.include_bindings,
        )
        return StreamingResponse(
            content=iter([buffer.getvalue()]),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except BackupManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to create backup")
        raise HTTPException(status_code=500, detail=f"创建备份失败: {str(e)}")


@router.post("/preview", response_model=BackupPreviewResponse)
async def preview_backup(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Preview a backup ZIP: show contents and detect conflicts."""
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="仅支持 .zip 格式的备份文件")

    zip_bytes = await file.read()
    try:
        preview = await backup_manager.preview_backup(session, zip_bytes)
        return preview
    except BackupManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to preview backup")
        raise HTTPException(status_code=500, detail=f"预览备份失败: {str(e)}")


@router.post("/restore", response_model=RestoreResponse)
async def restore_backup(
    file: UploadFile = File(...),
    options: str = Form("{}"),
    session: AsyncSession = Depends(get_session),
):
    """Restore modules and services from a backup ZIP."""
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="仅支持 .zip 格式的备份文件")

    zip_bytes = await file.read()

    # Parse options
    try:
        options_data = json.loads(options)
        restore_options = RestoreOptions(**options_data)
    except (json.JSONDecodeError, Exception) as e:
        raise HTTPException(status_code=400, detail=f"无效的恢复选项: {str(e)}")

    try:
        result = await backup_manager.restore_backup(
            session=session,
            zip_bytes=zip_bytes,
            options=restore_options,
            service_manager=service_manager,
        )
        return result
    except BackupManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to restore backup")
        raise HTTPException(status_code=500, detail=f"恢复备份失败: {str(e)}")
