"""拥堵指数核心算法模块。

核心策略
--------
1. **Hold 挂起**：``n < 2`` 时绝不 ``clear()`` 历史队列。
2. **车队切分 (Platoon)**：间距 > ``platoon_gap_factor × 车长`` 的跨队配对忽略。
3. **连续状态机**：B/C 区 ``σ`` 从 20 连续映射到 80，无 20→80 跳崖。
4. **路口降分**：静动混合 + 部分车高速通过 → 封顶 ~30 分。
5. **时间基 EMA**：平滑与限幅按秒计，与 FPS 解耦。
"""

from __future__ import annotations

import math
from collections import deque
from typing import Deque, List, Optional, Sequence, Tuple

import numpy as np

from .config import CongestionConfig
from .data_cleaner import CleanedDetection, DataCleaner

# 输出时间常数（秒）：越大越稳
_OUTPUT_TAU_SEC: float = 1.5
# 指数变化速率上限（分/秒）
_MAX_DELTA_PER_SEC: float = 8.0
# Hold（n<2）衰减速率（分/秒）
_HOLD_DECAY_PER_SEC: float = 6.0
# 连续无车超过该秒数后指数归零（队列仍保留）
_EMPTY_RESET_SEC: float = 0.5


class CongestionCalculator:
    """拥堵指标计算与连续响应式状态机。"""

    def __init__(self, config: CongestionConfig, cleaner: DataCleaner) -> None:
        self.config: CongestionConfig = config
        self.cleaner: DataCleaner = cleaner

        maxlen = self.config.window_size
        self._dist_time_series: Deque[float] = deque(maxlen=maxlen)
        self._v_time_series: Deque[float] = deque(maxlen=maxlen)
        self._static_dist_time_series: Deque[float] = deque(maxlen=maxlen)

        self._last_valid_dist: float = 0.0
        self._last_c_t: float = 0.0
        self.empty_frames_counter: int = 0
        self._hold_empty_seconds: float = 0.0
        self.last_hold_empty_seconds: float = 0.0
        self._sparse_streak: int = 0
        self._dist_warmup_remaining: int = 0
        self.last_dist_appended: bool = False
        self.last_dist_warmup_remaining: int = 0

        self.last_smoothed_c_t: float = 0.0
        self.last_avg_speed: float = 0.0
        self.last_sigma_dist: float = 0.0
        self.last_median_distance: float = 0.0
        self.last_scenario: str = "init"
        self.last_r_stopped: float = 0.0
        self.last_v_moving_avg: float = 0.0
        self.last_sigma_static: float = 0.0
        self.last_raw_index: float = 0.0

        # 调试：每帧几何 / 窗口中间量
        self.last_platoon_count: int = 0
        self.last_intra_gap_count: int = 0
        self.last_gap_threshold: float = 0.0
        self.last_median_bbox_span: float = 0.0
        self.last_dist_window_mean: float = 0.0
        self.last_dist_window_len: int = 0

    def get_debug_snapshot(self) -> dict[str, float | int | str]:
        """返回当前帧算法中间量（供 HUD / WebSocket 调试）。"""
        return {
            "raw_index": round(self.last_raw_index, 2),
            "median_distance": round(self.last_median_distance, 2),
            "dist_window_mean": round(self.last_dist_window_mean, 2),
            "dist_window_len": self.last_dist_window_len,
            "sigma_dist": round(self.last_sigma_dist, 3),
            "v_avg": round(self.last_avg_speed, 3),
            "v_moving_avg": round(self.last_v_moving_avg, 3),
            "r_stopped": round(self.last_r_stopped, 3),
            "sigma_static": round(self.last_sigma_static, 3),
            "platoon_count": self.last_platoon_count,
            "intra_gap_count": self.last_intra_gap_count,
            "gap_threshold": round(self.last_gap_threshold, 1),
            "median_bbox_span": round(self.last_median_bbox_span, 1),
            "empty_frames_counter": self.empty_frames_counter,
            "hold_empty_seconds": round(self._hold_empty_seconds, 2),
            "dist_warmup_remaining": self.last_dist_warmup_remaining,
            "dist_appended": self.last_dist_appended,
            "scenario": self.last_scenario,
        }

    def _update_dist_window_stats(self) -> None:
        if self._dist_time_series:
            arr = np.asarray(self._dist_time_series, dtype=np.float64)
            self.last_dist_window_mean = float(np.mean(arr))
            self.last_dist_window_len = len(self._dist_time_series)
        else:
            self.last_dist_window_mean = 0.0
            self.last_dist_window_len = 0

    # ------------------------------------------------------------------
    # 单帧降维：车队内相邻欧氏距离中位数 D_t
    # ------------------------------------------------------------------

    def _compute_frame_distance_scalar(
        self, clean_vehicles: Sequence[CleanedDetection]
    ) -> float:
        """提取当前帧车距标量 ``D_t``。

        - ``n ≥ 2``：主方向排序 → 车队切分 → 段内相邻距中位数。
        - ``n < 2``：继承 ``_last_valid_dist``。
        """
        if len(clean_vehicles) < 2:
            return self._last_valid_dist
        d_t = self._platoon_median_distance(clean_vehicles)
        if d_t > 0.0:
            self._last_valid_dist = d_t
        return d_t if d_t > 0.0 else self._last_valid_dist

    def process_frame_distance(
        self, clean_vehicles: Sequence[CleanedDetection]
    ) -> float:
        return self._compute_frame_distance_scalar(clean_vehicles)

    def compute_temporal_variance(
        self, series: Optional[Sequence[float]] = None
    ) -> float:
        hist = self._dist_time_series if series is None else series
        if len(hist) < 2:
            return 0.0
        return float(np.std(np.asarray(hist, dtype=np.float64), ddof=0))

    def calculate_distance_fluctuation(
        self, clean_vehicles: Sequence[CleanedDetection]
    ) -> float:
        d_t = self._compute_frame_distance_scalar(clean_vehicles)
        self.last_median_distance = d_t
        self.last_dist_appended = False
        self.last_dist_warmup_remaining = self._dist_warmup_remaining

        can_append = (
            len(clean_vehicles) >= 2
            and d_t > 0.0
            and self._dist_warmup_remaining <= 0
        )
        if can_append:
            self._dist_time_series.append(d_t)
            self.last_dist_appended = True
        elif len(clean_vehicles) >= 2 and d_t > 0.0 and self._dist_warmup_remaining > 0:
            self._dist_warmup_remaining -= 1
            self.last_dist_warmup_remaining = self._dist_warmup_remaining

        self._update_dist_window_stats()
        return self.compute_temporal_variance()

    # ------------------------------------------------------------------
    # 基础指标
    # ------------------------------------------------------------------

    def calculate_avg_speed(
        self, clean_vehicles: Sequence[CleanedDetection]
    ) -> float:
        if not clean_vehicles:
            return 0.0
        return float(
            np.mean([float(v["displacement"]) for v in clean_vehicles])
        )

    def split_static_moving(
        self, clean_vehicles: Sequence[CleanedDetection]
    ) -> Tuple[List[CleanedDetection], List[CleanedDetection]]:
        v_static = self.config.v_static
        static: List[CleanedDetection] = []
        moving: List[CleanedDetection] = []
        for v in clean_vehicles:
            if float(v["displacement"]) <= v_static:
                static.append(v)
            else:
                moving.append(v)
        return static, moving

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def compute_congestion_index(
        self,
        clean_vehicles: Sequence[CleanedDetection],
        dt: Optional[float] = None,
    ) -> float:
        dt = self._resolve_dt(dt)
        n = len(clean_vehicles)
        cfg = self.config
        v_low = float(cfg.speed_threshold)
        v_high = float(cfg.v_high)

        if n < 2:
            self.empty_frames_counter += 1
            self._sparse_streak += 1
            self._hold_empty_seconds += dt
            self.last_hold_empty_seconds = self._hold_empty_seconds
            self.last_avg_speed = self.calculate_avg_speed(clean_vehicles)
            self.last_sigma_dist = self.compute_temporal_variance()
            # 无车帧不展示 0：保留上次有效 D_t，避免误解为「车距=0」
            self.last_median_distance = (
                self._last_valid_dist if self._last_valid_dist > 0.0 else 0.0
            )
            self.last_r_stopped = 0.0
            self.last_v_moving_avg = 0.0
            self.last_sigma_static = self.compute_temporal_variance(
                self._static_dist_time_series
            )
            self.last_scenario = "hold_few_vehicles"
            self.last_raw_index = 0.0
            self.last_dist_appended = False
            self.last_dist_warmup_remaining = self._dist_warmup_remaining
            self._update_dist_window_stats()

            if self._hold_empty_seconds > _EMPTY_RESET_SEC:
                self._last_c_t = 0.0
            else:
                self._last_c_t = max(
                    0.0, self._last_c_t - _HOLD_DECAY_PER_SEC * dt
                )

            self.last_smoothed_c_t = self._last_c_t
            return self._last_c_t

        # 从少车/无车恢复：启动 warmup，长时空窗后清空陈旧 D_t 样本
        if self._sparse_streak > 0:
            warmup = max(1, int(cfg.dist_warmup_frames))
            self._dist_warmup_remaining = warmup
            if self._hold_empty_seconds > _EMPTY_RESET_SEC:
                self._dist_time_series.clear()
                self._static_dist_time_series.clear()
        self._sparse_streak = 0
        self.empty_frames_counter = 0
        self._hold_empty_seconds = 0.0
        self.last_hold_empty_seconds = 0.0

        v_avg = self.calculate_avg_speed(clean_vehicles)
        if self._dist_warmup_remaining <= 0:
            self._v_time_series.append(v_avg)
        sigma_dist = self.calculate_distance_fluctuation(clean_vehicles)

        static_vehicles, moving_vehicles = self.split_static_moving(clean_vehicles)
        r_stopped = len(static_vehicles) / float(n)
        v_moving_avg = self.calculate_avg_speed(moving_vehicles)
        sigma_static = self._update_static_temporal_sigma(static_vehicles)

        self.last_avg_speed = v_avg
        self.last_sigma_dist = sigma_dist
        self.last_r_stopped = r_stopped
        self.last_v_moving_avg = v_moving_avg
        self.last_sigma_static = sigma_static

        raw_c_t = self._compute_raw_congestion_index(
            v_avg,
            sigma_dist,
            v_low,
            v_high,
            r_stopped,
            v_moving_avg,
            sigma_static,
            static_vehicles,
            moving_vehicles,
        )
        self.last_raw_index = raw_c_t
        return self._apply_ema_smoothing(raw_c_t, dt)

    # ------------------------------------------------------------------
    # 连续响应式状态机
    # ------------------------------------------------------------------

    def _compute_raw_congestion_index(
        self,
        v_avg: float,
        sigma_dist: float,
        v_low: float,
        v_high: float,
        r_stopped: float,
        v_moving_avg: float,
        sigma_static: float,
        static_vehicles: Sequence[CleanedDetection],
        moving_vehicles: Sequence[CleanedDetection],
    ) -> float:
        cfg = self.config

        speed_score = self._speed_congestion_subscore(v_avg, v_low, v_high)
        fluct_score = self._fluctuation_congestion_subscore(sigma_dist)
        raw_c_t = self._blend_congestion_subscores(speed_score, fluct_score)

        if v_avg >= v_high:
            self.last_scenario = "A_free_flow"
        elif v_avg <= v_low:
            if sigma_dist <= cfg.sigma_low:
                self.last_scenario = "B_orderly_wait"
            elif sigma_dist >= cfg.sigma_high:
                self.last_scenario = "C_stop_and_go"
            else:
                self.last_scenario = "D_transition"
        else:
            self.last_scenario = "D_slow_follow"

        if self._is_intersection_flow(
            r_stopped,
            v_moving_avg,
            sigma_static,
            static_vehicles,
            moving_vehicles,
        ):
            ratio = float(np.clip((r_stopped - 0.2) / 0.6, 0.0, 1.0))
            cap = 20.0 + 10.0 * ratio
            raw_c_t = min(raw_c_t, cap)
            self.last_scenario = "E_intersection"

        return float(np.clip(raw_c_t, 0.0, 100.0))

    def _speed_congestion_subscore(
        self, v_avg: float, v_low: float, v_high: float
    ) -> float:
        """速度子分：越高越拥堵（0–100）。"""
        if v_avg >= v_high:
            return max(0.0, 15.0 - (v_avg - v_high) * 1.5)
        if v_avg <= v_low:
            t = 1.0 - float(np.clip(v_avg / max(v_low, 1e-6), 0.0, 1.0))
            return 20.0 + t * 60.0
        speed_ratio = float(
            np.clip((v_avg - v_low) / max(v_high - v_low, 1e-6), 0.0, 1.0)
        )
        return 45.0 - speed_ratio * 30.0

    def _fluctuation_congestion_subscore(self, sigma_dist: float) -> float:
        """车距波动子分：越高越拥堵（0–100）。"""
        return self._score_stopped_flow(sigma_dist)

    def _blend_congestion_subscores(
        self, speed_score: float, fluct_score: float
    ) -> float:
        """按 ``index_alpha`` / ``index_beta`` 加权合成原始拥堵分。"""
        cfg = self.config
        weight_sum = cfg.index_alpha + cfg.index_beta
        alpha = cfg.index_alpha / weight_sum
        beta = cfg.index_beta / weight_sum
        return alpha * speed_score + beta * fluct_score

    def _score_stopped_flow(self, sigma_dist: float) -> float:
        """停滞区连续映射：σ 从 low→high 对应 20→80，再高至 sigma_max 映射到 100。"""
        cfg = self.config
        if sigma_dist <= cfg.sigma_low:
            return 20.0
        if sigma_dist >= cfg.sigma_high:
            span = max(cfg.sigma_max - cfg.sigma_high, 1e-6)
            ratio = float(
                np.clip((sigma_dist - cfg.sigma_high) / span, 0.0, 1.0)
            )
            return 80.0 + ratio * 20.0
        mid_span = max(cfg.sigma_high - cfg.sigma_low, 1e-6)
        ratio = float((sigma_dist - cfg.sigma_low) / mid_span)
        return 20.0 + ratio * 60.0

    def _is_intersection_flow(
        self,
        r_stopped: float,
        v_moving_avg: float,
        sigma_static: float,
        static_vehicles: Sequence[CleanedDetection],
        moving_vehicles: Sequence[CleanedDetection],
    ) -> bool:
        """路口等灯：静动混合、部分车道仍在放行。"""
        cfg = self.config
        if not (0.2 < r_stopped < 0.8):
            return False
        if len(moving_vehicles) < 1 or len(static_vehicles) < 2:
            return False
        if v_moving_avg <= cfg.v_high:
            return False
        return sigma_static < cfg.sigma_low

    # ------------------------------------------------------------------
    # 时间基 EMA + 速率限幅
    # ------------------------------------------------------------------

    def _resolve_dt(self, dt: Optional[float]) -> float:
        if dt is None or dt <= 0:
            dt = 1.0 / max(float(self.config.fps), 1.0)
        return float(np.clip(dt, 1e-3, 0.25))

    def _apply_ema_smoothing(self, raw_c_t: float, dt: float) -> float:
        alpha_t = 1.0 - math.exp(-dt / _OUTPUT_TAU_SEC)
        target_c_t = alpha_t * raw_c_t + (1.0 - alpha_t) * self._last_c_t
        max_delta = _MAX_DELTA_PER_SEC * dt
        delta = float(
            np.clip(target_c_t - self._last_c_t, -max_delta, max_delta)
        )
        self._last_c_t = float(np.clip(self._last_c_t + delta, 0.0, 100.0))
        self.last_smoothed_c_t = self._last_c_t
        return self._last_c_t

    # ------------------------------------------------------------------
    # 窗口 / 复位
    # ------------------------------------------------------------------

    def resize_window(self) -> None:
        maxlen = self.config.window_size
        self._dist_time_series = deque(self._dist_time_series, maxlen=maxlen)
        self._v_time_series = deque(self._v_time_series, maxlen=maxlen)
        self._static_dist_time_series = deque(
            self._static_dist_time_series, maxlen=maxlen
        )
        self.cleaner.resize_window()

    def reset(self) -> None:
        self._dist_time_series.clear()
        self._v_time_series.clear()
        self._static_dist_time_series.clear()
        self._last_valid_dist = 0.0
        self._last_c_t = 0.0
        self.empty_frames_counter = 0
        self._hold_empty_seconds = 0.0
        self.last_hold_empty_seconds = 0.0
        self._sparse_streak = 0
        self._dist_warmup_remaining = 0
        self.last_dist_appended = False
        self.last_dist_warmup_remaining = 0
        self.last_smoothed_c_t = 0.0
        self.last_avg_speed = 0.0
        self.last_sigma_dist = 0.0
        self.last_median_distance = 0.0
        self.last_scenario = "init"
        self.last_r_stopped = 0.0
        self.last_v_moving_avg = 0.0
        self.last_sigma_static = 0.0
        self.last_raw_index = 0.0
        self.last_platoon_count = 0
        self.last_intra_gap_count = 0
        self.last_gap_threshold = 0.0
        self.last_median_bbox_span = 0.0
        self.last_dist_window_mean = 0.0
        self.last_dist_window_len = 0

    # ------------------------------------------------------------------
    # 几何：车队切分
    # ------------------------------------------------------------------

    def _platoon_median_distance(
        self, vehicles: Sequence[CleanedDetection]
    ) -> float:
        """排序后按间距切分车队，只在同队内部统计相邻距中位数。"""
        if len(vehicles) < 2:
            self.last_platoon_count = 0
            self.last_intra_gap_count = 0
            self.last_gap_threshold = 0.0
            self.last_median_bbox_span = 0.0
            return 0.0

        cfg = self.config
        coords = np.asarray([v["center"] for v in vehicles], dtype=np.float64)
        spans = np.asarray(
            [max(float(v["bbox_span"]), 1.0) for v in vehicles],
            dtype=np.float64,
        )
        self.last_median_bbox_span = float(np.median(spans))

        major_axis = int(np.argmax(np.var(coords, axis=0)))
        order = np.argsort(coords[:, major_axis])
        sorted_coords = coords[order]
        sorted_spans = spans[order]

        gaps = np.linalg.norm(np.diff(sorted_coords, axis=0), axis=1)
        pair_thresholds = (
            cfg.platoon_gap_factor * (sorted_spans[:-1] + sorted_spans[1:]) * 0.5
        )
        self.last_gap_threshold = float(np.median(pair_thresholds))

        intra_gaps: List[float] = []
        platoon_start = 0
        platoon_count = 1
        for i, gap in enumerate(gaps):
            if gap > pair_thresholds[i]:
                intra_gaps.extend(gaps[platoon_start:i].tolist())
                platoon_start = i + 1
                platoon_count += 1
        intra_gaps.extend(gaps[platoon_start:].tolist())

        self.last_platoon_count = platoon_count
        self.last_intra_gap_count = len(intra_gaps)

        if not intra_gaps:
            return 0.0
        return float(np.median(np.asarray(intra_gaps, dtype=np.float64)))

    def _update_static_temporal_sigma(
        self, static_vehicles: Sequence[CleanedDetection]
    ) -> float:
        if len(static_vehicles) >= 2 and self._dist_warmup_remaining <= 0:
            d_static = self._platoon_median_distance(static_vehicles)
            if d_static > 0.0:
                self._static_dist_time_series.append(d_static)
        return self.compute_temporal_variance(self._static_dist_time_series)
