"""FastAPI 主入口：MJPEG 视频流 + WebSocket 指标推送。

运行::

    python app.py
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from analyzer import UPLOADS_DIR, VIDEOS_DIR, engine
from server_config import resolve_server_bind

ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))

HOST, PORT = resolve_server_bind()


class StartRequest(BaseModel):
    model: str = Field(..., description="相对 my_uav_vehicle_single 的模型路径")
    video: str = Field(..., description="视频绝对路径或 uploads/videos 下文件名")
    params: Optional[Dict[str, Any]] = None
    step_mode: bool = Field(False, description="逐帧调试：启动后等待手动下一帧")


class ParamsRequest(BaseModel):
    params: Dict[str, Any]


class StepModeRequest(BaseModel):
    enabled: bool


class MetricsHub:
    def __init__(self) -> None:
        self.clients: Set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    def broadcast_threadsafe(self, payload: dict) -> None:
        if not self._loop or not self.clients:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)

    async def _broadcast(self, payload: dict) -> None:
        dead: List[WebSocket] = []
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


hub = MetricsHub()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    hub.bind_loop(asyncio.get_running_loop())
    engine.subscribe(hub.broadcast_threadsafe)
    yield
    engine.unsubscribe(hub.broadcast_threadsafe)
    engine.stop()


app = FastAPI(
    title="UAV Traffic Congestion Analyzer",
    version="1.1.0",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# 页面与 API
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/models")
async def api_models() -> JSONResponse:
    return JSONResponse({"models": engine.list_models()})


@app.get("/api/videos")
async def api_videos() -> JSONResponse:
    return JSONResponse({"videos": engine.list_videos()})


@app.get("/api/params")
async def api_get_params() -> JSONResponse:
    return JSONResponse({"params": engine.get_params()})


@app.get("/api/status")
async def api_status() -> JSONResponse:
    return JSONResponse(engine.get_status())


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)) -> JSONResponse:
    if not file.filename:
        raise HTTPException(400, "空文件名")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
        raise HTTPException(400, f"不支持的视频格式: {suffix}")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS_DIR / Path(file.filename).name
    dest.write_bytes(await file.read())
    return JSONResponse(
        {
            "ok": True,
            "name": dest.name,
            "path": str(dest.resolve()),
            "size_mb": f"{dest.stat().st_size / (1024 * 1024):.1f}",
        }
    )


@app.post("/api/start")
async def api_start(body: StartRequest) -> JSONResponse:
    try:
        engine.start(body.model, body.video, body.params, step_mode=body.step_mode)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"启动失败: {exc}") from exc
    return JSONResponse({"ok": True, "status": engine.get_status()})


@app.post("/api/stop")
async def api_stop() -> JSONResponse:
    engine.stop()
    return JSONResponse({"ok": True, "status": engine.get_status()})


@app.post("/api/pause")
async def api_pause() -> JSONResponse:
    paused = engine.toggle_pause()
    return JSONResponse({"ok": True, "paused": paused, "status": engine.get_status()})


@app.post("/api/reset")
async def api_reset() -> JSONResponse:
    engine.reset()
    return JSONResponse({"ok": True, "status": engine.get_status()})


@app.post("/api/params")
async def api_params(body: ParamsRequest) -> JSONResponse:
    engine.update_params(body.params)
    return JSONResponse({"ok": True, "params": engine.get_params()})


@app.post("/api/step-mode")
async def api_step_mode(body: StepModeRequest) -> JSONResponse:
    engine.set_step_mode(body.enabled)
    return JSONResponse({"ok": True, "status": engine.get_status()})


@app.post("/api/step-next")
async def api_step_next() -> JSONResponse:
    if not engine.step_next_frame():
        raise HTTPException(400, "未处于逐帧调试模式或未在运行")
    return JSONResponse({"ok": True, "status": engine.get_status()})


@app.get("/api/export/metrics.csv")
async def api_export_metrics_csv() -> Response:
    """下载当前会话全部帧级指标 CSV（可用 Excel / pandas 分析）。"""
    count = engine.get_metrics_row_count()
    if count <= 0:
        raise HTTPException(404, "暂无记录数据，请先运行分析")
    csv_text = engine.export_metrics_csv()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"congestion_metrics_{stamp}.csv"
    # UTF-8 BOM 便于 Excel 正确打开中文列
    body = b"\xef\xbb\xbf" + csv_text.encode("utf-8")
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# MJPEG：/video_feed（主） + /stream.mjpg（兼容）
# ---------------------------------------------------------------------------

async def _mjpeg_generator():
    boundary = "frame"
    last_jpeg: bytes = b""
    while True:
        jpeg = engine.get_jpeg()
        if jpeg and jpeg != last_jpeg:
            last_jpeg = jpeg
            yield (
                b"--" + boundary.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                + jpeg
                + b"\r\n"
            )
        await asyncio.sleep(0.03)


def _mjpeg_response() -> StreamingResponse:
    return StreamingResponse(
        _mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.get("/video_feed")
async def video_feed() -> StreamingResponse:
    """MJPEG 视频流（叠加检测框 / Track ID / 拥堵状态）。"""
    return _mjpeg_response()


@app.get("/stream.mjpg")
async def stream_mjpg() -> StreamingResponse:
    """兼容旧路径。"""
    return _mjpeg_response()


# ---------------------------------------------------------------------------
# WebSocket：/ws（主） + /ws/metrics（兼容）
# ---------------------------------------------------------------------------

async def _ws_metrics_handler(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        await ws.send_json({"type": "status", **engine.get_status()})
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=30.0)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping", "t": time.time()})
                continue

            if not isinstance(msg, dict):
                continue
            action = msg.get("action")
            if action == "ping":
                await ws.send_json({"type": "pong", "t": time.time()})
            elif action == "update_params" and isinstance(msg.get("params"), dict):
                engine.update_params(msg["params"])
                await ws.send_json({"type": "params", "params": engine.get_params()})
    except WebSocketDisconnect:
        hub.disconnect(ws)
    except Exception:
        hub.disconnect(ws)


@app.websocket("/ws")
async def ws_main(ws: WebSocket) -> None:
    """实时推送 congestion_index / v_avg / sigma_dist / scenario。"""
    await _ws_metrics_handler(ws)


@app.websocket("/ws/metrics")
async def ws_metrics(ws: WebSocket) -> None:
    await _ws_metrics_handler(ws)


if __name__ == "__main__":
    print("=" * 56)
    print(" UAV Traffic Congestion Analyzer")
    print(f" Open: http://localhost:{PORT}")
    print(f" Bind: {HOST}:{PORT}")
    print(" Providers: DirectML → CPU")
    print(" Models: my_uav_vehicle_single/**/*.{onnx,pt}")
    print(" Tip: set PORT=8001 to force a specific port")
    print("=" * 56)
    uvicorn.run(app, host=HOST, port=PORT, reload=False, log_level="info")
