"""The desktop main window.

Layout (900x600 minimum):

* top status bar — tier · deploy mode · actual backend · device · API · HF
* ``决策`` tab — question + dynamic options on the left, result card on the right
* ``设置`` tab — tier cards, engine management, download sources, HF login/upload

Visual language follows the spec: rounded cards, primary ``#4F7CFF``, dark mode,
state colours (ready green / warning amber / disabled grey + lock), backend badges
(内置 blue-grey, Ollama teal, vLLM violet, llama.cpp dark green, Docker navy).
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase

# 系统自带的中文字体文件，按平台列出。Qt 的字体数据库在 offscreen、
# 精简 Linux 与 CI 环境里可能只列得出字体名却加载不了字体文件，
# 结果是所有中文渲染成豆腐块 —— 所以这里直接注册文件。
_CJK_FONT_FILES = (
    # Windows
    "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
    "C:/Windows/Fonts/msyhbd.ttc",   # 微软雅黑 粗体
    "C:/Windows/Fonts/simhei.ttf",   # 黑体
    "C:/Windows/Fonts/simsun.ttc",   # 宋体
    "C:/Windows/Fonts/Deng.ttf",     # 等线
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)


def ensure_cjk_font() -> bool:
    """把系统中文字体文件注册进 Qt 应用字体库。

    返回是否至少注册成功一个。失败不影响启动 —— 只是中文可能显示成方框。
    """

    import os

    loaded = False
    for path in _CJK_FONT_FILES:
        if not os.path.exists(path):
            continue
        try:
            if QFontDatabase.addApplicationFont(path) >= 0:
                loaded = True
        except Exception:
            continue
    return loaded
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.config import get_config

from .api_client import ApiClient

# 主题调色板 / Theme palettes.
# 所有颜色都在这里定义；apply_theme() 重新绑定模块级常量，
# 窗口在主题变化后重建，f-string 捕获的新颜色才会生效。
THEMES: Dict[str, Dict[str, str]] = {
    "dark": {
        "primary": "#4F7CFF",
        "bg": "#14161B",
        "card": "#1D2027",
        "card_disabled": "#191B20",
        "line": "#2A2E37",
        "text": "#E8EAED",
        "muted": "#9AA0A6",
        "on_accent": "#06281F",
        "ready": "#34A853",
        "warning": "#F9AB00",
        "danger": "#FB923C",
        "online": "#4F7CFF",
        "api_purple": "#A78BFA",
        "engine_teal": "#2DD4BF",
        "hf": "#FF9D00",
        "llamacpp": "#1F7A5C",
        "docker": "#1E40AF",
        "builtin": "#8A94A6",
    },
    "light": {
        "primary": "#3B63E8",
        "bg": "#F4F6FA",
        "card": "#FFFFFF",
        "card_disabled": "#EEF1F6",
        "line": "#DDE2EA",
        "text": "#16191F",
        "muted": "#5B6472",
        "on_accent": "#FFFFFF",
        "ready": "#17803D",
        "warning": "#9A6700",
        "danger": "#B23C0C",
        "online": "#3B63E8",
        "api_purple": "#6D4AD6",
        "engine_teal": "#0F766E",
        "hf": "#A85B00",
        "llamacpp": "#146B4E",
        "docker": "#1E3A8A",
        "builtin": "#5B6472",
    },
}

THEME = "dark"

PRIMARY = BG = CARD = CARD_DISABLED = TEXT = MUTED = ""
LINE = READY = WARNING = DANGER = ONLINE = API_PURPLE = ENGINE_TEAL = ""
ON_ACCENT = ""
BADGE_COLORS: Dict[str, str] = {}


def apply_theme(name: str = "dark") -> str:
    """Rebind the module-level colour constants for *name*.

    调用后必须重建窗口：现有控件的样式是构造时用 f-string 固化的。
    *Widgets freeze their stylesheet at construction, so rebuild after calling this.*
    """
    global THEME, PRIMARY, BG, CARD, CARD_DISABLED, LINE, TEXT, MUTED, ON_ACCENT
    global READY, WARNING, DANGER, ONLINE, API_PURPLE, ENGINE_TEAL, BADGE_COLORS

    THEME = name if name in THEMES else "dark"
    pal = THEMES[THEME]

    PRIMARY = pal["primary"]
    BG = pal["bg"]
    CARD = pal["card"]
    CARD_DISABLED = pal["card_disabled"]
    LINE = pal["line"]
    TEXT = pal["text"]
    MUTED = pal["muted"]
    ON_ACCENT = pal["on_accent"]
    READY = pal["ready"]
    WARNING = pal["warning"]
    DANGER = pal["danger"]
    ONLINE = pal["online"]
    API_PURPLE = pal["api_purple"]
    ENGINE_TEAL = pal["engine_teal"]

    BADGE_COLORS = {
        "builtin": pal["builtin"],
        "ollama": ENGINE_TEAL,
        "vllm": API_PURPLE,
        "llamacpp": pal["llamacpp"],
        "docker": pal["docker"],
        "custom": PRIMARY,
        "huggingface": pal["hf"],
    }
    return THEME


apply_theme("dark")


def sheet(widget: QWidget, extra: str = "") -> str:
    return f"""
    QWidget#{widget.objectName()} {{ background: {CARD}; border-radius: 14px; border: 1px solid {LINE}; }}
    {extra}
    """


class Worker(QThread):
    """Runs a blocking API call off the UI thread."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class StatusBar(QFrame):
    def __init__(self, parent=None, on_theme_toggle=None) -> None:
        super().__init__(parent)
        self.setObjectName("statusBar")
        self.setFixedHeight(40)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(10)
        self._chips: Dict[str, QLabel] = {}

        self._chip_row = QWidget()
        chip_layout = QHBoxLayout(self._chip_row)
        chip_layout.setContentsMargins(0, 0, 0, 0)
        chip_layout.setSpacing(10)
        layout.addWidget(self._chip_row, 1)

        self.theme_btn = QPushButton("浅色" if THEME == "dark" else "深色")
        self.theme_btn.setObjectName("themeButton")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setFixedHeight(26)
        self.theme_btn.setStyleSheet(
            f"QPushButton#themeButton {{ background:{CARD}; color:{TEXT};"
            f" border:1px solid {LINE}; border-radius:10px; padding:2px 12px;"
            f" font-weight:600; }}"
            f"QPushButton#themeButton:hover {{ border-color:{PRIMARY}; color:{PRIMARY}; }}"
        )
        if on_theme_toggle is not None:
            self.theme_btn.clicked.connect(on_theme_toggle)
        layout.addWidget(self.theme_btn, 0)

        self.refresh({})

    def _chip(self, key: str, text: str, color: str = MUTED) -> QLabel:
        if key in self._chips:
            label = self._chips[key]
        else:
            label = QLabel()
            self._chips[key] = label
            self._chip_row.layout().addWidget(label)
        label.setText(text)
        label.setStyleSheet(
            f"background:{CARD}; color:{color}; border-radius:10px; padding:4px 10px;"
            f"font-weight:600;"
        )
        return label

    def refresh(self, state: Dict[str, Any]) -> None:
        self._chip("tier", f"档位 {state.get('tier', '-')}", TEXT)
        self._chip("mode", f"{state.get('mode', '-')}", BADGE_COLORS.get(state.get("mode", ""), MUTED))
        self._chip("backend", f"后端 {state.get('backend', '-')}", BADGE_COLORS.get(state.get("mode", ""), MUTED))
        self._chip("device", state.get("device", "-"), MUTED)
        self._chip("api", "API " + ("运行中" if state.get("api") else "未启动"),
                   READY if state.get("api") else MUTED)
        self._chip("hf", "HF " + ("已登录" if state.get("hf") else "未登录"),
                   BADGE_COLORS.get("huggingface", MUTED) if state.get("hf") else MUTED)


class OptionEditor(QFrame):
    """Dynamic option list: add with Enter, remove with a button."""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("optionEditor")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入选项后按回车添加（至少 2 个）")
        self.input.returnPressed.connect(self.add_option)
        self.add_btn = QPushButton("添加")
        self.add_btn.clicked.connect(self.add_option)
        row.addWidget(self.input, 1)
        row.addWidget(self.add_btn)
        layout.addLayout(row)

        self.list = QListWidget()
        self.list.setFixedHeight(150)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        layout.addWidget(self.list)

        self.remove_btn = QPushButton("删除选中项")
        self.remove_btn.clicked.connect(self.remove_selected)
        layout.addWidget(self.remove_btn)

    def add_option(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, f"opt{self.list.count() + 1}")
        self.list.addItem(item)
        self.input.clear()
        self.changed.emit()

    def remove_selected(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self.changed.emit()

    def set_options(self, texts: List[str]) -> None:
        self.list.clear()
        for text in texts:
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, f"opt{self.list.count() + 1}")
            self.list.addItem(item)
        self.changed.emit()

    def options(self) -> List[Dict[str, str]]:
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            out.append({"id": item.data(Qt.UserRole) or f"opt{i + 1}", "text": item.text()})
        return out

    def count(self) -> int:
        return self.list.count()


class DecisionTab(QWidget):
    decided = Signal(dict)

    def __init__(self, client: ApiClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self._streaming = False

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        left = QFrame()
        left.setObjectName("decisionLeft")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(12)

        left_layout.addWidget(QLabel("问题"))
        self.question = QTextEdit()
        self.question.setPlaceholderText("例如：周末两天，预算 500，两个人出行，去哪里？")
        self.question.setFixedHeight(90)
        left_layout.addWidget(self.question)

        left_layout.addWidget(QLabel("选项（回车添加，至少 2 个）"))
        self.options = OptionEditor()
        self.options.set_options(["近郊露营", "周边古镇一日游"])
        self.options.changed.connect(self._sync_button_state)
        left_layout.addWidget(self.options)

        self.hint = QLabel("")
        self.hint.setStyleSheet(f"color:{WARNING};")
        left_layout.addWidget(self.hint)

        controls = QHBoxLayout()
        self.decide_btn = QPushButton("开始决策")
        self.decide_btn.setObjectName("primaryButton")
        self.decide_btn.clicked.connect(self.run_decision)
        self.again_btn = QPushButton("重新决策")
        self.again_btn.clicked.connect(self.run_decision)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.clicked.connect(self.clear)
        self.force_online = QPushButton("强制联网")
        self.force_online.clicked.connect(lambda: self.run_decision(force=True))
        controls.addWidget(self.decide_btn)
        controls.addWidget(self.again_btn)
        controls.addWidget(self.force_online)
        controls.addWidget(self.clear_btn)
        left_layout.addLayout(controls)
        left_layout.addStretch(1)

        right = QFrame()
        right.setObjectName("decisionRight")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 16, 16, 16)

        self.result_title = QLabel("结果")
        self.result_title.setStyleSheet("font-size:18px; font-weight:700;")
        right_layout.addWidget(self.result_title)

        self.badge = QLabel("来源：本地")
        self.badge.setStyleSheet(
            f"background:{ENGINE_TEAL}; color:{ON_ACCENT}; border-radius:10px; padding:3px 10px;"
            f"font-weight:700;"
        )
        self.badge.setFixedHeight(26)
        right_layout.addWidget(self.badge)

        self.selected = QLabel("尚未决策")
        self.selected.setStyleSheet(f"font-size:24px; font-weight:700; color:{TEXT};")
        self.selected.setWordWrap(True)
        right_layout.addWidget(self.selected)

        self.reason = QLabel("")
        self.reason.setStyleSheet(f"color:{MUTED}; font-size:14px;")
        self.reason.setWordWrap(True)
        right_layout.addWidget(self.reason)

        self.confidence = QLabel("")
        self.confidence.setStyleSheet(f"color:{TEXT}; font-weight:600;")
        right_layout.addWidget(self.confidence)

        self.alternatives = QListWidget()
        self.alternatives.setFixedHeight(120)
        right_layout.addWidget(QLabel("候选排序"))
        right_layout.addWidget(self.alternatives)

        self.sources = QListWidget()
        self.sources.setFixedHeight(90)
        right_layout.addWidget(QLabel("来源"))
        right_layout.addWidget(self.sources)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{MUTED};")
        right_layout.addWidget(self.status)
        right_layout.addStretch(1)

        root.addWidget(left, 1)
        root.addWidget(right, 1)
        self._sync_button_state()

    # -- actions -----------------------------------------------------------

    def _sync_button_state(self) -> None:
        valid = bool(self.question.toPlainText().strip()) and self.options.count() >= 2
        self.decide_btn.setEnabled(valid)
        self.again_btn.setEnabled(valid)
        self.force_online.setEnabled(valid)
        if not self.question.toPlainText().strip():
            self.hint.setText("问题不能为空")
        elif self.options.count() < 2:
            self.hint.setText("至少需要 2 个选项")
        else:
            self.hint.setText("")

    def clear(self) -> None:
        self.question.clear()
        self.options.list.clear()
        self.selected.setText("尚未决策")
        self.reason.setText("")
        self.confidence.setText("")
        self.alternatives.clear()
        self.sources.clear()
        self.badge.setText("来源：本地")
        self._sync_button_state()

    def run_decision(self, force: bool = False) -> None:
        if not self.question.toPlainText().strip() or self.options.count() < 2:
            self._sync_button_state()
            return
        self._sync_button_state()
        self.status.setText("推理中…")
        self.decide_btn.setEnabled(False)

        payload = {
            "question": self.question.toPlainText().strip(),
            "options": self.options.options(),
            "mode": "online" if force else "auto",
            "model_tier": "auto",
        }
        self._worker = Worker(self._call, payload)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _call(self, payload: dict) -> dict:
        return self.client.decide(payload)

    def _on_done(self, body: dict) -> None:
        self.decide_btn.setEnabled(True)
        self.status.setText(f"完成 · {body.get('latency_ms', 0)} ms")
        self._render(body)
        self.decided.emit(body)

    def _on_failed(self, message: str) -> None:
        self.decide_btn.setEnabled(True)
        self.status.setText(f"失败：{message}")
        QMessageBox.warning(self, "决策失败", message)

    def _render(self, body: dict) -> None:
        selected = body.get("selected") or {}
        self.selected.setText(str(selected.get("text", "")))
        self.reason.setText(str(body.get("reason", "")))
        self.confidence.setText(f"置信度 {selected.get('confidence', 0) * 100:.0f}%")
        source = body.get("source", "local")
        self.badge.setText("来源：" + {"local": "本地", "online": "联网", "hybrid": "本地+联网"}.get(source, source))
        self.badge.setStyleSheet(
            f"background:{PRIMARY if source != 'local' else ENGINE_TEAL}; color:{ON_ACCENT};"
            f"border-radius:10px; padding:3px 10px; font-weight:700;"
        )

        self.alternatives.clear()
        for alt in body.get("alternatives", []):
            self.alternatives.addItem(
                f"{alt.get('text', '')}  —  {alt.get('score', 0) * 100:.0f}%"
            )

        self.sources.clear()
        for src in body.get("sources", []):
            item = QListWidgetItem(f"{src.get('title', '')}  ({src.get('domain', '')})")
            item.setData(Qt.UserRole, src.get("url", ""))
            self.sources.addItem(item)


class TierCard(QFrame):
    """One tier, one deploy mode: the card the settings page renders."""

    action = Signal(str, str)   # (tier, action)

    def __init__(self, tier: str, mode: str, data: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.tier = tier
        self.mode = mode
        self.data = data
        self.setObjectName(f"tierCard_{tier}_{mode}")
        self._build()

    def _build(self) -> None:
        state = self.data.get("status", "disabled")
        disabled = state == "disabled"
        self.setStyleSheet(
            f"QFrame#{self.objectName()} {{ background:{CARD_DISABLED if disabled else CARD};"
            f" border-radius:14px; border:1px solid {LINE}; }}"
            f"QLabel {{ background:transparent; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel(f"{self.tier} · {self.data.get('params', '')}")
        title.setStyleSheet("font-size:16px; font-weight:700;")
        head.addWidget(title)
        head.addStretch(1)

        mode_badge = QLabel(self.mode)
        mode_badge.setStyleSheet(
            f"background:{BADGE_COLORS.get(self.mode, MUTED)}; color:{ON_ACCENT};"
            f"border-radius:8px; padding:2px 8px; font-weight:700;"
        )
        head.addWidget(mode_badge)
        layout.addLayout(head)

        meta = QLabel(
            f"磁盘 {self.data.get('disk_mb', 0) / 1000:.1f} GB · "
            f"峰值内存 {self.data.get('peak_memory_gb', 0):.1f} GB"
        )
        meta.setStyleSheet(f"color:{MUTED};")
        layout.addWidget(meta)

        state_text = {"ready": "可用", "warning": "性能警告", "disabled": "不可用"}.get(state, state)
        state_label = QLabel(("🔒 " if disabled else "") + state_text)
        color = {"ready": READY, "warning": WARNING, "disabled": MUTED}.get(state, MUTED)
        state_label.setStyleSheet(f"color:{color}; font-weight:700;")
        layout.addWidget(state_label)

        if self.data.get("current"):
            layout.addWidget(QLabel("使用中", ))

        if disabled:
            reason = self.data.get("unavailable_reason") or "硬件不达标"
            info = QLabel(reason)
            info.setWordWrap(True)
            info.setStyleSheet(f"color:{MUTED};")
            layout.addWidget(info)
            btn = QPushButton("为什么不可用")
            btn.clicked.connect(lambda: self.action.emit(self.tier, "why"))
        elif state == "warning":
            info = QLabel(self.data.get("warning_reason") or "性能可能较差")
            info.setWordWrap(True)
            info.setStyleSheet(f"color:{WARNING};")
            layout.addWidget(info)
            row = QHBoxLayout()
            load = QPushButton("切换加载")
            load.clicked.connect(lambda: self.action.emit(self.tier, "load"))
            row.addWidget(load)
            if not self.data.get("downloaded"):
                dl = QPushButton("下载")
                dl.clicked.connect(lambda: self.action.emit(self.tier, "download"))
                row.addWidget(dl)
            layout.addLayout(row)
        else:
            row = QHBoxLayout()
            if self.data.get("downloaded"):
                load = QPushButton("切换加载")
                load.clicked.connect(lambda: self.action.emit(self.tier, "load"))
                row.addWidget(load)
            else:
                dl = QPushButton("下载")
                dl.clicked.connect(lambda: self.action.emit(self.tier, "download"))
                row.addWidget(dl)
            layout.addLayout(row)


class SettingsTab(QWidget):
    state_changed = Signal()

    def __init__(self, client: ApiClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self._mode = "builtin"
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("部署模式"))
        self.mode_box = QComboBox()
        self.mode_box.addItems(["builtin", "ollama", "vllm", "llamacpp", "docker"])
        self.mode_box.currentTextChanged.connect(self._on_mode_changed)
        toolbar.addWidget(self.mode_box)
        refresh = QPushButton("重新检测硬件")
        refresh.clicked.connect(self.refresh)
        toolbar.addWidget(refresh)
        toolbar.addStretch(1)
        self.api_btn = QPushButton("启动 API 服务")
        self.api_btn.clicked.connect(self.toggle_api)
        toolbar.addWidget(self.api_btn)
        root.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.tier_layout = QVBoxLayout(inner)
        self.tier_layout.setContentsMargins(0, 0, 0, 0)
        self.tier_layout.setSpacing(10)
        self.tier_layout.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        self.hw_label = QLabel("")
        self.hw_label.setStyleSheet(f"color:{MUTED};")
        self.hw_label.setWordWrap(True)
        root.addWidget(self.hw_label)

    def _on_mode_changed(self, mode: str) -> None:
        self._mode = mode
        self.refresh()

    def toggle_api(self) -> None:
        from .server_host import ServerHost

        host = ServerHost()
        if host.start() and host.wait_ready():
            self.api_btn.setText("API 运行中")
            QMessageBox.information(self, "API 服务", "API 已启动：http://127.0.0.1:8686\n/docs 可调试全部端点")
        else:
            QMessageBox.warning(self, "API 服务", f"启动失败：{host.error}")
        self.state_changed.emit()

    def refresh(self) -> None:
        self._worker = Worker(self.client.models, True)
        self._worker.done.connect(self._render)
        self._worker.failed.connect(lambda m: self.hw_label.setText(f"刷新失败：{m}"))
        self._worker.start()

    def _render(self, body: dict) -> None:
        while self.tier_layout.count() > 1:
            item = self.tier_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for tier in body.get("tiers", []):
            card = TierCard(tier["tier"], tier.get("deploy_mode", self._mode), tier)
            card.action.connect(self._on_action)
            self.tier_layout.insertWidget(self.tier_layout.count() - 1, card)

        hw = body.get("hardware", {})
        gpus = ", ".join(f"{g['name']} ({g['vram_gb']:.0f}GB)" for g in hw.get("gpus", [])) or "无"
        self.hw_label.setText(
            f"可用内存 {hw.get('available_memory_gb', 0):.1f} GB · 磁盘剩余 {hw.get('free_disk_gb', 0):.0f} GB · "
            f"AVX2 {'是' if hw.get('avx2') else '否'} · GPU: {gpus}"
        )

    def _on_action(self, tier: str, action: str) -> None:
        if action == "why":
            self._show_why(tier)
        elif action == "load":
            self._load_tier(tier)
        elif action == "download":
            self._download_tier(tier)

    def _show_why(self, tier: str) -> None:
        try:
            body = self.client.why_unavailable(tier, self._mode)
        except Exception as exc:
            QMessageBox.warning(self, "不可用原因", str(exc))
            return
        text = [f"档位 {tier} · {body.get('deploy_mode')} · {body.get('state')}"]
        if body.get("reason"):
            text.append(f"原因：{body['reason']}")
        text.append("")
        text.append("需要什么：")
        for n in body.get("needs", []):
            text.append(f"  · {n}")
        text.append("当前是什么：")
        for c in body.get("current", []):
            text.append(f"  · {c}")
        text.append("如何解决：")
        for f in body.get("how_to_fix", []):
            text.append(f"  · {f}")
        QMessageBox.information(self, "为什么不可用", "\n".join(text))

    def _load_tier(self, tier: str) -> None:
        try:
            self.client.set_deploy_mode(tier, self._mode)
            self.client.activate_tier(tier)
        except Exception as exc:
            QMessageBox.warning(self, "切换失败", str(exc))
            return
        self.state_changed.emit()
        self.refresh()

    def _download_tier(self, tier: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("下载源选择")
        form = QFormLayout(dialog)
        provider = QComboBox()
        provider.addItems(["github", "huggingface"])
        mirror = QComboBox()
        form.addRow("Provider", provider)
        form.addRow("Mirror", mirror)
        info = QLabel("")
        form.addRow(info)
        buttons = QHBoxLayout()
        ok = QPushButton("开始下载")
        cancel = QPushButton("取消")
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        form.addRow(buttons)

        def fill_mirrors():
            try:
                payload = self.client.download_sources()
            except Exception:
                payload = {}
            mirror.clear()
            for group in payload.get("groups", []):
                if group["provider"] == provider.currentText():
                    mirror.addItems([m["name"] for m in group["mirrors"]])
            info.setText(f"预计 URL 模板变量将在确认后展开")

        provider.currentTextChanged.connect(fill_mirrors)
        fill_mirrors()
        ok.clicked.connect(dialog.accept)
        cancel.clicked.connect(dialog.reject)
        if dialog.exec() != QDialog.Accepted:
            return

        try:
            result = self.client.start_download(tier, provider.currentText(),
                                                mirror.currentText())
        except Exception as exc:
            QMessageBox.warning(self, "下载失败", str(exc))
            return
        QMessageBox.information(self, "下载已开始", f"任务 {result.get('task_id')}\n可在日志中查看进度")


class MainWindow(QMainWindow):
    def __init__(self, client: Optional[ApiClient] = None, on_theme_toggle=None) -> None:
        super().__init__()
        self.client = client or ApiClient()
        self.setWindowTitle("应急食品 · paimind")
        self.resize(1000, 660)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.status = StatusBar(on_theme_toggle=on_theme_toggle)
        layout.addWidget(self.status)

        self.tabs = QTabWidget()
        self.decision = DecisionTab(self.client)
        self.decision.question.textChanged.connect(self.decision._sync_button_state)
        self.settings = SettingsTab(self.client)
        self.settings.state_changed.connect(self._refresh_status)
        self.decision.decided.connect(self._refresh_status)
        self.tabs.addTab(self.decision, "决策")
        self.tabs.addTab(self.settings, "设置")
        layout.addWidget(self.tabs, 1)

        self.setCentralWidget(central)
        self._apply_theme()
        self._refresh_status()
        # Populate the settings tab on startup — otherwise it stays empty until the
        # user clicks "重新检测硬件" and the tier cards never appear.
        self.settings.refresh()

    def _apply_theme(self) -> None:
        self.setStyleSheet(f"""
        QMainWindow, QWidget {{ background:{BG}; color:{TEXT}; font-family:'Microsoft YaHei','Segoe UI',sans-serif; }}
        QLineEdit, QTextEdit, QListWidget, QComboBox {{
            background:{CARD}; color:{TEXT}; border:1px solid {LINE}; border-radius:8px; padding:6px;
        }}
        QPushButton {{
            background:{CARD}; color:{TEXT}; border:1px solid {LINE}; border-radius:8px;
            padding:7px 14px;
        }}
        QPushButton:hover {{ border-color:{PRIMARY}; }}
        QPushButton#primaryButton {{ background:{PRIMARY}; color:{ON_ACCENT}; border:none; font-weight:700; }}
        QPushButton:disabled {{ opacity:0.4; }}
        QTabBar::tab {{ background:transparent; color:{MUTED}; padding:10px 18px; }}
        QTabBar::tab:selected {{ color:{TEXT}; border-bottom:2px solid {PRIMARY}; }}
        QScrollArea {{ border:none; }}
        """)

    def _refresh_status(self, *_: Any) -> None:
        try:
            models = self.client.models()
            engines = self.client.engines()
        except Exception:
            self.status.refresh({"api": False, "device": "未连接"})
            return

        tier = models.get("current_tier", "-")
        mode = models.get("current_mode", "-")
        backend = mode
        for engine in engines.get("engines", []):
            if engine["engine"] == mode and engine["reachable"]:
                backend = engine["state"]
        try:
            hw = self.client.hardware()
            device = (hw.get("hardware", {}).get("hostname", "-"))
        except Exception:
            device = "-"
        try:
            hf = bool(self.client.hf_status().get("logged_in"))
        except Exception:
            hf = False
        try:
            api = bool(self.client.health().get("status") == "ok")
        except Exception:
            api = False

        self.status.refresh({"tier": tier, "mode": mode, "backend": backend,
                             "device": device, "api": api, "hf": hf})

    def closeEvent(self, event) -> None:  # noqa: N802
        self.client.close()
        super().closeEvent(event)
