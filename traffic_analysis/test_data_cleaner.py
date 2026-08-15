"""DataCleaner 独立测试脚本。

直接运行::

    python -m traffic_analysis.test_data_cleaner
"""

from __future__ import annotations

import math
import random
from typing import List, Tuple

from traffic_analysis import CongestionConfig, DataCleaner

Point = Tuple[float, float]


def _rms_deviation(points: List[Point], origin: Point = (100.0, 100.0)) -> float:
    """计算点集相对原点的均方根偏差。"""
    if not points:
        return 0.0
    sq = sum((p[0] - origin[0]) ** 2 + (p[1] - origin[1]) ** 2 for p in points)
    return math.sqrt(sq / len(points))


def test_data_cleaner() -> None:
    """覆盖坐标平滑、死区过滤、滑动窗口三类场景。"""
    print("=" * 60)
    print("DataCleaner 测试开始")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. 测试坐标平滑：高斯抖动 → EMA 抑制
    # ------------------------------------------------------------------
    print("\n[1] 测试坐标平滑（EMA）")
    print("-" * 60)

    random.seed(42)
    config_smooth = CongestionConfig(alpha=0.4, deadzone=2.0)
    cleaner_smooth = DataCleaner(config_smooth)

    true_center: Point = (100.0, 100.0)
    sigma = 3.0  # 高斯抖动标准差（像素）
    n_frames = 20

    raw_points: List[Point] = []
    smooth_points: List[Point] = []

    print(f"{'帧':>4}  {'原始坐标':>22}  {'平滑坐标':>22}")
    for i in range(n_frames):
        noisy: Point = (
            true_center[0] + random.gauss(0.0, sigma),
            true_center[1] + random.gauss(0.0, sigma),
        )
        cleaned = cleaner_smooth.process_frame(
            [{"track_id": 1, "center": noisy}]
        )
        smoothed = cleaned[0]["center"]
        raw_points.append(noisy)
        smooth_points.append(smoothed)
        print(
            f"{i:4d}  "
            f"({noisy[0]:7.2f}, {noisy[1]:7.2f})  "
            f"({smoothed[0]:7.2f}, {smoothed[1]:7.2f})"
        )

    raw_rms = _rms_deviation(raw_points, true_center)
    smooth_rms = _rms_deviation(smooth_points, true_center)
    print(f"\n原始坐标 RMS 偏差: {raw_rms:.4f} px")
    print(f"平滑坐标 RMS 偏差: {smooth_rms:.4f} px")
    assert smooth_rms < raw_rms, (
        f"平滑后 RMS ({smooth_rms:.4f}) 应小于原始 RMS ({raw_rms:.4f})"
    )
    print("[PASS] EMA 平滑有效：平滑轨迹更贴近真实中心 (100, 100)")

    # ------------------------------------------------------------------
    # 2. 测试死区过滤：filter_displacement
    # ------------------------------------------------------------------
    print("\n[2] 测试死区过滤（filter_displacement）")
    print("-" * 60)

    config_dz = CongestionConfig(deadzone=2.0)
    cleaner_dz = DataCleaner(config_dz)

    tiny = 1.5
    filtered_tiny = cleaner_dz.filter_displacement(tiny)
    print(f"位移 {tiny} px  → filter_displacement = {filtered_tiny}")
    assert filtered_tiny == 0.0, (
        f"小于 deadzone({config_dz.deadzone}) 的位移应被置为 0.0，"
        f"实际得到 {filtered_tiny}"
    )
    print(f"[PASS] {tiny} px < deadzone({config_dz.deadzone}) → 输出 0.0")

    large = 5.0
    filtered_large = cleaner_dz.filter_displacement(large)
    print(f"位移 {large} px  → filter_displacement = {filtered_large}")
    assert filtered_large == large, (
        f"大于 deadzone 的位移应原样保留，期望 {large}，实际 {filtered_large}"
    )
    print(f"[PASS] {large} px >= deadzone({config_dz.deadzone}) → 保留 {large}")

    # 边界：恰好等于 deadzone 时保留（过滤条件为严格小于）
    edge = config_dz.deadzone
    filtered_edge = cleaner_dz.filter_displacement(edge)
    print(f"位移 {edge} px  → filter_displacement = {filtered_edge}")
    assert filtered_edge == edge
    print(f"[PASS] 等于 deadzone 的位移保留为 {edge}")

    # ------------------------------------------------------------------
    # 3. 测试滑动窗口：1000 帧后长度严格 ≤ window_size
    # ------------------------------------------------------------------
    print("\n[3] 测试滑动窗口长度限制")
    print("-" * 60)

    config_win = CongestionConfig(fps=30.0, window_seconds=30.0)
    window_size = config_win.window_size
    cleaner_win = DataCleaner(config_win)

    print(f"配置 window_size = {window_size}（fps={config_win.fps} × "
          f"{config_win.window_seconds}s）")
    print("循环写入 1000 帧…")

    total_frames = 1000
    for i in range(total_frames):
        # 缓慢平移，确保每帧都有有效观测
        center: Point = (100.0 + 0.1 * i, 100.0)
        cleaner_win.process_frame([{"track_id": 1, "center": center}])

        # 过程中窗口也不得超过上限
        assert len(cleaner_win.timestamps) <= window_size, (
            f"第 {i} 帧后 timestamps 长度 {len(cleaner_win.timestamps)} "
            f"超过 window_size={window_size}"
        )
        hist = cleaner_win.histories[1]
        assert len(hist) <= window_size, (
            f"第 {i} 帧后 histories[1] 长度 {len(hist)} "
            f"超过 window_size={window_size}"
        )

    ts_len = len(cleaner_win.timestamps)
    hist_len = len(cleaner_win.histories[1])
    traj_len = len(cleaner_win.get_trajectory(1))

    print(f"写入帧数:           {total_frames}")
    print(f"timestamps 长度:    {ts_len}")
    print(f"histories[1] 长度:  {hist_len}")
    print(f"get_trajectory 长度:{traj_len}")

    assert ts_len == window_size, (
        f"timestamps 长度应为 {window_size}，实际 {ts_len}"
    )
    assert hist_len == window_size, (
        f"histories[1] 长度应为 {window_size}，实际 {hist_len}"
    )
    assert traj_len == window_size, (
        f"轨迹长度应为 {window_size}，实际 {traj_len}"
    )
    assert ts_len < total_frames, "窗口应丢弃超出部分，长度须小于总帧数"
    print(f"[PASS] Deque 窗口严格限制在 {window_size} 帧内 "
          f"（{total_frames} → {ts_len}）")

    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("全部测试通过")
    print("=" * 60)


if __name__ == "__main__":
    test_data_cleaner()
