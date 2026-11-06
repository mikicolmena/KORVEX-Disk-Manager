#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KORVEX Disk Manager · punto de entrada (Windows y Linux)."""
import os
import sys
import glob
import shutil
import logging
from datetime import datetime

from platform_utils import (IS_WINDOWS, IS_LINUX, APP_NAME, APP_ID, APP_VERSION, config_dir,
                            config_file, legacy_config_file, log_dir, find_icon_path, is_admin,
                            relaunch_as_admin_windows, ensure_user_desktop_entry)

MAX_LOGS = 20


def setup_logging():
    path = log_dir()
    os.makedirs(path, exist_ok=True)
    # Conservar solo los últimos MAX_LOGS registros
    for old in sorted(glob.glob(os.path.join(path, "*.log")))[:-(MAX_LOGS - 1)]:
        try:
            os.remove(old)
        except OSError:
            pass
    log_file = os.path.join(path, datetime.now().strftime("%Y-%m-%d_%H-%M-%S.log"))
    handlers = [logging.FileHandler(log_file, encoding="utf-8")]
    if sys.stdout is not None:      # En el .exe sin consola stdout es None
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        handlers=handlers)

    def handle_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.critical("Excepción no controlada:", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = handle_exception
    logging.info("--- INICIANDO %s %s en %s (%s) ---", APP_NAME, APP_VERSION, sys.platform,
                 os.path.basename(log_file))


def migrate_legacy_config():
    """Copia la config de la ruta antigua (~/Documents/Korvex Disk Manager)."""
    old, new = legacy_config_file(), config_file()
    if os.path.exists(old) and not os.path.exists(new):
        try:
            os.makedirs(config_dir(), exist_ok=True)
            shutil.copy2(old, new)
            logging.info("Configuración migrada desde %s", old)
        except OSError as e:
            logging.warning("No se pudo migrar la configuración antigua: %s", e)


def main():
    setup_logging()

    if IS_WINDOWS:
        # Leer discos físicos, formatear y clonar requiere administrador
        if not is_admin() and "--no-elevate" not in sys.argv:
            logging.info("Solicitando elevación de permisos (UAC)…")
            if relaunch_as_admin_windows():
                sys.exit(0)
            logging.warning("UAC rechazado: se continúa sin permisos de administrador")
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelId("korvex.diskmanager.v1")
        except Exception:
            pass

    from PyQt6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon
    from PyQt6.QtGui import QIcon
    from PyQt6.QtCore import Qt, QLockFile, QTimer
    from style import STYLESHEET
    from ui import DiskManagerWindow

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    if IS_LINUX:
        ensure_user_desktop_entry()
        # En Wayland el icono de la barra de tareas sale del .desktop con este nombre
        app.setDesktopFileName(APP_ID)
    icon_path = find_icon_path()
    icon = QIcon(icon_path) if icon_path else QIcon()
    app.setWindowIcon(icon)
    app.setStyleSheet(STYLESHEET)
    # Con bandeja, cerrar la ventana no debe terminar la app (se sale de forma explícita)
    app.setQuitOnLastWindowClosed(not QSystemTrayIcon.isSystemTrayAvailable())

    # Una sola instancia: dos ventanas podrían lanzar operaciones a la vez sobre el mismo disco
    os.makedirs(config_dir(), exist_ok=True)
    lock = QLockFile(os.path.join(config_dir(), "instance.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(200):
        QMessageBox.information(None, APP_NAME, f"{APP_NAME} ya está abierto.")
        sys.exit(0)

    if IS_WINDOWS and not is_admin():
        QMessageBox.warning(None, APP_NAME,
                            "Se está ejecutando sin permisos de administrador.\n"
                            "Podrás ver los discos, pero no modificarlos.")
    if IS_LINUX and not shutil.which("lsblk"):
        QMessageBox.critical(None, APP_NAME, "No se encontró 'lsblk' (paquete util-linux).")
        sys.exit(1)

    migrate_legacy_config()
    window = DiskManagerWindow(config_file(), icon)
    app.aboutToQuit.connect(window.power.stop)

    if not IS_WINDOWS:
        # SIGTERM (p. ej. al cerrar sesión): no salir mientras se escribe en un disco
        import signal

        def on_term(*_):
            if window.busy_writing():
                logging.warning("SIGTERM ignorado: hay una operación escribiendo en disco")
                return
            window._quitting = True
            app.quit()
        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGHUP, on_term)
        # Dar a Python la oportunidad de atender señales mientras corre el bucle de Qt
        tick = QTimer(); tick.timeout.connect(lambda: None); tick.start(500)

    window.show()
    rc = app.exec()
    lock.unlock()
    sys.exit(rc)


if __name__ == "__main__":
    main()
