# -*- coding: utf-8 -*-
"""Actualizaciones desde las releases de GitHub (sin Qt).

Flujo:
  1. check_latest()   consulta la última release y la compara con APP_VERSION.
  2. download()       descarga el instalador de este sistema + SHA256SUMS.txt + su firma.
  3. verify()         comprueba la firma Ed25519 de SHA256SUMS.txt con las claves públicas
                      de abajo y después el SHA-256 del instalador.
  4. Windows: apply_windows() sustituye el .exe en marcha y relanza.
     Linux (.deb): el helper de root vuelve a verificar todo e instala con apt.

La firma la genera GitHub Actions con el secreto UPDATE_SIGNING_KEY (ver RELEASING.md).
Lo usa también el helper de root, así que no puede depender de Qt.
"""
import os
import re
import sys
import json
import base64
import hashlib
import urllib.request

from platform_utils import (APP_VERSION, APP_ID, IS_WINDOWS, IS_LINUX, LINUX_INSTALL_DIR,
                            base_dir)

GITHUB_REPO = "mikicolmena/KORVEX-Disk-Manager"
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
USER_AGENT = f"KORVEX-Disk-Manager/{APP_VERSION}"

SUMS_NAME = "SHA256SUMS.txt"
SIG_NAME = "SHA256SUMS.txt.sig"
WINDOWS_ASSET = "KORVEX-Disk-Manager.exe"
DEB_PACKAGE = APP_ID

# Claves públicas Ed25519 (32 bytes en base64) que pueden firmar releases.
# Se admite más de una para poder rotar la clave sin dejar tiradas las versiones instaladas:
# añade la nueva aquí, publica una versión firmada con la ANTIGUA y a partir de la siguiente
# firma ya con la nueva.
UPDATE_PUBLIC_KEYS = [
    "aTzPwD4ye1xLZiW719TT2luRCFzKzP6nt62bvW5SrfE=",
]

CHUNK = 256 * 1024


class UpdateError(Exception):
    pass


# ----------------------------------------------------------------------
# Versiones
# ----------------------------------------------------------------------
def parse_version(v: str):
    m = re.match(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(v or ""))
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def is_newer(remote: str, local: str = APP_VERSION) -> bool:
    r, l = parse_version(remote), parse_version(local)
    return bool(r and l and r > l)


# ----------------------------------------------------------------------
# Tipo de instalación
# ----------------------------------------------------------------------
def install_kind() -> str:
    """'exe' (Windows, PyInstaller) · 'deb' (paquete instalado) · 'source' (código fuente)."""
    if IS_WINDOWS and getattr(sys, "frozen", False):
        return "exe"
    if IS_LINUX and os.path.abspath(base_dir()) == LINUX_INSTALL_DIR:
        return "deb"
    return "source"


def asset_name(version: str):
    kind = install_kind()
    if kind == "exe":
        return WINDOWS_ASSET
    if kind == "deb":
        return f"{DEB_PACKAGE}_{version}_all.deb"
    return None


# ----------------------------------------------------------------------
# Red
# ----------------------------------------------------------------------
def _request(url, accept=None):
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30)


def check_latest() -> dict:
    """Devuelve info de la última release. Lanza UpdateError si no se puede consultar."""
    try:
        with _request(API_LATEST, "application/vnd.github+json") as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise UpdateError(f"No se pudo consultar GitHub: {e}")
    tag = str(data.get("tag_name") or "")
    version = tag.lstrip("v")
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets") or []}
    name = asset_name(version)
    return {
        "version": version, "tag": tag, "newer": is_newer(version),
        "notes": str(data.get("body") or ""), "url": data.get("html_url") or RELEASES_PAGE,
        "kind": install_kind(), "asset": name,
        "asset_url": assets.get(name) if name else None,
        "sums_url": assets.get(SUMS_NAME), "sig_url": assets.get(SIG_NAME),
    }


def _download_to(url, path, progress=None, cancelled=None, limit=None):
    tmp = path + ".part"
    try:
        with _request(url) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancelled and cancelled():
                    raise UpdateError("Descarga cancelada.")
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                done += len(chunk)
                if limit and done > limit:
                    raise UpdateError("El archivo descargado es demasiado grande.")
                f.write(chunk)
                if progress:
                    progress(done, total)
        os.replace(tmp, path)
    except UpdateError:
        _rm(tmp)
        raise
    except Exception as e:
        _rm(tmp)
        raise UpdateError(f"Error al descargar {os.path.basename(path)}: {e}")


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


def download_dir() -> str:
    """Carpeta de descargas (sumas, firma y, en Linux, el .deb). Se vacía al arrancar."""
    if IS_WINDOWS:
        import tempfile
        return os.path.join(tempfile.gettempdir(), f"{APP_ID}-updates")
    root = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(root, APP_ID, "updates")


def _new_exe_path() -> str:
    # Junto al .exe: así el cambio de nombre final es atómico (mismo volumen)
    return os.path.abspath(sys.executable) + ".new"


def download(info: dict, progress=None, cancelled=None) -> dict:
    """Descarga instalador, sumas y firma, y lo verifica todo. Devuelve las rutas."""
    if not info.get("asset_url"):
        raise UpdateError(f"La release {info.get('tag')} no incluye {info.get('asset') or 'un instalador para este sistema'}.")
    if not info.get("sums_url") or not info.get("sig_url"):
        raise UpdateError(f"La release {info.get('tag')} no está firmada ({SUMS_NAME} / {SIG_NAME}). "
                          "Por seguridad no se instala automáticamente.")
    folder = download_dir()
    os.makedirs(folder, exist_ok=True)
    sums = os.path.join(folder, f"{APP_ID}-{info['version']}-{SUMS_NAME}")
    sig = sums + ".sig"
    _download_to(info["sums_url"], sums, limit=64 * 1024)
    _download_to(info["sig_url"], sig, limit=1024)
    # Antes de bajar el archivo grande: firma válida y versión firmada = la anunciada y más nueva
    expected, signed_ver = verified_sha256(sums, sig, info["asset"])
    if signed_ver != info["version"] or not is_newer(signed_ver):
        raise UpdateError(f"La versión firmada ({signed_ver or '?'}) no coincide con la release "
                          f"({info['version']}) o no es más nueva que la instalada.")
    target = _new_exe_path() if info["kind"] == "exe" else os.path.join(folder, info["asset"])
    _download_to(info["asset_url"], target, progress, cancelled, limit=1024 ** 3)
    got = sha256_file(target)
    if got != expected:
        _rm(target)
        raise UpdateError("El archivo descargado no coincide con su suma SHA-256 firmada. "
                          "No se ha instalado nada.")
    return {"file": target, "sums": sums, "sig": sig, "sha256": expected}


# ----------------------------------------------------------------------
# Verificación
# ----------------------------------------------------------------------
def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_signature(data: bytes, signature: bytes) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        raise UpdateError("Falta la librería 'cryptography' para comprobar la firma "
                          "(paquete python3-cryptography).")
    for k in UPDATE_PUBLIC_KEYS:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(k)).verify(signature, data)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def verified_sha256(sums_path, sig_path, asset) -> str:
    """Comprueba la firma de SHA256SUMS.txt y devuelve (suma esperada de `asset`, versión).

    La versión sale del nombre del .deb listado en el propio archivo firmado: así una
    release antigua (bien firmada) no se puede hacer pasar por una nueva."""
    with open(sums_path, "rb") as f:
        sums = f.read()
    with open(sig_path, "rb") as f:
        sig_raw = f.read().strip()
    try:
        sig = base64.b64decode(sig_raw, validate=True) if len(sig_raw) != 64 else sig_raw
    except ValueError:
        sig = sig_raw
    if not verify_signature(sums, sig):
        raise UpdateError("La firma de la actualización no es válida. No se ha instalado nada.")
    sha, version = None, None
    for line in sums.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            continue
        name = parts[1].lstrip("*")
        m = re.fullmatch(re.escape(DEB_PACKAGE) + r"_(\d+\.\d+\.\d+)_all\.deb", name)
        if m:
            version = m.group(1)
        if name == asset:
            sha = parts[0]
    if sha is None:
        raise UpdateError(f"{asset} no aparece en {SUMS_NAME}.")
    return sha, version


# ----------------------------------------------------------------------
# Windows: sustituir el .exe en marcha
# ----------------------------------------------------------------------
def old_exe_path() -> str:
    return os.path.abspath(sys.executable) + ".old"


def apply_windows(new_file: str) -> str:
    """Un .exe en ejecución no se puede sobrescribir, pero sí renombrar."""
    exe = os.path.abspath(sys.executable)
    old = old_exe_path()
    _rm(old)
    os.replace(exe, old)
    try:
        os.replace(new_file, exe)
    except OSError:
        os.replace(old, exe)        # deshacer: que la app siga funcionando
        raise
    return exe


def cleanup_after_update():
    """Se llama al arrancar: borra el .exe anterior y descargas viejas o a medias."""
    if IS_WINDOWS and getattr(sys, "frozen", False):
        for p in (old_exe_path(), _new_exe_path(), _new_exe_path() + ".part"):
            _rm(p)
    folder = download_dir()
    if os.path.isdir(folder):
        for n in os.listdir(folder):
            _rm(os.path.join(folder, n))


def restart_env() -> dict:
    """Entorno para relanzar la app. Un .exe de PyInstaller (onefile) que se lanza a sí
    mismo debe pedir un arranque limpio; si no, el hijo intentaría reutilizar la carpeta
    temporal del padre, que se borra al cerrarse."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        for k in list(env):
            if k == "_MEIPASS2" or k.startswith("_PYI_"):
                env.pop(k, None)
    return env
