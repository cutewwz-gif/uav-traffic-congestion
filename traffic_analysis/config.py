"""拥堵分析相关配置。

无人机在约 80 米高度以 -90° 垂直正俯视拍摄，分析直接基于
二维图像像素坐标 (x, y)，无需三维投影变换。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CongestionConfig:
    """道路拥堵分析的全局配置参数。

    Attributes:
        fps: 视频帧率（帧/秒）。默认 30。
        window_seconds: 滑动窗口时长（秒）。默认 **3 秒**（30 FPS → 90 帧），
            便于对车流变化做秒级响应。
        deadzone: 位移死区阈值（像素）。单帧位移小于该值时视为 0，
            用于消除无人机悬停微抖。默认 2.0。
        alpha: EMA（一阶指数平滑）系数，取值范围 (0, 1]。
            越大越贴近原始观测，越小越平滑。默认 0.4。
        speed_threshold: 近零速度阈值（像素/帧）。``V_avg`` 不高于该值
            时视为停滞/极缓行（``V_avg ≈ 0``）。默认 0.5。
        distance_std_threshold: 车距标准差参考阈值（像素），兼容旧接口；
            新状态机优先使用 ``sigma_low`` / ``sigma_high``。默认 15.0。
        v_high: 畅通行驶速度阈值（像素/帧）。``V_avg > v_high`` 判定为
            畅通（场景 A）。默认 8.0。
        v_max: 速度归一化上限（像素/帧），用于场景 D 公式。默认 15.0。
        sigma_low: 车距波动低阈值。停滞且 ``σ_dist < sigma_low`` 判定为
            红灯有序等待（场景 B）。默认 5.0。
        sigma_high: 车距波动高阈值。停滞且 ``σ_dist > sigma_high`` 判定为
            走走停停严重拥堵（场景 C）。默认 20.0。
        sigma_max: 车距标准差归一化上限，用于场景 C/D。默认 40.0。
        index_alpha: 拥堵指数中速度项权重 ``α``。默认 0.67。
        index_beta: 拥堵指数中车距波动项权重 ``β``。默认 0.33。
        v_static: 静止判定阈值（像素/帧）。单车 ``displacement`` 不高于该值
            时划入静止组，用于十字路口混行分析。默认 1.0。
        empty_reset_seconds: 已弃用物理清空；``n < 2`` 时改为对指数做
            ``×0.92`` 缓慢衰减，绝不 ``clear()`` 历史队列。保留字段仅兼容旧配置。
        output_ema_alpha: 输出层 EMA 系数（代码内固定 0.08，此字段供文档/扩展）。
        platoon_gap_factor: 车队切分倍率。排序后相邻间距超过
            ``factor × 平均 bbox 跨度`` 时视为不同队列，不参与车距统计。默认 1.5。
        default_bbox_span: 无检测框尺寸时的默认「车长」像素回退值。默认 40.0。
        dist_warmup_frames: 从无车/少车恢复后，连续有效帧达到该数才向
            ``D_t`` 窗口追加，避免 0↔大值突变抬高 ``σ_dist``。默认 5。
    """

    fps: float = 30.0
    window_seconds: float = 3.0
    deadzone: float = 2.0
    alpha: float = 0.4
    speed_threshold: float = 0.5
    distance_std_threshold: float = 15.0
    v_high: float = 8.0
    v_max: float = 15.0
    sigma_low: float = 5.0
    sigma_high: float = 20.0
    sigma_max: float = 40.0
    index_alpha: float = 0.67
    index_beta: float = 0.33
    v_static: float = 1.0
    empty_reset_seconds: float = 1.0
    output_ema_alpha: float = 0.08
    platoon_gap_factor: float = 1.5
    default_bbox_span: float = 40.0
    dist_warmup_frames: int = 5

    def __post_init__(self) -> None:
        """校验配置合法性。"""
        if self.fps <= 0:
            raise ValueError(f"fps 必须为正数，当前值: {self.fps}")
        if self.window_seconds <= 0:
            raise ValueError(
                f"window_seconds 必须为正数，当前值: {self.window_seconds}"
            )
        if self.deadzone < 0:
            raise ValueError(f"deadzone 不能为负数，当前值: {self.deadzone}")
        if not (0.0 < self.alpha <= 1.0):
            raise ValueError(
                f"alpha 必须在 (0, 1] 范围内，当前值: {self.alpha}"
            )
        if self.speed_threshold < 0:
            raise ValueError(
                f"speed_threshold 不能为负数，当前值: {self.speed_threshold}"
            )
        if self.distance_std_threshold < 0:
            raise ValueError(
                "distance_std_threshold 不能为负数，"
                f"当前值: {self.distance_std_threshold}"
            )
        if self.v_high < 0:
            raise ValueError(f"v_high 不能为负数，当前值: {self.v_high}")
        if self.v_max <= 0:
            raise ValueError(f"v_max 必须为正数，当前值: {self.v_max}")
        if self.v_high > self.v_max:
            raise ValueError(
                f"v_high ({self.v_high}) 不应大于 v_max ({self.v_max})"
            )
        if self.sigma_low < 0 or self.sigma_high < 0 or self.sigma_max <= 0:
            raise ValueError("sigma_low / sigma_high 不能为负，sigma_max 须为正")
        if self.sigma_low > self.sigma_high:
            raise ValueError(
                f"sigma_low ({self.sigma_low}) 不应大于 "
                f"sigma_high ({self.sigma_high})"
            )
        if self.sigma_high > self.sigma_max:
            raise ValueError(
                f"sigma_high ({self.sigma_high}) 不应大于 "
                f"sigma_max ({self.sigma_max})"
            )
        if self.index_alpha < 0 or self.index_beta < 0:
            raise ValueError("index_alpha / index_beta 不能为负数")
        weight_sum = self.index_alpha + self.index_beta
        if weight_sum <= 0:
            raise ValueError("index_alpha + index_beta 必须为正")
        if self.v_static < 0:
            raise ValueError(f"v_static 不能为负数，当前值: {self.v_static}")
        if self.empty_reset_seconds <= 0:
            raise ValueError(
                f"empty_reset_seconds 必须为正数，当前值: {self.empty_reset_seconds}"
            )
        if self.platoon_gap_factor <= 0:
            raise ValueError(
                f"platoon_gap_factor 必须为正数，当前值: {self.platoon_gap_factor}"
            )
        if self.default_bbox_span <= 0:
            raise ValueError(
                f"default_bbox_span 必须为正数，当前值: {self.default_bbox_span}"
            )
        if self.dist_warmup_frames < 0:
            raise ValueError(
                f"dist_warmup_frames 不能为负数，当前值: {self.dist_warmup_frames}"
            )

    @property
    def window_size(self) -> int:
        """滑动窗口帧数（例如 FPS=30、3 秒 → 90 帧）。"""
        return max(1, int(self.fps * self.window_seconds))

    @property
    def empty_reset_frames(self) -> int:
        """无车复位所需的连续稀疏帧数（默认 1 秒）。"""
        return max(1, int(self.fps * self.empty_reset_seconds))
