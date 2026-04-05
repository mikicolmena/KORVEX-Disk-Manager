import sys
import os
import ctypes
from ctypes import wintypes
import logging
import json
import subprocess
from datetime import datetime

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QScrollArea, QFrame, QSplitter, QProgressBar,
                             QTableWidget, QTableWidgetItem, QHeaderView, QMenu, QMessageBox,
                             QDialog, QFormLayout, QComboBox, QLineEdit, QCheckBox, QDialogButtonBox,
                             QSystemTrayIcon)
from PyQt6.QtCore import Qt, QSettings, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QIcon, QColor, QPalette, QCursor, QAction, QCloseEvent

# ============================================================================
# 1. SISTEMA DE LOGS PROFESIONAL
# ============================================================================
def setup_logging():
    documents_path = os.path.join(os.path.expanduser("~"), "Documents", "Korvex Disk Manager")
    logs_path = os.path.join(documents_path, "logs")
    if not os.path.exists(logs_path):
        os.makedirs(logs_path)
        
    log_filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S.log")
    log_file_path = os.path.join(logs_path, log_filename)
    
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file_path, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.critical("Excepción no controlada:", exc_info=(exc_type, exc_value, exc_traceback))
        
    sys.excepthook = handle_exception
    logging.info(f"--- INICIANDO KORVEX DISK MANAGER ({log_filename}) ---")

# ============================================================================
# 2. FUNCIONES DE AYUDA Y PRIVILEGIOS
# ============================================================================
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def format_bytes(bytes_val):
    if bytes_val is None or bytes_val == "": return "N/A"
    try:
        bytes_val = float(bytes_val)
        if bytes_val >= 1024**4: return f"{(bytes_val / 1024**4):.2f} TB"
        elif bytes_val >= 1024**3: return f"{(bytes_val / 1024**3):.2f} GB"
        elif bytes_val >= 1024**2: return f"{(bytes_val / 1024**2):.0f} MB"
        elif bytes_val >= 1024: return f"{(bytes_val / 1024):.0f} KB"
        else: return f"{int(bytes_val)} B"
    except:
        return "N/A"

# ============================================================================
# 3. DIÁLOGOS DE LA APLICACIÓN (Ajustes, Cierre y Formateo)
# ============================================================================
class SettingsDialog(QDialog):
    """Ventana de Ajustes de la Aplicación"""
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Ajustes - Korvex Disk Manager")
        self.setFixedSize(380, 180)
        
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: white; }
            QLabel { color: white; font-weight: bold; font-size: 13px; }
            QCheckBox { color: white; font-size: 12px; }
            QCheckBox::indicator { width: 15px; height: 15px; background-color: #1e1e1e; border: 1px solid #555; border-radius: 3px; }
            QCheckBox::indicator:checked { background-color: #00aaff; border: 1px solid #00aaff; }
            QPushButton { background-color: #333; color: white; padding: 6px 15px; border: 1px solid #555; border-radius: 3px; }
            QPushButton:hover { background-color: #444; border-color: #00aaff; }
        """)

        layout = QVBoxLayout(self)
        
        # Leemos los ajustes actuales (por defecto: preguntar siempre, y preferir bandeja)
        ask_close = self.settings.value("ask_on_close", True, type=bool)
        tray_close = self.settings.value("minimize_to_tray", True, type=bool)

        lbl_title = QLabel("Comportamiento al cerrar (Botón 'X')")
        layout.addWidget(lbl_title)

        self.chk_ask = QCheckBox("Preguntar siempre qué hacer al cerrar")
        self.chk_ask.setChecked(ask_close)
        
        self.chk_tray = QCheckBox("Por defecto, minimizar a la bandeja del sistema")
        self.chk_tray.setChecked(tray_close)
        
        # Lógica visual: Si preguntas siempre, la acción por defecto importa menos, 
        # pero ambas se guardan.
        layout.addWidget(self.chk_ask)
        layout.addWidget(self.chk_tray)
        
        layout.addStretch()

        self.btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btn_save = self.btn_box.button(QDialogButtonBox.StandardButton.Save)
        btn_save.setText("Guardar")
        btn_save.setStyleSheet("background-color: #00aaff; color: white; font-weight: bold; border: none;")
        
        btn_cancel = self.btn_box.button(QDialogButtonBox.StandardButton.Cancel)
        btn_cancel.setText("Cancelar")

        self.btn_box.accepted.connect(self.save_settings)
        self.btn_box.rejected.connect(self.reject)
        layout.addWidget(self.btn_box)

    def save_settings(self):
        self.settings.setValue("ask_on_close", self.chk_ask.isChecked())
        self.settings.setValue("minimize_to_tray", self.chk_tray.isChecked())
        logging.info("Ajustes de la aplicación guardados.")
        self.accept()

class CloseConfirmDialog(QDialog):
    """Diálogo personalizado al darle a la X"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cerrar Korvex Disk Manager")
        self.setFixedSize(400, 160)
        
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: white; }
            QLabel { color: white; font-size: 13px; }
            QCheckBox { color: white; font-size: 11px; margin-top: 10px; }
            QCheckBox::indicator { width: 14px; height: 14px; background-color: #1e1e1e; border: 1px solid #555; border-radius: 3px; }
            QCheckBox::indicator:checked { background-color: #00aaff; border: 1px solid #00aaff; }
            QPushButton { background-color: #333; color: white; padding: 8px 15px; border: 1px solid #555; border-radius: 3px; font-weight: bold; }
            QPushButton:hover { background-color: #444; border-color: #00aaff; }
        """)

        layout = QVBoxLayout(self)
        
        lbl_msg = QLabel("¿Qué deseas hacer con la aplicación?")
        lbl_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_msg)
        
        btn_layout = QHBoxLayout()
        self.btn_tray = QPushButton("Minimizar a la Bandeja")
        self.btn_tray.setStyleSheet("background-color: #00aaff; border: none;")
        self.btn_quit = QPushButton("Cerrar Completamente")
        self.btn_quit.setStyleSheet("background-color: #aa0000; border: none;")
        
        btn_layout.addWidget(self.btn_tray)
        btn_layout.addWidget(self.btn_quit)
        layout.addLayout(btn_layout)
        
        self.chk_remember = QCheckBox("Recordar mi decisión y no volver a preguntar (Puedes cambiarlo en Ajustes)")
        layout.addWidget(self.chk_remember)
        
        self.action = None
        self.btn_tray.clicked.connect(self.choose_tray)
        self.btn_quit.clicked.connect(self.choose_quit)

    def choose_tray(self):
        self.action = "tray"
        self.accept()
        
    def choose_quit(self):
        self.action = "quit"
        self.accept()

class FormatDialog(QDialog):
    """Diálogo de opciones de formateo (Diseño Premium)"""
    def __init__(self, target_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Formatear {target_name}")
        self.setFixedSize(350, 220)
        
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: white; }
            QLabel { color: white; font-weight: bold; }
            QComboBox, QLineEdit { 
                background-color: #1e1e1e; 
                color: white; 
                padding: 5px; 
                border: 1px solid #555; 
                border-radius: 3px; 
            }
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
        form_layout.addRow("Etiqueta del volumen:", self.le_label)
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
        return {
            "fs": self.cb_fs.currentText(),
            "label": self.le_label.text(),
            "quick": self.chk_quick.isChecked()
        }

# ============================================================================
# 4. WORKER: MOTOR DE LECTURA DE DISCOS
# ============================================================================
class DiskReaderWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def run(self):
        logging.info("Iniciando escaneo profundo de discos...")
        ps_script = """
        $disks = Get-Disk | Select-Object Number, FriendlyName, Size, OperationalStatus, IsBoot, IsSystem
        $partitions = Get-Partition | Select-Object DiskNumber, PartitionNumber, DriveLetter, Size, Type
        $volumes = Get-Volume | Select-Object DriveLetter, FileSystemLabel, FileSystem, SizeRemaining
        @{ disks=$disks; partitions=$partitions; volumes=$volumes } | ConvertTo-Json -Depth 3 -Compress
        """
        try:
            cmd = ['powershell', '-ExecutionPolicy', 'Bypass', '-NoProfile', '-Command', ps_script]
            result = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            
            if result.returncode != 0:
                raise Exception(f"Error en PS: {result.stderr}")
                
            data = json.loads(result.stdout)
            
            for key in ['disks', 'partitions', 'volumes']:
                if isinstance(data.get(key), dict):
                    data[key] = [data[key]]
                elif data.get(key) is None:
                    data[key] = []
                    
            logging.info(f"Escaneo finalizado. Discos encontrados: {len(data['disks'])}")
            self.finished.emit(data)
            
        except Exception as e:
            logging.error(f"Fallo al leer discos: {e}")
            self.error.emit(str(e))

# ============================================================================
# 5. INTERFAZ GRÁFICA PRINCIPAL
# ============================================================================
class DiskManagerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KORVEX Disk Manager")
        
        # Carga de ajustes desde Documentos/Korvex Disk Manager/config.ini
        path_config = os.path.join(os.path.expanduser("~"), "Documents", "Korvex Disk Manager", "config.ini")
        self.settings = QSettings(path_config, QSettings.Format.IniFormat)
        
        screen_geo = QApplication.primaryScreen().geometry()
        width = int(screen_geo.width() * 0.75) 
        height = int(screen_geo.height() * 0.85) 
        x = (screen_geo.width() - width) // 2
        y = (screen_geo.height() - height) // 2
        self.setGeometry(x, y, width, height)
        self.setStyleSheet("background-color: #1e1e1e; color: white;")

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.load_disks)

        self.reader_worker = None

        self.init_ui()
        self.setup_tray_icon()
        self.load_disks()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)

        header_layout = QHBoxLayout()
        lbl_title = QLabel("KORVEX Disk Manager")
        lbl_title.setFont(QFont("Orbitron", 20, QFont.Weight.Bold)) 
        lbl_title.setStyleSheet("color: #00aaff; letter-spacing: -1px; border: none;") 
        header_layout.addWidget(lbl_title)
        
        self.lbl_status = QLabel("Preparado.")
        self.lbl_status.setStyleSheet("color: #888; border: none; margin-left: 15px;")
        header_layout.addWidget(self.lbl_status)
        
        header_layout.addStretch()

        self.btn_refresh = QPushButton("🔄")
        self.btn_refresh.setFixedSize(35, 35)
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.setToolTip("Escanear discos manualmente")
        self.btn_refresh.setStyleSheet("QPushButton { background-color: #333; color: white; border: 1px solid #555; border-radius: 5px; font-size: 16px; } QPushButton:hover { background-color: #444; border-color: #00aaff; }")
        self.btn_refresh.clicked.connect(self.load_disks)
        header_layout.addWidget(self.btn_refresh)

        self.btn_settings = QPushButton("⚙️")
        self.btn_settings.setFixedSize(35, 35)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setStyleSheet("QPushButton { background-color: #333; color: white; border: 1px solid #555; border-radius: 5px; font-size: 16px; } QPushButton:hover { background-color: #444; border-color: #00aaff; }")
        self.btn_settings.clicked.connect(self.open_settings)
        header_layout.addWidget(self.btn_settings)
        
        main_layout.addLayout(header_layout)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet("QSplitter::handle { background-color: transparent; height: 10px; }")

        self.top_panel = QFrame()
        self.top_panel.setStyleSheet("background-color: #252526; border-radius: 10px; border: 1px solid #3e3e42;")
        top_layout = QVBoxLayout(self.top_panel)
        top_layout.setContentsMargins(5, 5, 5, 5)

        self.table_volumes = QTableWidget()
        self.table_volumes.setColumnCount(7)
        self.table_volumes.setHorizontalHeaderLabels(["Volumen", "Nombre", "Formato", "Tipo", "Capacidad", "Espacio Libre", "% Libre"])
        self.table_volumes.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_volumes.verticalHeader().setVisible(False)
        self.table_volumes.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_volumes.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_volumes.setShowGrid(False)
        self.table_volumes.setStyleSheet("""
            QTableWidget { background-color: transparent; color: white; border: none; font-size: 12px; }
            QHeaderView::section { background-color: #333; color: #00aaff; font-weight: bold; padding: 5px; border: 1px solid #3e3e42; }
            QTableWidget::item { padding: 5px; border-bottom: 1px solid #3e3e42; }
            QTableWidget::item:selected { background-color: #004466; }
        """)
        top_layout.addWidget(self.table_volumes)
        
        self.bottom_panel = QScrollArea()
        self.bottom_panel.setWidgetResizable(True)
        self.bottom_panel.setStyleSheet("QScrollArea { border: 1px solid #3e3e42; background-color: #252526; border-radius: 10px; }")
        
        self.disks_container = QWidget()
        self.disks_container.setStyleSheet("background-color: transparent;")
        self.disks_layout = QVBoxLayout(self.disks_container)
        self.disks_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.disks_layout.setSpacing(15)
        
        self.bottom_panel.setWidget(self.disks_container)

        splitter.addWidget(self.top_panel)
        splitter.addWidget(self.bottom_panel)
        splitter.setSizes([int(self.height() * 0.4), int(self.height() * 0.6)])
        
        main_layout.addWidget(splitter)

    # ============================================================================
    # BANDEJA DEL SISTEMA Y EVENTO DE CIERRE
    # ============================================================================
    def setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.windowIcon())
        
        tray_menu = QMenu()
        tray_menu.setStyleSheet("""
            QMenu { background-color: #252526; color: white; border: 1px solid #444; }
            QMenu::item { padding: 8px 25px 8px 25px; }
            QMenu::item:selected { background-color: #00aaff; }
        """)
        
        show_action = QAction("Mostrar panel de control", self)
        show_action.triggered.connect(self.showNormal)
        
        quit_action = QAction("Salir de Korvex Disk Manager", self)
        quit_action.triggered.connect(self.force_quit)
        
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
        
        # Si haces doble click en el icono, se abre la app
        self.tray_icon.activated.connect(self.tray_icon_activated)

    def tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()

    def force_quit(self):
        """Sale completamente de la app, ignorando la bandeja"""
        logging.info("Cerrando la aplicación desde la bandeja del sistema.")
        QApplication.quit()

    def closeEvent(self, event: QCloseEvent):
        """Intercepta cuando el usuario le da a la X de la ventana"""
        ask_on_close = self.settings.value("ask_on_close", True, type=bool)
        minimize_to_tray = self.settings.value("minimize_to_tray", True, type=bool)

        if ask_on_close:
            dialog = CloseConfirmDialog(self)
            dialog.exec()
            
            if dialog.action:
                # Si marcó la casilla de recordar, actualizamos los settings
                if dialog.chk_remember.isChecked():
                    self.settings.setValue("ask_on_close", False)
                    self.settings.setValue("minimize_to_tray", dialog.action == "tray")
                    logging.info(f"Preferencia guardada: Minimizar a la bandeja = {dialog.action == 'tray'}")

                if dialog.action == "tray":
                    event.ignore()
                    self.hide()
                    self.tray_icon.showMessage("Korvex Disk Manager", "La aplicación sigue ejecutándose en segundo plano.", QSystemTrayIcon.MessageIcon.Information, 2000)
                elif dialog.action == "quit":
                    event.accept()
            else:
                # Si el usuario cierra el diálogo sin elegir nada (le da a la X del diálogo)
                event.ignore()
        else:
            if minimize_to_tray:
                event.ignore()
                self.hide()
            else:
                event.accept()

    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        dialog.exec()

    # ============================================================================
    # INTERCEPTACIÓN DE EVENTOS (HOT-PLUG)
    # ============================================================================
    def nativeEvent(self, eventType, message):
        try:
            try: is_win_msg = (bytes(eventType) == b"windows_generic_MSG")
            except: is_win_msg = False
                
            if is_win_msg:
                addr = int(message)
                if addr != 0:
                    msg = wintypes.MSG.from_address(addr)
                    if msg.message == 0x0219:
                        if msg.wParam in (0x8000, 0x8004):
                            if hasattr(self, 'lbl_status') and hasattr(self, 'btn_refresh'):
                                logging.info("Hardware USB/Disco detectado por Windows.")
                                self.lbl_status.setText("Cambio de hardware detectado. Actualizando...")
                                self.btn_refresh.setEnabled(False)
                                self.refresh_timer.start(1500)
        except Exception as e:
            logging.error(f"Error interceptando evento: {e}")
        return False, 0

    def load_disks(self):
        if self.reader_worker is not None and self.reader_worker.isRunning(): return
            
        self.btn_refresh.setEnabled(False)
        self.lbl_status.setText("Leyendo discos físicos...")
        
        self.clear_layout(self.disks_layout)
        self.lbl_loading = QLabel("⏳ Analizando discos y particiones...\nPor favor, espera.")
        self.lbl_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_loading.setStyleSheet("color: #888; font-size: 16px; font-weight: bold; margin-top: 50px;")
        self.disks_layout.addWidget(self.lbl_loading)
        
        self.table_volumes.setRowCount(0)
        
        self.reader_worker = DiskReaderWorker()
        self.reader_worker.finished.connect(self.populate_disks)
        self.reader_worker.error.connect(lambda e: self.lbl_status.setText(f"Error: {e}"))
        self.reader_worker.start()

    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    # ============================================================================
    # MENÚS CONTEXTUALES 
    # ============================================================================
    def confirm_action_spanish(self, title, message):
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(title)
        msg_box.setText(message)
        msg_box.setIcon(QMessageBox.Icon.Warning)
        
        msg_box.setWindowIcon(self.windowIcon())
        
        msg_box.setStyleSheet("""
            QMessageBox { background-color: #252526; }
            QLabel { color: white; font-size: 13px; }
            QPushButton { background-color: #444; color: white; padding: 6px 15px; border: 1px solid #555; border-radius: 3px; }
            QPushButton:hover { background-color: #555; }
        """)
        
        btn_si = msg_box.addButton("Sí, formatear", QMessageBox.ButtonRole.AcceptRole)
        btn_si.setStyleSheet("background-color: #aa0000; color: white; border: none; font-weight: bold; padding: 6px 15px; border-radius: 3px;")
        
        btn_no = msg_box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        
        msg_box.exec()
        return msg_box.clickedButton() == btn_si

    def show_disk_menu(self, pos, disk_num, is_system):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #252526; color: white; border: 1px solid #444; border-radius: 5px; }
            QMenu::item { padding: 8px 25px 8px 25px; }
            QMenu::item:selected { background-color: #00aaff; color: white; }
            QMenu::item:disabled { color: #666; background-color: transparent; }
        """)
        
        action_format = menu.addAction("Formatear Disco Completo...")
        action_clone = menu.addAction("Clonar Disco Completo...")
        
        if is_system:
            action_format.setEnabled(False)
            action_clone.setEnabled(False)
            
        selected = menu.exec(QCursor.pos())
        
        if selected == action_format:
            dialog = FormatDialog(f"el Disco {disk_num}", self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                format_settings = dialog.get_data()
                
                warning_text = f"ADVERTENCIA: Vas a formatear TODO el Disco {disk_num} ({format_settings['fs']}).\n\n¡Todos los datos y particiones se perderán irremediablemente!\n¿Estás seguro?"
                if self.confirm_action_spanish("Confirmación Crítica", warning_text):
                    logging.info(f"Usuario confirmó formateo completo del Disco {disk_num} con ajustes: {format_settings}")
                    self.lbl_status.setText(f"Preparando formateo del Disco {disk_num}...")

    def show_part_menu(self, pos, disk_num, drive_letter, is_system):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #252526; color: white; border: 1px solid #444; border-radius: 5px; }
            QMenu::item { padding: 8px 25px 8px 25px; }
            QMenu::item:selected { background-color: #00aaff; color: white; }
            QMenu::item:disabled { color: #666; background-color: transparent; }
        """)
        
        action_open = menu.addAction("Abrir explorador")
        menu.addSeparator()
        action_format = menu.addAction("Formatear partición...")
        action_clone = menu.addAction("Clonar partición...")
        action_delete = menu.addAction("Eliminar partición...")
        
        if not drive_letter or not str(drive_letter).strip():
            action_open.setEnabled(False)
        
        if is_system:
            action_format.setEnabled(False)
            action_delete.setEnabled(False)
            action_clone.setEnabled(False)
            
        selected = menu.exec(QCursor.pos())
        
        if selected == action_open:
            try:
                os.startfile(f"{drive_letter}:\\")
                logging.info(f"Explorador de Windows abierto en la unidad {drive_letter}:\\")
            except Exception as e:
                logging.error(f"Error al abrir el explorador para {drive_letter}: {e}")
                self.lbl_status.setText(f"No se pudo abrir {drive_letter}:")

        elif selected == action_format:
            target_name = f"la unidad {drive_letter}:" if drive_letter else "esta partición"
            
            dialog = FormatDialog(target_name, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                format_settings = dialog.get_data()
                
                warning_text = f"ADVERTENCIA: Dar formato a {target_name} ({format_settings['fs']}) borrará todos los datos en ella.\n\n¿Estás seguro?"
                if self.confirm_action_spanish("Confirmar Formateo", warning_text):
                    logging.info(f"Usuario confirmó formateo de {target_name} con ajustes: {format_settings}")
                    self.lbl_status.setText(f"Preparando formateo de {target_name}...")

    # ============================================================================
    # GENERADOR VISUAL
    # ============================================================================
    def populate_disks(self, data):
        self.lbl_status.setText("Discos cargados y analizados.")
        self.btn_refresh.setEnabled(True)
        self.clear_layout(self.disks_layout)
        self.table_volumes.setRowCount(0)
        
        disks = sorted(data.get('disks', []), key=lambda x: x.get('Number', 0))
        partitions = data.get('partitions', [])
        volumes = data.get('volumes', [])
        
        for disk in disks:
            disk_num = disk.get('Number')
            disk_parts = [p for p in partitions if p.get('DiskNumber') == disk_num]
            
            has_c_drive = any(p.get('DriveLetter') == 'C' for p in disk_parts)
            is_system_disk = disk.get('IsBoot') or disk.get('IsSystem') or has_c_drive
            
            disk_frame = QFrame()
            disk_frame.setStyleSheet("background-color: #333; border: 1px solid #555; border-radius: 5px;")
            
            disk_frame.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            disk_frame.customContextMenuRequested.connect(
                lambda pos, dn=disk_num, s=is_system_disk: self.show_disk_menu(pos, dn, s)
            )

            disk_layout = QHBoxLayout(disk_frame)
            
            info_layout = QVBoxLayout()
            disk_name = f"Disco {disk_num}\n{disk.get('FriendlyName', 'Desconocido')}"
            size_formatted = format_bytes(disk.get('Size'))
            status = "En línea" if disk.get('OperationalStatus') == 1 else "Desconocido"
            
            lbl_disk_name = QLabel(f"{disk_name}\n{size_formatted}\n{status}")
            lbl_disk_name.setFixedWidth(150)
            lbl_disk_name.setStyleSheet("color: white; border: none; font-weight: bold;")
            info_layout.addWidget(lbl_disk_name)
            
            if is_system_disk:
                lbl_system = QLabel("(Protegido 🔒)")
                lbl_system.setStyleSheet("color: #00aaff; border: none; font-weight: bold;")
                info_layout.addWidget(lbl_system)
                
            info_layout.addStretch()
            disk_layout.addLayout(info_layout)
            
            line = QFrame()
            line.setFrameShape(QFrame.Shape.VLine)
            line.setStyleSheet("background-color: #555;")
            disk_layout.addWidget(line)
            
            parts_container = QWidget()
            parts_layout = QHBoxLayout(parts_container)
            parts_layout.setContentsMargins(0, 0, 0, 0)
            parts_layout.setSpacing(5)
            
            disk_parts = sorted(disk_parts, key=lambda x: x.get('PartitionNumber', 0))
            
            for part in disk_parts:
                drive_letter = part.get('DriveLetter')
                part_size = part.get('Size')
                part_type = part.get('Type')
                
                vol = next((v for v in volumes if v.get('DriveLetter') == drive_letter), None)
                
                is_system_part = (drive_letter == 'C') or (part_type in ["System", "Recovery", "Reserved"]) or is_system_disk
                
                part_frame = QFrame()
                part_border = "#00aaff" if not is_system_part else "#555"
                part_frame.setStyleSheet(f"background-color: #1e1e1e; border: 1px solid {part_border}; border-radius: 3px;")
                
                part_frame.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                part_frame.customContextMenuRequested.connect(
                    lambda pos, dn=disk_num, dl=drive_letter, sp=is_system_part: self.show_part_menu(pos, dn, dl, sp)
                )

                part_layout = QVBoxLayout(part_frame)
                
                header_bar = QFrame()
                header_bar.setFixedHeight(5)
                header_bar.setStyleSheet(f"background-color: {part_border}; border: none;")
                part_layout.addWidget(header_bar)
                
                letter_str = f"({drive_letter}:)" if drive_letter and str(drive_letter).strip() else "(Oculta)"
                lbl_part_info = QLabel(f"{letter_str}\n{format_bytes(part_size)}\n{part_type}")
                lbl_part_info.setStyleSheet("color: white; border: none; font-size: 11px;")
                lbl_part_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
                part_layout.addWidget(lbl_part_info)
                
                if vol and drive_letter and str(drive_letter).strip():
                    row = self.table_volumes.rowCount()
                    self.table_volumes.insertRow(row)
                    
                    fs_label = vol.get('FileSystemLabel') or "Disco Local"
                    file_sys = vol.get('FileSystem') or "RAW"
                    free_bytes = vol.get('SizeRemaining', 0)
                    percent_free = int((free_bytes / part_size) * 100) if part_size > 0 else 0
                    
                    self.table_volumes.setItem(row, 0, QTableWidgetItem(f"{drive_letter}:"))
                    self.table_volumes.setItem(row, 1, QTableWidgetItem(fs_label))
                    self.table_volumes.setItem(row, 2, QTableWidgetItem(file_sys))
                    self.table_volumes.setItem(row, 3, QTableWidgetItem(part_type))
                    self.table_volumes.setItem(row, 4, QTableWidgetItem(format_bytes(part_size)))
                    self.table_volumes.setItem(row, 5, QTableWidgetItem(format_bytes(free_bytes)))
                    self.table_volumes.setItem(row, 6, QTableWidgetItem(f"{percent_free} %"))
                
                if vol and vol.get('SizeRemaining') is not None:
                    free_bytes = vol.get('SizeRemaining', 0)
                    used_bytes = part_size - free_bytes
                    percent_used = int((used_bytes / part_size) * 100) if part_size > 0 else 0
                    
                    progress = QProgressBar()
                    progress.setFixedHeight(8)
                    progress.setValue(percent_used)
                    progress.setTextVisible(False)
                    progress.setStyleSheet(f"QProgressBar {{ border: none; background-color: #444; border-radius: 4px; }} QProgressBar::chunk {{ background-color: {part_border}; border-radius: 4px; }}")
                    part_layout.addWidget(progress)
                
                stretch_factor = max(1, int(part_size / (1024**3)))
                parts_layout.addWidget(part_frame, stretch=stretch_factor)
            
            disk_layout.addWidget(parts_container, stretch=1)
            self.disks_layout.addWidget(disk_frame)


if __name__ == "__main__":
    setup_logging()
    
    if not is_admin():
        logging.info("Solicitando permisos de administrador...")
        if getattr(sys, 'frozen', False):
            script = sys.executable
            params = " ".join(sys.argv[1:])
        else:
            script = sys.executable
            params = f'"{os.path.abspath(sys.argv[0])}" ' + " ".join(sys.argv[1:])
        
        ctypes.windll.shell32.ShellExecuteW(None, "runas", script, params, None, 1)
        sys.exit()

    app = QApplication(sys.argv)
    
    app_icon = QIcon("KorvexLogo.ico")
    if app_icon.isNull():
        app_icon = QIcon("KorvexLogo.png")
    
    app.setWindowIcon(app_icon)
    
    window = DiskManagerWindow()
    window.show()
    sys.exit(app.exec())