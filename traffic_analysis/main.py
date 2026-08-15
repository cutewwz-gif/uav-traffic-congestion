"""交通拥堵分析 — 模拟数据生成与可视化主程序。

运行方式::

    python -m traffic_analysis.main
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Generator, List, Tuple

import matplotlib.pyplot as plt
import numpy as np

from .calculator import CongestionCalculator
from .config import CongestionConfig
from .data_cleaner import DataCleaner

Point = Tuple[float, float]
Detection = Dict[str, object]

# 仿真时长与帧率
FPS: float = 30.0
DURATION_SECONDS: float = 90.0
TOTAL_FRAMES: int = int(FPS * DURATION_SECONDS)  # 2700
NUM_VEHICLES: int = 10
SCENE_FRAMES: int = int(FPS * 30.0)  # 每场景 900 帧


def generate_simulated_traffic(
    seed: int = 42,
) -> Generator[List[Detection], None, None]:
    """生成持续 90 秒（30 FPS → 2700 帧）的三段式模拟交通流。

    阶段划分：

    - **0~30 s（Scene A 畅通）**：约 12 px/frame 平移，相邻车距约 60 px。
    - **30~60 s（Scene B 红灯）**：速度为 0，车距固定约 25 px，仅有微抖。
    - **60~90 s（Scene C 严重堵车）**：速度接近 0，车距在 15~60 px 间
      缓慢伸缩抖动（保证单帧位移小，窗口内 ``σ_dist`` 大）。

    Yields:
        每帧的检测列表 ``[{"track_id": int, "center": (x, y)}, ...]``。
    """
    rng = np.random.default_rng(seed)
    track_ids = list(range(1, NUM_VEHICLES + 1))
    y_base = 240.0

    # 初始：沿 x 轴排布，间距 60
    positions = np.array(
        [80.0 + i * 60.0 for i in range(NUM_VEHICLES)], dtype=np.float64
    )

    # Scene C 用的慢随机游走偏移（围绕固定基准点）
    c_offsets = np.zeros(NUM_VEHICLES, dtype=np.float64)

    for frame in range(TOTAL_FRAMES):
        if frame < SCENE_FRAMES:
            detections = _scene_a_free_flow(
                frame, positions, track_ids, y_base, rng
            )

        elif frame < 2 * SCENE_FRAMES:
            detections = _scene_b_red_light(
                frame, positions, track_ids, y_base, rng
            )

        else:
            detections = _scene_c_congestion(
                frame, positions, c_offsets, track_ids, y_base, rng
            )

        yield detections


def _scene_a_free_flow(
    frame: int,
    positions: np.ndarray,
    track_ids: List[int],
    y_base: float,
    rng: np.random.Generator,
) -> List[Detection]:
    """Scene A：畅通，~12 px/frame，间距 ~60 px。"""
    speed = 12.0 + float(rng.normal(0.0, 0.25))
    gap_noise = rng.normal(0.0, 1.2, size=NUM_VEHICLES - 1)

    positions[0] += speed
    for i in range(1, NUM_VEHICLES):
        positions[i] = positions[i - 1] + 60.0 + float(gap_noise[i - 1])

    return _pack_detections(track_ids, positions, y_base, rng, jitter_std=0.35)


def _scene_b_red_light(
    frame: int,
    positions: np.ndarray,
    track_ids: List[int],
    y_base: float,
    rng: np.random.Generator,
) -> List[Detection]:
    """Scene B：红灯。前 3 秒平滑减速并压缩车距，之后静止微抖。"""
    local = frame - SCENE_FRAMES
    settle_frames = int(FPS * 3.0)  # 3 秒过渡，避免瞬移尖峰

    if local < settle_frames:
        # 从当前间距线性过渡到 25px，速度从 ~12 降到 0
        progress = (local + 1) / settle_frames
        speed = 12.0 * (1.0 - progress)
        target_gap = 60.0 + (25.0 - 60.0) * progress

        positions[0] += speed
        for i in range(1, NUM_VEHICLES):
            desired = positions[i - 1] + target_gap
            # 临界阻尼跟随，避免振荡
            positions[i] += 0.45 * (desired - positions[i])
    else:
        # 完全静止：锁定为精确 25px 间距（仅检测微抖）
        if local == settle_frames:
            anchor = float(positions[0])
            for i in range(NUM_VEHICLES):
                positions[i] = anchor + i * 25.0

    return _pack_detections(track_ids, positions, y_base, rng, jitter_std=0.7)


def _scene_c_congestion(
    frame: int,
    positions: np.ndarray,
    offsets: np.ndarray,
    track_ids: List[int],
    y_base: float,
    rng: np.random.Generator,
) -> List[Detection]:
    """Scene C：严重堵车。

    目标间距以方波在 15 px / 60 px 间切换：长保持 + 短过渡。
    保持阶段速度≈0；窗口内中位车距呈双峰 → ``σ_dist`` 升高，
    触发场景 C（走走停停）。过渡帧可能出现短暂速度尖峰，可忽略。
    """
    local = frame - 2 * SCENE_FRAMES

    if local == 0:
        anchor = float(np.mean(positions)) - 0.5 * (NUM_VEHICLES - 1) * 25.0
        for i in range(NUM_VEHICLES):
            positions[i] = anchor + i * 25.0
        offsets[:] = 0.0

    # 周期 10 s：4 s@15 + 1 s 过渡 + 4 s@60 + 1 s 过渡
    period = int(FPS * 10.0)
    cycle = local % period
    hold = int(FPS * 4.0)
    edge = int(FPS * 1.0)

    if cycle < hold:
        target_gap = 15.0
    elif cycle < hold + edge:
        t = (cycle - hold) / edge
        target_gap = 15.0 + (60.0 - 15.0) * t
    elif cycle < 2 * hold + edge:
        target_gap = 60.0
    else:
        t = (cycle - 2 * hold - edge) / edge
        target_gap = 60.0 + (15.0 - 60.0) * t

    # 检测框微抖 + 轻微车位挪动（模拟走走停停）
    offsets[:] = 0.7 * offsets + rng.normal(0.0, 0.6, size=NUM_VEHICLES)
    offsets[:] = np.clip(offsets, -6.0, 6.0)

    # 保持阶段几乎不蠕动；过渡阶段允许轻微整体移动
    creep = 0.0
    if hold <= cycle < hold + edge or cycle >= 2 * hold + edge:
        creep = float(rng.uniform(0.0, 1.5))
    elif rng.random() < 0.1:
        creep = float(rng.uniform(0.0, 1.2))

    center = float(np.mean(positions)) + creep
    leftmost = center - 0.5 * target_gap * (NUM_VEHICLES - 1)
    for i in range(NUM_VEHICLES):
        positions[i] = leftmost + i * target_gap + float(offsets[i])

    return _pack_detections(track_ids, positions, y_base, rng, jitter_std=0.5)


def _pack_detections(
    track_ids: List[int],
    positions: np.ndarray,
    y_base: float,
    rng: np.random.Generator,
    jitter_std: float,
) -> List[Detection]:
    """将一维 x 坐标打包为带微抖的检测列表。"""
    detections: List[Detection] = []
    for tid, x in zip(track_ids, positions):
        jx = float(rng.normal(0.0, jitter_std))
        jy = float(rng.normal(0.0, jitter_std))
        center: Point = (float(x) + jx, y_base + jy)
        detections.append({"track_id": int(tid), "center": center})
    return detections


def main() -> None:
    """逐帧仿真 → 拥堵指数计算 → Matplotlib 三子图可视化。"""
    # 窗口取 10 s：每个 30 s 场景有足够时间建立「纯净」统计，
    # 避免 30 s 窗口跨场景污染导致状态机误判。
    config = CongestionConfig(fps=FPS, window_seconds=10.0)
    cleaner = DataCleaner(config)
    calculator = CongestionCalculator(config, cleaner)

    indices: List[float] = []
    speeds: List[float] = []
    sigmas: List[float] = []
    scenarios: List[str] = []
    times: List[float] = []

    print("=" * 60)
    print("Traffic congestion simulation start")
    print(f"Frames: {TOTAL_FRAMES}  |  FPS: {FPS}  |  Duration: {DURATION_SECONDS}s")
    print("=" * 60)

    for frame_idx, detections in enumerate(generate_simulated_traffic()):
        cleaned = cleaner.process_frame(detections)
        index = calculator.compute_congestion_index(cleaned)

        t = frame_idx / FPS
        times.append(t)
        indices.append(index)
        speeds.append(calculator.last_avg_speed)
        sigmas.append(calculator.last_sigma_dist)
        scenarios.append(calculator.last_scenario)

        if frame_idx % int(FPS * 10) == 0 or frame_idx == TOTAL_FRAMES - 1:
            print(
                f"[t={t:5.1f}s]  C_t={index:6.2f}  "
                f"V_avg={calculator.last_avg_speed:6.3f}  "
                f"sigma={calculator.last_sigma_dist:6.3f}  "
                f"scene={calculator.last_scenario}"
            )

    _print_phase_summary(times, indices, speeds, sigmas, scenarios)
    out_path = Path(__file__).resolve().parent.parent / "simulation_result.png"
    _plot_results(times, indices, speeds, sigmas, out_path)


def _print_phase_summary(
    times: List[float],
    indices: List[float],
    speeds: List[float],
    sigmas: List[float],
    scenarios: List[str],
) -> None:
    """按三个场景区间打印均值摘要（取后半段以避开过渡）。"""
    print("\n" + "-" * 60)
    print("Phase summary (second half of each scene, skip transitions)")
    phases = [
        ("A free-flow 0-30s", 15.0, 30.0),
        ("B red-light 30-60s", 45.0, 60.0),
        ("C congestion 60-90s", 75.0, 90.0),
    ]
    t_arr = np.asarray(times)
    idx_arr = np.asarray(indices)
    spd_arr = np.asarray(speeds)
    sig_arr = np.asarray(sigmas)

    for name, t0, t1 in phases:
        mask = (t_arr >= t0) & (t_arr < t1)
        if not np.any(mask):
            continue
        sc_slice = [scenarios[i] for i, m in enumerate(mask) if m]
        dominant = max(set(sc_slice), key=sc_slice.count)
        print(
            f"  {name}:  "
            f"C_mean={np.mean(idx_arr[mask]):5.1f}  "
            f"V_mean={np.mean(spd_arr[mask]):5.2f}  "
            f"sig_mean={np.mean(sig_arr[mask]):5.2f}  "
            f"dominant={dominant}"
        )
    print("-" * 60)


def _plot_results(
    times: List[float],
    indices: List[float],
    speeds: List[float],
    sigmas: List[float],
    out_path: Path,
) -> None:
    """绘制三子图分析图并保存、展示。"""
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    fig.suptitle(
        "UAV Traffic Congestion Simulation (90s @ 30 FPS)",
        fontsize=14,
        fontweight="bold",
    )

    scene_spans = [
        (0.0, 30.0, "#c6efce", "Scene A: Free Flow"),
        (30.0, 60.0, "#ffe699", "Scene B: Red Light"),
        (60.0, 90.0, "#f4b183", "Scene C: Severe Congestion"),
    ]

    def _paint_scenes(ax: plt.Axes, with_label: bool) -> None:
        for t0, t1, color, label in scene_spans:
            ax.axvspan(
                t0,
                t1,
                facecolor=color,
                alpha=0.35,
                label=label if with_label else None,
                zorder=0,
            )

    # --- 图 1：拥堵指数 ---
    ax0 = axes[0]
    _paint_scenes(ax0, with_label=True)
    ax0.plot(times, indices, color="#1f4e79", linewidth=1.2, label=r"$C_t$ Index")
    ax0.set_ylabel(r"Congestion Index $C_t$")
    ax0.set_ylim(-2, 105)
    ax0.set_title("Congestion Index over Time")
    ax0.legend(loc="upper left", fontsize=8, ncol=2)
    ax0.grid(True, alpha=0.3)

    # --- 图 2：平均速度 ---
    ax1 = axes[1]
    _paint_scenes(ax1, with_label=False)
    ax1.plot(times, speeds, color="#2e7d32", linewidth=1.2, label=r"$V_{avg}$")
    ax1.set_ylabel(r"Avg Speed $V_{avg}$ (px/frame)")
    ax1.set_title("Average Pixel Speed")
    ax1.legend(loc="upper right", fontsize=8)
    ax1.grid(True, alpha=0.3)

    # --- 图 3：车距波动 ---
    ax2 = axes[2]
    _paint_scenes(ax2, with_label=False)
    ax2.plot(times, sigmas, color="#c62828", linewidth=1.2, label=r"$\sigma_{dist}$")
    ax2.set_ylabel(r"Distance Fluctuation $\sigma_{dist}$ (px)")
    ax2.set_xlabel("Time (s)")
    ax2.set_title("Inter-vehicle Distance Fluctuation")
    ax2.legend(loc="upper right", fontsize=8)
    ax2.grid(True, alpha=0.3)

    for ax in axes:
        ax.axvline(30.0, color="gray", linestyle="--", linewidth=0.8, alpha=0.7)
        ax.axvline(60.0, color="gray", linestyle="--", linewidth=0.8, alpha=0.7)
        ax.set_xlim(0.0, 90.0)

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_path, dpi=150)
    print(f"\nFigure saved: {out_path}")
    plt.show()


if __name__ == "__main__":
    main()
