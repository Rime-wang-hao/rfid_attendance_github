# -*- coding: utf-8 -*-
"""PyQt5 全局 QSS 样式。"""
from __future__ import annotations


PALETTE = {
    "primary":      "#4F46E5",   # indigo 600
    "primary_hov":  "#4338CA",   # indigo 700
    "primary_prs":  "#3730A3",   # indigo 800
    "primary_soft": "#EEF2FF",   # indigo 50
    "accent":       "#10B981",   # emerald 500
    "warn":         "#F59E0B",   # amber 500
    "danger":       "#EF4444",   # red 500
    "info":         "#3B82F6",   # blue 500
    "bg":           "#F8FAFC",   # slate 50
    "bg_alt":       "#F1F5F9",   # slate 100
    "card":         "#FFFFFF",
    "border":       "#E2E8F0",   # slate 200
    "border_strong":"#CBD5E1",   # slate 300
    "text":         "#0F172A",   # slate 900
    "text_muted":   "#64748B",   # slate 500
    "text_faint":   "#94A3B8",   # slate 400
    "shadow":       "rgba(15,23,42,0.08)",
    "in":           "#10B981",
    "out":          "#F59E0B",
    "unknown":      "#94A3B8",
}


QSS = f"""
* {{
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 14px;
    color: {PALETTE['text']};
}}

QMainWindow, #centralWidget {{
    background-color: {PALETTE['bg']};
}}

/* ---------- 侧边栏 ---------- */
#sidebar {{
    background-color: {PALETTE['card']};
    border-right: 1px solid {PALETTE['border']};
}}
#sidebarTitle {{
    font-size: 18px;
    font-weight: 700;
    color: {PALETTE['primary']};
    padding: 24px 20px 8px 20px;
    background: transparent;
}}
#sidebarSubtitle {{
    font-size: 12px;
    color: {PALETTE['text_muted']};
    padding: 0 20px 24px 20px;
    background: transparent;
}}
/* 注意：这里必须用 ID 选择器（#NavButton）而不是类型选择器（NavButton）——
   NavButton 与 QPushButton 同属"类型选择器"，优先级相同，会被下面的
   QPushButton 规则覆盖，导致未选中的菜单项也变成实心主色按钮。 */
#NavButton {{
    text-align: left;
    padding: 12px 20px;
    border: none;
    border-left: 3px solid transparent;
    background: transparent;
    color: {PALETTE['text_muted']};
    font-size: 14px;
    font-weight: 500;
    outline: 0;
}}
#NavButton:hover {{
    background-color: {PALETTE['bg_alt']};
    color: {PALETTE['text']};
}}
#NavButton:checked {{
    background-color: {PALETTE['primary_soft']};
    color: {PALETTE['primary']};
    border-left: 3px solid {PALETTE['primary']};
    font-weight: 600;
}}
#sidebarFooter {{
    color: {PALETTE['text_faint']};
    font-size: 11px;
    padding: 12px 20px;
    background: transparent;
}}

/* ---------- 通用卡片 / 分组 ---------- */
Card {{
    background-color: {PALETTE['card']};
    border: 1px solid {PALETTE['border']};
    border-radius: 12px;
}}
#cardTitle {{
    font-size: 16px;
    font-weight: 700;
    color: {PALETTE['text']};
    background: transparent;
}}
#cardHint {{
    color: {PALETTE['text_muted']};
    font-size: 12px;
    background: transparent;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background-color: {PALETTE['primary']};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: 600;
    outline: 0;
}}
QPushButton:hover    {{ background-color: {PALETTE['primary_hov']}; }}
QPushButton:pressed  {{ background-color: {PALETTE['primary_prs']}; }}
QPushButton:disabled {{ background-color: {PALETTE['border_strong']}; color: white; }}

QPushButton[variant="secondary"] {{
    background-color: {PALETTE['card']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
}}
QPushButton[variant="secondary"]:hover {{
    background-color: {PALETTE['bg_alt']};
    border-color: {PALETTE['border_strong']};
}}
QPushButton[variant="danger"] {{
    background-color: {PALETTE['danger']};
}}
QPushButton[variant="danger"]:hover {{
    background-color: #DC2626;
}}
QPushButton[variant="ghost"] {{
    background: transparent;
    color: {PALETTE['text_muted']};
}}
QPushButton[variant="ghost"]:hover {{
    color: {PALETTE['text']};
    background: {PALETTE['bg_alt']};
}}

/* ---------- 输入框 ---------- */
QLineEdit, QSpinBox, QComboBox, QDateEdit, QTimeEdit, QTextEdit, QPlainTextEdit {{
    background: {PALETTE['card']};
    border: 1px solid {PALETTE['border']};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {PALETTE['primary_soft']};
    selection-color: {PALETTE['primary']};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QDateEdit:focus, QTimeEdit:focus,
QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {PALETTE['primary']};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background: {PALETTE['card']};
    border: 1px solid {PALETTE['border']};
    border-radius: 6px;
    padding: 4px;
    selection-background-color: {PALETTE['primary_soft']};
    selection-color: {PALETTE['primary']};
}}

/* ---------- 标签 ---------- */
QLabel {{ background: transparent; }}
#h1 {{ font-size: 22px; font-weight: 700; color: {PALETTE['text']}; }}
#h2 {{ font-size: 16px; font-weight: 600; color: {PALETTE['text']}; }}
#muted {{ color: {PALETTE['text_muted']}; font-size: 12px; }}
#statValue {{ font-size: 28px; font-weight: 800; color: {PALETTE['primary']}; }}
#statLabel {{ color: {PALETTE['text_muted']}; font-size: 12px; font-weight: 500; }}

#statusDot[state="ok"]   {{ background: {PALETTE['accent']};  border-radius: 7px; }}
#statusDot[state="warn"] {{ background: {PALETTE['warn']};    border-radius: 7px; }}
#statusDot[state="err"]  {{ background: {PALETTE['danger']};  border-radius: 7px; }}
#statusDot[state="off"]  {{ background: {PALETTE['text_faint']}; border-radius: 7px; }}

#heroName {{
    font-size: 48px;
    font-weight: 800;
    color: {PALETTE['text']};
}}
#heroSub {{
    font-size: 18px;
    color: {PALETTE['text_muted']};
}}
#heroEpc {{
    font-family: "Cascadia Mono","Consolas",monospace;
    color: {PALETTE['text_muted']};
    font-size: 13px;
}}
#heroEmpty {{
    font-size: 22px;
    color: {PALETTE['text_faint']};
}}
#heroEventIn  {{ color: {PALETTE['in']};  font-size: 24px; font-weight: 700; }}
#heroEventOut {{ color: {PALETTE['out']}; font-size: 24px; font-weight: 700; }}
#heroEventUnk {{ color: {PALETTE['unknown']}; font-size: 24px; font-weight: 700; }}

/* ---------- 表格 ---------- */
QHeaderView::section {{
    background: {PALETTE['bg_alt']};
    color: {PALETTE['text_muted']};
    border: none;
    border-bottom: 1px solid {PALETTE['border']};
    padding: 10px 8px;
    font-weight: 600;
    font-size: 12px;
}}
QTableWidget, QTableView {{
    background: {PALETTE['card']};
    alternate-background-color: {PALETTE['bg_alt']};
    gridline-color: {PALETTE['border']};
    border: 1px solid {PALETTE['border']};
    border-radius: 10px;
    selection-background-color: {PALETTE['primary_soft']};
    selection-color: {PALETTE['primary']};
}}
QTableWidget::item, QTableView::item {{
    padding: 8px;
    border: none;
}}
QTableCornerButton::section {{
    background: {PALETTE['bg_alt']};
    border: none;
    border-bottom: 1px solid {PALETTE['border']};
}}

/* ---------- 状态栏 ---------- */
QStatusBar {{
    background: {PALETTE['card']};
    border-top: 1px solid {PALETTE['border']};
    color: {PALETTE['text_muted']};
}}

/* ---------- 页面滚动区 ---------- */
/* QScrollArea 默认会画一层不透明背景，这里让它透出页面底色 */
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
#pageFooter {{
    background: {PALETTE['card']};
    border-top: 1px solid {PALETTE['border']};
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px;
}}
QScrollBar::handle:vertical {{
    background: {PALETTE['border_strong']};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {PALETTE['text_faint']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 4px;
}}
QScrollBar::handle:horizontal {{
    background: {PALETTE['border_strong']};
    border-radius: 4px;
    min-width: 30px;
}}

/* ---------- 分组框 ---------- */
QGroupBox {{
    background: {PALETTE['card']};
    border: 1px solid {PALETTE['border']};
    border-radius: 10px;
    margin-top: 16px;
    font-weight: 600;
    padding: 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: {PALETTE['text_muted']};
    background: transparent;
}}
"""
