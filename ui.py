import os
import json
import subprocess
import logging
from ctypes import wintypes
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
                             QScrollArea, QFrame, QSplitter, QProgressBar, QTableWidget, 
                             QTableWidgetItem, QHeaderView, QMenu, QMessageBox, QDialog, 
                             QFormLayout, QComboBox, QLineEdit, QCheckBox, QDialogButtonBox,
                             QSystemTrayIcon, QListWidget, QStackedWidget, QApplication, QMainWindow)
from PyQt6.QtCore import Qt, QSettings, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QIcon, QCursor, QAction, QCloseEvent

# Importamos los workers desde nuestros nuevos módulos
from format import FormatWorker
from clon import CloneWorker

def format_bytes(bytes_val):
    if bytes_val is None or bytes_val == "": return "N/A"
    try:
        bytes_val = float(bytes_val)
        if bytes_val >= 1024**4: return f"{(bytes_val / 1024**4):.2f} TB"
        elif bytes_val >= 1024**3: return f"{(bytes_val / 1024**3):.2f} GB"
        elif bytes_val >= 1024**2: return f"{(bytes_val / 1024**2):.0f} MB"
        elif bytes_val >= 1024: return f"{(bytes_val / 1024):.0f} KB"
        else: return f"{int(bytes_val)} B"
    except: return "N/A"

class DiskReaderWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def run(self):
        ps_script = """
        $disks = Get-Disk | Select-Object Number, FriendlyName, Size, OperationalStatus, IsBoot, IsSystem
        $partitions = Get-Partition | Select-Object DiskNumber, PartitionNumber, DriveLetter, Size, Type
        $volumes = Get-Volume | Select-Object DriveLetter, FileSystemLabel, FileSystem, SizeRemaining
        @{ disks=$disks; partitions=$partitions; volumes=$volumes } | ConvertTo-Json -Depth 3 -Compress
        """
        try:
            cmd = ['powershell', '-ExecutionPolicy', 'Bypass', '-NoProfile', '-Command', ps_script]
            result = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode != 0: raise Exception(result.stderr)
            data = json.loads(result.stdout)
            for key in ['disks', 'partitions', 'volumes']:
                if isinstance(data.get(key), dict): data[key] = [data[key]]
                elif data.get(key) is None: data[key] = []
            self.finished.emit(data)
        except Exception as e:
            self.error.emit(str(e))

class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Ajustes - Korvex Disk Manager")
        self.setMinimumSize(600, 450)
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; }
            QFrame#Separator { background-color: #3e3e42; }
            QLabel#SectionTitle { color: #00aaff; font-weight: bold; font-size: 14px; }
            QLabel { color: #dcdcdc; }
            QListWidget { background-color: #252526; border: none; border-right: 1px solid #3e3e42; outline: none; }
            QListWidget::item { padding: 10px; color: #aaa; border-left: 3px solid transparent; }
            QListWidget::item:selected { background-color: #2d2d30; color: white; border-left: 3px solid #00aaff; }
            QCheckBox { color: white; spacing: 8px; }
            QCheckBox::indicator { width: 16px; height: 16px; background: #2d2d30; border: 1px solid #3e3e42; border-radius: 3px; }
            QCheckBox::indicator:checked { background: #00aaff; border-color: #00aaff; }
            QPushButton#SaveBtn { background-color: #00aaff; color: white; font-weight: bold; border-radius: 4px; padding: 6px 20px; }
            QPushButton#CancelBtn { background-color: #3e3e42; color: white; border-radius: 4px; padding: 6px 20px; }
        """)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(160)
        self.nav_list.addItems(["General", "Interfaz", "Avanzado"])
        main_layout.addWidget(self.nav_list)

        self.pages = QStackedWidget()
        main_layout.addWidget(self.pages)

        # General
        page_general = QWidget()
        gen_layout = QVBoxLayout(page_general)
        lbl_gen = QLabel("Ajustes Generales"); lbl_gen.setObjectName("SectionTitle"); gen_layout.addWidget(lbl_gen)
        sep = QFrame(); sep.setObjectName("Separator"); sep.setFixedHeight(1); gen_layout.addWidget(sep)
        gen_layout.addSpacing(15)

        self.chk_ask = QCheckBox("Preguntar siempre al cerrar la aplicación")
        self.chk_ask.setChecked(self.settings.value("ask_on_close", True, type=bool))
        self.chk_tray = QCheckBox("Por defecto, minimizar a la bandeja del sistema")
        self.chk_tray.setChecked(self.settings.value("minimize_to_tray", True, type=bool))
        
        gen_layout.addWidget(self.chk_ask)
        gen_layout.addWidget(self.chk_tray)
        gen_layout.addStretch()
        self.pages.addWidget(page_general)

        # Actions
        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(20, 10, 20, 20)
        btn_cancel = QPushButton("Cancelar"); btn_cancel.setObjectName("CancelBtn"); btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("Guardar"); btn_save.setObjectName("SaveBtn"); btn_save.clicked.connect(self.save)
        actions_layout.addStretch()
        actions_layout.addWidget(btn_cancel)
        actions_layout.addWidget(btn_save)

        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 20, 0, 0)
        right_layout.addWidget(self.pages)
        right_layout.addLayout(actions_layout)
        main_layout.addWidget(right_container)

        self.nav_list.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav_list.setCurrentRow(0)

    def save(self):
        self.settings.setValue("ask_on_close", self.chk_ask.isChecked())
        self.settings.setValue("minimize_to_tray", self.chk_tray.isChecked())
        self.accept()

class CloseConfirmDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cerrar Korvex Disk Manager")
        self.setFixedSize(420, 180)
        self.action = None
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; border: 1px solid #3e3e42; }
            QLabel { color: white; font-size: 13px; }
            QCheckBox { color: #aaa; font-size: 11px; }
            QCheckBox::indicator { width: 14px; height: 14px; background-color: #1e1e1e; border: 1px solid #555; border-radius: 3px; }
            QCheckBox::indicator:checked { background-color: #00aaff; border: 1px solid #00aaff; }
            QPushButton#TrayBtn { background-color: #00aaff; color: white; font-weight: bold; border: none; border-radius: 4px; padding: 10px; }
            QPushButton#QuitBtn { background-color: #333; color: #ff5555; font-weight: bold; border: 1px solid #555; border-radius: 4px; padding: 10px; }
        """)
        layout = QVBoxLayout(self)
        lbl = QLabel("¿Cómo quieres cerrar Korvex Disk Manager?")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)
        
        btns = QHBoxLayout()
        self.btn_tray = QPushButton("Bandeja del Sistema"); self.btn_tray.setObjectName("TrayBtn")
        self.btn_quit = QPushButton("Cerrar Aplicación"); self.btn_quit.setObjectName("QuitBtn")
        btns.addWidget(self.btn_tray); btns.addWidget(self.btn_quit)
        layout.addLayout(btns)
        
        self.chk_rem = QCheckBox("Recordar mi decisión")
        layout.addWidget(self.chk_rem)
        
        self.btn_tray.clicked.connect(lambda: self.finish("tray"))
        self.btn_quit.clicked.connect(lambda: self.finish("quit"))

    def finish(self, act):
        self.action = act
        self.accept()

class FormatDialog(QDialog):
    def __init__(self, target_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Formatear {target_name}")
        self.setFixedSize(350, 220)
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: white; }
            QLabel { color: white; font-weight: bold; }
            QComboBox, QLineEdit { background-color: #1e1e1e; color: white; padding: 5px; border: 1px solid #555; border-radius: 3px; }
            QComboBox:focus, QLineEdit:focus { border: 1px solid #00aaff; }
            QComboBox::drop-down { border: none; }
            QCheckBox { color: white; }
        """)
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()
        
        self.cb_fs = QComboBox()
        self.cb_fs.addItems(["NTFS", "exFAT", "FAT32"])
        self.le_label = QLineEdit("NuevoVolumen")
        self.le_label.setMaxLength(32)
        self.chk_quick = QCheckBox("Dar formato rápido")
        self.chk_quick.setChecked(True)

        form_layout.addRow("Sistema de archivos:", self.cb_fs)
        form_layout.addRow("Etiqueta:", self.le_label)
        form_layout.addRow("", self.chk_quick)
        layout.addLayout(form_layout)

        self.btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_ok = self.btn_box.button(QDialogButtonBox.StandardButton.Ok)
        btn_ok.setText("Iniciar")
        btn_ok.setStyleSheet("background-color: #00aaff; color: white; padding: 6px 15px; border: none; border-radius: 3px; font-weight: bold;")
        btn_cancel = self.btn_box.button(QDialogButtonBox.StandardButton.Cancel)
        btn_cancel.setText("Cancelar")
        btn_cancel.setStyleSheet("background-color: #333; color: white; padding: 6px 15px; border: 1px solid #555; border-radius: 3px;")
        
        self.btn_box.accepted.connect(self.accept)
        self.btn_box.rejected.connect(self.reject)
        layout.addWidget(self.btn_box)

    def get_data(self):
        return {"fs": self.cb_fs.currentText(), "label": self.le_label.text(), "quick": self.chk_quick.isChecked()}


class DiskManagerWindow(QMainWindow):
    def __init__(self, settings_path):
        super().__init__()
        self.setWindowTitle("KORVEX Disk Manager")
        self.settings = QSettings(settings_path, QSettings.Format.IniFormat)
        self.reader_worker = None
        self.format_worker = None
        
        self.setup_ui_base()
        self.setup_tray_icon()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.load_disks)
        self.load_disks()

    def setup_ui_base(self):
        self.setStyleSheet("background-color: #1e1e1e; color: white;")
        screen_geo = QApplication.primaryScreen().geometry()
        width, height = int(screen_geo.width() * 0.75), int(screen_geo.height() * 0.85)
        self.setGeometry((screen_geo.width() - width) // 2, (screen_geo.height() - height) // 2, width, height)
        
        central = QWidget(); self.setCentralWidget(central)
        layout = QVBoxLayout(central); layout.setSpacing(15); layout.setContentsMargins(20, 20, 20, 20)

        header = QHBoxLayout()
        lbl_title = QLabel("KORVEX Disk Manager")
        lbl_title.setFont(QFont("Orbitron", 20, QFont.Weight.Bold)) 
        lbl_title.setStyleSheet("color: #00aaff; border: none;") 
        header.addWidget(lbl_title)
        
        self.lbl_status = QLabel("Preparado.")
        self.lbl_status.setStyleSheet("color: #888; border: none; margin-left: 15px;")
        header.addWidget(self.lbl_status); header.addStretch()

        self.btn_refresh = QPushButton("🔄"); self.btn_refresh.setFixedSize(35, 35)
        self.btn_refresh.setStyleSheet("QPushButton { background: #333; border: 1px solid #555; border-radius: 5px; font-size: 16px; } QPushButton:hover { border-color: #00aaff; }")
        self.btn_refresh.clicked.connect(self.load_disks)
        header.addWidget(self.btn_refresh)

        self.btn_settings = QPushButton("⚙️"); self.btn_settings.setFixedSize(35, 35)
        self.btn_settings.setStyleSheet("QPushButton { background: #333; border: 1px solid #555; border-radius: 5px; font-size: 16px; } QPushButton:hover { border-color: #00aaff; }")
        self.btn_settings.clicked.connect(lambda: SettingsDialog(self.settings, self).exec())
        header.addWidget(self.btn_settings)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet("QSplitter::handle { background: transparent; height: 10px; }")

        self.top_panel = QFrame(); self.top_panel.setStyleSheet("background: #252526; border-radius: 10px; border: 1px solid #3e3e42;")
        top_layout = QVBoxLayout(self.top_panel); top_layout.setContentsMargins(5, 5, 5, 5)
        
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["Volumen", "Nombre", "Formato", "Tipo", "Capacidad", "Libre", "% Libre"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setStyleSheet("QTableWidget { background: transparent; border: none; } QHeaderView::section { background: #333; color: #00aaff; font-weight: bold; border: 1px solid #3e3e42; } QTableWidget::item { padding: 5px; border-bottom: 1px solid #3e3e42; } QTableWidget::item:selected { background: #004466; }")
        top_layout.addWidget(self.table)
        
        self.bottom_panel = QScrollArea(); self.bottom_panel.setWidgetResizable(True)
        self.bottom_panel.setStyleSheet("QScrollArea { border: 1px solid #3e3e42; background: #252526; border-radius: 10px; }")
        self.disks_container = QWidget(); self.disks_container.setStyleSheet("background: transparent;")
        self.disks_layout = QVBoxLayout(self.disks_container); self.disks_layout.setAlignment(Qt.AlignmentFlag.AlignTop); self.disks_layout.setSpacing(15)
        self.bottom_panel.setWidget(self.disks_container)

        splitter.addWidget(self.top_panel); splitter.addWidget(self.bottom_panel)
        splitter.setSizes([int(self.height() * 0.4), int(self.height() * 0.6)])
        layout.addWidget(splitter)

    def setup_tray_icon(self):
        self.tray = QSystemTrayIcon(self); self.tray.setIcon(self.windowIcon())
        menu = QMenu()
        menu.setStyleSheet("QMenu { background: #252526; border: 1px solid #444; } QMenu::item:selected { background: #00aaff; }")
        menu.addAction("Mostrar", self.showNormal)
        menu.addSeparator()
        menu.addAction("Salir", QApplication.quit)
        self.tray.setContextMenu(menu); self.tray.show()
        self.tray.activated.connect(lambda r: self.showNormal() if r == QSystemTrayIcon.ActivationReason.DoubleClick else None)

    def closeEvent(self, event):
        ask = self.settings.value("ask_on_close", True, type=bool)
        to_tray = self.settings.value("minimize_to_tray", True, type=bool)
        if ask:
            d = CloseConfirmDialog(self)
            if d.exec():
                if d.chk_rem.isChecked():
                    self.settings.setValue("ask_on_close", False)
                    self.settings.setValue("minimize_to_tray", d.action == "tray")
                if d.action == "tray":
                    event.ignore(); self.hide(); self.tray.showMessage("KORVEX", "Ejecutándose en 2º plano", QSystemTrayIcon.MessageIcon.Information)
                else: event.accept()
            else: event.ignore()
        else:
            if to_tray: event.ignore(); self.hide()
            else: event.accept()

    def nativeEvent(self, eventType, message):
        try:
            if bytes(eventType) == b"windows_generic_MSG":
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0219 and msg.wParam in (0x8000, 0x8004):
                    self.lbl_status.setText("Cambio de hardware. Actualizando...")
                    self.refresh_timer.start(1500)
        except: pass
        return False, 0

    def load_disks(self):
        if self.reader_worker and self.reader_worker.isRunning(): return
        self.btn_refresh.setEnabled(False)
        self.lbl_status.setText("Leyendo discos...")
        while self.disks_layout.count():
            w = self.disks_layout.takeAt(0).widget(); w.deleteLater() if w else None
        self.table.setRowCount(0)
        
        self.reader_worker = DiskReaderWorker()
        self.reader_worker.finished.connect(self.populate_disks)
        self.reader_worker.error.connect(lambda e: self.lbl_status.setText(f"Error: {e}"))
        self.reader_worker.start()

    def show_part_menu(self, pos, disk_num, drive_letter, is_system):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background: #252526; border: 1px solid #444; } QMenu::item { padding: 8px 25px; } QMenu::item:selected { background: #00aaff; } QMenu::item:disabled { color: #666; }")
        act_open = menu.addAction("Abrir explorador"); menu.addSeparator()
        act_fmt = menu.addAction("Formatear partición...")
        
        if not drive_letter: act_open.setEnabled(False)
        if is_system: act_fmt.setEnabled(False)
            
        sel = menu.exec(QCursor.pos())
        if sel == act_open: os.startfile(f"{drive_letter}:\\")
        elif sel == act_fmt:
            dialog = FormatDialog(f"Unidad {drive_letter}:", self)
            if dialog.exec():
                data = dialog.get_data()
                msg = QMessageBox(self)
                msg.setWindowTitle("Peligro"); msg.setText(f"Se borrarán todos los datos en {drive_letter}:. ¿Confirmar?"); msg.setWindowIcon(self.windowIcon())
                msg.setStyleSheet("QMessageBox { background: #252526; } QPushButton { background: #444; padding: 6px 15px; border-radius: 3px; }")
                btn_si = msg.addButton("Sí, formatear", QMessageBox.ButtonRole.AcceptRole)
                btn_si.setStyleSheet("background: #aa0000; font-weight: bold;")
                msg.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
                msg.exec()
                
                if msg.clickedButton() == btn_si:
                    self.lbl_status.setText(f"Formateando {drive_letter}:...")
                    self.format_worker = FormatWorker(drive_letter, data['fs'], data['label'], data['quick'])
                    self.format_worker.finished.connect(self.on_format_finished)
                    self.format_worker.start()

    def on_format_finished(self, success, message):
        if success:
            QMessageBox.information(self, "Completado", message)
            self.load_disks() # Refrescar la tabla automáticamente después de formatear
        else:
            QMessageBox.critical(self, "Error", message)
            self.lbl_status.setText("Fallo en formateo.")

    def populate_disks(self, data):
        self.lbl_status.setText("Discos cargados.")
        self.btn_refresh.setEnabled(True)
        disks = sorted(data.get('disks', []), key=lambda x: x.get('Number', 0))
        partitions = data.get('partitions', [])
        volumes = data.get('volumes', [])
        
        for disk in disks:
            d_num = disk.get('Number')
            d_parts = [p for p in partitions if p.get('DiskNumber') == d_num]
            is_sys = disk.get('IsBoot') or disk.get('IsSystem') or any(p.get('DriveLetter') == 'C' for p in d_parts)
            
            # SOLUCIÓN BUG "ERROR" ESTADO DEL DISCO:
            # Capturamos correctamente el estado que devuelve PowerShell ("Online", "Offline", etc.)
            op_status = disk.get('OperationalStatus')
            if isinstance(op_status, list) and len(op_status) > 0:
                op_status = op_status[0]
            status_str = str(op_status).strip().upper()
            estado_texto = "En línea" if "ONLINE" in status_str else f"Estado: {op_status}"
            
            frame = QFrame(); frame.setStyleSheet("background: #333; border: 1px solid #555; border-radius: 5px;")
            layout = QHBoxLayout(frame)
            
            info = QVBoxLayout()
            # Ahora la etiqueta de información del disco incluye el estado correcto
            lbl = QLabel(f"Disco {d_num}\n{disk.get('FriendlyName', 'Desconocido')}\n{format_bytes(disk.get('Size'))}\n{estado_texto}")
            lbl.setMinimumWidth(180)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-weight: bold;")
            info.addWidget(lbl)
            
            if is_sys: 
                l_sys = QLabel("(Protegido 🔒)"); l_sys.setStyleSheet("color: #00aaff; font-weight: bold;"); info.addWidget(l_sys)
            info.addStretch(); layout.addLayout(info)
            
            line = QFrame(); line.setFrameShape(QFrame.Shape.VLine); line.setStyleSheet("background: #555;"); layout.addWidget(line)
            
            p_cont = QWidget(); p_lay = QHBoxLayout(p_cont); p_lay.setContentsMargins(0,0,0,0); p_lay.setSpacing(5)
            
            for part in sorted(d_parts, key=lambda x: x.get('PartitionNumber', 0)):
                letter = part.get('DriveLetter'); size = part.get('Size'); p_type = part.get('Type')
                vol = next((v for v in volumes if v.get('DriveLetter') == letter), None)
                sys_part = (letter == 'C') or (p_type in ["System", "Recovery"]) or is_sys
                
                p_frame = QFrame(); border = "#555" if sys_part else "#00aaff"
                p_frame.setStyleSheet(f"background: #1e1e1e; border: 1px solid {border}; border-radius: 3px;")
                p_frame.setMinimumWidth(110)
                p_frame.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                p_frame.customContextMenuRequested.connect(lambda pos, dn=d_num, dl=letter, sp=sys_part: self.show_part_menu(pos, dn, dl, sp))
                
                pf_lay = QVBoxLayout(p_frame)
                hb = QFrame(); hb.setFixedHeight(5); hb.setStyleSheet(f"background: {border}; border: none;"); pf_lay.addWidget(hb)
                plbl = QLabel(f"({letter}:)\n{format_bytes(size)}\n{p_type}" if letter else f"(Oculta)\n{format_bytes(size)}\n{p_type}")
                plbl.setStyleSheet("font-size: 11px;"); plbl.setAlignment(Qt.AlignmentFlag.AlignCenter); pf_lay.addWidget(plbl)
                
                if vol and letter:
                    row = self.table.rowCount(); self.table.insertRow(row)
                    free = vol.get('SizeRemaining', 0)
                    perc = int((free/size)*100) if size > 0 else 0
                    
                    # SOLUCIÓN NOMBRE TABLA: Si la partición no tiene etiqueta, hereda el nombre físico del disco
                    fs_label = vol.get('FileSystemLabel')
                    if not fs_label or str(fs_label).strip() == "":
                        fs_label = disk.get('FriendlyName', 'Disco Local')
                        
                    self.table.setItem(row, 0, QTableWidgetItem(f"{letter}:"))
                    self.table.setItem(row, 1, QTableWidgetItem(fs_label))
                    self.table.setItem(row, 2, QTableWidgetItem(vol.get('FileSystem') or "RAW"))
                    self.table.setItem(row, 3, QTableWidgetItem(p_type))
                    self.table.setItem(row, 4, QTableWidgetItem(format_bytes(size)))
                    self.table.setItem(row, 5, QTableWidgetItem(format_bytes(free)))
                    self.table.setItem(row, 6, QTableWidgetItem(f"{perc} %"))
                    
                    bar = QProgressBar(); bar.setFixedHeight(8); bar.setValue(100 - perc); bar.setTextVisible(False)
                    bar.setStyleSheet(f"QProgressBar {{ border: none; background: #444; border-radius: 4px; }} QProgressBar::chunk {{ background: {border}; border-radius: 4px; }}")
                    pf_lay.addWidget(bar)
                    
                p_lay.addWidget(p_frame, stretch=max(1, int(size/(1024**3))))
                
            layout.addWidget(p_cont, stretch=1); self.disks_layout.addWidget(frame)