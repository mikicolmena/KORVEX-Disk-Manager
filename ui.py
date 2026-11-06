# -*- coding: utf-8 -*-
"""Interfaz de KORVEX Disk Manager (estilo KORVEX Audio Console)."""
import os
import shutil
import logging
import subprocess
from typing import List, Optional, Callable

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QFrame,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView, QMenu, QMessageBox,
    QDialog, QComboBox, QLineEdit, QCheckBox, QDialogButtonBox, QSystemTrayIcon,
    QListWidget, QListWidgetItem, QStackedWidget, QApplication, QMainWindow,
    QSizePolicy, QAbstractItemView, QPlainTextEdit, QGridLayout, QSpinBox, QSlider,
    QFileDialog,
)
from PyQt6.QtCore import Qt, QSettings, QThread, pyqtSignal, QTimer, QRectF
from PyQt6.QtGui import QCursor, QIcon, QPixmap, QPainter, QColor, QStandardItemModel, QPainterPath

import disks as D
import rawio as R
import diag
from disks import Disk, Partition, format_bytes
from operations import (OpWorker, OPS, PHASES, FILESYSTEMS, FS_ARG, FS_TIPS,
                        available_filesystems, clean_label)
from platform_utils import (IS_WINDOWS, IS_LINUX, APP_NAME, APP_VERSION, open_path, log_dir,
                            is_admin, has_pkexec, PowerGuard, journal_read, journal_clear)
from style import (TEXT_MAIN, TEXT_MUTED, DANGER, SUCCESS, ACCENT, WARNING, BORDER, PANEL_BG)

HAS_UDISKS = IS_LINUX and shutil.which("udisksctl") is not None
MiB = D.MiB


# ======================================================================
# UTILIDADES DE INTERFAZ
# ======================================================================
def _sep():
    s = QFrame(); s.setObjectName("Separator"); s.setFixedHeight(1)
    return s


def _badge(text, kind="Badge", tip=""):
    b = QLabel(text); b.setObjectName(kind)
    if tip:
        b.setToolTip(tip)
    return b


def _human_time(seconds: float) -> str:
    s = int(max(0, seconds))
    if s >= 3600:
        return f"{s // 3600} h {s % 3600 // 60:02d} min"
    if s >= 60:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s} s"


def _usage_bar(part: Partition, height=6) -> Optional[QProgressBar]:
    if part.free is None or part.size <= 0:
        return None
    used = max(0, min(100, int(round((part.size - part.free) * 100 / part.size))))
    bar = QProgressBar(); bar.setRange(0, 100); bar.setValue(used); bar.setTextVisible(False)
    bar.setFixedHeight(height)
    bar.setProperty("level", "full" if used >= 90 else "high" if used >= 75 else "ok")
    bar.setProperty("protected", part.protected)
    bar.setToolTip(f"{used} % usado · {format_bytes(part.free)} libres")
    return bar


def tinted_pixmap(icon: QIcon, color: str, size: int, dpr: float = 1.0) -> QPixmap:
    """El logo es negro sobre transparente: se repinta en otro color para
    que se vea sobre el fondo oscuro."""
    px = int(size * dpr)
    src = icon.pixmap(px, px)
    if src.isNull():
        return QPixmap()
    out = QPixmap(src.size()); out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(out.rect(), QColor(color))
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


def badge_icon(icon: QIcon) -> QIcon:
    """K blanca sobre un cuadrado redondeado del color de acento: se ve tanto
    en barras/bandejas claras como oscuras."""
    res = QIcon()
    for s in (16, 22, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(s, s); pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath(); path.addRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)
        p.fillPath(path, QColor(ACCENT))
        k = tinted_pixmap(icon, "#FFFFFF", int(s * 0.72))
        if not k.isNull():
            p.drawPixmap((s - k.width()) // 2, (s - k.height()) // 2, k)
        p.end()
        res.addPixmap(pm)
    return res


def _msg(parent, kind, title, text):
    box = QMessageBox(parent)
    box.setIcon({"info": QMessageBox.Icon.Information, "warn": QMessageBox.Icon.Warning,
                 "error": QMessageBox.Icon.Critical}[kind])
    box.setWindowTitle(title); box.setText(text)
    box.exec()


# ======================================================================
# WORKERS DE LECTURA
# ======================================================================
class DiskReaderWorker(QThread):
    done = pyqtSignal(list)
    error = pyqtSignal(str)

    def run(self):
        try:
            self.done.emit(D.list_disks())
        except Exception as e:
            logging.exception("Error leyendo discos")
            self.error.emit(str(e))


class CommandWorker(QThread):
    """Ejecuta un comando corto de usuario (montar/desmontar con udisksctl)."""
    done = pyqtSignal(bool, str)

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd

    def run(self):
        try:
            r = subprocess.run(self.cmd, capture_output=True, text=True, timeout=60)
            self.done.emit(r.returncode == 0, (r.stdout if r.returncode == 0 else r.stderr).strip())
        except Exception as e:
            self.done.emit(False, str(e))


# ======================================================================
# DIÁLOGOS BASE
# ======================================================================
class BaseDialog(QDialog):
    def __init__(self, title, heading, sub="", width=460, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title); self.setModal(True); self.setFixedWidth(width)
        self.lay = QVBoxLayout(self); self.lay.setContentsMargins(24, 24, 24, 24); self.lay.setSpacing(14)
        h = QLabel(f"<b>{heading}</b>"); h.setWordWrap(True); h.setStyleSheet("font-size: 13pt;")
        self.lay.addWidget(h)
        if sub:
            s = QLabel(sub); s.setObjectName("SubTitle"); s.setWordWrap(True); self.lay.addWidget(s)
        self.lay.addWidget(_sep())

    def add_buttons(self, ok_text="Continuar", ok_kind="PrimaryBtn"):
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setObjectName(ok_kind); self.ok_btn.setText(ok_text)
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        self.lay.addWidget(bb)
        return bb

    def hint(self, text, obj="SettingHint"):
        l = QLabel(text); l.setObjectName(obj); l.setWordWrap(True); l.setTextFormat(Qt.TextFormat.RichText)
        self.lay.addWidget(l)
        return l


class ConfirmDangerDialog(QDialog):
    """Confirmación con botón rojo. Si se indica `confirm_text`, hay que escribirlo."""

    def __init__(self, title, heading, body, ok_label, confirm_text=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title); self.setModal(True); self.setFixedWidth(520)
        self._confirm = confirm_text
        l = QVBoxLayout(self); l.setContentsMargins(24, 24, 24, 24); l.setSpacing(14)
        h = QLabel(f"<b>{heading}</b>"); h.setWordWrap(True)
        h.setStyleSheet("font-size: 12pt;"); l.addWidget(h)
        w = QLabel(body); w.setObjectName("DangerWarn"); w.setWordWrap(True)
        w.setTextFormat(Qt.TextFormat.RichText); l.addWidget(w)
        self.edit = None
        if confirm_text:
            hl = QLabel(f"Escribe «<b>{confirm_text}</b>» para confirmar:")
            hl.setStyleSheet(f"color: {TEXT_MUTED};"); l.addWidget(hl)
            self.edit = QLineEdit(); self.edit.setPlaceholderText(confirm_text)
            self.edit.textChanged.connect(self._check)
            self.edit.returnPressed.connect(lambda: self._ok.isEnabled() and self.accept())
            l.addWidget(self.edit)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setObjectName("DangerBtn"); self._ok.setText(ok_label)
        self._ok.setEnabled(confirm_text is None)
        cancel = bb.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("Cancelar"); cancel.setDefault(True); cancel.setAutoDefault(True)
        self._ok.setAutoDefault(False)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        l.addWidget(bb)

    def _check(self, text):
        self._ok.setEnabled(text.strip().lower() == self._confirm.lower())


def confirm_danger(parent, title, heading, body, ok_label, token=None) -> bool:
    return ConfirmDangerDialog(title, heading, body, ok_label, token, parent).exec() == QDialog.DialogCode.Accepted


# ======================================================================
# DIÁLOGOS DE PARTICIÓN
# ======================================================================
class FsDialog(BaseDialog):
    """Elegir sistema de archivos + etiqueta (formatear, convertir, crear partición)."""

    def __init__(self, title, heading, sub, default_label="NuevoVolumen", removable=False,
                 exclude_fs=None, size_range=None, show_quick=None, note=None, parent=None):
        super().__init__(title, heading, sub, 460, parent)
        if note:
            self.hint(note, "DangerWarn")
        grid = QGridLayout(); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(10)
        row = 0
        self.size_spin = None
        if size_range:
            mn, mx = size_range
            grid.addWidget(QLabel("Tamaño (MB)"), row, 0)
            self.size_spin = QSpinBox(); self.size_spin.setRange(max(1, mn // MiB), max(1, mx // MiB))
            self.size_spin.setValue(mx // MiB); self.size_spin.setSingleStep(1024)
            self.size_spin.setMinimumHeight(36)
            grid.addWidget(self.size_spin, row, 1); row += 1
        grid.addWidget(QLabel("Sistema de archivos"), row, 0)
        self.cb_fs = QComboBox()
        fss = [f for f in available_filesystems() if not exclude_fs or f.lower() != exclude_fs.lower()]
        self.cb_fs.addItems(fss)
        default = "NTFS" if IS_WINDOWS else "exFAT" if removable else "ext4"
        if default in fss:
            self.cb_fs.setCurrentText(default)
        grid.addWidget(self.cb_fs, row, 1); row += 1
        grid.addWidget(QLabel("Etiqueta"), row, 0)
        self.le_label = QLineEdit(default_label)
        grid.addWidget(self.le_label, row, 1)
        grid.setColumnStretch(1, 1)
        self.lay.addLayout(grid)
        self.tip = self.hint("")
        self.chk_quick = QCheckBox("Formato rápido")
        self.chk_quick.setChecked(True)
        self.chk_quick.setVisible(IS_WINDOWS if show_quick is None else show_quick)
        self.lay.addWidget(self.chk_quick)
        if not fss:
            self.tip.setText("No hay herramientas mkfs instaladas. Instala e2fsprogs, ntfs-3g, "
                             "exfatprogs o dosfstools.")
        self.add_buttons()
        self.ok_btn.setEnabled(bool(fss))
        self.cb_fs.currentTextChanged.connect(self._on_fs)
        self._on_fs(self.cb_fs.currentText())

    def _on_fs(self, fs):
        if not fs:
            return
        mx = FILESYSTEMS.get(fs, 11)
        self.le_label.setMaxLength(mx)
        self.tip.setText(f"{FS_TIPS.get(fs, '')} Etiqueta: máximo {mx} caracteres.")

    def get_data(self):
        fs = self.cb_fs.currentText()
        return {"fs": fs, "label": clean_label(self.le_label.text().strip(), fs),
                "quick": self.chk_quick.isChecked(),
                "size": self.size_spin.value() * MiB if self.size_spin else None}


class LabelDialog(BaseDialog):
    def __init__(self, part: Partition, parent=None):
        super().__init__("Cambiar etiqueta", f"Etiqueta de {part.display_name}",
                         "Cambia el nombre del volumen. No se borra ningún dato.", 420, parent)
        mx = {"ntfs": 32, "exfat": 11, "fat32": 11, "xfs": 12}.get(part.fs_norm, 16)
        self.edit = QLineEdit(part.label); self.edit.setMaxLength(mx)
        self.edit.setPlaceholderText("Sin etiqueta")
        self.lay.addWidget(self.edit)
        self.hint(f"Máximo {mx} caracteres." + (" En FAT32 se guarda en mayúsculas." if part.fs_norm == "fat32" else "")
                  + (" La unidad se desmontará un momento y se volverá a montar." if IS_LINUX and part.mountpoints
                     and part.fs_norm not in ("ext2", "ext3", "ext4") else ""))
        self.add_buttons("Cambiar")

    def value(self):
        return self.edit.text().strip()


class LetterDialog(BaseDialog):
    def __init__(self, part: Partition, letters, parent=None):
        super().__init__("Cambiar letra", f"Letra de {part.display_name}",
                         "Los datos no se tocan. Los programas que usaran la letra anterior "
                         "(accesos directos, rutas guardadas) dejarán de encontrarla.", 420, parent)
        self.cb = QComboBox(); self.cb.setMinimumHeight(36)
        for L in letters:
            self.cb.addItem(f"{L}:", L)
        if part.letter:
            self.cb.addItem("— Quitar la letra (ocultar la unidad) —", "")
        self.lay.addWidget(self.cb)
        self.add_buttons("Cambiar")

    def value(self):
        return self.cb.currentData()


class ResizeDialog(BaseDialog):
    def __init__(self, disk: Disk, part: Partition, parent=None):
        super().__init__("Redimensionar partición", f"Redimensionar {part.display_name}",
                         f"{part.fs.upper()} · {format_bytes(part.size)} actuales", 480, parent)
        self.cur = part.size // MiB
        mx = (part.size + disk.gap_after(part)) // MiB
        used = (part.size - part.free) if part.free is not None else None
        mn = max(16, ((used + max(used // 10, 256 * MiB)) // MiB) if used is not None else 16)
        mn = min(mn, self.cur)
        self.slider = QSlider(Qt.Orientation.Horizontal); self.slider.setRange(mn, mx); self.slider.setValue(self.cur)
        self.spin = QSpinBox(); self.spin.setRange(mn, mx); self.spin.setValue(self.cur); self.spin.setSuffix(" MB")
        self.spin.setMinimumHeight(36)
        self.slider.valueChanged.connect(self.spin.setValue); self.spin.valueChanged.connect(self.slider.setValue)
        row = QHBoxLayout(); row.addWidget(self.slider, 1); row.addWidget(self.spin)
        self.lay.addLayout(row)
        self.info = self.hint("")
        self.spin.valueChanged.connect(self._upd)
        lim = (f"Mínimo aproximado: {format_bytes(mn * MiB)} (espacio usado + margen)." if used is not None else
               "El mínimo real lo comprobará la herramienta antes de cambiar nada.")
        self.hint(f"{lim}<br>Máximo: {format_bytes(mx * MiB)} "
                  + ("(espacio libre contiguo detrás de la partición)." if mx > self.cur else
                     "(no hay espacio libre detrás para ampliar)."))
        self.hint("Antes de redimensionar se comprueba el sistema de archivos. Aun así, es la operación "
                  "más delicada: <b>haz copia de seguridad</b> de lo importante." +
                  (" La partición se desmontará." if part.mountpoints else ""), "DangerWarn")
        self.add_buttons("Continuar")
        self._upd(self.cur)

    def _upd(self, v):
        diff = v - self.cur
        self.info.setText("Sin cambios." if abs(diff) < 1 else
                          f"{'Ampliar' if diff > 0 else 'Reducir'} {format_bytes(abs(diff) * MiB)} → "
                          f"{format_bytes(v * MiB)}")
        if hasattr(self, "ok_btn"):
            self.ok_btn.setEnabled(abs(diff) >= 1)

    def value(self):
        return self.spin.value() * MiB


class ConvertChoiceDialog(BaseDialog):
    def __init__(self, part: Partition, parent=None):
        super().__init__("Convertir formato", f"Convertir {part.display_name} ({part.fs.upper()})",
                         "", 480, parent)
        self.choice = None
        b1 = QPushButton("🔄  Convertir a NTFS conservando los datos"); b1.setObjectName("PrimaryBtn")
        b2 = QPushButton("🧹  Reformatear a otro sistema (borra todo)"); b2.setObjectName("DangerBtn")
        for b in (b1, b2):
            b.setMinimumHeight(42); b.setCursor(Qt.CursorShape.PointingHandCursor); self.lay.addWidget(b)
        self.hint("La conversión sin pérdida usa la herramienta convert de Windows. Es fiable, pero "
                  "no se puede deshacer y conviene tener copia de seguridad.")
        b1.clicked.connect(lambda: self._pick("convert")); b2.clicked.connect(lambda: self._pick("format"))
        c = QPushButton("Cancelar"); c.clicked.connect(self.reject); self.lay.addWidget(c)

    def _pick(self, v):
        self.choice = v
        self.accept()


# ======================================================================
# DIÁLOGOS DE DISCO
# ======================================================================
class InitDialog(BaseDialog):
    def __init__(self, disk: Disk, parent=None):
        super().__init__("Inicializar disco", f"Inicializar {disk.title} · {disk.model or 'Desconocido'}",
                         "Crea una tabla de particiones nueva y vacía. Las particiones que haya ahora "
                         "desaparecerán.", 480, parent)
        g = QGridLayout(); g.setHorizontalSpacing(14); g.setVerticalSpacing(10)
        g.addWidget(QLabel("Tipo de tabla"), 0, 0)
        self.cb_style = QComboBox()
        self.cb_style.addItem("GPT (recomendado, discos modernos y UEFI)", "gpt")
        self.cb_style.addItem("MBR (equipos antiguos con BIOS, máx. 2 TB)", "mbr")
        if disk.size > 2 * 1024 ** 4:
            self.cb_style.model().item(1).setEnabled(False)
        g.addWidget(self.cb_style, 0, 1)
        self.chk_create = QCheckBox("Crear una partición que ocupe todo el disco")
        self.chk_create.setChecked(True)
        g.addWidget(self.chk_create, 1, 0, 1, 2)
        g.addWidget(QLabel("Sistema de archivos"), 2, 0)
        self.cb_fs = QComboBox(); self.cb_fs.addItems(available_filesystems())
        d = "NTFS" if IS_WINDOWS else "exFAT" if disk.is_usb else "ext4"
        if d in available_filesystems():
            self.cb_fs.setCurrentText(d)
        g.addWidget(self.cb_fs, 2, 1)
        g.addWidget(QLabel("Etiqueta"), 3, 0)
        self.le = QLineEdit("NuevoVolumen"); g.addWidget(self.le, 3, 1)
        g.setColumnStretch(1, 1)
        self.lay.addLayout(g)
        self.chk_create.toggled.connect(lambda v: (self.cb_fs.setEnabled(v), self.le.setEnabled(v)))
        self.add_buttons()

    def get_data(self):
        create = None
        if self.chk_create.isChecked() and self.cb_fs.currentText():
            fs = self.cb_fs.currentText()
            create = {"fs": FS_ARG[fs], "label": clean_label(self.le.text().strip(), fs)}
        return {"style": self.cb_style.currentData(), "create": create}


class EraseDialog(BaseDialog):
    def __init__(self, disk: Disk, parent=None):
        super().__init__("Borrado seguro", f"Borrado seguro de {disk.title} · {disk.model or 'Desconocido'}",
                         f"Se sobrescribirá el disco entero ({format_bytes(disk.size)}) para que los datos "
                         f"no se puedan recuperar.", 500, parent)
        self.cb = QComboBox(); self.cb.setMinimumHeight(36)
        self.cb.addItem("Ceros · 1 pasada (recomendado)", "zero")
        self.cb.addItem("Datos aleatorios · 1 pasada", "random")
        if IS_LINUX and disk.discard:
            self.cb.addItem("TRIM completo (SSD, muy rápido)", "discard")
        self.lay.addWidget(self.cb)
        self.chk_verify = QCheckBox("Verificar al terminar (relee el disco entero)")
        self.chk_verify.setChecked(True); self.lay.addWidget(self.chk_verify)
        self.tip = self.hint("")
        self.cb.currentIndexChanged.connect(self._upd); self._upd()
        self.add_buttons("Continuar", "DangerBtn")

    def _upd(self):
        m = self.cb.currentData()
        self.chk_verify.setEnabled(m != "discard")
        self.tip.setText({
            "zero": "Una pasada de ceros es suficiente para los discos actuales.",
            "random": "Algo más lento. Útil si quieres que el disco no parezca vacío.",
            "discard": "Pide al SSD que descarte todos sus bloques. Es casi instantáneo; en la "
                       "mayoría de SSD los datos dejan de ser legibles.",
        }[m] + (" En SSD, una pasada de escritura no garantiza borrar las celdas de reserva." if m != "discard"
                and disk.rotational is False else ""))

    def get_data(self):
        return {"method": self.cb.currentData(),
                "verify": self.chk_verify.isChecked() and self.cb.currentData() != "discard"}


class ImageCreateDialog(BaseDialog):
    def __init__(self, disk: Disk, parent=None):
        super().__init__("Crear imagen", f"Imagen de {disk.title} · {disk.model or 'Desconocido'}",
                         f"Se guardará una copia exacta del disco entero ({format_bytes(disk.size)}) en un "
                         f"archivo .img. No se modifica el disco.", 520, parent)
        row = QHBoxLayout()
        self.path = QLineEdit(os.path.join(os.path.expanduser("~"),
                                           f"{(disk.model or 'disco').replace(' ', '_')}_{disk.short_device}.img"))
        b = QPushButton("Examinar…"); b.clicked.connect(self._browse)
        row.addWidget(self.path, 1); row.addWidget(b); self.lay.addLayout(row)
        self.chk_verify = QCheckBox("Verificar al terminar (relee disco e imagen y compara SHA-256)")
        self.chk_verify.setChecked(True); self.lay.addWidget(self.chk_verify)
        self.hint("Hace falta tanto espacio libre como el tamaño del disco. La imagen no se puede guardar "
                  "en el mismo disco que se copia. Se guarda también un archivo .sha256 para comprobar "
                  "la imagen antes de restaurarla.")
        self.add_buttons("Crear imagen")

    def _browse(self):
        p, _ = QFileDialog.getSaveFileName(self, "Guardar imagen", self.path.text(), "Imagen de disco (*.img)")
        if p:
            self.path.setText(p)


class WriteImageDialog(BaseDialog):
    """Restaurar una imagen .img o grabar una ISO en un disco."""

    def __init__(self, kind, disks: List[Disk], preselect: Optional[Disk] = None, parent=None):
        iso = kind == "iso"
        super().__init__("Grabar ISO en USB" if iso else "Restaurar imagen",
                         "Crear un USB arrancable" if iso else "Restaurar una imagen en un disco",
                         "Se sobrescribirá el disco elegido por completo.", 540, parent)
        self.kind, self.disks = kind, disks
        row = QHBoxLayout()
        self.path = QLineEdit(); self.path.setPlaceholderText("Archivo .iso" if iso else "Archivo .img")
        b = QPushButton("Examinar…"); b.clicked.connect(self._browse)
        row.addWidget(self.path, 1); row.addWidget(b); self.lay.addLayout(row)
        self.lay.addWidget(QLabel("Disco destino"))
        self.cb = QComboBox(); self.cb.setMinimumHeight(36); self.lay.addWidget(self.cb)
        self.chk_all = QCheckBox("Mostrar también discos internos (no extraíbles)")
        self.chk_all.setVisible(iso); self.chk_all.toggled.connect(self._fill)
        self.lay.addWidget(self.chk_all)
        self.chk_verify = QCheckBox("Verificar al terminar (relee y compara SHA-256)")
        self.chk_verify.setChecked(True); self.lay.addWidget(self.chk_verify)
        self.chk_pre = QCheckBox("Comprobar la imagen con su archivo .sha256 antes de escribir")
        self.chk_pre.setChecked(True); self.chk_pre.setVisible(not iso); self.lay.addWidget(self.chk_pre)
        self.info = self.hint("")
        self.add_buttons("Continuar", "DangerBtn")
        self.path.textChanged.connect(self._fill)
        self._pre = preselect
        if preselect and iso and not preselect.is_usb:
            self.chk_all.setChecked(True)
        self._fill()

    def _browse(self):
        flt = "Imagen ISO (*.iso *.img)" if self.kind == "iso" else "Imagen de disco (*.img *.bin *.raw);;Todos (*)"
        p, _ = QFileDialog.getOpenFileName(self, "Elegir archivo", os.path.expanduser("~"), flt)
        if p:
            self.path.setText(p)

    def _fill(self):
        prev = self.cb.currentData() or (self._pre.key if self._pre else None)
        self.cb.blockSignals(True); self.cb.clear()
        path = self.path.text().strip()
        size = os.path.getsize(path) if path and os.path.isfile(path) else 0
        for d in self.disks:
            if self.kind == "iso" and not self.chk_all.isChecked() and not d.is_usb:
                continue
            reason = D.check_image_target(d, size, path, self.disks) if size else D.check_disk_writable(d)
            self.cb.addItem(("🔒 " if reason else "") + d.describe(), d.key)
            if reason:
                it = self.cb.model().item(self.cb.count() - 1); it.setEnabled(False); it.setToolTip(reason)
        i = self.cb.findData(prev)
        if i >= 0 and self.cb.model().item(i).isEnabled():
            self.cb.setCurrentIndex(i)
        else:
            for j in range(self.cb.count()):
                if self.cb.model().item(j).isEnabled():
                    self.cb.setCurrentIndex(j); break
        self.cb.blockSignals(False)
        notes = []
        if size:
            notes.append(f"Tamaño del archivo: {format_bytes(size)}.")
            if self.kind == "iso":
                info = R.iso_info(path)
                if info["windows"] or not info["hybrid"]:
                    notes.append(f"<span style='color:{WARNING}'>⚠ Esta ISO "
                                 + ("parece un instalador de Windows" if info["windows"] else "no es «híbrida»")
                                 + ": grabada así probablemente <b>no arrancará</b> desde USB. Para instaladores de "
                                 "Windows usa herramientas específicas (Rufus, WoeUSB, Ventoy).</span>")
                else:
                    notes.append(f"ISO arrancable detectada{' · ' + info['label'] if info['label'] else ''}.")
            elif R.read_sidecar_sha(path) is None:
                notes.append("No hay archivo .sha256 junto a la imagen: no se podrá comprobar antes.")
        if self.cb.count() == 0 or not any(self.cb.model().item(j).isEnabled() for j in range(self.cb.count())):
            notes.append(f"<span style='color:{DANGER}'>No hay ningún disco destino válido.</span>")
        self.info.setText("<br>".join(notes))
        ok = bool(size) and self.cb.currentData() is not None and self.cb.model().item(self.cb.currentIndex()).isEnabled() \
            if self.cb.count() else False
        if hasattr(self, "ok_btn"):
            self.ok_btn.setEnabled(ok)

    def get_data(self):
        return {"path": self.path.text().strip(), "disk": D.find_disk(self.disks, self.cb.currentData()),
                "verify": self.chk_verify.isChecked(), "precheck": self.chk_pre.isChecked()}


class DiskPickDialog(BaseDialog):
    def __init__(self, title, sub, disks: List[Disk], check: Callable, parent=None):
        super().__init__(title, title, sub, 500, parent)
        self.disks = disks
        self.cb = QComboBox(); self.cb.setMinimumHeight(36); self.lay.addWidget(self.cb)
        for d in disks:
            reason = check(d)
            self.cb.addItem(("🔒 " if reason else "") + d.describe(), d.key)
            if reason:
                it = self.cb.model().item(self.cb.count() - 1); it.setEnabled(False); it.setToolTip(reason)
        for j in range(self.cb.count()):
            if self.cb.model().item(j).isEnabled():
                self.cb.setCurrentIndex(j); break
        self.add_buttons()
        self.ok_btn.setEnabled(any(self.cb.model().item(j).isEnabled() for j in range(self.cb.count())))

    def disk(self):
        return D.find_disk(self.disks, self.cb.currentData())


# ======================================================================
# DIÁLOGOS DE RESULTADO
# ======================================================================
class ResultDialog(QDialog):
    def __init__(self, title, parent=None, width=640):
        super().__init__(parent)
        self.setWindowTitle(title); self.setMinimumWidth(width)
        self.lay = QVBoxLayout(self); self.lay.setContentsMargins(24, 24, 24, 24); self.lay.setSpacing(14)

    def heading(self, text, color=None):
        h = QLabel(f"<b>{text}</b>"); h.setWordWrap(True)
        h.setStyleSheet(f"font-size: 14pt; color: {color or TEXT_MAIN};")
        self.lay.addWidget(h)

    def stats(self, items):
        g = QGridLayout(); g.setHorizontalSpacing(12); g.setVerticalSpacing(12)
        for i, (name, value) in enumerate(items):
            card = QFrame(); card.setObjectName("Card")
            cl = QVBoxLayout(card); cl.setContentsMargins(16, 12, 16, 12); cl.setSpacing(2)
            v = QLabel(str(value)); v.setStyleSheet("font-size: 15pt; font-weight: 800;")
            n = QLabel(name); n.setObjectName("SettingHint")
            cl.addWidget(v); cl.addWidget(n)
            g.addWidget(card, i // 4, i % 4)
        self.lay.addLayout(g)

    def text(self, t, obj="SettingHint"):
        l = QLabel(t); l.setObjectName(obj); l.setWordWrap(True); l.setTextFormat(Qt.TextFormat.RichText)
        l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lay.addWidget(l)

    def finish(self):
        b = QPushButton("Cerrar"); b.setObjectName("PrimaryBtn"); b.clicked.connect(self.accept)
        row = QHBoxLayout(); row.addStretch(); row.addWidget(b); self.lay.addLayout(row)


def show_smart(parent, disk: Disk, s: dict):
    d = ResultDialog(f"Salud · {disk.title}", parent, 760)
    verdict = diag.smart_verdict(s)
    txt, col = {"good": ("✔  Buen estado", SUCCESS), "warn": ("⚠  Requiere atención", WARNING),
                "bad": ("✖  El disco está fallando: haz copia de seguridad YA", DANGER),
                "unknown": ("Estado desconocido", TEXT_MUTED)}[verdict]
    d.heading(txt, col)
    d.text(f"{s.get('model') or disk.model} · nº de serie {s.get('serial') or '—'} · firmware "
           f"{s.get('firmware') or '—'} · fuente: {s.get('source')}")
    items = [("Temperatura", f"{s['temperature']} °C" if s.get("temperature") is not None else "—"),
             ("Horas encendido", f"{s['power_on_hours']:,}".replace(",", ".") if s.get("power_on_hours") is not None else "—"),
             ("Encendidos", s.get("power_cycles") if s.get("power_cycles") is not None else "—"),
             ("Desgaste SSD", f"{s['wear_pct']} %" if s.get("wear_pct") is not None else "—")]
    d.stats(items)
    if s.get("warnings"):
        d.text("<br>".join(f"⚠ {w}" for w in s["warnings"]), "DangerWarn")
    for n in s.get("notes") or []:
        d.text(n)
    attrs = s.get("attributes") or []
    if attrs:
        t = QTableWidget(len(attrs), 6)
        t.setHorizontalHeaderLabels(["ID", "Atributo", "Valor", "Peor", "Umbral", "Dato bruto"])
        t.verticalHeader().setVisible(False); t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setShowGrid(False); t.setMinimumHeight(260)
        for r, a in enumerate(attrs):
            for c, k in enumerate(("id", "name", "value", "worst", "thresh", "raw")):
                it = QTableWidgetItem("" if a.get(k) is None else str(a.get(k)))
                if a.get("failing") or (a.get("id") in diag.ATA_CRITICAL and str(a.get("raw", "0")).split()[0] not in ("0", "")):
                    it.setForeground(QColor(DANGER))
                t.setItem(r, c, it)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        d.lay.addWidget(t, 1)
    d.finish(); d.exec()


def show_bench(parent, disk: Disk, b: dict):
    d = ResultDialog(f"Velocidad · {disk.title}", parent)
    d.heading(f"{disk.model or disk.title}")
    mbs = lambda v: f"{v / 1e6:.0f} MB/s"
    d.stats([("Secuencial (media)", mbs(b["seq_avg"])), ("Inicio del disco", mbs(b["seq_start"])),
             ("Mitad", mbs(b["seq_mid"])), ("Final", mbs(b["seq_end"])),
             ("Aleatorio 4K", f"{b['rand_iops']:.0f} IOPS"), ("Aleatorio 4K", mbs(b["rand_mbs"])),
             ("Latencia media", f"{b['lat_avg_ms']:.2f} ms"), ("Latencia p99", f"{b['lat_p99_ms']:.2f} ms")])
    d.text("Solo lectura, sin caché del sistema" if b.get("direct") else
           "Solo lectura (este sistema no permite saltarse la caché: los valores pueden ser optimistas)")
    d.finish(); d.exec()


def show_scan(parent, disk: Disk, s: dict):
    d = ResultDialog(f"Escaneo · {disk.title}", parent)
    if s["bad_count"]:
        d.heading(f"✖  {s['bad_count']} sectores ilegibles", DANGER)
        d.text("El disco tiene zonas que no se pueden leer. Copia tus datos cuanto antes y plantéate "
               "sustituirlo. Consulta también la Salud SMART.", "DangerWarn")
        txt = QPlainTextEdit(); txt.setReadOnly(True); txt.setMinimumHeight(160)
        txt.setPlainText("Sectores (LBA, de {} bytes):\n".format(s["sector"]) +
                         "\n".join(str(x) for x in s["bad_sectors"]) +
                         ("\n… (lista recortada)" if s.get("truncated") else ""))
        d.lay.addWidget(txt)
    else:
        d.heading("✔  Sin sectores defectuosos", SUCCESS)
        d.text(f"Se ha leído el disco entero ({format_bytes(disk.size)}) en {_human_time(s['seconds'])}.")
    d.finish(); d.exec()


def show_output(parent, title, msg, output, ok=True):
    d = ResultDialog(title, parent)
    d.heading(msg, SUCCESS if ok else WARNING)
    if output:
        txt = QPlainTextEdit(); txt.setReadOnly(True); txt.setMinimumHeight(220); txt.setPlainText(output)
        d.lay.addWidget(txt)
    d.finish(); d.exec()


# ======================================================================
# CIERRE
# ======================================================================
class CloseConfirmDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Cerrar {APP_NAME}"); self.setModal(True); self.setFixedWidth(440)
        self.action = None
        l = QVBoxLayout(self); l.setContentsMargins(24, 24, 24, 24); l.setSpacing(16)
        t = QLabel(f"<b>¿Cómo quieres cerrar {APP_NAME}?</b>"); t.setStyleSheet("font-size: 12pt;")
        t.setWordWrap(True); l.addWidget(t)
        s = QLabel("En la bandeja del sistema sigue detectando cambios de discos.")
        s.setObjectName("SubTitle"); s.setWordWrap(True); l.addWidget(s)
        row = QHBoxLayout(); row.setSpacing(10)
        b_tray = QPushButton("Minimizar a la bandeja"); b_tray.setObjectName("PrimaryBtn")
        b_quit = QPushButton("Cerrar aplicación"); b_quit.setObjectName("DangerBtn")
        for b in (b_tray, b_quit):
            b.setCursor(Qt.CursorShape.PointingHandCursor); b.setMinimumHeight(40); row.addWidget(b)
        l.addLayout(row)
        self.chk_rem = QCheckBox("Recordar mi decisión"); l.addWidget(self.chk_rem)
        b_tray.clicked.connect(lambda: self._finish("tray"))
        b_quit.clicked.connect(lambda: self._finish("quit"))

    def _finish(self, act):
        self.action = act
        self.accept()


class BusyCloseDialog(QDialog):
    """Se muestra al intentar cerrar (Alt+F4) mientras hay una operación."""

    def __init__(self, worker: OpWorker, tray_available: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Operación en curso"); self.setModal(True); self.setFixedWidth(520)
        self.action = "stay"
        l = QVBoxLayout(self); l.setContentsMargins(24, 24, 24, 24); l.setSpacing(14)
        t = QLabel(f"<b>Hay una operación en curso</b>"); t.setStyleSheet("font-size: 13pt;"); l.addWidget(t)
        s = QLabel(f"{worker.info['title']}: {worker.description}"); s.setObjectName("SubTitle")
        s.setWordWrap(True); l.addWidget(s)
        if worker.cancelable:
            body = ("Si sales ahora, la operación se cancelará de forma controlada"
                    + (" y el disco destino quedará incompleto (habrá que volver a escribirlo o formatearlo)."
                       if worker.writes else "."))
        else:
            body = ("Esta operación <b>no se puede interrumpir</b> sin riesgo de dañar el disco y perder "
                    "datos. Espera a que termine.")
        w = QLabel(body); w.setObjectName("DangerWarn"); w.setWordWrap(True); l.addWidget(w)
        b_stay = QPushButton("Seguir con la operación"); b_stay.setObjectName("PrimaryBtn")
        b_stay.setDefault(True); b_stay.setMinimumHeight(40)
        b_stay.clicked.connect(self.reject); l.addWidget(b_stay)
        if tray_available:
            b_tray = QPushButton("Minimizar a la bandeja y seguir"); b_tray.setMinimumHeight(40)
            b_tray.clicked.connect(lambda: self._finish("tray")); l.addWidget(b_tray)
        if worker.cancelable:
            b_quit = QPushButton("Cancelar la operación y salir"); b_quit.setObjectName("DangerBtn")
            b_quit.setMinimumHeight(40); b_quit.clicked.connect(lambda: self._finish("cancel_quit"))
            l.addWidget(b_quit)

    def _finish(self, act):
        self.action = act
        self.accept()


# ======================================================================
# BANNER DE OPERACIÓN EN CURSO
# ======================================================================
class OperationBanner(QFrame):
    cancel_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Card")
        self.setStyleSheet(f"QFrame#Card {{ border-color: {ACCENT}; }}")
        l = QVBoxLayout(self); l.setContentsMargins(20, 14, 20, 14); l.setSpacing(8)
        top = QHBoxLayout()
        self.lbl_title = QLabel(); self.lbl_title.setObjectName("SettingLabel")
        self.lbl_phase = QLabel(); self.lbl_phase.setObjectName("SettingHint")
        top.addWidget(self.lbl_title); top.addSpacing(10); top.addWidget(self.lbl_phase); top.addStretch()
        self.btn_log = QPushButton("Detalles"); self.btn_log.setCheckable(True)
        self.btn_cancel = QPushButton("Cancelar")
        for b in (self.btn_log, self.btn_cancel):
            b.setCursor(Qt.CursorShape.PointingHandCursor); top.addWidget(b)
        l.addLayout(top)
        self.bar = QProgressBar(); self.bar.setObjectName("Big"); self.bar.setTextVisible(False)
        self.bar.setRange(0, 0); l.addWidget(self.bar)
        self.lbl_stats = QLabel(); self.lbl_stats.setObjectName("SettingHint"); l.addWidget(self.lbl_stats)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(160)
        self.log.setVisible(False); l.addWidget(self.log)
        self.btn_log.toggled.connect(self.log.setVisible)
        self.btn_cancel.clicked.connect(self._cancel)
        self.setVisible(False)

    def start(self, worker: OpWorker):
        self.lbl_title.setText(f"⏳  {worker.info['title']} · {worker.description}")
        self.lbl_phase.setText("Esperando permisos…" if IS_LINUX else "Preparando…")
        self.lbl_phase.setStyleSheet("")
        self.bar.setRange(0, 0); self.lbl_stats.setText("")
        self.log.clear()
        self.btn_cancel.setVisible(worker.cancelable); self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText("Cancelar")
        self.btn_cancel.setToolTip("" if worker.cancelable else "")
        if not worker.cancelable:
            self.lbl_stats.setText("Esta operación no se puede interrumpir. No apagues el equipo ni "
                                   "desconectes el disco.")
        self.setVisible(True)

    def _cancel(self):
        if confirm_danger(self.window(), "Cancelar operación", "¿Cancelar la operación?",
                          "Si es una escritura, el disco destino quedará incompleto y habrá que volver "
                          "a escribirlo o formatearlo.", "Sí, cancelar"):
            self.btn_cancel.setEnabled(False); self.btn_cancel.setText("Cancelando…")
            self.cancel_requested.emit()

    def on_progress(self, phase, done, total, speed, eta):
        total = max(int(total), 1)
        self.lbl_phase.setText(PHASES.get(phase, phase))
        self.bar.setRange(0, 1000); self.bar.setValue(int(done * 1000 / total))
        if phase == "rand":
            self.lbl_stats.setText(f"{done / 1000:.0f} de {total / 1000:.0f} s")
        else:
            self.lbl_stats.setText(f"{int(done * 100 / total)} %  ·  {format_bytes(done)} de {format_bytes(total)}"
                                   f"  ·  {format_bytes(speed)}/s  ·  quedan {_human_time(eta)}")

    def append_log(self, msg):
        if msg:
            self.log.appendPlainText(msg)
            if not self.bar.maximum():
                self.lbl_phase.setText(msg[:120])

    def finish(self, ok, msg):
        self.setVisible(False)


# ======================================================================
# PÁGINA: DISCOS
# ======================================================================
class PartBlock(QFrame):
    menu_requested = pyqtSignal(object, object)   # disk, part

    def __init__(self, disk: Disk, part: Partition):
        super().__init__()
        self.disk, self.part = disk, part
        self.setObjectName("PartBlock")
        self.setProperty("protected", part.protected)
        self.setMinimumWidth(130); self.setFixedHeight(112)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        l = QVBoxLayout(self); l.setContentsMargins(12, 0, 12, 10); l.setSpacing(4)
        strip = QFrame(); strip.setFixedHeight(4)
        strip.setStyleSheet(f"background-color: {TEXT_MUTED if part.protected else ACCENT}; "
                            f"border: none; border-radius: 2px;")
        l.addWidget(strip); l.addSpacing(6)
        name = QLabel(("🔒 " if part.protected else "") + part.display_name)
        name.setObjectName("PartName"); l.addWidget(name)
        meta = [part.fs.upper() if part.fs else "Sin formato", format_bytes(part.size)]
        m = QLabel(" · ".join(meta)); m.setObjectName("PartMeta"); l.addWidget(m)
        sub = part.label or part.ptype
        if sub:
            s = QLabel(sub); s.setObjectName("PartMeta"); l.addWidget(s)
        l.addStretch(1)
        bar = _usage_bar(part)
        if bar:
            l.addWidget(bar)
        tip = [f"<b>{part.display_name}</b>"]
        if IS_LINUX:
            tip.append(part.device)
        if part.mountpoints:
            tip.append("Montado en: " + ", ".join(part.mountpoints))
        if part.free is not None:
            tip.append(f"Libre: {format_bytes(part.free)} de {format_bytes(part.size)}")
        if part.fstab:
            tip.append(f"En /etc/fstab: {', '.join(part.fstab)}")
        if part.protected:
            tip.append(f"<span style='color:{DANGER}'>Protegida: {part.protect_reason}</span>")
        self.setToolTip("<br>".join(tip))

    def mousePressEvent(self, e):
        self.menu_requested.emit(self.disk, self.part)


class GapBlock(QFrame):
    menu_requested = pyqtSignal(object, int, int)  # disk, inicio, tamaño

    def __init__(self, disk: Disk, start: int, size: int):
        super().__init__()
        self.disk, self.start, self.size = disk, start, size
        self.setMinimumWidth(110); self.setFixedHeight(112)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("GapBlock")
        self.setStyleSheet(f"QFrame#GapBlock {{ background: transparent; border: 2px dashed {BORDER}; border-radius: 8px; }}"
                           f"QFrame#GapBlock:hover {{ border-color: {ACCENT}; }}"
                           f"QFrame#GapBlock QLabel {{ border: none; background: transparent; }}")
        ul = QVBoxLayout(self); ul.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for t in ("Sin asignar", format_bytes(size)):
            w = QLabel(t); w.setObjectName("PartMeta"); w.setAlignment(Qt.AlignmentFlag.AlignCenter); ul.addWidget(w)
        self.setToolTip("Espacio libre. Pulsa para crear una partición.")

    def mousePressEvent(self, e):
        self.menu_requested.emit(self.disk, self.start, self.size)


class DiskCard(QFrame):
    part_menu_requested = pyqtSignal(object, object)
    gap_menu_requested = pyqtSignal(object, int, int)
    disk_menu_requested = pyqtSignal(object)

    def __init__(self, disk: Disk):
        super().__init__()
        self.disk = disk
        self.setObjectName("Card")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda _p: self.disk_menu_requested.emit(self.disk))
        l = QVBoxLayout(self); l.setContentsMargins(20, 18, 20, 18); l.setSpacing(12)

        head = QHBoxLayout(); head.setSpacing(10)
        icon = QLabel("🔌" if disk.is_usb else "💽"); icon.setStyleSheet("font-size: 20pt;")
        head.addWidget(icon)
        tcol = QVBoxLayout(); tcol.setSpacing(2)
        t = QLabel(f"{disk.title}  ·  {disk.model or 'Desconocido'}"); t.setObjectName("DiskTitle")
        tcol.addWidget(t)
        meta = [format_bytes(disk.size)]
        if disk.bus:
            meta.append(disk.bus)
        if disk.rotational is not None:
            meta.append("HDD" if disk.rotational else "SSD")
        meta.append(disk.style if disk.style and disk.style != "RAW" else "Sin inicializar")
        meta.append(disk.short_device)
        m = QLabel("  ·  ".join(meta)); m.setObjectName("DiskMeta")
        m.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tcol.addWidget(m)
        head.addLayout(tcol, 1)
        if disk.is_system:
            head.addWidget(_badge("🔒 SISTEMA", "BadgeLock", f"Protegido: {disk.system_reason}"))
        if not disk.online:
            head.addWidget(_badge("OFFLINE", "BadgeMuted", disk.status))
        if disk.read_only:
            head.addWidget(_badge("SOLO LECTURA", "BadgeMuted"))
        if disk.is_usb:
            head.addWidget(_badge("EXTRAÍBLE"))
        tools = QPushButton("🧰  Herramientas"); tools.setCursor(Qt.CursorShape.PointingHandCursor)
        tools.clicked.connect(lambda: self.disk_menu_requested.emit(self.disk))
        head.addWidget(tools)
        l.addLayout(head)

        row = QHBoxLayout(); row.setSpacing(8)
        total = max(disk.size, 1)
        segs = [(p.start, "p", p) for p in disk.partitions] + [(a, "g", (a, s)) for a, s in disk.free_regions()]
        for _start, kind, obj in sorted(segs, key=lambda x: x[0]):
            if kind == "p":
                blk = PartBlock(disk, obj); blk.menu_requested.connect(self.part_menu_requested)
                row.addWidget(blk, max(1, int(obj.size * 1000 / total)))
            else:
                g = GapBlock(disk, obj[0], obj[1]); g.menu_requested.connect(self.gap_menu_requested)
                row.addWidget(g, max(1, int(obj[1] * 1000 / total)))
        if not segs:
            e = QLabel("Disco sin inicializar · usa 🧰 Herramientas → Inicializar disco"
                       if disk.style == "RAW" else "Sin particiones")
            e.setObjectName("PartMeta"); row.addWidget(e)
        l.addLayout(row)


class DisksPage(QWidget):
    refresh_requested = pyqtSignal()
    part_menu_requested = pyqtSignal(object, object)
    gap_menu_requested = pyqtSignal(object, int, int)
    disk_menu_requested = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self); l.setContentsMargins(30, 30, 30, 30); l.setSpacing(20)
        h = QHBoxLayout()
        t = QLabel("Discos"); t.setObjectName("PageTitle"); h.addWidget(t)
        h.addSpacing(12)
        hint = QLabel("Pulsa una partición o un espacio libre para ver sus acciones"); hint.setObjectName("SubTitle")
        h.addWidget(hint); h.addStretch()
        self.btn_refresh = QPushButton("⟳  Actualizar")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.refresh_requested)
        h.addWidget(self.btn_refresh)
        l.addLayout(h)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.container = QWidget()
        self.list_layout = QVBoxLayout(self.container)
        self.list_layout.setContentsMargins(0, 0, 6, 0); self.list_layout.setSpacing(16)
        scroll.setWidget(self.container)
        l.addWidget(scroll, 1)
        self.show_message("Leyendo discos…")

    def _clear(self):
        while self.list_layout.count():
            it = self.list_layout.takeAt(0)
            w = it.widget()
            if w:
                w.hide(); w.deleteLater()

    def show_message(self, text):
        self._clear()
        e = QLabel(text); e.setObjectName("EmptyLbl"); e.setWordWrap(True)
        e.setAlignment(Qt.AlignmentFlag.AlignCenter); e.setMinimumHeight(120)
        self.list_layout.addWidget(e); self.list_layout.addStretch(1)

    def set_disks(self, disks: List[Disk]):
        self._clear()
        if not disks:
            self.show_message("No se ha encontrado ningún disco.")
            return
        for d in disks:
            card = DiskCard(d)
            card.part_menu_requested.connect(self.part_menu_requested)
            card.gap_menu_requested.connect(self.gap_menu_requested)
            card.disk_menu_requested.connect(self.disk_menu_requested)
            self.list_layout.addWidget(card)
        self.list_layout.addStretch(1)


# ======================================================================
# PÁGINA: VOLÚMENES
# ======================================================================
class VolumesPage(QWidget):
    refresh_requested = pyqtSignal()
    part_menu_requested = pyqtSignal(object, object)
    COLS = ["Volumen", "Etiqueta", "Disco", "Formato", "Tipo", "Capacidad", "Libre", "Uso"]

    def __init__(self):
        super().__init__()
        self._rows = []
        l = QVBoxLayout(self); l.setContentsMargins(30, 30, 30, 30); l.setSpacing(20)
        h = QHBoxLayout()
        t = QLabel("Volúmenes"); t.setObjectName("PageTitle"); h.addWidget(t)
        h.addSpacing(12)
        hint = QLabel("Doble clic para abrir · clic derecho para más acciones"); hint.setObjectName("SubTitle")
        h.addWidget(hint); h.addStretch()
        b = QPushButton("⟳  Actualizar"); b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(self.refresh_requested); h.addWidget(b)
        self.btn_refresh = b
        l.addLayout(h)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        hh = self.table.horizontalHeader()
        for c in range(len(self.COLS)):
            mode = QHeaderView.ResizeMode.Stretch if c in (0, 1, 4) else QHeaderView.ResizeMode.ResizeToContents
            hh.setSectionResizeMode(c, mode)
        hh.setSectionResizeMode(len(self.COLS) - 1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(len(self.COLS) - 1, 170)
        hh.setFixedHeight(40); hh.setHighlightSections(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setShowGrid(False); self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._ctx)
        self.table.cellDoubleClicked.connect(self._dbl)
        l.addWidget(self.table, 1)

    def set_disks(self, disks: List[Disk]):
        self.table.setRowCount(0)
        self._rows = []
        for d in disks:
            for p in d.partitions:
                if not (p.fs or p.letter or p.mountpoints):
                    continue
                r = self.table.rowCount(); self.table.insertRow(r)
                self._rows.append((d, p))
                vals = [("🔒 " if p.protected else "") + p.display_name, p.label, d.title,
                        (p.fs or "RAW").upper() if IS_LINUX else (p.fs or "RAW"), p.ptype,
                        format_bytes(p.size), format_bytes(p.free) if p.free is not None else "—"]
                for c, v in enumerate(vals):
                    it = QTableWidgetItem(v)
                    if p.protected:
                        it.setToolTip(f"Protegida: {p.protect_reason}")
                    self.table.setItem(r, c, it)
                bar = _usage_bar(p, height=8)
                cell = QWidget(); cell.setObjectName("Transparent")
                cl = QHBoxLayout(cell); cl.setContentsMargins(12, 0, 12, 0); cl.setSpacing(8)
                if bar:
                    pct = QLabel(f"{bar.value()} %"); pct.setObjectName("PartMeta"); pct.setFixedWidth(40)
                    cl.addWidget(bar, 1); cl.addWidget(pct)
                self.table.setCellWidget(r, len(self.COLS) - 1, cell)

    def _ctx(self, pos):
        r = self.table.rowAt(pos.y())
        if 0 <= r < len(self._rows):
            self.part_menu_requested.emit(*self._rows[r])

    def _dbl(self, r, _c):
        if 0 <= r < len(self._rows):
            path = self._rows[r][1].openable_path
            if path:
                open_path(path)


# ======================================================================
# PÁGINA: CLONAR
# ======================================================================
class ClonePage(QWidget):
    start_requested = pyqtSignal(object, object, bool)   # src, dst, verificar

    def __init__(self):
        super().__init__()
        self.disks: List[Disk] = []
        self.busy = False
        l = QVBoxLayout(self); l.setContentsMargins(40, 40, 40, 40); l.setSpacing(20)
        l.setAlignment(Qt.AlignmentFlag.AlignTop)
        t = QLabel("Clonar disco"); t.setObjectName("PageTitle"); l.addWidget(t)
        s = QLabel("Copia exacta sector a sector de un disco completo en otro. Todo el contenido "
                   "del disco destino se perderá. El disco del sistema nunca puede ser destino.")
        s.setObjectName("SubTitle"); s.setWordWrap(True); l.addWidget(s)
        card = QFrame(); card.setObjectName("Card")
        cl = QGridLayout(card); cl.setContentsMargins(24, 20, 24, 20)
        cl.setHorizontalSpacing(16); cl.setVerticalSpacing(12)
        lo = QLabel("Origen"); lo.setObjectName("SettingLabel")
        ld = QLabel("Destino"); ld.setObjectName("SettingLabel")
        self.cb_src = QComboBox(); self.cb_dst = QComboBox()
        for cb in (self.cb_src, self.cb_dst):
            cb.setMinimumHeight(40)
            cb.currentIndexChanged.connect(self._update_check)
        arrow = QLabel("➜"); arrow.setStyleSheet(f"color: {ACCENT}; font-size: 18pt;")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(lo, 0, 0); cl.addWidget(ld, 0, 2)
        cl.addWidget(self.cb_src, 1, 0); cl.addWidget(arrow, 1, 1); cl.addWidget(self.cb_dst, 1, 2)
        cl.setColumnStretch(0, 1); cl.setColumnStretch(2, 1)
        self.lbl_src_info = QLabel(); self.lbl_src_info.setObjectName("SettingHint"); self.lbl_src_info.setWordWrap(True)
        self.lbl_dst_info = QLabel(); self.lbl_dst_info.setObjectName("SettingHint"); self.lbl_dst_info.setWordWrap(True)
        cl.addWidget(self.lbl_src_info, 2, 0); cl.addWidget(self.lbl_dst_info, 2, 2)
        cl.addWidget(_sep(), 3, 0, 1, 3)
        self.chk_verify = QCheckBox("Verificar al terminar (relee ambos discos y compara SHA-256; tarda el doble)")
        self.chk_verify.setChecked(True)
        cl.addWidget(self.chk_verify, 4, 0, 1, 3)
        self.lbl_check = QLabel(); self.lbl_check.setWordWrap(True)
        cl.addWidget(self.lbl_check, 5, 0, 1, 2)
        self.btn_start = QPushButton("🧬  Clonar"); self.btn_start.setObjectName("DangerBtn")
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor); self.btn_start.setMinimumHeight(40)
        self.btn_start.clicked.connect(self._on_start)
        cl.addWidget(self.btn_start, 5, 2, alignment=Qt.AlignmentFlag.AlignRight)
        l.addWidget(card)
        l.addStretch()

    def set_disks(self, disks: List[Disk]):
        if self.busy:
            return
        self.disks = disks
        prev_src, prev_dst = self.cb_src.currentData(), self.cb_dst.currentData()
        for cb in (self.cb_src, self.cb_dst):
            cb.blockSignals(True); cb.clear()
        self.cb_dst.addItem("— Elige el disco destino —", None)
        for d in disks:
            text = d.describe()
            self.cb_src.addItem(("🔒 " if d.is_system else "") + text, d.key)
            reason = D.check_disk_writable(d)
            self.cb_dst.addItem(("🔒 " if reason else "") + text, d.key)
            if reason:
                item = self.cb_dst.model().item(self.cb_dst.count() - 1)
                item.setEnabled(False); item.setToolTip(reason)
        i = self.cb_src.findData(prev_src)
        if i >= 0:
            self.cb_src.setCurrentIndex(i)
        prev = self._disk(prev_dst)
        j = self.cb_dst.findData(prev_dst) if prev else -1
        if j >= 0 and self.cb_dst.model().item(j).isEnabled():
            self.cb_dst.setCurrentIndex(j)
        for cb in (self.cb_src, self.cb_dst):
            cb.blockSignals(False)
        self._update_check()

    def preselect_source(self, disk: Disk):
        i = self.cb_src.findData(disk.key)
        if i >= 0 and not self.busy:
            self.cb_src.setCurrentIndex(i)

    def _disk(self, key) -> Optional[Disk]:
        return D.find_disk(self.disks, key) if key is not None else None

    def _info(self, d: Optional[Disk]) -> str:
        if not d:
            return ""
        parts = ", ".join(p.display_name for p in d.partitions) or "sin particiones"
        txt = f"{d.short_device} · {d.bus or '—'} · {d.style or '—'}<br>Particiones: {parts}"
        if d.is_system:
            txt += f"<br><span style='color:{WARNING}'>Disco del sistema: solo lectura</span>"
        return txt

    def _update_check(self):
        src, dst = self._disk(self.cb_src.currentData()), self._disk(self.cb_dst.currentData())
        self.lbl_src_info.setText(self._info(src))
        self.lbl_dst_info.setText(self._info(dst))
        if dst is None:
            self._set_check(None, "Elige el disco destino.")
            return
        reason = D.check_clone_allowed(src, dst)
        if reason:
            self._set_check(False, reason)
        elif IS_LINUX and not has_pkexec():
            self._set_check(False, "Falta pkexec (polkit) para obtener permisos de administrador.")
        else:
            msg = f"Se sobrescribirá por completo {dst.title} ({format_bytes(dst.size)})."
            if src.is_system:
                msg += " Aviso: el origen es el sistema en uso; cierra programas para minimizar cambios."
            self._set_check(True, msg)

    def _set_check(self, ok, msg):
        color = TEXT_MUTED if ok is None else SUCCESS if ok else DANGER
        icon = "" if ok is None else "✔  " if ok else "✖  "
        self.lbl_check.setText(f"<span style='color:{color}'>{icon}{msg}</span>")
        self.btn_start.setEnabled(bool(ok) and not self.busy)

    def _on_start(self):
        src, dst = self._disk(self.cb_src.currentData()), self._disk(self.cb_dst.currentData())
        if D.check_clone_allowed(src, dst) is None:
            self.start_requested.emit(src, dst, self.chk_verify.isChecked())

    def set_busy(self, busy: bool):
        self.busy = busy
        for w in (self.cb_src, self.cb_dst, self.chk_verify):
            w.setEnabled(not busy)
        if busy:
            self.btn_start.setEnabled(False)
        else:
            self._update_check()


# ======================================================================
# PÁGINA: HERRAMIENTAS
# ======================================================================
class ToolsPage(QWidget):
    tool_requested = pyqtSignal(str)

    TOOLS = [
        ("Diagnóstico", [
            ("smart", "🩺", "Salud SMART", "Temperatura, horas de uso, sectores reasignados y desgaste del SSD."),
            ("bench", "⚡", "Test de velocidad", "Lectura secuencial y aleatoria. No escribe nada."),
            ("scan", "🔍", "Escaneo de superficie", "Lee el disco entero buscando sectores defectuosos."),
        ]),
        ("Copias e imágenes", [
            ("clone", "🧬", "Clonar disco", "Copia exacta de un disco en otro."),
            ("image_create", "💾", "Crear imagen", "Guarda un disco completo en un archivo .img."),
            ("image_restore", "♻️", "Restaurar imagen", "Escribe un archivo .img en un disco."),
            ("iso", "🚀", "USB arrancable", "Graba una ISO (Linux, utilidades…) en una memoria USB."),
        ]),
        ("Preparar y borrar", [
            ("init", "🧱", "Inicializar disco", "Tabla de particiones nueva GPT o MBR."),
            ("erase", "🔥", "Borrado seguro", "Sobrescribe el disco entero para que nada sea recuperable."),
        ]),
    ]

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget(); scroll.setWidget(body); outer.addWidget(scroll)
        l = QVBoxLayout(body); l.setContentsMargins(40, 40, 40, 40); l.setSpacing(18)
        l.setAlignment(Qt.AlignmentFlag.AlignTop)
        t = QLabel("Herramientas"); t.setObjectName("PageTitle"); l.addWidget(t)
        s = QLabel("Utilidades de disco completo. Las de partición (letra, etiqueta, formato, "
                   "redimensionar, comprobar…) están al pulsar cada partición en «Discos».")
        s.setObjectName("SubTitle"); s.setWordWrap(True); l.addWidget(s)
        for section, tools in self.TOOLS:
            h = QLabel(section.upper()); h.setObjectName("SettingHint")
            h.setStyleSheet("font-weight: 700; letter-spacing: 1px; margin-top: 8px;"); l.addWidget(h)
            g = QGridLayout(); g.setHorizontalSpacing(14); g.setVerticalSpacing(14)
            for i, (key, emoji, title, desc) in enumerate(tools):
                card = QFrame(); card.setObjectName("Card"); card.setMinimumHeight(130)
                cl = QVBoxLayout(card); cl.setContentsMargins(20, 16, 20, 16); cl.setSpacing(6)
                tl = QLabel(f"{emoji}  {title}"); tl.setObjectName("SettingLabel"); cl.addWidget(tl)
                dl = QLabel(desc); dl.setObjectName("SettingHint"); dl.setWordWrap(True); cl.addWidget(dl)
                cl.addStretch()
                b = QPushButton("Abrir…"); b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, k=key: self.tool_requested.emit(k))
                row = QHBoxLayout(); row.addStretch(); row.addWidget(b); cl.addLayout(row)
                g.addWidget(card, i // 3, i % 3)
            for c in range(3):
                g.setColumnStretch(c, 1)
            l.addLayout(g)


# ======================================================================
# PÁGINA: AJUSTES
# ======================================================================
class SettingsPage(QWidget):
    def __init__(self, settings: QSettings, tray_available: bool):
        super().__init__()
        self.settings = settings
        l = QVBoxLayout(self); l.setContentsMargins(40, 40, 40, 40); l.setSpacing(20)
        l.setAlignment(Qt.AlignmentFlag.AlignTop)
        t = QLabel("Ajustes"); t.setObjectName("PageTitle"); l.addWidget(t)
        c1 = self._card(l, "Al cerrar la ventana", "Qué hacer cuando pulsas la X de la ventana. "
                        "Mientras haya una operación en curso la ventana no se cierra.")
        self.chk_ask = QCheckBox("Preguntar siempre al cerrar")
        self.chk_ask.setChecked(settings.value("ask_on_close", True, type=bool))
        self.chk_tray = QCheckBox("Minimizar a la bandeja del sistema en lugar de cerrar")
        self.chk_tray.setChecked(settings.value("minimize_to_tray", True, type=bool) and tray_available)
        if not tray_available:
            self.chk_ask.setEnabled(False); self.chk_tray.setEnabled(False)
            note = QLabel("Tu escritorio no tiene bandeja del sistema: la ventana se cerrará directamente.")
            note.setObjectName("SettingHint"); note.setWordWrap(True); c1.addWidget(note)
        self.chk_ask.toggled.connect(lambda v: self.settings.setValue("ask_on_close", v))
        self.chk_tray.toggled.connect(lambda v: self.settings.setValue("minimize_to_tray", v))
        c1.addWidget(self.chk_ask); c1.addWidget(self.chk_tray)

        c_sec = self._card(l, "Protecciones activas", "")
        items = [
            "🔒 El disco del sistema nunca se formatea, se reparticiona ni se usa como destino.",
            "🔒 Las particiones de arranque, recuperación, EFI y swap no se tocan.",
            "✅ Antes de escribir se vuelve a leer el estado de los discos y se repiten las comprobaciones"
            + (" (también dentro del proceso de root)." if IS_LINUX else "."),
            "⌨️ Las operaciones que borran datos piden escribir el nombre del disco o la partición.",
            "🛑 Durante una operación no se puede cerrar la app, y se impide apagar o suspender el equipo.",
            "📝 Si la app se cierra a la fuerza, al volver a abrirla se avisa de la operación interrumpida.",
        ]
        if IS_LINUX:
            items += ["📄 No se borran ni cambian particiones que estén en /etc/fstab.",
                      "💾 Antes de cambiar una tabla de particiones se guarda una copia en "
                      "/var/lib/korvex-disk-manager/partition-tables."]
        for it in items:
            lab = QLabel(it); lab.setObjectName("SettingHint"); lab.setWordWrap(True); c_sec.addWidget(lab)

        c2 = self._card(l, "Registros", f"Cada sesión guarda un log en {log_dir()}"
                        + (" · las operaciones de root, en /var/log/korvex-disk-manager.log" if IS_LINUX else ""))
        b = QPushButton("📂  Abrir carpeta de logs"); b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda: open_path(log_dir()))
        row = QHBoxLayout(); row.addWidget(b); row.addStretch(); c2.addLayout(row)

        if IS_WINDOWS:
            perm = "Administrador" if is_admin() else "Usuario (sin permisos de escritura)"
        else:
            perm = "Usuario · pide contraseña (pkexec) para cada operación"
        c3 = self._card(l, "Acerca de", "")
        info = QLabel(f"{APP_NAME} {APP_VERSION}<br>Sistema: {'Windows' if IS_WINDOWS else 'Linux'}"
                      f"<br>Permisos: {perm}")
        info.setObjectName("SettingHint"); c3.addWidget(info)

    def _card(self, parent_layout, title, hint):
        card = QFrame(); card.setObjectName("Card")
        cl = QVBoxLayout(card); cl.setContentsMargins(24, 20, 24, 20); cl.setSpacing(8)
        lt = QLabel(title); lt.setObjectName("SettingLabel"); cl.addWidget(lt)
        if hint:
            lh = QLabel(hint); lh.setObjectName("SettingHint"); lh.setWordWrap(True); cl.addWidget(lh)
        parent_layout.addWidget(card)
        return cl


# ======================================================================
# VENTANA PRINCIPAL
# ======================================================================
class DiskManagerWindow(QMainWindow):
    PAGES = ("💽  Discos", "📁  Volúmenes", "🧬  Clonar", "🧰  Herramientas", "⚙️  Ajustes")

    def __init__(self, settings_path: str, icon: QIcon):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icon)
        self.app_icon = icon
        self.settings = QSettings(settings_path, QSettings.Format.IniFormat)
        self.disks: List[Disk] = []
        self.reader: Optional[DiskReaderWorker] = None
        self.op: Optional[OpWorker] = None
        self._op_done_cb = None
        self.cmd_worker: Optional[CommandWorker] = None
        self._quitting = False
        self._quit_after_op = False
        self._kbd_close = False
        self.power = PowerGuard()
        self.tray_available = QSystemTrayIcon.isSystemTrayAvailable()

        self.resize(1320, 820); self.setMinimumSize(1050, 660)
        self._build_ui(icon)
        self._setup_tray(icon)

        self.refresh_timer = QTimer(self); self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.load_disks)
        self.hw_timer = None
        if IS_LINUX:
            self._hw_sig = self._linux_signature()
            self.hw_timer = QTimer(self); self.hw_timer.setInterval(2000)
            self.hw_timer.timeout.connect(self._poll_linux_hw); self.hw_timer.start()
        self.load_disks()
        QTimer.singleShot(400, self._check_interrupted_op)

    # --------------------------------------------------------------
    def _build_ui(self, icon: QIcon):
        central = QWidget(); self.setCentralWidget(central)
        ml = QHBoxLayout(central); ml.setContentsMargins(0, 0, 0, 0); ml.setSpacing(0)

        side = QWidget(); side.setObjectName("SideBarWrap"); side.setFixedWidth(240)
        sl = QVBoxLayout(side); sl.setContentsMargins(0, 24, 0, 16); sl.setSpacing(12)
        brand = QHBoxLayout(); brand.setContentsMargins(24, 0, 16, 8); brand.setSpacing(12)
        logo = QLabel()
        pm = tinted_pixmap(icon, TEXT_MAIN, 38, self.devicePixelRatioF()) if not icon.isNull() else QPixmap()
        if not pm.isNull():
            logo.setPixmap(pm); brand.addWidget(logo)
        bt = QVBoxLayout(); bt.setSpacing(0)
        b1 = QLabel("KORVEX"); b1.setObjectName("Brand")
        b2 = QLabel("Disk Manager"); b2.setObjectName("BrandSub")
        bt.addWidget(b1); bt.addWidget(b2); brand.addLayout(bt); brand.addStretch()
        sl.addLayout(brand)
        self.sidebar = QListWidget(); self.sidebar.setObjectName("SideBar")
        for label in self.PAGES:
            self.sidebar.addItem(QListWidgetItem(label))
        sl.addWidget(self.sidebar, 1)
        self.lbl_status = QLabel("Preparado."); self.lbl_status.setObjectName("StatusLbl")
        self.lbl_status.setWordWrap(True); self.lbl_status.setContentsMargins(24, 0, 16, 0)
        sl.addWidget(self.lbl_status)
        ml.addWidget(side)
        vline = QFrame(); vline.setObjectName("Separator"); vline.setFixedWidth(1)
        ml.addWidget(vline)

        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(0)
        self.banner = OperationBanner()
        bwrap = QWidget(); bwl = QVBoxLayout(bwrap); bwl.setContentsMargins(30, 20, 30, 0)
        bwl.addWidget(self.banner); bwrap.setVisible(False); self.banner_wrap = bwrap
        rl.addWidget(bwrap)
        self.stack = QStackedWidget()
        self.disks_page = DisksPage()
        self.volumes_page = VolumesPage()
        self.clone_page = ClonePage()
        self.tools_page = ToolsPage()
        self.settings_page = SettingsPage(self.settings, self.tray_available)
        for p in (self.disks_page, self.volumes_page, self.clone_page, self.tools_page, self.settings_page):
            self.stack.addWidget(p)
        rl.addWidget(self.stack, 1)
        ml.addWidget(right, 1)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        self.disks_page.refresh_requested.connect(self.load_disks)
        self.volumes_page.refresh_requested.connect(self.load_disks)
        self.disks_page.part_menu_requested.connect(self.show_part_menu)
        self.volumes_page.part_menu_requested.connect(self.show_part_menu)
        self.disks_page.gap_menu_requested.connect(self.show_gap_menu)
        self.disks_page.disk_menu_requested.connect(self.show_disk_menu)
        self.clone_page.start_requested.connect(self.start_clone)
        self.tools_page.tool_requested.connect(self.run_tool)
        self.banner.cancel_requested.connect(self.cancel_op)

    def _setup_tray(self, icon):
        self.tray = None
        if not self.tray_available:
            return
        self.tray = QSystemTrayIcon(badge_icon(icon) if not icon.isNull() else icon, self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction("Mostrar", self._show_from_tray)
        menu.addSeparator()
        menu.addAction("Salir", self._quit_from_tray)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self._show_from_tray() if r in (QSystemTrayIcon.ActivationReason.DoubleClick,
                                                      QSystemTrayIcon.ActivationReason.Trigger) else None)
        self.tray.show()

    def _show_from_tray(self):
        self.showNormal(); self.raise_(); self.activateWindow()

    def _quit_from_tray(self):
        if self._op_running():
            self._show_from_tray()
            _msg(self, "warn", "Operación en curso",
                 "No se puede salir mientras hay una operación en curso. Espera a que termine.")
            return
        self._quitting = True
        QApplication.quit()

    def set_status(self, text):
        self.lbl_status.setText(text)

    def _op_running(self) -> bool:
        return self.op is not None and self.op.isRunning()

    # --------------------------------------------------------------
    # Lectura de discos
    # --------------------------------------------------------------
    def load_disks(self):
        if self.reader and self.reader.isRunning():
            return
        for b in (self.disks_page.btn_refresh, self.volumes_page.btn_refresh):
            b.setEnabled(False)
        if not self._op_running():
            self.set_status("Leyendo discos…")
        self.reader = DiskReaderWorker()
        self.reader.done.connect(self._on_disks)
        self.reader.error.connect(self._on_disks_error)
        self.reader.start()

    def _refresh_buttons(self):
        for b in (self.disks_page.btn_refresh, self.volumes_page.btn_refresh):
            b.setEnabled(True)

    def _on_disks(self, disks):
        self.disks = disks
        self._refresh_buttons()
        self.disks_page.set_disks(disks)
        self.volumes_page.set_disks(disks)
        self.clone_page.set_disks(disks)
        if not self._op_running():
            n = len(disks)
            self.set_status(f"{n} disco{'s' if n != 1 else ''} detectado{'s' if n != 1 else ''}.")

    def _on_disks_error(self, err):
        self._refresh_buttons()
        self.set_status("Error al leer los discos.")
        self.disks_page.show_message(f"No se pudieron leer los discos:\n{err}")

    def _linux_signature(self):
        sig = []
        for p in ("/proc/partitions", "/proc/self/mounts"):
            try:
                with open(p) as f:
                    sig.append(f.read())
            except OSError:
                pass
        return hash(tuple(sig))

    def _poll_linux_hw(self):
        sig = self._linux_signature()
        if sig != self._hw_sig:
            self._hw_sig = sig
            if not self._op_running():
                self.set_status("Cambio detectado. Actualizando…")
            self.refresh_timer.start(1200)

    # --------------------------------------------------------------
    # Eventos nativos de Windows
    # --------------------------------------------------------------
    def nativeEvent(self, eventType, message):
        if IS_WINDOWS:
            try:
                if bytes(eventType) == b"windows_generic_MSG":
                    from ctypes import wintypes
                    msg = wintypes.MSG.from_address(int(message))
                    m = msg.message
                    if m == 0x0219 and msg.wParam in (0x8000, 0x8004):      # WM_DEVICECHANGE
                        self.refresh_timer.start(1500)
                    elif m == 0x0112 and (msg.wParam & 0xFFF0) == 0xF060 and self._op_running():
                        # WM_SYSCOMMAND / SC_CLOSE: el ratón (botón X) envía coordenadas;
                        # Alt+F4 (acelerador del sistema) envía -1 en la palabra alta.
                        hi = (int(msg.lParam) >> 16) & 0xFFFF
                        if hi == 0xFFFF or int(msg.lParam) == 0:
                            self._kbd_close = True
                            return False, 0
                        QTimer.singleShot(0, self._warn_x_blocked)
                        return True, 0
                    elif m == 0x0011 and self._op_running() and self.op.writes:   # WM_QUERYENDSESSION
                        return True, 0      # FALSE: no permitir cerrar sesión / apagar
            except Exception:
                pass
        return False, 0

    def _warn_x_blocked(self):
        _msg(self, "warn", "Operación en curso",
             "La ventana no se puede cerrar mientras hay una operación en curso, para no dañar el "
             "disco ni perder datos.\n\nSi de verdad necesitas salir, pulsa Alt+F4.")

    # --------------------------------------------------------------
    # Operaciones
    # --------------------------------------------------------------
    def _can_start(self) -> bool:
        if self._op_running():
            _msg(self, "warn", "Operación en curso", "Espera a que termine la operación actual.")
            return False
        if IS_WINDOWS and not is_admin():
            _msg(self, "error", "Permisos", "La aplicación no tiene permisos de administrador. "
                                            "Vuelve a abrirla y acepta el aviso de Windows.")
            return False
        if IS_LINUX and not has_pkexec():
            _msg(self, "error", "Permisos", "Falta pkexec (polkit) para obtener permisos de administrador.")
            return False
        return True

    def run_op(self, op: str, params: dict, description: str, on_done=None):
        if not self._can_start():
            return
        self.op = OpWorker(op, params, description)
        self._op_done_cb = on_done
        self.op.progress.connect(self.banner.on_progress)
        self.op.log.connect(self.banner.append_log)
        self.op.finished.connect(self._on_op_finished)
        self.banner.start(self.op); self.banner_wrap.setVisible(True)
        self.clone_page.set_busy(True)
        self.set_status(f"{OPS[op]['title']}…")
        if self.op.writes:
            self.power.start(f"{OPS[op]['title']} en curso", int(self.winId()) if IS_WINDOWS else None)
        self.op.start()

    def cancel_op(self):
        if self._op_running():
            self.op.cancel()

    def _on_op_finished(self, ok, msg, data):
        w = self.op
        self.power.stop()
        self.banner.finish(ok, msg); self.banner_wrap.setVisible(False)
        self.clone_page.set_busy(False)
        title = w.info["title"] if w else "Operación"
        self.set_status(f"{title}: {'completado' if ok else 'no completado'}.")
        if self.tray and not self.isVisible():
            self.tray.showMessage(APP_NAME, f"{title}: {'completado' if ok else 'no completado'}.")
        if self._quit_after_op:
            self._quitting = True
            QApplication.quit()
            return
        cb, self._op_done_cb = self._op_done_cb, None
        if data.get("auth") is False:
            self.set_status("Autenticación cancelada.")
        elif cb:
            cb(ok, msg, data)
        elif ok:
            _msg(self, "info", title, msg)
        else:
            _msg(self, "error", title, msg)
        if data.get("remount") and HAS_UDISKS:
            self._run_udisks(["udisksctl", "mount", "-b", data["remount"]], "Volviendo a montar…", quiet=True)
        if w and w.writes:
            self.load_disks()

    def _check_interrupted_op(self):
        j = journal_read()
        if not j:
            return
        logging.warning("Operación interrumpida detectada: %s", j)
        box = QMessageBox(self); box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Operación interrumpida")
        txt = (f"La última vez la aplicación se cerró sin terminar una operación:\n\n"
               f"  {j.get('title', j.get('op'))}: {j.get('description', '')}\n"
               f"  Empezó: {j.get('started', '?')}\n\n")
        if j.get("op") in ("clone", "erase", "image_restore", "init", "part_create", "part_delete",
                           "part_resize", "format", "convert"):
            txt += ("El disco o la partición de destino puede haber quedado incompleto o dañado. "
                    "Comprueba su contenido y, si hace falta, repite la operación o formatéalo.")
        elif j.get("op") == "image_create":
            txt += "El archivo de imagen puede estar incompleto: bórralo y créalo de nuevo."
        else:
            txt += "Comprueba que el disco se ve correctamente."
        btn_online = None
        if IS_WINDOWS and j.get("offline_disk") is not None:
            txt += f"\n\nEl Disco {j['offline_disk']} se quedó OFFLINE."
            btn_online = box.addButton(f"Poner online el Disco {j['offline_disk']}", QMessageBox.ButtonRole.AcceptRole)
        box.setText(txt)
        box.addButton("Entendido", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if btn_online is not None and box.clickedButton() == btn_online:
            res = D.run_powershell(f"Set-Disk -Number {int(j['offline_disk'])} -IsOffline $false")
            if res.returncode != 0:
                _msg(self, "error", "Error", res.stderr.strip())
        journal_clear()
        self.load_disks()

    # --------------------------------------------------------------
    # Menús
    # --------------------------------------------------------------
    def _add_reason(self, menu, reason):
        if reason:
            a = menu.addAction(f"    🔒 {reason[:110]}{'…' if len(reason) > 110 else ''}")
            a.setEnabled(False)

    def show_part_menu(self, disk: Disk, part: Partition):
        menu = QMenu(self)
        a_open = menu.addAction("📂  Abrir"); a_open.setEnabled(bool(part.openable_path))
        a_mount = a_umount = None
        if HAS_UDISKS:
            if part.mountpoints:
                a_umount = menu.addAction("⏏  Desmontar")
                a_umount.setEnabled(not part.critical and not self._op_running())
            else:
                a_mount = menu.addAction("📌  Montar")
                a_mount.setEnabled(bool(part.fs) and not part.busy_reason
                                   and part.fs not in ("swap", "LVM2_member", "crypto_LUKS"))
        menu.addSeparator()
        meta = D.check_part_meta(disk, part)
        dest = D.check_part_destructive(disk, part)
        a_letter = None
        if IS_WINDOWS:
            a_letter = menu.addAction("🔤  Cambiar letra…"); a_letter.setEnabled(meta is None)
        a_label = menu.addAction("🏷️  Cambiar etiqueta…"); a_label.setEnabled(meta is None)
        self._add_reason(menu, meta)
        menu.addSeparator()
        # Comprobar sin reparar es solo lectura: en Windows se permite también en C: (chkdsk sin /f)
        check_ok = meta is None or (IS_WINDOWS and bool(part.letter) and not part.special and bool(part.fs))
        a_check = menu.addAction("🩺  Comprobar errores"); a_check.setEnabled(check_ok)
        a_repair = menu.addAction("🛠️  Reparar…"); a_repair.setEnabled(dest is None and bool(part.fs))
        menu.addSeparator()
        a_fmt = menu.addAction("🧹  Formatear…"); a_fmt.setEnabled(dest is None)
        a_conv = menu.addAction("🔄  Convertir formato…"); a_conv.setEnabled(dest is None and bool(part.fs))
        resize_ok = (dest is None and not part.whole_disk and
                     part.fs_norm in (("ntfs",) if IS_WINDOWS else ("ext4", "ext3", "ext2", "ntfs")))
        a_resize = menu.addAction("↔️  Redimensionar…"); a_resize.setEnabled(resize_ok)
        a_del = menu.addAction("🗑️  Eliminar partición…"); a_del.setEnabled(dest is None and not part.whole_disk)
        self._add_reason(menu, dest)
        if dest is None and not resize_ok and not part.whole_disk:
            self._add_reason(menu, "Redimensionar: solo " + ("NTFS" if IS_WINDOWS else "ext4 y NTFS"))

        sel = menu.exec(QCursor.pos())
        if sel is None:
            return
        if sel == a_open:
            if not open_path(part.openable_path):
                _msg(self, "warn", "Abrir", "No se pudo abrir el explorador de archivos.")
        elif a_mount is not None and sel == a_mount:
            self._run_udisks(["udisksctl", "mount", "-b", part.device], f"Montando {part.device}…")
        elif a_umount is not None and sel == a_umount:
            self._run_udisks(["udisksctl", "unmount", "-b", part.device], f"Desmontando {part.device}…")
        elif a_letter is not None and sel == a_letter:
            self.do_letter(disk, part)
        elif sel == a_label:
            self.do_label(disk, part)
        elif sel == a_check:
            self.do_fsck(disk, part, False)
        elif sel == a_repair:
            self.do_fsck(disk, part, True)
        elif sel == a_fmt:
            self.do_format(disk, part)
        elif sel == a_conv:
            self.do_convert(disk, part)
        elif sel == a_resize:
            self.do_resize(disk, part)
        elif sel == a_del:
            self.do_delete(disk, part)

    def show_gap_menu(self, disk: Disk, start: int, size: int):
        menu = QMenu(self)
        reason = D.check_disk_writable(disk)
        a = menu.addAction(f"➕  Crear partición ({format_bytes(size)})…"); a.setEnabled(reason is None)
        self._add_reason(menu, reason)
        if menu.exec(QCursor.pos()) == a:
            self.do_create(disk, start, size)

    def show_disk_menu(self, disk: Disk):
        menu = QMenu(self)
        a_smart = menu.addAction("🩺  Salud SMART")
        a_bench = menu.addAction("⚡  Test de velocidad")
        a_scan = menu.addAction("🔍  Escaneo de superficie")
        menu.addSeparator()
        a_clone = menu.addAction("🧬  Clonar este disco…")
        a_img = menu.addAction("💾  Crear imagen…")
        w = D.check_disk_writable(disk)
        a_rest = menu.addAction("♻️  Restaurar imagen aquí…"); a_rest.setEnabled(w is None)
        a_iso = menu.addAction("🚀  Grabar ISO aquí…"); a_iso.setEnabled(w is None)
        menu.addSeparator()
        a_init = menu.addAction("🧱  Inicializar disco…"); a_init.setEnabled(w is None)
        a_erase = menu.addAction("🔥  Borrado seguro…"); a_erase.setEnabled(w is None)
        self._add_reason(menu, w)
        sel = menu.exec(QCursor.pos())
        if sel is None:
            return
        {a_smart: lambda: self.do_smart(disk), a_bench: lambda: self.do_bench(disk),
         a_scan: lambda: self.do_scan(disk), a_clone: lambda: self._go_clone(disk),
         a_img: lambda: self.do_image_create(disk), a_rest: lambda: self.do_write_image("image", disk),
         a_iso: lambda: self.do_write_image("iso", disk), a_init: lambda: self.do_init(disk),
         a_erase: lambda: self.do_erase(disk)}.get(sel, lambda: None)()

    def run_tool(self, key):
        if key == "clone":
            self.sidebar.setCurrentRow(2)
            return
        if key in ("image_restore", "iso"):
            self.do_write_image("image" if key == "image_restore" else "iso", None)
            return
        info = {
            "smart": ("Salud SMART", "Elige el disco a revisar.", lambda d: None, self.do_smart),
            "bench": ("Test de velocidad", "Solo lectura: no se escribe nada.", lambda d: None, self.do_bench),
            "scan": ("Escaneo de superficie", "Solo lectura: puede tardar horas en discos grandes.",
                     lambda d: None, self.do_scan),
            "image_create": ("Crear imagen", "Elige el disco a copiar.", lambda d: None, self.do_image_create),
            "init": ("Inicializar disco", "El disco del sistema no aparece disponible.",
                     D.check_disk_writable, self.do_init),
            "erase": ("Borrado seguro", "El disco del sistema no aparece disponible.",
                      D.check_disk_writable, self.do_erase),
        }[key]
        dlg = DiskPickDialog(info[0], info[1], self.disks, info[2], self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.disk():
            info[3](dlg.disk())

    def _run_udisks(self, cmd, status, quiet=False):
        if self.cmd_worker and self.cmd_worker.isRunning():
            return
        self.set_status(status)
        self.cmd_worker = CommandWorker(cmd)
        self.cmd_worker.done.connect(lambda ok, m: self._on_udisks_done(ok, m, quiet))
        self.cmd_worker.start()

    def _on_udisks_done(self, ok, msg, quiet):
        logging.info("udisksctl: %s %s", ok, msg)
        if not ok and not quiet:
            _msg(self, "warn", "Error", msg or "La operación no se pudo completar.")
        self.load_disks()

    # --------------------------------------------------------------
    # Acciones de partición
    # --------------------------------------------------------------
    def _pkey(self, part):
        return {"part": part.key} if IS_WINDOWS else {"dev": part.device}

    def do_letter(self, disk, part):
        import win_ops
        dlg = LetterDialog(part, win_ops.free_letters(), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.run_op("letter", dict(self._pkey(part), letter=dlg.value()), part.display_name)

    def do_label(self, disk, part):
        dlg = LabelDialog(part, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.run_op("label", dict(self._pkey(part), label=dlg.value()), part.display_name)

    def do_fsck(self, disk, part, repair):
        if repair:
            if not confirm_danger(self, "Reparar", f"¿Reparar {part.display_name}?",
                                  "La reparación modifica el sistema de archivos para corregir errores. "
                                  "Normalmente es segura, pero si el disco está fallando conviene hacer "
                                  "antes una copia de los datos." +
                                  ("<br><br>La partición se desmontará mientras tanto." if part.mountpoints else ""),
                                  "Reparar"):
                return
        elif IS_LINUX and part.mountpoints:
            r = QMessageBox.question(self, "Comprobar", f"Para comprobar {part.display_name} hay que "
                                     f"desmontarla un momento (se volverá a montar al terminar). ¿Continuar?")
            if r != QMessageBox.StandardButton.Yes:
                return

        def done(ok, msg, data):
            show_output(self, "Reparar" if repair else "Comprobar", msg, data.get("output", ""),
                        ok and data.get("clean", True))
        self.run_op("fsck", dict(self._pkey(part), repair=repair), part.display_name, done)

    def do_format(self, disk, part, title="Formatear partición", exclude=None, note=None):
        where = part.device if IS_LINUX else f"Partición {part.number}"
        dlg = FsDialog(title, f"{title.split()[0]} {part.display_name}",
                       f"{where} · {format_bytes(part.size)} · {disk.title} ({disk.model or 'Desconocido'})",
                       part.label or "NuevoVolumen", disk.is_usb, exclude, note=note, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.get_data()
        mounted = (f"<br><br>Se desmontará de {', '.join(part.mountpoints)}." if IS_LINUX and part.mountpoints else "")
        if not confirm_danger(self, "Confirmar formateo", f"¿Formatear {part.display_name} en {data['fs']}?",
                              f"Se borrarán <b>todos los datos</b> de {where} ({format_bytes(part.size)}) en "
                              f"{disk.title} · {disk.model or 'Desconocido'}. Esta acción no se puede deshacer.{mounted}",
                              "Sí, formatear", part.short_name):
            return
        params = dict(self._pkey(part), fs=data["fs"] if IS_WINDOWS else FS_ARG[data["fs"]],
                      label=data["label"], quick=data["quick"])
        self.run_op("format", params, f"{part.display_name} → {data['fs']}")

    def do_convert(self, disk, part):
        if IS_WINDOWS and part.fs.upper() in ("FAT32", "FAT"):
            dlg = ConvertChoiceDialog(part, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            if dlg.choice == "convert":
                if not part.letter:
                    _msg(self, "warn", "Convertir", "Asigna antes una letra a la partición.")
                    return
                if not confirm_danger(self, "Convertir a NTFS", f"¿Convertir {part.display_name} a NTFS?",
                                      "Los datos se conservan, pero la conversión no se puede deshacer "
                                      "(para volver a FAT32 habría que formatear). Se recomienda tener copia "
                                      "de seguridad. La unidad se desmontará durante el proceso.",
                                      "Convertir"):
                    return
                self.run_op("convert", {"part": part.key}, f"{part.display_name} FAT32 → NTFS",
                            lambda ok, msg, d: show_output(self, "Convertir", msg, d.get("output", ""), ok))
                return
        self.do_format(disk, part, "Convertir formato", exclude=part.fs_norm,
                       note=(f"Pasar de {part.fs.upper()} a otro sistema de archivos requiere formatear: "
                             f"<b>se borrarán los datos</b>. Copia antes lo que quieras conservar."))

    def do_resize(self, disk, part):
        dlg = ResizeDialog(disk, part, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new = dlg.value()
        if not confirm_danger(self, "Confirmar redimensionado", f"¿Redimensionar {part.display_name}?",
                              f"De {format_bytes(part.size)} a <b>{format_bytes(new)}</b>. Si se corta la "
                              f"luz o se desconecta el disco durante el proceso, los datos de la partición "
                              f"pueden perderse. No apagues ni desconectes nada hasta que termine.",
                              "Redimensionar", part.short_name):
            return
        self.run_op("part_resize", dict(self._pkey(part), new_size=new),
                    f"{part.display_name} → {format_bytes(new)}")

    def do_delete(self, disk, part):
        if not confirm_danger(self, "Eliminar partición", f"¿Eliminar {part.display_name}?",
                              f"Se eliminará la partición ({format_bytes(part.size)}, {part.fs or 'sin formato'}) de "
                              f"{disk.title} · {disk.model or 'Desconocido'}. <b>Sus datos se perderán.</b>",
                              "Eliminar", part.short_name):
            return
        self.run_op("part_delete", self._pkey(part), f"{part.display_name} en {disk.title}")

    def do_create(self, disk, start, size):
        dlg = FsDialog("Crear partición", f"Nueva partición en {disk.title}",
                       f"{disk.model or 'Desconocido'} · espacio libre de {format_bytes(size)}",
                       "NuevoVolumen", disk.is_usb, size_range=(16 * MiB, size), show_quick=False, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.get_data()
        psize = min(data["size"], size)
        params = {"disk": disk.key, "exp_size": disk.size, "start": start, "size": psize,
                  "fs": data["fs"] if IS_WINDOWS else FS_ARG[data["fs"]], "label": data["label"]}
        self.run_op("part_create", params, f"{format_bytes(psize)} {data['fs']} en {disk.title}")

    # --------------------------------------------------------------
    # Acciones de disco
    # --------------------------------------------------------------
    def _dparams(self, disk):
        return {"disk": disk.key, "exp_size": disk.size}

    def do_smart(self, disk):
        self.run_op("smart", {"disk": disk.key}, disk.describe(),
                    lambda ok, msg, d: show_smart(self, disk, d["smart"]) if ok and d.get("smart")
                    else _msg(self, "warn" if not ok else "info", "Salud SMART", msg))

    def do_bench(self, disk):
        self.run_op("bench", self._dparams(disk), disk.describe(),
                    lambda ok, msg, d: show_bench(self, disk, d["bench"]) if ok and d.get("bench")
                    else _msg(self, "warn", "Test de velocidad", msg))

    def do_scan(self, disk):
        r = QMessageBox.question(self, "Escaneo de superficie",
                                 f"Se leerá el disco entero ({format_bytes(disk.size)}). Es solo lectura, pero "
                                 f"puede tardar mucho. ¿Empezar?")
        if r != QMessageBox.StandardButton.Yes:
            return
        self.run_op("scan", self._dparams(disk), disk.describe(),
                    lambda ok, msg, d: show_scan(self, disk, d["scan"]) if ok and d.get("scan")
                    else _msg(self, "warn", "Escaneo de superficie", msg))

    def _go_clone(self, disk: Disk):
        self.sidebar.setCurrentRow(2)
        self.clone_page.preselect_source(disk)

    def start_clone(self, src: Disk, dst: Disk, verify: bool):
        reason = D.check_clone_allowed(src, dst)
        if reason:
            _msg(self, "error", "No se puede clonar", reason)
            return
        parts = ", ".join(p.display_name for p in dst.partitions) or "ninguna"
        body = (f"Se van a <b>sobrescribir todos los datos</b> de:<br>"
                f"&nbsp;&nbsp;<b>{dst.describe()}</b> ({dst.short_device})<br>"
                f"&nbsp;&nbsp;Particiones que se perderán: {parts}<br><br>"
                f"con una copia exacta de:<br>&nbsp;&nbsp;{src.describe()} ({src.short_device})<br><br>"
                + ("Las particiones del destino se desmontarán antes de empezar."
                   if IS_LINUX else "El disco destino se pondrá offline durante la copia.")
                + " Esta acción no se puede deshacer.")
        if not confirm_danger(self, "Confirmar clonación", "¿Clonar disco? El destino se borrará por completo.",
                              body, "Clonar y borrar destino", dst.confirm_token):
            return
        params = {"src": src.key, "dst": dst.key, "src_size": src.size, "dst_size": dst.size, "verify": verify}
        self.run_op("clone", params, f"{src.title} → {dst.title}")

    def do_image_create(self, disk):
        dlg = ImageCreateDialog(disk, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        path = os.path.abspath(dlg.path.text().strip())
        if not path:
            return
        reason = D.check_image_source(disk, path, self.disks)
        if reason:
            _msg(self, "error", "Crear imagen", reason)
            return
        if os.path.exists(path):
            r = QMessageBox.question(self, "Crear imagen", f"{path} ya existe. ¿Reemplazarlo?")
            if r != QMessageBox.StandardButton.Yes:
                return
            try:
                os.remove(path)
                if os.path.exists(path + ".sha256"):
                    os.remove(path + ".sha256")
            except OSError as e:
                _msg(self, "error", "Crear imagen", f"No se pudo reemplazar el archivo: {e}")
                return
        params = dict(self._dparams(disk), path=path, verify=dlg.chk_verify.isChecked())
        self.run_op("image_create", params, f"{disk.title} → {os.path.basename(path)}")

    def do_write_image(self, kind, disk: Optional[Disk]):
        dlg = WriteImageDialog(kind, self.disks, disk, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.get_data()
        target = data["disk"]
        size = os.path.getsize(data["path"]) if os.path.isfile(data["path"]) else 0
        reason = D.check_image_target(target, size, data["path"], self.disks)
        if reason:
            _msg(self, "error", "No se puede escribir", reason)
            return
        name = os.path.basename(data["path"])
        if not confirm_danger(self, "Confirmar", f"¿Escribir {name} en {target.title}?",
                              f"Se <b>borrarán todos los datos</b> de {target.describe()} ({target.short_device}) "
                              f"y se sustituirán por el contenido de {name} ({format_bytes(size)}).",
                              "Escribir y borrar disco", target.confirm_token):
            return
        params = dict(self._dparams(target), path=data["path"], verify=data["verify"],
                      precheck=data["precheck"] and kind != "iso", kind=kind)
        self.run_op("image_restore", params, f"{name} → {target.title}")

    def do_init(self, disk):
        dlg = InitDialog(disk, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.get_data()
        parts = ", ".join(p.display_name for p in disk.partitions) or "ninguna"
        if not confirm_danger(self, "Inicializar disco", f"¿Inicializar {disk.title} como {data['style'].upper()}?",
                              f"Se eliminarán todas las particiones de {disk.describe()} ({parts}). "
                              f"<b>Los datos dejarán de ser accesibles.</b>",
                              "Inicializar", disk.confirm_token):
            return
        self.run_op("init", dict(self._dparams(disk), **data), f"{disk.title} ({data['style'].upper()})")

    def do_erase(self, disk):
        dlg = EraseDialog(disk, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.get_data()
        if not confirm_danger(self, "Borrado seguro", f"¿Borrar para siempre {disk.title}?",
                              f"Se destruirán <b>todos los datos</b> de {disk.describe()} ({disk.short_device}). "
                              f"No se podrán recuperar de ninguna forma.",
                              "Borrar para siempre", disk.confirm_token):
            return
        self.run_op("erase", dict(self._dparams(disk), **data), disk.describe())

    # --------------------------------------------------------------
    # Cierre
    # --------------------------------------------------------------
    def closeEvent(self, event):
        if self._quitting:
            event.accept()
            return
        if self._op_running():
            # En Windows solo llega aquí con Alt+F4 (la X se bloquea en nativeEvent).
            # En Linux no se puede distinguir la X de Alt+F4: ambos muestran esta confirmación.
            self._kbd_close = False
            event.ignore()
            d = BusyCloseDialog(self.op, self.tray_available, self)
            d.exec()
            if d.action == "tray":
                self.hide()
            elif d.action == "cancel_quit" and self.op.cancelable:
                self._quit_after_op = True
                self.op.cancel()
                self.banner.btn_cancel.setEnabled(False); self.banner.btn_cancel.setText("Cancelando…")
                self.set_status("Cancelando para salir…")
            return
        action = "quit"
        if self.tray_available:
            if self.settings.value("ask_on_close", True, type=bool):
                d = CloseConfirmDialog(self)
                if not d.exec():
                    event.ignore()
                    return
                action = d.action
                if d.chk_rem.isChecked():
                    self.settings.setValue("ask_on_close", False)
                    self.settings.setValue("minimize_to_tray", action == "tray")
                    self.settings_page.chk_ask.setChecked(False)
                    self.settings_page.chk_tray.setChecked(action == "tray")
            else:
                action = "tray" if self.settings.value("minimize_to_tray", True, type=bool) else "quit"
        if action == "tray":
            event.ignore(); self.hide()
            self.tray.showMessage(APP_NAME, "Sigue ejecutándose en segundo plano.",
                                  QSystemTrayIcon.MessageIcon.Information, 3000)
            return
        self._quitting = True
        event.accept()
        QApplication.quit()

    def busy_writing(self) -> bool:
        return self._op_running() and self.op.writes
