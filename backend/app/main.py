"""本地性能监测 API：仅绑定 127.0.0.1，由浏览器控制台调用。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse

from .adb import AdbError, enrich_device, list_devices, list_surface_layers
from .models import StartSessionRequest
from .monitor import MonitorManager
from .interaction import InteractionError, InteractionTraceManager


DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    manager = MonitorManager(DATA_ROOT)
    manager.store.recover_interrupted_sessions()
    app.state.manager = manager
    app.state.interactions = InteractionTraceManager(DATA_ROOT)
    yield


app = FastAPI(title="Android 车机性能监测 API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def manager_from(request: Request) -> MonitorManager:
    return request.app.state.manager  # type: ignore[no-any-return]


@app.get("/api/health")
async def health(request: Request) -> dict[str, object]:
    try:
        devices = await list_devices()
        return {"status": "ok", "adb": "available", "device_count": len(devices), "active_sessions": len(manager_from(request).active)}
    except AdbError as exc:
        return {"status": "degraded", "adb": "unavailable", "detail": str(exc), "device_count": 0}


@app.get("/api/devices")
async def devices() -> list[dict[str, object]]:
    try:
        found = await list_devices()
        enriched = await __import__("asyncio").gather(*(enrich_device(device) for device in found))
        return [asdict(device) for device in enriched]
    except AdbError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/surface-layers")
async def surface_layers(serial: str = Query(min_length=1, max_length=128)) -> dict[str, object]:
    try:
        return {"layers": await list_surface_layers(serial)}
    except AdbError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/sessions", status_code=201)
async def start_session(payload: StartSessionRequest, request: Request) -> dict[str, object]:
    manager = manager_from(request)
    try:
        session_id = await manager.start(payload)
    except (AdbError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return manager.store.get_session(session_id) or {"session_id": session_id, "state": "running"}


@app.post("/api/sessions/{session_id}/stop")
async def stop_session(session_id: str, request: Request) -> dict[str, object]:
    manager = manager_from(request)
    try:
        await manager.stop(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    return manager.store.get_session(session_id) or {"session_id": session_id, "state": "stopped"}


@app.get("/api/sessions/{session_id}")
async def session_detail(session_id: str, request: Request) -> dict[str, object]:
    session = manager_from(request).store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


@app.get("/api/sessions/{session_id}/series")
async def session_series(session_id: str, request: Request, limit: int = Query(default=180, ge=1, le=1000)) -> dict[str, object]:
    store = manager_from(request).store
    if store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"points": store.get_series(session_id, limit)}


@app.get("/api/sessions/{session_id}/processes")
async def session_processes(
    session_id: str,
    request: Request,
    metric: str = Query(default="cpu", pattern="^(cpu|pss|rss)$"),
    limit: int = Query(default=10, ge=1, le=100),
) -> dict[str, object]:
    store = manager_from(request).store
    if store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"processes": store.get_processes(session_id, metric, limit)}


@app.get("/api/sessions/{session_id}/events")
async def session_events(session_id: str, request: Request) -> dict[str, object]:
    store = manager_from(request).store
    if store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"events": store.get_events(session_id)}


@app.get("/api/sessions/{session_id}/export", response_class=PlainTextResponse)
async def export_session(session_id: str, request: Request) -> PlainTextResponse:
    store = manager_from(request).store
    if store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return PlainTextResponse(
        store.to_csv(session_id),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{session_id}-samples.csv"'},
    )
def interactions_from(request: Request) -> InteractionTraceManager:
    return request.app.state.interactions  # type: ignore[no-any-return]


@app.post("/api/interactions", status_code=201)
async def start_interaction(payload: dict[str, object], request: Request) -> dict[str, object]:
    serial = payload.get("serial")
    duration = payload.get("duration_seconds", 15)
    if not isinstance(serial, str) or not serial.strip():
        raise HTTPException(status_code=422, detail="serial 不能为空")
    if not isinstance(duration, int):
        raise HTTPException(status_code=422, detail="duration_seconds 必须为整数")
    manager = interactions_from(request)
    try:
        interaction_id = await manager.start(serial.strip(), duration)
    except (InteractionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return manager.get(interaction_id) or {"interaction_id": interaction_id, "state": "queued"}


@app.get("/api/interactions/{interaction_id}")
async def interaction_detail(interaction_id: str, request: Request) -> dict[str, object]:
    detail = interactions_from(request).get(interaction_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="交互诊断不存在")
    return detail


@app.get("/api/interactions/{interaction_id}/trace")
async def download_interaction_trace(interaction_id: str, request: Request) -> FileResponse:
    trace_path = interactions_from(request).trace_file(interaction_id)
    if trace_path is None:
        raise HTTPException(status_code=409, detail="trace 尚未完成或不可用")
    return FileResponse(trace_path, media_type="application/octet-stream", filename=trace_path.name)
