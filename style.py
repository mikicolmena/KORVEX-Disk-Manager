# -*- coding: utf-8 -*-
"""Paleta y hoja de estilos de KORVEX (compartida con KORVEX Audio Console)."""

APP_BG = "#0B0B0E"; PANEL_BG = "#15151C"; PANEL_LIGHT = "#1F1F2A"
BORDER = "#2A2A35"; BORDER_HOVER = "#3E3E4E"
ACCENT = "#5E6AD2"; ACCENT_HOVER = "#707EED"; ACCENT_DIM = "rgba(94, 106, 210, 0.15)"
TEXT_MAIN = "#F1F1F4"; TEXT_MUTED = "#8B8B9E"
DANGER = "#E54D4D"; DANGER_DIM = "rgba(229, 77, 77, 0.15)"; SUCCESS = "#43B581"
WARNING = "#E5A84D"

STYLESHEET = f"""
QWidget {{ background-color: {APP_BG}; color: {TEXT_MAIN}; font-family: "Segoe UI", "Inter", sans-serif; font-size: 10pt; }}
QScrollArea, QScrollArea > QWidget > QWidget, QLabel, QWidget#Transparent {{ background-color: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER_HOVER}; border-radius: 5px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXT_MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER_HOVER}; border-radius: 5px; min-width: 40px; }}

QListWidget#SideBar {{ background-color: {APP_BG}; border: none; outline: none; }}
QListWidget#SideBar::item {{ padding: 12px 20px; margin: 4px 12px; border-radius: 8px; color: {TEXT_MUTED}; font-weight: 600; font-size: 11pt; background-color: transparent; }}
QListWidget#SideBar::item:hover {{ background-color: {PANEL_BG}; color: {TEXT_MAIN}; }}
QListWidget#SideBar::item:selected {{ background-color: {ACCENT_DIM}; color: {ACCENT}; }}
QWidget#SideBarWrap {{ background-color: {APP_BG}; }}
QLabel#Brand {{ color: {TEXT_MAIN}; font-size: 13pt; font-weight: 800; letter-spacing: 1px; }}
QLabel#BrandSub {{ color: {TEXT_MUTED}; font-size: 9pt; }}
QLabel#StatusLbl {{ color: {TEXT_MUTED}; font-size: 9pt; }}

QFrame#Card {{ background-color: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#Card:hover {{ border-color: {BORDER_HOVER}; }}
QFrame#Card QLabel, QFrame#Card QCheckBox, QFrame#Card QWidget#Transparent {{ background-color: transparent; }}
QFrame#Separator {{ background-color: {BORDER}; border: none; }}

QFrame#PartBlock {{ background-color: {PANEL_LIGHT}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#PartBlock:hover {{ border-color: {ACCENT}; background-color: {ACCENT_DIM}; }}
QFrame#PartBlock[protected="true"]:hover {{ border-color: {BORDER_HOVER}; background-color: {PANEL_LIGHT}; }}
QFrame#PartBlock QLabel {{ background-color: transparent; }}

QLabel#Badge {{ background-color: {ACCENT_DIM}; color: {ACCENT}; border-radius: 6px; padding: 2px 8px; font-size: 8.5pt; font-weight: 700; }}
QLabel#BadgeLock {{ background-color: {DANGER_DIM}; color: {DANGER}; border-radius: 6px; padding: 2px 8px; font-size: 8.5pt; font-weight: 700; }}
QLabel#BadgeMuted {{ background-color: {PANEL_LIGHT}; color: {TEXT_MUTED}; border-radius: 6px; padding: 2px 8px; font-size: 8.5pt; font-weight: 700; }}

QPushButton {{ background-color: {PANEL_LIGHT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 16px; color: {TEXT_MAIN}; font-weight: 600; }}
QPushButton:hover {{ background-color: {BORDER_HOVER}; }}
QPushButton:pressed {{ background-color: {ACCENT}; border-color: {ACCENT}; color: #FFF; }}
QPushButton:disabled {{ background-color: {PANEL_BG}; color: {TEXT_MUTED}; border-color: {BORDER}; }}
QPushButton#PrimaryBtn {{ background-color: {ACCENT}; border: none; color: #FFF; }}
QPushButton#PrimaryBtn:hover {{ background-color: {ACCENT_HOVER}; }}
QPushButton#PrimaryBtn:disabled {{ background-color: {BORDER}; color: {TEXT_MUTED}; }}
QPushButton#DangerBtn {{ background-color: {DANGER}; border: none; color: #FFF; }}
QPushButton#DangerBtn:hover {{ background-color: #F06060; }}
QPushButton#DangerBtn:disabled {{ background-color: {BORDER}; color: {TEXT_MUTED}; }}

QTableWidget {{ background-color: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 12px; gridline-color: {BORDER}; outline: none; }}
QTableWidget::item {{ padding: 4px 12px; border-bottom: 1px solid {BORDER}; }}
QTableWidget::item:selected {{ background-color: {ACCENT_DIM}; color: {TEXT_MAIN}; }}
QHeaderView {{ background-color: transparent; }}
QHeaderView::section {{ background-color: {PANEL_BG}; color: {TEXT_MUTED}; padding: 0 12px; border: none; border-bottom: 2px solid {BORDER}; font-weight: 700; font-size: 9pt; text-transform: uppercase; }}
QTableCornerButton::section {{ background-color: {PANEL_BG}; border: none; }}

QComboBox {{ background-color: {PANEL_LIGHT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 0 10px; color: {TEXT_MAIN}; min-height: 32px; font-size: 10pt; }}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox:disabled {{ color: {TEXT_MUTED}; border-color: {BORDER}; }}
QComboBox::drop-down {{ border: none; width: 22px; background: transparent; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; border-style: solid; border-width: 5px 5px 0 5px; border-color: {TEXT_MUTED} transparent transparent transparent; margin-right: 6px; }}
QComboBox QAbstractItemView {{ background-color: {PANEL_LIGHT}; border: 1px solid {BORDER}; border-radius: 6px; selection-background-color: {ACCENT}; color: {TEXT_MAIN}; outline: none; padding: 4px; }}

QCheckBox {{ color: {TEXT_MAIN}; font-size: 10pt; spacing: 10px; padding: 4px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER_HOVER}; background: {PANEL_BG}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox:disabled {{ color: {TEXT_MUTED}; }}

QProgressBar {{ background-color: {PANEL_LIGHT}; border: none; border-radius: 4px; }}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 4px; }}
QProgressBar#Big {{ border-radius: 6px; min-height: 12px; max-height: 12px; }}
QProgressBar#Big::chunk {{ border-radius: 6px; }}
QProgressBar[level="high"]::chunk {{ background-color: {WARNING}; }}
QProgressBar[level="full"]::chunk {{ background-color: {DANGER}; }}
QProgressBar[protected="true"]::chunk {{ background-color: {TEXT_MUTED}; }}

QMenu {{ background-color: {PANEL_LIGHT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 8px 24px; border-radius: 6px; color: {TEXT_MAIN}; background: transparent; }}
QMenu::item:selected {{ background-color: {ACCENT}; color: #FFF; }}
QMenu::item:disabled {{ color: {TEXT_MUTED}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

QToolTip {{ background-color: {PANEL_LIGHT}; color: {TEXT_MAIN}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px; }}

QDialog {{ background-color: {APP_BG}; }}
QMessageBox {{ background-color: {APP_BG}; }}
QMessageBox QLabel {{ color: {TEXT_MAIN}; }}
QLineEdit {{ background-color: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 8px; padding: 10px 14px; color: {TEXT_MAIN}; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QPlainTextEdit {{ background-color: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px; color: {TEXT_MUTED}; font-family: "Cascadia Mono", "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 9pt; }}

QLabel#PageTitle {{ font-size: 18pt; font-weight: 800; color: {TEXT_MAIN}; letter-spacing: -0.5px; }}
QLabel#SubTitle {{ color: {TEXT_MUTED}; font-size: 10pt; }}
QLabel#DangerWarn {{ color: {DANGER}; font-size: 10pt; font-weight: 600; }}
QLabel#SettingLabel {{ color: {TEXT_MAIN}; font-size: 11pt; font-weight: 600; }}
QLabel#SettingHint {{ color: {TEXT_MUTED}; font-size: 9.5pt; }}
QLabel#DiskTitle {{ color: {TEXT_MAIN}; font-size: 12pt; font-weight: 700; }}
QLabel#DiskMeta {{ color: {TEXT_MUTED}; font-size: 9.5pt; }}
QLabel#PartName {{ color: {TEXT_MAIN}; font-weight: 700; font-size: 10pt; }}
QLabel#PartMeta {{ color: {TEXT_MUTED}; font-size: 8.5pt; }}
QLabel#EmptyLbl {{ color: {TEXT_MUTED}; font-size: 11pt; }}
"""
