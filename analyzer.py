"""实时视频拥堵分析引擎：ONNX+DirectML + 隔帧推理 + CongestionCalculator。"""

from __future__ import annotations

import csv
import io
import queue
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
from ultralytics import YOLO

from ort_dml import get_ort_providers, patch_ultralytics_onnx_for_directml
from traffic_analysis import CongestionCalculator, CongestionConfig, DataCleaner

ROOT = Path(__file__).resolve().parent
# 权重目录（用户所称 _single → my_uav_vehicle_single）
SINGLE_DIR = ROOT / "my_uav_vehicle_single"
VIDEOS_DIR = ROOT / "videos"
UPLOADS_DIR = ROOT / "uploads"

JPEG_QUALITY = 45
FRAME_QUEUE_SIZE = 2
# 每 N 帧做一次 YOLO 检测；中间帧用轨迹预测
DETECT_EVERY_N = 2
# 输出锁死 30 FPS：不追满 GPU/内存，算法与图表均按此时基
LOCKED_FPS = 30.0

SCENARIO_LABELS = {
    "A_free_flow": "畅通",
    "B_orderly_wait": "有序等待",
    "C_stop_and_go": "走走停停",
    "D_slow_follow": "慢速跟车",
    "D_transition": "过渡状态",
    "E_intersection": "路口等灯",
    "hold_few_vehicles": "车辆不足",
    "edge_few_vehicles": "车辆不足",
    "empty_reset": "无车复位",
    "init": "就绪",
}

_SENTINEL = object()

# 启动时注入 DirectML provider
_DML_PROVIDER = patch_ultralytics_onnx_for_directml()
print(f"[ort] providers prefer={get_ort_providers()} active_primary={_DML_PROVIDER}")


@dataclass
class AnalysisParams:
    """可从前端热更新的分析参数。"""

    index_alpha: float = 0.67
    index_beta: float = 0.33
    speed_threshold: float = 0.5
    v_high: float = 8.0
    v_max: float = 15.0
    sigma_low: float = 5.0
    sigma_high: float = 20.0
    sigma_max: float = 40.0
    deadzone: float = 2.0
    ema_alpha: float = 0.4
    conf: float = 0.25
    iou: float = 0.5
    max_long_edge: int = 640
    window_seconds: float = 3.0
    detect_every_n: int = DETECT_EVERY_N


@dataclass
class FrameMetrics:
    """单帧推送给前端的指标。"""

    frame_idx: int = 0
    timestamp: float = 0.0
    congestion_index: float = 0.0
    v_avg: float = 0.0
    sigma_dist: float = 0.0
    scenario: str = "init"
    scenario_label: str = "就绪"
    vehicle_count: int = 0
    fps: float = 0.0
    inferred: bool = True  # True=本帧跑了 YOLO；False=轨迹预测帧
    # 调试中间量
    raw_index: float = 0.0
    median_distance: float = 0.0
    dist_window_mean: float = 0.0
    dist_window_len: int = 0
    v_moving_avg: float = 0.0
    r_stopped: float = 0.0
    sigma_static: float = 0.0
    platoon_count: int = 0
    intra_gap_count: int = 0
    gap_threshold: float = 0.0
    median_bbox_span: float = 0.0
    empty_frames_counter: int = 0
    hold_empty_seconds: float = 0.0
    dist_warmup_remaining: int = 0
    dist_appended: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class _TrackState:
    """单目标缓存，用于隔帧轨迹预测。"""

    track_id: int
    xyxy: np.ndarray  # (4,)
    center: Tuple[float, float]
    velocity: Tuple[float, float]  # px/frame
    conf: float = 0.0


class VideoAnalysisEngine:
    """双线程分析引擎：Producer（读帧+推理/预测） / Consumer（JPEG 推流）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._producer_thread: Optional[threading.Thread] = None
        self._consumer_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()

        self._model: Optional[YOLO] = None
        self._model_path: Optional[str] = None
        self._source_model: Optional[str] = None
        self._cap: Optional[cv2.VideoCapture] = None

        self._config = CongestionConfig()
        self._cleaner = DataCleaner(self._config)
        self._calculator = CongestionCalculator(self._config, self._cleaner)
        self._params = AnalysisParams()

        self._latest_jpeg: bytes = _placeholder_jpeg("Waiting for analysis…")
        self._latest_metrics = FrameMetrics()
        self._running = False
        self._paused = False

        self._frame_queue: queue.Queue = queue.Queue(maxsize=FRAME_QUEUE_SIZE)
        self._track_cache: Dict[int, _TrackState] = {}

        self._subscribers: Set[Callable[[dict], None]] = set()
        self._metric_history: List[FrameMetrics] = []

        self._step_mode: bool = False
        self._step_gate: threading.Event = threading.Event()
        self._step_waiting: bool = False

    # ------------------------------------------------------------------
    # 资源枚举
    # ------------------------------------------------------------------

    @staticmethod
    def list_models() -> List[Dict[str, str]]:
        """扫描 ``my_uav_vehicle_single``：优先 ``.onnx``，其次 ``.pt``。"""
        if not SINGLE_DIR.is_dir():
            return []
        items: List[Dict[str, str]] = []
        for p in sorted(SINGLE_DIR.rglob("*.onnx")):
            if p.is_file():
                items.append(_model_entry(p, "onnx"))
        for p in sorted(SINGLE_DIR.rglob("*.pt")):
            if p.is_file():
                items.append(_model_entry(p, "pt"))
        return items

    @staticmethod
    def list_videos() -> List[Dict[str, str]]:
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        files: List[Path] = []
        for folder in (VIDEOS_DIR, UPLOADS_DIR):
            for ext in ("*.mp4", "*.avi", "*.mov", "*.mkv", "*.webm"):
                files.extend(folder.glob(ext))
        files = sorted(set(files), key=lambda p: p.stat().st_mtime, reverse=True)
        return [
            {
                "name": p.name,
                "path": str(p.resolve()),
                "folder": p.parent.name,
                "size_mb": f"{p.stat().st_size / (1024 * 1024):.1f}",
            }
            for p in files
        ]

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------

    def start(
        self,
        model_name: str,
        video_path: str,
        params: Optional[dict] = None,
        step_mode: bool = False,
    ) -> None:
        with self._lock:
            if self._running:
                self.stop()

            model_file = (SINGLE_DIR / model_name).resolve()
            try:
                model_file.relative_to(SINGLE_DIR.resolve())
            except ValueError as exc:
                raise FileNotFoundError(f"非法模型路径: {model_name}") from exc
            if not model_file.is_file():
                raise FileNotFoundError(f"模型不存在: {model_file}")

            video = Path(video_path)
            if not video.is_file():
                for folder in (VIDEOS_DIR, UPLOADS_DIR):
                    candidate = folder / video_path
                    if candidate.is_file():
                        video = candidate
                        break
            if not video.is_file():
                raise FileNotFoundError(f"视频不存在: {video_path}")

            if params:
                self.update_params(params)

            self._stop_event.clear()
            self._pause_event.clear()
            self._paused = False
            self._step_mode = step_mode
            self._step_waiting = step_mode
            if step_mode:
                self._step_gate.clear()
                self._paused = True
            else:
                self._step_gate.set()
            self._drain_queue()
            self._track_cache.clear()

            self._latest_jpeg = _placeholder_jpeg("Loading ONNX / DirectML…")
            infer_path = self._ensure_onnx_weights(model_file)

            if self._model is None or self._model_path != str(infer_path):
                # 再次确保 DML patch（防止被其他 import 覆盖）
                patch_ultralytics_onnx_for_directml()
                self._model = YOLO(str(infer_path), task="detect")
                self._model_path = str(infer_path)
            else:
                self._model.predictor = None
            self._source_model = str(model_file)

            self._rebuild_pipeline()
            self._cap = cv2.VideoCapture(str(video))
            if not self._cap.isOpened():
                raise RuntimeError(f"无法打开视频: {video}")

            self._running = True
            self._latest_jpeg = _placeholder_jpeg("Starting…")
            self._producer_thread = threading.Thread(
                target=self._producer_loop, name="analysis-producer", daemon=True
            )
            self._consumer_thread = threading.Thread(
                target=self._consumer_loop, name="analysis-consumer", daemon=True
            )
            self._producer_thread.start()
            self._consumer_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.clear()
        try:
            self._frame_queue.put_nowait(_SENTINEL)
        except queue.Full:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frame_queue.put_nowait(_SENTINEL)
            except queue.Full:
                pass

        threads = []
        with self._lock:
            if self._producer_thread:
                threads.append(self._producer_thread)
            if self._consumer_thread:
                threads.append(self._consumer_thread)
            self._running = False
            self._paused = False
            self._step_mode = False
            self._step_gate.set()
            self._step_waiting = False

        for thread in threads:
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=5.0)

        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
            self._producer_thread = None
            self._consumer_thread = None
            self._drain_queue()
            self._track_cache.clear()
            self._latest_jpeg = _placeholder_jpeg("Stopped")
            self._latest_metrics = FrameMetrics()

    def reset(self) -> None:
        """停止分析并清空拥堵状态（重置按钮）。"""
        self.stop()
        with self._lock:
            self._cleaner.reset()
            self._calculator.reset()
            self._track_cache.clear()
            self._metric_history.clear()
            self._latest_jpeg = _placeholder_jpeg("Ready")

    def pause(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._paused = True
            self._pause_event.set()

    def resume(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._paused = False
            self._pause_event.clear()

    def toggle_pause(self) -> bool:
        with self._lock:
            if not self._running:
                return False
            if self._step_mode:
                return True
            if self._paused:
                self.resume()
                return False
            self.pause()
            return True

    def set_step_mode(self, enabled: bool) -> None:
        """开启/关闭逐帧调试。开启后 producer 每帧等待 step_next_frame()。"""
        with self._lock:
            if not self._running:
                self._step_mode = enabled
                return
            self._step_mode = enabled
            if enabled:
                self._pause_event.clear()
                self._paused = True
                self._step_gate.clear()
                self._step_waiting = True
            else:
                self._step_gate.set()
                self._step_waiting = False
                self._paused = False

    def step_next_frame(self) -> bool:
        """逐帧模式下放行一帧（仅在本帧已处理完毕、等待中时有效）。"""
        with self._lock:
            if not self._running or not self._step_mode:
                return False
            if not self._step_waiting:
                return False
            self._step_gate.set()
            self._step_waiting = False
            return True

    def update_params(self, params: dict) -> None:
        with self._lock:
            old_window = self._params.window_seconds
            for key, value in params.items():
                if hasattr(self._params, key) and value is not None:
                    setattr(self._params, key, type(getattr(self._params, key))(value))
            self._apply_params_to_config()
            if abs(self._params.window_seconds - old_window) > 1e-9:
                self._calculator.resize_window()

    def get_params(self) -> dict:
        with self._lock:
            return asdict(self._params)

    def get_status(self) -> dict:
        with self._lock:
            model_name = None
            if self._model_path:
                try:
                    model_name = str(
                        Path(self._model_path).resolve().relative_to(SINGLE_DIR.resolve())
                    ).replace("\\", "/")
                except ValueError:
                    model_name = Path(self._model_path).name
            return {
                "running": self._running,
                "paused": self._paused,
                "step_mode": self._step_mode,
                "step_waiting": self._step_waiting,
                "metrics_count": len(self._metric_history),
                "model": model_name,
                "providers": get_ort_providers(),
                "metrics": self._latest_metrics.to_dict(),
            }

    def export_metrics_csv(self) -> str:
        """将全部已记录帧指标导出为 CSV 字符串（UTF-8 BOM 友好）。"""
        fieldnames = [
            "frame_idx",
            "timestamp",
            "congestion_index",
            "raw_index",
            "v_avg",
            "v_moving_avg",
            "sigma_dist",
            "sigma_static",
            "median_distance",
            "dist_window_mean",
            "dist_window_len",
            "r_stopped",
            "vehicle_count",
            "platoon_count",
            "intra_gap_count",
            "gap_threshold",
            "median_bbox_span",
            "empty_frames_counter",
            "hold_empty_seconds",
            "dist_warmup_remaining",
            "dist_appended",
            "scenario",
            "scenario_label",
            "inferred",
            "fps",
        ]
        with self._lock:
            rows = [m.to_dict() for m in self._metric_history]

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return buf.getvalue()

    def get_metrics_row_count(self) -> int:
        with self._lock:
            return len(self._metric_history)

    def get_jpeg(self) -> bytes:
        with self._lock:
            return self._latest_jpeg

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._subscribers.add(callback)

    def unsubscribe(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._subscribers.discard(callback)

    # ------------------------------------------------------------------
    # ONNX
    # ------------------------------------------------------------------

    def _ensure_onnx_weights(self, model_file: Path) -> Path:
        """优先已有 ``.onnx``；仅有 ``.pt`` 时一次性 export。"""
        suffix = model_file.suffix.lower()
        if suffix == ".onnx":
            return model_file
        if suffix != ".pt":
            raise ValueError(f"不支持的模型格式: {model_file.suffix}")

        existing = _find_existing_onnx(model_file)
        if existing is not None:
            print(f"[model] 使用已有 ONNX: {existing}")
            return existing

        print(f"[model] 导出 ONNX（一次性）: {model_file.name}")
        with self._lock:
            self._latest_jpeg = _placeholder_jpeg("Exporting ONNX (one-time)…")

        pt_model = YOLO(str(model_file))
        exported = pt_model.export(
            format="onnx",
            imgsz=640,
            simplify=True,
            dynamic=False,
            opset=12,
        )
        onnx_path = Path(str(exported))
        if not onnx_path.is_file():
            raise RuntimeError(f"ONNX 导出失败: {exported}")
        print(f"[model] ONNX 已导出: {onnx_path}")
        return onnx_path

    # ------------------------------------------------------------------
    # 流水线
    # ------------------------------------------------------------------

    def _rebuild_pipeline(self) -> None:
        self._apply_params_to_config()
        self._config = CongestionConfig(
            fps=30.0,
            window_seconds=self._params.window_seconds,
            deadzone=self._params.deadzone,
            alpha=self._params.ema_alpha,
            speed_threshold=self._params.speed_threshold,
            v_high=self._params.v_high,
            v_max=self._params.v_max,
            sigma_low=self._params.sigma_low,
            sigma_high=self._params.sigma_high,
            sigma_max=self._params.sigma_max,
            index_alpha=self._params.index_alpha,
            index_beta=self._params.index_beta,
        )
        self._cleaner = DataCleaner(self._config)
        self._calculator = CongestionCalculator(self._config, self._cleaner)
        self._metric_history.clear()

    def _apply_params_to_config(self) -> None:
        p = self._params
        cfg = self._config
        cfg.fps = LOCKED_FPS
        cfg.index_alpha = p.index_alpha
        cfg.index_beta = p.index_beta
        cfg.speed_threshold = p.speed_threshold
        cfg.v_high = p.v_high
        cfg.v_max = p.v_max
        cfg.sigma_low = p.sigma_low
        cfg.sigma_high = p.sigma_high
        cfg.sigma_max = p.sigma_max
        cfg.deadzone = p.deadzone
        cfg.alpha = p.ema_alpha
        cfg.window_seconds = p.window_seconds
        self._cleaner.config = cfg
        self._calculator.config = cfg
        self._cleaner.config.fps = LOCKED_FPS
    def _drain_queue(self) -> None:
        while True:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                break

    def _enqueue_frame(self, annotated: np.ndarray, metrics: FrameMetrics) -> None:
        item = (annotated, metrics)
        try:
            self._frame_queue.put_nowait(item)
        except queue.Full:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frame_queue.put_nowait(item)
            except queue.Full:
                pass

    def _producer_loop(self) -> None:
        assert self._cap is not None and self._model is not None
        frame_idx = 0
        fps_ema = 0.0

        src_fps = float(self._cap.get(cv2.CAP_PROP_FPS) or LOCKED_FPS)
        if src_fps <= 1e-3:
            src_fps = LOCKED_FPS
        # 算法窗口 / 平滑一律按锁帧率，与片源标称 FPS 解耦
        self._config.fps = LOCKED_FPS
        self._cleaner.config.fps = LOCKED_FPS

        frame_dt = 1.0 / LOCKED_FPS
        frame_interval = frame_dt
        # 片源快于 30 时丢帧保实时，避免半速播放还狂吃算力
        src_per_out = max(src_fps / LOCKED_FPS, 1.0)
        skip_accum = 0.0
        next_tick = time.perf_counter()

        while not self._stop_event.is_set():
            step_mode = False
            with self._lock:
                step_mode = self._step_mode

            if step_mode:
                if not self._step_gate.wait(timeout=0.05):
                    continue
                self._step_gate.clear()
                with self._lock:
                    self._step_waiting = False
            elif self._pause_event.is_set():
                time.sleep(0.05)
                next_tick = time.perf_counter()
                continue

            # 逐帧模式：严格一帧一帧，不跳片源帧
            if step_mode:
                ok, frame = self._cap.read()
            else:
                skip_accum += src_per_out
                n_advance = max(1, int(skip_accum))
                skip_accum -= n_advance
                for _ in range(n_advance - 1):
                    if not self._cap.grab():
                        break
                ok, frame = self._cap.read()

            if not ok:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self._cleaner.reset()
                self._calculator.reset()
                self._track_cache.clear()
                if self._model is not None:
                    self._model.predictor = None
                frame_idx = 0
                skip_accum = 0.0
                next_tick = time.perf_counter()
                continue

            t0 = time.perf_counter()
            with self._lock:
                params = AnalysisParams(**asdict(self._params))
                self._apply_params_to_config()

            resized, _ = resize_long_edge(frame, params.max_long_edge)
            every_n = max(1, int(params.detect_every_n))
            do_infer = (frame_idx % every_n == 0) or (not self._track_cache)

            if do_infer:
                annotated, metrics = self._infer_and_track(
                    resized, frame_idx, LOCKED_FPS, params, frame_dt
                )
            else:
                annotated, metrics = self._predict_tracks(
                    resized, frame_idx, LOCKED_FPS, frame_dt
                )

            # 硬锁 30 FPS：逐帧模式不节流
            if not step_mode:
                next_tick += frame_interval
                now = time.perf_counter()
                sleep_t = next_tick - now
                if sleep_t > 0.0005:
                    time.sleep(sleep_t)
                elif sleep_t < -0.25:
                    next_tick = time.perf_counter()

            wall_dt = time.perf_counter() - t0
            inst_fps = 1.0 / wall_dt if wall_dt > 1e-6 else LOCKED_FPS
            fps_ema = inst_fps if fps_ema <= 0 else (0.85 * fps_ema + 0.15 * inst_fps)
            metrics.fps = round(fps_ema, 1)

            self._enqueue_frame(annotated, metrics)
            frame_idx += 1

            if step_mode:
                with self._lock:
                    self._step_waiting = True

        try:
            self._frame_queue.put_nowait(_SENTINEL)
        except queue.Full:
            pass
        with self._lock:
            self._running = False

    def _consumer_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._frame_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break

            annotated, metrics = item  # type: ignore[misc]
            ok_enc, buf = cv2.imencode(
                ".jpg",
                annotated,
                [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
            )
            jpeg = buf.tobytes() if ok_enc else self.get_jpeg()

            with self._lock:
                self._latest_jpeg = jpeg
                self._latest_metrics = metrics
                self._metric_history.append(metrics)
                subscribers = list(self._subscribers)
                payload = metrics.to_dict()
                payload["step_mode"] = self._step_mode
                payload["step_waiting"] = self._step_waiting
                payload["metrics_count"] = len(self._metric_history)

            for cb in subscribers:
                try:
                    cb(payload)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # 检测 / 隔帧预测
    # ------------------------------------------------------------------

    def _infer_and_track(
        self,
        frame: np.ndarray,
        frame_idx: int,
        src_fps: float,
        params: AnalysisParams,
        dt: float = 1.0 / 30.0,
    ) -> Tuple[np.ndarray, FrameMetrics]:
        assert self._model is not None
        results = self._model.track(
            source=frame,
            persist=True,
            tracker="bytetrack.yaml",
            conf=params.conf,
            iou=params.iou,
            verbose=False,
        )

        detections: List[dict] = []
        annotated = frame.copy()
        boxes = results[0].boxes
        new_cache: Dict[int, _TrackState] = {}

        if boxes is not None and len(boxes) > 0:
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy() if boxes.conf is not None else np.zeros(len(xyxy))
            ids = (
                boxes.id.cpu().numpy().astype(int)
                if boxes.id is not None
                else np.arange(len(xyxy))
            )
            for i, (box, tid) in enumerate(zip(xyxy, ids)):
                tid_i = int(tid)
                x1, y1, x2, y2 = [float(v) for v in box.tolist()]
                cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                prev = self._track_cache.get(tid_i)
                if prev is not None:
                    vx = cx - prev.center[0]
                    vy = cy - prev.center[1]
                else:
                    vx, vy = 0.0, 0.0
                new_cache[tid_i] = _TrackState(
                    track_id=tid_i,
                    xyxy=np.array([x1, y1, x2, y2], dtype=np.float64),
                    center=(cx, cy),
                    velocity=(vx, vy),
                    conf=float(confs[i]),
                )
                detections.append(
                    {"track_id": tid_i, "center": (cx, cy), "bbox_span": max(x2 - x1, y2 - y1)}
                )
                _draw_box(annotated, x1, y1, x2, y2, tid_i, float(confs[i]))

        self._track_cache = new_cache
        return self._finalize_frame(
            annotated, detections, frame_idx, src_fps, inferred=True, dt=dt
        )

    def _predict_tracks(
        self,
        frame: np.ndarray,
        frame_idx: int,
        src_fps: float,
        dt: float = 1.0 / 30.0,
    ) -> Tuple[np.ndarray, FrameMetrics]:
        """隔帧：用上一检测帧的速度做匀速外推。"""
        annotated = frame.copy()
        detections: List[dict] = []
        updated: Dict[int, _TrackState] = {}

        for tid, st in self._track_cache.items():
            vx, vy = st.velocity
            cx = st.center[0] + vx
            cy = st.center[1] + vy
            xyxy = st.xyxy.copy()
            xyxy[0] += vx
            xyxy[1] += vy
            xyxy[2] += vx
            xyxy[3] += vy
            updated[tid] = _TrackState(
                track_id=tid,
                xyxy=xyxy,
                center=(cx, cy),
                velocity=(vx, vy),
                conf=st.conf,
            )
            detections.append(
                {
                    "track_id": tid,
                    "center": (cx, cy),
                    "bbox_span": max(float(xyxy[2] - xyxy[0]), float(xyxy[3] - xyxy[1])),
                }
            )
            _draw_box(
                annotated,
                float(xyxy[0]),
                float(xyxy[1]),
                float(xyxy[2]),
                float(xyxy[3]),
                tid,
                st.conf,
                predicted=True,
            )

        self._track_cache = updated
        return self._finalize_frame(
            annotated, detections, frame_idx, src_fps, inferred=False, dt=dt
        )

    def _finalize_frame(
        self,
        annotated: np.ndarray,
        detections: List[dict],
        frame_idx: int,
        src_fps: float,
        inferred: bool,
        dt: float = 1.0 / 30.0,
    ) -> Tuple[np.ndarray, FrameMetrics]:
        cleaned = self._cleaner.process_frame(
            detections, timestamp=frame_idx / src_fps
        )
        index = self._calculator.compute_congestion_index(cleaned, dt=dt)
        scenario = self._calculator.last_scenario
        label = SCENARIO_LABELS.get(scenario, scenario)

        metrics = FrameMetrics(
            frame_idx=frame_idx,
            timestamp=round(frame_idx / src_fps, 3),
            congestion_index=round(float(index), 2),
            v_avg=round(float(self._calculator.last_avg_speed), 3),
            sigma_dist=round(float(self._calculator.last_sigma_dist), 3),
            scenario=scenario,
            scenario_label=label,
            vehicle_count=len(cleaned),
            inferred=inferred,
            raw_index=round(float(self._calculator.last_raw_index), 2),
            median_distance=round(float(self._calculator.last_median_distance), 2),
            dist_window_mean=round(float(self._calculator.last_dist_window_mean), 2),
            dist_window_len=int(self._calculator.last_dist_window_len),
            v_moving_avg=round(float(self._calculator.last_v_moving_avg), 3),
            r_stopped=round(float(self._calculator.last_r_stopped), 3),
            sigma_static=round(float(self._calculator.last_sigma_static), 3),
            platoon_count=int(self._calculator.last_platoon_count),
            intra_gap_count=int(self._calculator.last_intra_gap_count),
            gap_threshold=round(float(self._calculator.last_gap_threshold), 1),
            median_bbox_span=round(float(self._calculator.last_median_bbox_span), 1),
            empty_frames_counter=int(self._calculator.empty_frames_counter),
            hold_empty_seconds=round(float(self._calculator.last_hold_empty_seconds), 2),
            dist_warmup_remaining=int(self._calculator.last_dist_warmup_remaining),
            dist_appended=bool(self._calculator.last_dist_appended),
        )
        return annotated, metrics

def _model_entry(path: Path, fmt: str) -> Dict[str, str]:
    return {
        "name": str(path.relative_to(SINGLE_DIR)).replace("\\", "/"),
        "path": str(path.resolve()),
        "format": fmt,
        "size_mb": f"{path.stat().st_size / (1024 * 1024):.1f}",
    }


def _find_existing_onnx(pt_path: Path) -> Optional[Path]:
    stem = pt_path.stem
    parent = pt_path.parent
    candidates = [
        parent / f"{stem}.onnx",
        parent / f"{stem}_imgsz640.onnx",
        parent / f"{stem}_640.onnx",
    ]
    if stem == "best":
        candidates.append(parent / "best_imgsz640.onnx")
    for c in candidates:
        if c.is_file():
            return c
    return None


def resize_long_edge(frame: np.ndarray, max_size: int = 640) -> tuple[np.ndarray, float]:
    h, w = frame.shape[:2]
    long_edge = max(h, w)
    if long_edge <= max_size:
        return frame, 1.0
    scale = max_size / float(long_edge)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA), scale


def _draw_box(
    frame: np.ndarray,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    tid: int,
    conf: float,
    predicted: bool = False,
) -> None:
    del tid, conf
    color = (0, 180, 220) if predicted else (46, 125, 50)
    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)


def _placeholder_jpeg(text: str) -> bytes:
    img = np.full((360, 640, 3), 32, dtype=np.uint8)
    cv2.putText(
        img, text, (40, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2, cv2.LINE_AA
    )
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return buf.tobytes() if ok else b""


engine = VideoAnalysisEngine()
