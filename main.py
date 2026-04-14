import sys
import os
import ctypes
import logging
from datetime import datetime
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

# Importamos la ventana principal desde nuestro nuevo módulo ui
from ui import DiskManagerWindow

def setup_logging():
    path_base = os.path.join(os.path.expanduser("~"), "Documents", "Korvex Disk Manager")
    logs_path = os.path.join(path_base, "logs")
    if not os.path.exists(logs_path): 
        os.makedirs(logs_path)
        
    log_file = os.path.join(logs_path, datetime.now().strftime("%Y-%m-%d_%H-%M-%S.log"))
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s',
                        handlers=[logging.FileHandler(log_file, encoding='utf-8'), logging.StreamHandler(sys.stdout)])
    
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.critical("Excepción no controlada:", exc_info=(exc_type, exc_value, exc_traceback))
        
    sys.excepthook = handle_exception
    logging.info(f"--- INICIANDO KORVEX DISK MANAGER ({os.path.basename(log_file)}) ---")

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

if __name__ == "__main__":
    setup_logging()
    
    # 1. Asegurar Permisos de Administrador (VITAL para leer discos físicos y formatear)
    if not is_admin():
        logging.info("Solicitando elevación de permisos (UAC)...")
        if getattr(sys, 'frozen', False):
            script = sys.executable
            params = " ".join(sys.argv[1:])
        else:
            script = sys.executable
            params = f'"{os.path.abspath(sys.argv[0])}" ' + " ".join(sys.argv[1:])
        
        ctypes.windll.shell32.ShellExecuteW(None, "runas", script, params, None, 1)
        sys.exit()

    # 2. Configurar la App ID para Windows (Opcional pero recomendable para la barra de tareas)
    myappid = 'korvex.diskmanager.v1'
    try: 
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelId(myappid)
    except: 
        pass

    # 3. Inicializar App y cargar Iconos
    app = QApplication(sys.argv)
    
    app_icon = QIcon("KorvexLogo.ico")
    if app_icon.isNull():
        app_icon = QIcon("KorvexLogo.png")
    app.setWindowIcon(app_icon)
    
    # 4. Lanzar la Interfaz desde ui.py
    path_config = os.path.join(os.path.expanduser("~"), "Documents", "Korvex Disk Manager", "config.ini")
    window = DiskManagerWindow(path_config)
    window.show()
    
    sys.exit(app.exec())