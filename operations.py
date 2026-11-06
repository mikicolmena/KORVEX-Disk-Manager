# -*- coding: utf-8 -*-
"""Capa Qt común para todas las operaciones de disco.

Linux:   cada operación se ejecuta en korvex_helper.py con pkexec (root).
Windows: cada operación se ejecuta en win_ops.py dentro de un hilo (la app
         ya es administrador).
"""
import json
import shutil
import logging
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from platform_utils import IS_WINDOWS, journal_write, journal_clear, journal_read
import rawio as R


# ----------------------------------------------------------------------
# Catálogo de operaciones
#   writes:     escribe en disco (bloquea el cierre, apagado, suspensión…)
#   cancelable: se puede interrumpir sin dejar nada a medias de forma peligrosa
# ----------------------------------------------------------------------
OPS = {
    "format":        dict(title="Formatear",               writes=True,  cancelable=False),
    "label":         dict(title="Cambiar etiqueta",        writes=True,  cancelable=False),
    "letter":        dict(title="Cambiar letra",           writes=True,  cancelable=False),
    "convert":       dict(title="Convertir a NTFS",        writes=True,  cancelable=False),
    "fsck":          dict(title="Comprobar / reparar",     writes=True,  cancelable=False),
    "part_create":   dict(title="Crear partición",         writes=True,  cancelable=False),
    "part_delete":   dict(title="Eliminar partición",      writes=True,  cancelable=False),
    "part_resize":   dict(title="Redimensionar partición", writes=True,  cancelable=False),
    "init":          dict(title="Inicializar disco",       writes=True,  cancelable=False),
    "clone":         dict(title="Clonar disco",            writes=True,  cancelable=True),
    "erase":         dict(title="Borrado seguro",          writes=True,  cancelable=True),
    "image_create":  dict(title="Crear imagen",            writes=True,  cancelable=True),
    "image_restore": dict(title="Restaurar imagen",        writes=True,  cancelable=True),
    "smart":         dict(title="Salud SMART",             writes=False, cancelable=True),
    "bench":         dict(title="Test de velocidad",       writes=False, cancelable=True),
    "scan":          dict(title="Escaneo de superficie",   writes=False, cancelable=True),
}

PHASES = {"copy": "Copiando…", "verify": "Verificando…", "wipe": "Borrando…", "scan": "Leyendo superficie…",
          "seq": "Lectura secuencial…", "rand": "Lectura aleatoria 4K…", "check": "Comprobando la imagen…"}


# ----------------------------------------------------------------------
# Sistemas de archivos
# ----------------------------------------------------------------------
if IS_WINDOWS:
    FILESYSTEMS = {"NTFS": 32, "exFAT": 11, "FAT32": 11}
else:
    FILESYSTEMS = {"ext4": 16, "NTFS": 32, "exFAT": 11, "FAT32": 11}
FS_ARG = {"ext4": "ext4", "NTFS": "ntfs", "exFAT": "exfat", "FAT32": "fat32"}
LINUX_MKFS = {"ext4": "mkfs.ext4", "NTFS": "mkfs.ntfs", "exFAT": "mkfs.exfat", "FAT32": "mkfs.vfat"}
FS_TIPS = {
    "ext4": "Nativo de Linux. Windows no lo lee sin programas extra.",
    "NTFS": "Nativo de Windows. Linux lo lee y escribe (ntfs-3g/ntfs3).",
    "exFAT": "Ideal para memorias USB: compatible con Windows, Linux y macOS.",
    "FAT32": "Máxima compatibilidad, pero archivos de 4 GB como máximo"
             + (" y volúmenes de 32 GB como máximo en Windows." if IS_WINDOWS else "."),
}


def _which(tool):
    return shutil.which(tool) or shutil.which(f"/usr/sbin/{tool}") or shutil.which(f"/sbin/{tool}")


def available_filesystems():
    """En Linux solo se ofrecen los que tienen su mkfs instalado."""
    if IS_WINDOWS:
        return list(FILESYSTEMS)
    return [fs for fs in FILESYSTEMS if _which(LINUX_MKFS[fs])]


def clean_label(label: str, fs: str) -> str:
    bad = "\"'\\/*?<>|:" if fs != "ext4" else "\"'\\/"
    label = "".join(ch for ch in label if ch.isprintable() and ch not in bad)
    return label[:FILESYSTEMS.get(fs, 11)]


# ----------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------
class _Ctx:
    """Contexto que reciben las operaciones de Windows."""

    def __init__(self, worker):
        self.w = worker
        self.meter = R.RateMeter()
        self.phase = None

    def progress(self, phase, done, total):
        if phase != self.phase:
            self.phase = phase
            self.meter.reset()
        if self.meter.should_emit(done, total):
            speed, eta = self.meter.stats(done, total)
            self.w.progress.emit(phase, int(done), int(total), float(speed), float(eta))

    def log(self, msg):
        logging.info("op: %s", msg)
        self.w.log.emit(msg)

    def cancelled(self):
        return self.w._cancel.is_set()


class OpWorker(QThread):
    progress = pyqtSignal(str, object, object, float, float)   # fase, hecho, total, B/s, ETA
    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str, object)                    # ok, mensaje, datos

    def __init__(self, op: str, params: dict, description: str):
        super().__init__()
        self.op = op
        self.params = params
        self.description = description
        self.info = OPS[op]
        self._cancel = threading.Event()
        self._helper = None

    @property
    def cancelable(self):
        return self.info["cancelable"]

    @property
    def writes(self):
        return self.info["writes"]

    def cancel(self):
        if not self.cancelable:
            return False
        self._cancel.set()
        if self._helper:
            self._helper.cancel()
        return True

    def run(self):
        logging.info("Operación %s: %s · %s", self.op, self.description, json.dumps(self.params, ensure_ascii=False))
        if self.writes:
            journal_write({"op": self.op, "title": self.info["title"], "description": self.description,
                           "params": self.params})
        ok, msg, data = False, "", {}
        try:
            ok, msg, data = self._run_windows() if IS_WINDOWS else self._run_linux()
        except Exception as e:
            logging.exception("Excepción en la operación")
            ok, msg, data = False, str(e), {}
        finally:
            # Solo se borra el registro si la operación ha terminado (bien o mal) de forma
            # controlada. Si la app muere, el registro queda y se avisa al volver a abrirla.
            if self.writes:
                j = journal_read() or {}
                if j.get("offline_disk") is None:
                    journal_clear()
                else:
                    j_note = "El disco quedó offline al terminar"
                    logging.warning("%s: %s", j_note, j.get("offline_disk"))
        (logging.info if ok else logging.error)("Resultado %s: %s", self.op, msg)
        self.finished.emit(ok, msg, data or {})

    def _run_linux(self):
        from helper_client import HelperProcess
        self._helper = HelperProcess([self.op, json.dumps(self.params, ensure_ascii=False)])
        if self._cancel.is_set():
            return False, "Operación cancelada antes de empezar.", {"cancelled": True}

        def on_event(ev):
            t = ev.get("type")
            if t == "progress":
                self.progress.emit(ev.get("phase", ""), int(ev.get("done", 0)), int(ev.get("total", 1)),
                                   float(ev.get("speed", 0)), float(ev.get("eta", 0)))
            elif t == "log":
                logging.info("helper: %s", ev.get("msg", ""))
                self.log.emit(ev.get("msg", ""))

        return self._helper.run(on_event)

    def _run_windows(self):
        import win_ops as W
        fn = W.OPS.get(self.op)
        if fn is None:
            return False, "Operación no disponible en Windows.", {}
        ctx = _Ctx(self)
        try:
            msg, data = fn(self.params, ctx)
            return data.pop("success", True), msg, data
        except R.OpCancelled:
            extra = "" if self.op in ("smart", "bench", "scan", "image_create") else (
                " El disco destino ha quedado incompleto y no es utilizable hasta que se vuelva "
                "a escribir o formatear.")
            return False, "Operación cancelada." + extra, {"cancelled": True}
        except (W.Fail, R.OpError) as e:
            return False, str(e), {}
