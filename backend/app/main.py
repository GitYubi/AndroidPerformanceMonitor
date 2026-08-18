"""本地性能监测 API：仅绑定 127.0.0.1，由浏览器控制台调用。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from .adb import AdbError, enrich_device, list_devices, list_surface_layers
from .fps_probe import PAGE_HTML, probe_status
from .frame_sources import probe_frame_capabilities
from .models import StartSessionRequest
from .monitor import MonitorManager
from .interaction import InteractionError, InteractionTraceManager
from .report import generate_report


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
    # 前端 dev server 可能落在 localhost 任意端口（3000 被占用时自动顺延），
    # 因此按回环地址 + 任意端口放行；后端本身仅绑定 127.0.0.1，不会暴露给外部。
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
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


@app.get("/api/frame-capabilities")
async def frame_capabilities(serial: str = Query(min_length=1, max_length=128)) -> dict[str, object]:
    """探测车机支持的帧率数据源（FrameTimeline / framestats / SF latency）。"""
    try:
        capabilities = await probe_frame_capabilities(serial)
    except AdbError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return capabilities.serializable()


@app.post("/api/sessions", status_code=201)
async def start_session(payload: StartSessionRequest, request: Request) -> dict[str, object]:
    manager = manager_from(request)
    try:
        session_id = await manager.start(payload)
    except (AdbError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return manager.store.get_session(session_id) or {"session_id": session_id, "state": "running"}


@app.get("/fps-probe", response_class=HTMLResponse)
async def fps_probe_page() -> HTMLResponse:
    """FPS 数据链路巡检页面（自包含，不依赖前端构建）。"""
    return HTMLResponse(content=PAGE_HTML)


@app.get("/api/fps-probe/status")
async def fps_probe_status(serial: str = Query(min_length=1, max_length=128)) -> dict[str, object]:
    """巡检当前前台界面的 R / P / Jank 可测状态。"""
    try:
        return await probe_status(serial)
    except AdbError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/sessions")
async def list_sessions(request: Request) -> dict[str, object]:
    """列出本地全部历史会话（按修改时间倒序）。"""
    return {"sessions": manager_from(request).store.list_sessions()}


@app.post("/api/sessions/import", status_code=201)
async def import_session(request: Request, file: UploadFile = File(...)) -> dict[str, object]:
    """导入外部会话数据库文件（monitor.db 二进制）。"""
    store = manager_from(request).store
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="文件为空")
    try:
        session_id = store.import_database(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return store.get_session(session_id) or {"session_id": session_id, "state": "imported"}


# 注意：必须在 /api/sessions/{session_id} 之前注册，否则 "active" 会被当作 session_id。
@app.get("/api/sessions/active")
async def active_session(request: Request) -> dict[str, object] | None:
    """返回当前正在运行的会话；前端刷新页面后据此恢复接管，而不是显示未开始状态。"""
    manager = manager_from(request)
    for session_id in manager.active:
        session = manager.store.get_session(session_id)
        if session is not None:
            return session
    return None


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


@app.get("/api/sessions/{session_id}/report")
async def session_report(
    session_id: str,
    request: Request,
    download: int = Query(default=0, ge=0, le=1),
) -> HTMLResponse:
    """生成会话的 HTML 性能测试报告（当前或历史会话均可）。"""
    store = manager_from(request).store
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        content = generate_report(session_id, store)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"报告生成失败：{exc}") from exc
    headers = {}
    if download:
        serial = str(session.get("serial", "device")).replace(":", "_")
        headers["Content-Disposition"] = f'attachment; filename="report_{serial}_{session_id[:8]}.html"'
    return HTMLResponse(content=content, headers=headers)


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
