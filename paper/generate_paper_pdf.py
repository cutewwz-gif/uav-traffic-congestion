# -*- coding: utf-8 -*-
"""按交通类期刊范文体例生成夏令营成果展示论文 PDF。"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parent
OUT_PDF = ROOT / "基于YOLOv12的无人机俯视交通流感知与道路拥堵智能研判系统.pdf"
FONT_SONG = r"C:\Windows\Fonts\simsun.ttc"
FONT_HEI = r"C:\Windows\Fonts\simhei.ttf"

pdfmetrics.registerFont(TTFont("SimSun", FONT_SONG))
pdfmetrics.registerFont(TTFont("SimHei", FONT_HEI))

# ---------------------------------------------------------------------------
# 样式（统一字号层级：标题16 / 一级节14 / 二级节12 / 正文11 / 参考文献10.5）
# ---------------------------------------------------------------------------

ST_TITLE = ParagraphStyle(
    "Title",
    fontName="SimHei",
    fontSize=16,
    leading=24,
    alignment=TA_CENTER,
    spaceAfter=6,
)
ST_SUBTITLE = ParagraphStyle(
    "Subtitle",
    fontName="SimSun",
    fontSize=11,
    leading=16,
    alignment=TA_CENTER,
    spaceAfter=4,
)
ST_AUTHOR = ParagraphStyle(
    "Author",
    fontName="SimSun",
    fontSize=12,
    leading=18,
    alignment=TA_CENTER,
    spaceAfter=10,
)
ST_META = ParagraphStyle(
    "Meta",
    fontName="SimSun",
    fontSize=10.5,
    leading=16,
    alignment=TA_CENTER,
    spaceAfter=8,
)
ST_SECTION = ParagraphStyle(
    "Section",
    fontName="SimHei",
    fontSize=14,
    leading=20,
    spaceBefore=10,
    spaceAfter=6,
)
ST_SUBSECTION = ParagraphStyle(
    "SubSection",
    fontName="SimHei",
    fontSize=12,
    leading=18,
    spaceBefore=6,
    spaceAfter=4,
)
ST_BODY = ParagraphStyle(
    "Body",
    fontName="SimSun",
    fontSize=11,
    leading=18,
    alignment=TA_JUSTIFY,
    firstLineIndent=22,
    spaceAfter=4,
)
ST_KW = ParagraphStyle(
    "KW",
    fontName="SimSun",
    fontSize=11,
    leading=18,
    alignment=TA_JUSTIFY,
    firstLineIndent=0,
    spaceAfter=4,
)
ST_FORMULA = ParagraphStyle(
    "Formula",
    fontName="SimSun",
    fontSize=11,
    leading=18,
    alignment=TA_CENTER,
    spaceBefore=4,
    spaceAfter=4,
)
ST_TABLE = ParagraphStyle(
    "Table",
    fontName="SimSun",
    fontSize=10.5,
    leading=16,
    alignment=TA_CENTER,
    firstLineIndent=0,
    spaceAfter=6,
)

ST_REF = ParagraphStyle(
    "Ref",
    fontName="SimSun",
    fontSize=10.5,
    leading=16,
    leftIndent=0,
    firstLineIndent=0,
    spaceAfter=3,
)


def lbl(text: str) -> str:
    """范文式标签：黑体标签 + 宋体正文。"""
    return f'<font name="SimHei">{text}</font>'


def body(text: str) -> str:
    return text.replace("\n", "<br/>")


def count_chars(text: str) -> int:
    plain = text.replace("<br/>", "").replace("<font name=\"SimHei\">", "").replace("</font>", "")
    return len(plain.replace("\n", "").replace(" ", ""))


PAPER_TITLE = "基于YOLOv12的无人机俯视交通流感知与道路拥堵智能研判系统"
PAPER_TITLE_EN = (
    "Intelligent Road Congestion Assessment System for UAV Top-Down "
    "Traffic Flow Perception Based on YOLOv12"
)

ABSTRACT_CN = (
    "随着城市化进程加快和机动车保有量持续增长，城市道路交通拥堵已成为制约出行效率的重要问题。"
    "传统拥堵研判多依赖路侧固定检测器或浮动车数据，存在布设成本高、难以快速机动部署等不足。"
    "本文面向无人机垂直俯视场景，设计并实现一套集成车辆检测、轨迹跟踪、拥堵量化与Web实时展示的分析系统。"
    "感知层采用VisDrone预训练权重域适应与X-AnyLabeling自标注数据集微调相结合的两阶段YOLOv12训练策略，"
    "配合ByteTrack实现多目标跟踪；算法层在二维像素空间提取平均速度、车队内车距中位数及车距时序波动等特征，"
    "构建含车队切分、连续状态机、Hold挂起与Dist Warmup抗抖动机制的拥堵指数模型，输出0—100分拥堵指数Ct；"
    "应用层基于FastAPI实现MJPEG视频流、WebSocket指标推送与90秒滚动曲线展示。"
    "实验表明，系统可在消费级GPU上稳定运行，能够区分畅通、有序等待、走走停停及路口等灯等典型场景，"
    "可为无人机临时交通监测与夏令营成果演示提供完整技术方案。"
)

KEYWORDS_CN = "无人机；YOLOv12；交通流感知；拥堵指数；ByteTrack；VisDrone迁移学习"
META_LINE = "中图分类号：U491.265    文献标识码：A    成果类型：夏令营项目展示"

ABSTRACT_EN = (
    "With accelerating urbanization, urban road congestion has become a major constraint on travel efficiency. "
    "Traditional assessment relies on fixed roadside detectors or probe vehicles, which are costly and hard to deploy quickly. "
    "This paper presents an integrated system for real-time congestion assessment from UAV vertical top-down video. "
    "A two-stage YOLOv12 pipeline—VisDrone domain adaptation and fine-tuning on a self-annotated X-AnyLabeling "
    "dataset—is combined with ByteTrack. Congestion features are extracted in pixel space, and a continuous-response "
    "index model with platoon splitting and anti-jitter mechanisms outputs a 0–100 score. A FastAPI platform provides "
    "MJPEG streaming, WebSocket metrics, and rolling charts. Experiments on self-collected UAV videos show that "
    "fine-tuned YOLOv12 reaches 97.7% mAP@0.5 on the validation set and the system distinguishes free flow, queued "
    "waiting, stop-and-go, and signalized intersections in real time on consumer-grade GPUs."
)
KEYWORDS_EN = "UAV; YOLOv12; traffic flow perception; congestion index; ByteTrack; VisDrone transfer learning"

SECTIONS: list[tuple[str, str | None, str | None, str | None]] = [
    (
        "0  引  言",
        None,
        "随着经济社会发展和城市化进程加快，交通拥堵问题日益突出。交通拥堵不仅降低出行效率、增加能源消耗和环境污染，"
        "也提高了城市交通治理成本。准确研判道路运行状态、及时识别拥堵态势，对交通规划、信号控制与出行诱导具有重要意义。",
        None,
    ),
    (
        "",
        None,
        "现有研究主要从三类路径展开。张溪等[1]利用交通运行指数历史数据，采用时间序列法对城市道路交通拥堵进行研判与预测；"
        "时柏营等[2]提出SSA-SVM短时交通拥堵指数预测模型；朱云等[3]基于模糊层次分析与神经网络建立城市道路交通拥堵评价模型；"
        "盖健[4]和潘旭[5]从大数据、物联网与多源数据融合角度，讨论拥堵态势感知、演化机理及智能疏导决策；"
        "罗运鹏等[6]对比多种深度学习检测算法，指出YOLO系列在道路拥堵识别中兼顾速度与精度；"
        "黄晓虹等[7]从城市道路交通供需演变与综合治理角度，强调建管并重背景下的精细化治理需求。",
        None,
    ),
    (
        "",
        None,
        "然而，上述研究多面向固定路侧检测器、浮动车数据或宏观指数预测，对无人机俯视视频这一机动、灵活、可快速部署的数据源关注不足。"
        "UAV在约80 m高度、-90°垂直俯拍时可获取大范围路网图像，但存在目标尺度小、检测框抖动大、路口多队列并存等特点，"
        "直接套用地面视角模型或简单一维车距统计易产生误判。针对上述问题，本文设计并实现基于YOLOv12的无人机俯视交通流感知与道路拥堵智能研判系统，"
        "形成可演示、可调试、可导出的夏令营成果。",
        None,
    ),
    (
        "1  系统总体设计",
        "1.1  需求分析与架构",
        "系统面向夏令营成果展示与实际演示需求，需满足：（1）实时性：视频流与指标同步更新；（2）准确性：稳定区分畅通与拥堵；"
        "（3）可解释性：输出中间量便于答辩说明；（4）工程性：Web界面可直接操作。据此采用感知层、数据层、算法层、应用层四层架构。",
        None,
    ),
    (
        "",
        "1.2  数据流程",
        "UAV俯视视频输入后，经YOLOv12检测与ByteTrack跟踪得到带ID的车辆轨迹；DataCleaner对轨迹平滑与窗口管理；"
        "CongestionCalculator计算拥堵指数Ct及场景标签；Web平台实时展示并支持CSV导出。算法时基统一锁定30 FPS，"
        "滑动窗口3 s，与前端90 s滚动曲线配合，便于观察指数变化趋势。",
        None,
    ),
    (
        "2  基于YOLOv12的车辆检测模型",
        "2.1  数据集标注",
        "本文使用X-AnyLabeling对自采集UAV俯视交通视频帧进行矩形框标注，类别为道路车辆。与VisDrone公开数据相比，"
        "自建数据更贴近本项目采集条件（俯视高度、道路类型、光照），有利于降低域偏移、提升实际视频上的检测效果。",
        None,
    ),
    (
        "",
        "2.2  两阶段迁移学习",
        "参考罗运鹏等[6]关于YOLO系列实时性与准确性的分析，本文采用YOLOv12作为检测框架，训练分两阶段："
        "第一阶段以VisDrone预训练YOLOv12权重为基座，在VisDrone合并数据集上域适应（输入1280，50 epoch，batch=4）；"
        "第二阶段以第一阶段best权重为基座，在X-AnyLabeling标注的merged_vehicle数据集上微调（1280，40 epoch），"
        "实现「通用UAV检测能力+项目场景定制」的分层策略。",
        None,
    ),
    (
        "",
        "2.3  跟踪与部署",
        "检测输出经ByteTrack关联得到稳定track_id。模型导出ONNX，推理端采用隔帧检测与轨迹外推（每2帧推理1次），"
        "在Windows平台通过ONNX Runtime与DirectML加速，于消费级AMD GPU上实现实时运行。",
        None,
    ),
    (
        "3  道路拥堵指数算法",
        "3.1  特征提取与数据清洗",
        "在垂直俯视条件下，系统基于二维像素坐标分析，无需三维标定。DataCleaner对YOLOv12与ByteTrack输出进行EMA平滑（α=0.4）、"
        "死区过滤（2 px）及3 s滑动窗口维护。特征包括：平均速度Vavg、帧车距Dt、车距波动σdist、静止占比rstop。",
        None,
    ),
    (
        "",
        "3.2  车队切分",
        "路口场景下，跨队列大间距若计入Dt会虚增σdist。本文沿方差最大轴排序，当相邻间距超过GapThr时切分车队，仅在队内统计车距：",
        "GapThr = k · (L_i + L_{i+1}) / 2，k = 1.5",
    ),
    (
        "",
        "3.3  连续状态机与指数合成",
        "根据Vavg与σdist划分场景：A畅通、B有序等待、C走走停停、D过渡、E路口等灯。速度子分Sspeed与波动子分Sfluct加权合成：",
        "C_t^{raw} = α · S_{speed} + β · S_{fluct}，α = 0.67，β = 0.33",
    ),
    (
        "",
        None,
        "B/C区间采用连续映射，σdist从σlow到σhigh对应20至80分，避免阈值跳崖；路口场景Ct封顶约30分。"
        "判定规则：Ct<60为畅通，Ct≥60为拥堵。",
        None,
    ),
    (
        "",
        "3.4  抗抖动机制",
        "针对检测抖动与车辆数波动，引入：（1）Hold挂起：n<2时不清空历史队列，指数按6分/秒衰减；"
        "（2）Dist Warmup：从无车恢复后连续5帧才写入Dt窗口；（3）时间基EMA：τ=1.5 s；"
        "（4）速率限幅：单帧变化不超过8分/秒；（5）Episode Reset：长时间空窗后清空陈旧Dt样本。",
        None,
    ),
    (
        "",
        None,
        "表1给出了主要场景判别条件与Ct典型区间。该设计借鉴时柏营等[2]、朱云等[3]关于拥堵指数连续性与分级判定的思路，"
        "但实现为可解释的分段函数与状态机，便于夏令营答辩时展示算法逻辑。",
        None,
    ),
    (
        "",
        None,
        "表1  典型交通场景判别条件",
        None,
    ),
    (
        "",
        None,
        "场景 | 速度特征 | 波动特征 | Ct区间 | 标签\n"
        "A畅通 | Vavg≥Vhigh | σdist低 | <60 | 畅通\n"
        "B有序等待 | 低速 | σdist≤σlow | 20—40 | 有序等待\n"
        "C走走停停 | 低速 | σdist≥σhigh | ≥60 | 拥堵\n"
        "E路口等灯 | 静动混合 | 静组波动小 | ≤30 | 路口等灯",
        None,
    ),
    (
        "4  系统实现与成果展示",
        "4.1  后端与前端",
        "系统基于Python与FastAPI实现。VideoAnalysisEngine采用生产者—消费者多线程模型，帧队列深度为2，"
        "避免积压造成延迟。WebSocket实时推送Ct、Vavg、σdist及场景标签；支持25列CSV导出，含raw分、Dt、WinMean、车队数等中间量。",
        None,
    ),
    (
        "",
        "4.2  展示界面",
        "前端布局为左侧控制、右侧视频与图表。视频区仅叠加检测框，无文字HUD遮挡；"
        "四指标卡片、90 s滚动曲线及调试面板集中于页面侧栏，满足夏令营现场演示与答辩讲解需求。"
        "图表纵轴压缩为0—75，橙色虚线标注60分阈值，与Ct<60畅通、Ct≥60拥堵的判定规则一致。",
        None,
    ),
    (
        "",
        "4.3  主要接口",
        "系统提供REST API与WebSocket：/api/start启动分析、/api/stop停止、/api/params热更新参数、"
        "/video_feed推送MJPEG、/ws推送实时指标、/api/export/metrics.csv导出全量数据。"
        "端口冲突时自动回退（8000→8001），降低部署门槛。",
        None,
    ),
    (
        "5  实验与分析",
        "5.1  实验环境",
        "实验平台为Windows PC，AMD GPU+DirectML；测试视频为自采集UAV俯视场景，涵盖路段直行、红灯排队、走走停停及路口混行。",
        None,
    ),
    (
        "",
        "5.2  典型场景结果",
        "在自建验证集上，VisDrone域适应阶段（50 epoch，输入1280）最终 mAP@0.5 为 91.2%，mAP@0.5:0.95 为 77.3%，"
        "精确率 94.5%，召回率 86.3%；X-AnyLabeling微调阶段（40 epoch）最终 mAP@0.5 为 97.7%，mAP@0.5:0.95 为 82.6%，"
        "精确率 95.3%，召回率 95.9%，表明两阶段迁移学习有效提升了俯视场景检测精度。拥堵研判方面，测试视频自由流片段 "
        "Ct 稳定低于 60；以 test.MP4 某帧为例，17 辆车时 Ct=38.5、σdist=1.75，系统判定为畅通，与人工观察一致。"
        "红灯排队片段 σdist 较小，Ct 约 20—40；走走停停片段 σdist 较高，Ct≥60；路口混行识别为 E 类，Ct 封顶约 30。",
        None,
    ),
    (
        "",
        "5.3  实时性能与调试功能",
        "隔帧推理（每2帧检测1次）配合30 FPS锁帧，Web端实测 FPS 约 17，WebSocket推送延迟低于帧间隔，"
        "DirectML 在 AMD GPU 上可稳定运行，满足夏令营现场演示。"
        "系统提供逐帧调试模式（Step Mode）与25列指标CSV导出，包含raw分、Dt、WinMean、Platoons、GapThr、hold秒数等，"
        "便于答辩时说明「某一时刻Ct为何升高或降低」。",
        None,
    ),
    (
        "",
        "5.4  讨论",
        "对比时间序列预测类方法[1][2]，本文侧重实时研判；对比CNN端到端识别[6]，本文强调可解释特征与状态机；"
        "与潘旭[5]、盖健[4]的大数据平台视角互补，本文提供微观视频级实现路径。"
        "局限性在于：尚未引入三维标定，速度为像素单位；单路分析为主，未做多路融合。",
        None,
    ),
    (
        "6  结  论",
        None,
        "本文面向无人机垂直俯视交通视频，完成了 YOLOv12 两阶段检测、像素空间拥堵指数算法及 Web 系统的集成实现。"
        "主要贡献包括：（1）VisDrone 域适应与 X-AnyLabeling 自标注微调相结合的分层训练策略；"
        "（2）含车队切分、连续状态机与五项抗抖动机制的 Ct 模型；（3）可演示、可调试、可导出的 FastAPI 平台。"
        "VisDrone 域适应与自标注微调相结合，提升了俯视场景检测适应性；车队切分、连续状态机与抗抖动机制有效缓解路口误判和指数跳变。"
        "局限性在于速度为像素单位、尚未引入三维标定，单路分析为主。后续将补充 MOTA 等跟踪指标，并探索与宏观拥堵指数[1][2]的融合分析。",
        None,
    ),
]

REFERENCES = [
    "[1] 张溪, 温慧敏, 穆毅, 等. 基于时间序列法的城市道路交通拥堵研判与应用实例[J]. 智能交通与安全, 2012(8): 421-424.",
    "[2] 时柏营, 杜文鑫, 梁成, 等. 基于SSA-SVM的短时交通拥堵指数预测[J]. 黑龙江交通科技, 2025(10): 120-124.",
    "[3] 朱云, 王建宇, 杨影, 等. 城市道路交通拥堵的模糊神经网络评析[J]. 北京工业大学学报, 2018, 38(5): 487-492.",
    "[4] 盖健, 孙明铭, 李雪, 等. 大数据环境下道路拥堵智能疏导技术分析[J]. 数字经济, 2025(Z2): 94-95.",
    "[5] 潘旭. 大数据分析驱动的城市道路交通拥堵演化机理与疏导模型研究[C]//2025年第八届工程领域数字化转型与新质生产力发展研究学术交流会论文集, 2025: 105-107.",
    "[6] 罗运鹏, 李艺. 基于卷积神经网络的道路拥堵识别模型分析[J]. 战略连线, 2025(18): 174-176.",
    "[7] 黄晓虹, 崔昂. 广州城市道路状况演变分析及拥堵治理对策研究[J]. 交通与港航, 2025(5): 55-61.",
]


def build_story() -> list:
    story: list = []
    story.append(Paragraph(PAPER_TITLE, ST_TITLE))
    story.append(Paragraph(PAPER_TITLE_EN, ST_SUBTITLE))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("（作者姓名）1", ST_AUTHOR))
    story.append(Paragraph("（1  单位名称，城市  邮编）", ST_AUTHOR))
    story.append(Spacer(1, 3 * mm))

    story.append(Paragraph(lbl("摘  要：") + ABSTRACT_CN, ST_BODY))
    story.append(Paragraph(lbl("关键词：") + KEYWORDS_CN, ST_KW))
    story.append(Paragraph(META_LINE, ST_META))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(lbl("Abstract: ") + ABSTRACT_EN, ST_BODY))
    story.append(Paragraph(lbl("Keywords: ") + KEYWORDS_EN, ST_KW))
    story.append(Spacer(1, 4 * mm))

    for sec_title, sub_title, content, formula in SECTIONS:
        if sec_title:
            story.append(Paragraph(sec_title, ST_SECTION))
        if sub_title:
            story.append(Paragraph(sub_title, ST_SUBSECTION))
        if content:
            if content.startswith("表1  "):
                story.append(Paragraph(content, ST_SUBSECTION))
            elif "|" in content and "A畅通" in content:
                story.append(Paragraph(body(content.replace("|", "  ")), ST_TABLE))
            else:
                story.append(Paragraph(body(content), ST_BODY))
        if formula:
            story.append(Paragraph(formula, ST_FORMULA))

    story.append(Paragraph("参考文献", ST_SECTION))
    for ref in REFERENCES:
        story.append(Paragraph(ref, ST_REF))

    return story


def build_pdf() -> None:
    all_text = ABSTRACT_CN + KEYWORDS_CN + "".join(
        (c or "") + (f or "") for _, _, c, f in SECTIONS
    ) + "".join(REFERENCES)
    n = count_chars(all_text)
    if n > 15000:
        raise RuntimeError(f"字数 {n} 超过15000上限")

    doc = SimpleDocTemplate(
        str(OUT_PDF),
        pagesize=A4,
        leftMargin=28 * mm,
        rightMargin=28 * mm,
        topMargin=25 * mm,
        bottomMargin=25 * mm,
        title=PAPER_TITLE,
    )

    def add_page_number(canvas, doc_obj):  # noqa: ANN001
        canvas.saveState()
        canvas.setFont("SimSun", 9)
        canvas.drawCentredString(A4[0] / 2, 15 * mm, str(canvas.getPageNumber()))
        canvas.restoreState()

    doc.build(build_story(), onFirstPage=add_page_number, onLaterPages=add_page_number)
    print(f"已生成: {OUT_PDF}")
    print(f"正文字数: {n}")


if __name__ == "__main__":
    build_pdf()
