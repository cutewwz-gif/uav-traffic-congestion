# uav-traffic-congestion

基于 **YOLO** 无人机视角车辆检测与 **拥堵指数** 实时分析的 Web GUI。支持 VisDrone 微调模型、ONNX + DirectML（AMD GPU）推理，以及 WebSocket 指标推送。

## 功能

- 上传 / 选择本地视频，实时 MJPEG 预览
- YOLO 车辆检测 + 轨迹跟踪 + 隔帧推理（降低 GPU 负载）
- 拥堵指数、平均速度、间距方差、场景分类（畅通 / 等待 / 走走停停等）
- 前端参数热更新（置信度、窗口长度、EMA 等）
- 独立 `traffic_analysis` 模块：模拟数据生成与可视化

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

> Windows AMD GPU（如 RX 6600S）使用 `onnxruntime-directml`，**不要**与标准 `onnxruntime` 同时安装。

### 2. 准备模型

将训练好的权重放到 `my_uav_vehicle_single/` 目录（支持 `.onnx` 或 `.pt`，优先 ONNX）：

```
my_uav_vehicle_single/
└── my_uav_vehicle_single/
    └── weights/
        └── best.onnx   # 或 best.pt（首次运行会自动导出 ONNX）
```

模型权重默认在 `.gitignore` 中（ONNX 常超过 GitHub 100MB 限制），需自行训练或下载后放置。

### 3. 启动 GUI

```bash
python run_gui.py
```

或双击 `run.bat`。浏览器会自动打开 `http://localhost:8000`（端口占用时会自动 fallback）。

也可直接运行 API 服务：

```bash
python app.py
```

## 目录结构

```
├── app.py                 # FastAPI 主入口（MJPEG + WebSocket）
├── run_gui.py             # 一键启动 + 打开浏览器
├── analyzer.py            # 实时分析引擎
├── ort_dml.py             # DirectML / ONNX Runtime 适配
├── traffic_analysis/      # 拥堵计算器 + 模拟数据
├── templates/index.html   # 前端页面
├── my_uav_vehicle_single/ # 模型权重目录
├── uploads/               # 上传视频（运行时生成）
└── paper/                 # 论文 / PPT 生成脚本
```

## 拥堵分析模块（离线）

运行模拟交通流并输出图表：

```bash
python -m traffic_analysis.main
```

## 技术栈

| 组件 | 说明 |
|------|------|
| YOLO (Ultralytics) | VisDrone 微调车辆检测 |
| FastAPI + WebSocket | 后端 API 与实时指标 |
| OpenCV | 视频读写与 MJPEG |
| ONNX Runtime DirectML | Windows AMD GPU 推理 |
| Matplotlib | 离线仿真可视化 |

## 训练说明

仓库内含训练记录 `args.yaml`（VisDrone → 单类 vehicle 微调，1280 输入）。完整训练流程与数据集需自行准备，可参考 `visdrone_training.html` 中的说明页面。

## License

MIT
