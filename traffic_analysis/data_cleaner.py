"""数据清洗与滑动窗口管理。

对 YOLO + ByteTrack 逐帧检测结果进行：
1. EMA 坐标平滑（抑制 Bounding Box 边缘抖动）
2. 死区过滤（消除无人机悬停微抖）
3. 滑动窗口维护（保留过去 N 秒内存活车辆的轨迹历史）
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, List, Mapping, Optional, Sequence, Tuple, TypedDict

from .config import CongestionConfig

DetectionLike = Mapping[str, object]
Point = Tuple[float, float]


class CleanedDetection(TypedDict):
    """单帧清洗后的车辆记录。"""

    track_id: int
    center: Point
    displacement: float
    bbox_span: float


class DataCleaner:
    """数据清洗与滑动窗口管理器。

    处理流程（每帧调用 :meth:`process_frame`）::

        原始 center
            → EMA 平滑
            → 与上一帧平滑坐标计算位移
            → 死区过滤（|Δ| < deadzone → 0）
            → 写入滑动窗口

    滑动窗口使用 ``collections.deque(maxlen=window_size)`` 自动丢弃
    超出窗口（默认 3 秒）的历史帧。

    Args:
        config: 拥堵分析配置。若为 ``None``，使用 :class:`CongestionConfig`
            的默认值。
    """

    def __init__(self, config: Optional[CongestionConfig] = None) -> None:
        self.config: CongestionConfig = config or CongestionConfig()

        # track_id → 上一帧 EMA 平滑后的中心坐标
        self._ema_centers: Dict[int, Point] = {}

        # 帧级时间戳历史（与坐标窗口对齐）
        self._timestamps: Deque[float] = deque(maxlen=self.config.window_size)

        # track_id → 该车在窗口内的平滑坐标序列
        self._histories: Dict[int, Deque[Optional[Point]]] = defaultdict(
            lambda: deque(maxlen=self.config.window_size)
        )

        # 当前已处理的帧序号（从 0 起）
        self._frame_index: int = 0

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    @property
    def frame_index(self) -> int:
        """已处理帧数（下一帧将写入的索引）。"""
        return self._frame_index

    @property
    def timestamps(self) -> Deque[float]:
        """滑动窗口内的帧时间戳（秒或任意单调时钟单位）。"""
        return self._timestamps

    @property
    def histories(self) -> Mapping[int, Deque[Optional[Point]]]:
        """各 track_id 在滑动窗口内的平滑坐标历史。

        窗口中某一帧若该车未出现，对应位置为 ``None``。
        """
        return self._histories

    def process_frame(
        self,
        detections: Sequence[DetectionLike],
        timestamp: Optional[float] = None,
    ) -> List[CleanedDetection]:
        """清洗当前帧检测结果，并更新滑动窗口。

        Args:
            detections: YOLO + ByteTrack 返回的逐帧车辆列表，每项至少包含::

                {"track_id": int, "center": (float, float)}

            timestamp: 当前帧时间戳。若为 ``None``，则按
                ``frame_index / fps`` 自动推算。

        Returns:
            清洗后的检测列表，每项包含 ``track_id``、平滑后的 ``center``
            以及经死区过滤后的 ``displacement``（像素）。
        """
        if timestamp is None:
            timestamp = self._frame_index / self.config.fps

        active_ids: set[int] = set()
        cleaned: List[CleanedDetection] = []

        for det in detections:
            track_id = int(det["track_id"])  # type: ignore[arg-type]
            raw_center = self._parse_center(det["center"])
            active_ids.add(track_id)

            smoothed = self._apply_ema(track_id, raw_center)
            # 位移基于「上一帧平滑坐标 → 当前平滑坐标」计算；
            # 须在更新 _ema_centers 之前调用。
            displacement = self._compute_displacement(track_id, smoothed)
            self._ema_centers[track_id] = smoothed

            raw_span = det.get("bbox_span")
            bbox_span = (
                float(raw_span)
                if isinstance(raw_span, (int, float)) and float(raw_span) > 0
                else self.config.default_bbox_span
            )

            cleaned.append(
                CleanedDetection(
                    track_id=track_id,
                    center=smoothed,
                    displacement=displacement,
                    bbox_span=bbox_span,
                )
            )

        self._update_window(timestamp, active_ids, cleaned)
        self._prune_inactive(active_ids)
        self._frame_index += 1
        return cleaned

    def get_active_track_ids(self) -> List[int]:
        """返回当前仍持有 EMA 状态的存活车辆 ID 列表。"""
        return list(self._ema_centers.keys())

    def get_trajectory(
        self, track_id: int, drop_missing: bool = True
    ) -> List[Point]:
        """获取指定车辆在滑动窗口内的平滑轨迹。

        Args:
            track_id: 目标跟踪 ID。
            drop_missing: 若为 ``True``（默认），丢弃窗口中该车未出现
                的帧（``None``）；若为 ``False``，保留 ``None`` 占位。

        Returns:
            平滑坐标点列表。车辆不存在时返回空列表。
        """
        history = self._histories.get(track_id)
        if history is None:
            return []
        if drop_missing:
            return [p for p in history if p is not None]
        # 保留 None 时返回类型放宽，调用方自行处理
        return list(history)  # type: ignore[return-value]

    def filter_displacement(self, displacement: float) -> float:
        """对位移施加死区过滤。

        Args:
            displacement: 单帧欧氏位移（像素）。

        Returns:
            若 ``displacement < deadzone``，返回 ``0.0``；否则原样返回。
        """
        if displacement < self.config.deadzone:
            return 0.0
        return displacement

    def clear_sliding_window(self) -> None:
        """清空轨迹 / 时间戳滑动窗口，保留 EMA 状态（无车快速复位用）。"""
        self._timestamps.clear()
        self._histories.clear()

    def resize_window(self) -> None:
        """按当前 ``config.window_size`` 重建 deque 容量，保留最近样本。"""
        maxlen = self.config.window_size
        self._timestamps = deque(self._timestamps, maxlen=maxlen)
        resized: Dict[int, Deque[Optional[Point]]] = defaultdict(
            lambda: deque(maxlen=maxlen)
        )
        for tid, hist in self._histories.items():
            resized[tid] = deque(hist, maxlen=maxlen)
        self._histories = resized

    def reset(self) -> None:
        """清空全部内部状态，便于重新开始分析。"""
        self._ema_centers.clear()
        self._timestamps.clear()
        self._histories.clear()
        self._frame_index = 0

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_center(center: object) -> Point:
        """将 center 字段解析为 ``(x, y)`` 浮点元组。"""
        if not isinstance(center, (tuple, list)) or len(center) != 2:
            raise TypeError(
                f"center 应为长度为 2 的 (x, y) 序列，实际收到: {center!r}"
            )
        return (float(center[0]), float(center[1]))

    def _apply_ema(self, track_id: int, raw: Point) -> Point:
        """对车辆中心坐标施加一阶指数平滑。

        公式::

            s_t = alpha * x_t + (1 - alpha) * s_{t-1}

        首帧直接采用原始观测值。
        """
        alpha = self.config.alpha
        prev = self._ema_centers.get(track_id)
        if prev is None:
            return raw
        return (
            alpha * raw[0] + (1.0 - alpha) * prev[0],
            alpha * raw[1] + (1.0 - alpha) * prev[1],
        )

    def _compute_displacement(self, track_id: int, current: Point) -> float:
        """计算相对上一帧平滑坐标的欧氏位移，并应用死区过滤。

        若位移小于 ``deadzone``，强制返回 ``0.0``，以消除无人机
        风袭悬停引起的亚像素级抖动。
        """
        prev = self._ema_centers.get(track_id)
        if prev is None:
            return 0.0

        dx = current[0] - prev[0]
        dy = current[1] - prev[1]
        distance = (dx * dx + dy * dy) ** 0.5
        return self.filter_displacement(distance)

    def _update_window(
        self,
        timestamp: float,
        active_ids: set[int],
        cleaned: Sequence[CleanedDetection],
    ) -> None:
        """将当前帧写入滑动窗口。

        - 时间戳追加到 ``_timestamps``
        - 存活车辆写入平滑坐标；本帧未出现的已知车辆写入 ``None``
        """
        self._timestamps.append(timestamp)

        center_map: Dict[int, Point] = {
            item["track_id"]: item["center"] for item in cleaned
        }

        # 确保所有当前活跃车辆都有历史队列
        for tid in active_ids:
            _ = self._histories[tid]  # 触发 defaultdict 创建

        # 对本窗口内已知的所有车辆追加本帧观测（或 None）
        known_ids = set(self._histories.keys()) | active_ids
        for tid in known_ids:
            self._histories[tid].append(center_map.get(tid))

    def _prune_inactive(self, active_ids: set[int]) -> None:
        """移除长期消失的车辆状态。

        若某车辆在整个滑动窗口内均无观测（全为 ``None``），
        则清除其 EMA 状态与历史，释放内存。
        """
        stale: List[int] = []
        for tid, history in self._histories.items():
            if tid in active_ids:
                continue
            if not any(p is not None for p in history):
                stale.append(tid)

        for tid in stale:
            self._histories.pop(tid, None)
            self._ema_centers.pop(tid, None)
