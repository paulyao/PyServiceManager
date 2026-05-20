"""Log streaming API routes."""
from fastapi import APIRouter, Depends, HTTPException, WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.service import Service
from app.core.log_streamer import log_streamer

router = APIRouter(prefix="/services", tags=["logs"])


@router.get("/{name}/logs")
async def get_service_logs(name: str, lines: int = 200, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Service).where(Service.name == name))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    logs = await log_streamer.get_history(name, lines=lines)
    return {"logs": logs, "count": len(logs)}


@router.websocket("/{name}/logs/ws")
async def websocket_service_logs(websocket: WebSocket, name: str):
    await log_streamer.stream_to_websocket(name, websocket)
