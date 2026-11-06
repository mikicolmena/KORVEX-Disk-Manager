# -*- coding: utf-8 -*-
"""Utilidades dependientes del sistema operativo (sin Qt).

Este módulo también lo importa el helper de root en Linux, así que no debe
depender de PyQt ni de nada que no esté en la librería estándar.
"""
import os
import sys
import shutil
import subprocess

APP_NAME = "KORVEX Disk Manager"
APP_ID = "korvex-disk-manager"
APP_VERSION = "1.0.0"

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

# Instalación del paquete .deb
LINUX_INSTALL_DIR = f"/usr/lib/{APP_ID}"

# En Windows evita que se abra una consola por cada subproceso
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0


def base_dir() -> str:
    """Carpeta donde están los .py y los recursos (también con PyInstaller)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(name: str) -> str:
    return os.path.join(base_dir(), name)


def find_icon_path():
    """Icono de la app. Se usa siempre una ruta absoluta: al elevar con UAC el
    directorio de trabajo pasa a ser System32 y las rutas relativas fallan."""
    if IS_WINDOWS:
        names = ["KorvexLogo.ico", "KorvexLogo-256.png"]
    else:
        names = ["KorvexLogo-256.png", "KorvexLogo-128.png", "KorvexLogo.ico"]
    candidates = [resource_path(n) for n in names]
    if IS_LINUX:
        candidates += [
            f"/usr/share/icons/hicolor/256x256/apps/{APP_ID}.png",
            f"/usr/share/icons/hicolor/128x128/apps/{APP_ID}.png",
        ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


# ----------------------------------------------------------------------
# Rutas de configuración y logs
# ----------------------------------------------------------------------
def config_dir() -> str:
    if IS_WINDOWS:
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(root, APP_NAME)
    root = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(root, APP_ID)


def log_dir() -> str:
    if IS_WINDOWS:
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(root, APP_NAME, "logs")
    root = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(root, APP_ID, "logs")


def config_file() -> str:
    return os.path.join(config_dir(), "config.ini")


def legacy_config_file() -> str:
    """Ruta usada por las versiones anteriores."""
    return os.path.join(os.path.expanduser("~"), "Documents", "Korvex Disk Manager", "config.ini")


# ----------------------------------------------------------------------
# Permisos
# ----------------------------------------------------------------------
def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def relaunch_as_admin_windows() -> bool:
    """Relanza el proceso pidiendo UAC. Devuelve True si se lanzó."""
    import ctypes
    if getattr(sys, "frozen", False):
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        params = subprocess.list2cmdline([os.path.abspath(sys.argv[0])] + sys.argv[1:])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    return rc > 32  # <= 32 significa error o UAC rechazado


def helper_command(args):
    """Comando para ejecutar el helper de root en Linux mediante pkexec."""
    installed = os.path.join(LINUX_INSTALL_DIR, "korvex_helper.py")
    here = resource_path("korvex_helper.py")
    if os.path.abspath(here) == installed and os.access(installed, os.X_OK):
        # Instalado por el .deb: ejecutable propio con su política de polkit
        return ["pkexec", installed] + list(args)
    py = sys.executable or shutil.which("python3") or "/usr/bin/python3"
    return ["pkexec", py, here] + list(args)


def has_pkexec() -> bool:
    return shutil.which("pkexec") is not None


# ----------------------------------------------------------------------
# Abrir carpetas
# ----------------------------------------------------------------------
def open_path(path: str) -> bool:
    try:
        if IS_WINDOWS:
            os.startfile(path)  # noqa: (solo existe en Windows)
        else:
            opener = shutil.which("xdg-open") or shutil.which("gio")
            if not opener:
                return False
            args = [opener, path] if opener.endswith("xdg-open") else [opener, "open", path]
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------
# Registro de operación en curso
# ----------------------------------------------------------------------
# Antes de empezar cualquier operación que escribe en disco se guarda aquí qué
# se está haciendo. Si la app se cierra a la fuerza (administrador de tareas,
# corte de luz…), al volver a abrirla se avisa del estado en que quedó el disco.
def journal_path() -> str:
    return os.path.join(config_dir(), "operacion_en_curso.json")


def _journal_save(data: dict):
    import json
    os.makedirs(config_dir(), exist_ok=True)
    tmp = journal_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, journal_path())       # escritura atómica


def journal_write(data: dict):
    import time
    _journal_save(dict(data, started=time.strftime("%Y-%m-%d %H:%M:%S"), pid=os.getpid()))


def journal_read():
    import json
    try:
        with open(journal_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def journal_update(**kw):
    data = journal_read()
    if data is not None:
        data.update(kw)
        _journal_save(data)


def journal_clear():
    try:
        os.remove(journal_path())
    except OSError:
        pass


# ----------------------------------------------------------------------
# Impedir apagado / suspensión mientras se escribe en disco
# ----------------------------------------------------------------------
class PowerGuard:
    def __init__(self):
        self._proc = None
        self._hwnd = None
        self.active = False

    def start(self, reason: str, hwnd=None):
        if self.active:
            return
        self.active = True
        if IS_WINDOWS:
            try:
                import ctypes
                ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_AWAYMODE_REQUIRED = 0x80000000, 0x1, 0x40
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)
                if hwnd:
                    self._hwnd = int(hwnd)
                    ctypes.windll.user32.ShutdownBlockReasonCreate(ctypes.c_void_p(self._hwnd),
                                                                   ctypes.c_wchar_p(reason))
            except Exception:
                pass
            return
        tool = shutil.which("systemd-inhibit")
        if not tool:
            return
        what = "shutdown:sleep:idle:handle-power-key:handle-suspend-key:handle-hibernate-key:handle-lid-switch"
        for mode in ("block", "delay"):
            try:
                p = subprocess.Popen([tool, f"--what={what}", f"--who={APP_NAME}", f"--why={reason}",
                                      f"--mode={mode}", "sleep", "infinity"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    p.wait(timeout=0.5)   # si termina enseguida es que no se concedió
                    continue
                except subprocess.TimeoutExpired:
                    self._proc = p
                    return
            except OSError:
                return

    def stop(self):
        if not self.active:
            return
        self.active = False
        if IS_WINDOWS:
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
                if self._hwnd:
                    ctypes.windll.user32.ShutdownBlockReasonDestroy(ctypes.c_void_p(self._hwnd))
            except Exception:
                pass
            self._hwnd = None
            return
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                pass
            self._proc = None


# ----------------------------------------------------------------------
# Icono en el escritorio cuando se ejecuta desde el código fuente (Linux)
# ----------------------------------------------------------------------
def ensure_user_desktop_entry():
    """En Wayland el icono de la barra de tareas sale del archivo .desktop. Si la
    app no está instalada con el .deb, se crea uno para el usuario."""
    if not IS_LINUX or os.path.exists(f"/usr/share/applications/{APP_ID}.desktop"):
        return
    try:
        data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        icon_src = resource_path("KorvexLogo-256.png")
        icon_dir = os.path.join(data_home, "icons", "hicolor", "256x256", "apps")
        app_dir = os.path.join(data_home, "applications")
        os.makedirs(icon_dir, exist_ok=True)
        os.makedirs(app_dir, exist_ok=True)
        icon_dst = os.path.join(icon_dir, f"{APP_ID}.png")
        if os.path.exists(icon_src) and not os.path.exists(icon_dst):
            shutil.copy2(icon_src, icon_dst)
        main_py = resource_path("main.py")
        content = (f"[Desktop Entry]\nType=Application\nName={APP_NAME}\n"
                   f"Comment=Ver, formatear y clonar discos\n"
                   f"Exec=\"{sys.executable}\" \"{main_py}\"\nIcon={APP_ID}\nTerminal=false\n"
                   f"Categories=System;Utility;\nStartupWMClass={APP_NAME}\n")
        path = os.path.join(app_dir, f"{APP_ID}.desktop")
        old = open(path).read() if os.path.exists(path) else ""
        if old != content:
            with open(path, "w") as f:
                f.write(content)
    except OSError:
        pass
