# -*- coding: utf-8 -*-
"""Lanza korvex_helper.py con pkexec (Linux) y traduce su salida JSON."""
import json
import logging
import subprocess
import threading

from platform_utils import helper_command, has_pkexec

PKEXEC_DISMISSED = (126, 127)


class HelperProcess:
    def __init__(self, args):
        self.args = list(args)
        self.proc = None
        self._lock = threading.Lock()
        self._cancelled = False

    def run(self, on_event):
        """Ejecuta el helper y llama on_event(dict) por cada línea.
        Devuelve (ok, mensaje, datos)."""
        if not has_pkexec():
            return False, ("No se encontró pkexec. Instala polkit (paquete 'pkexec' o "
                           "'policykit-1') para poder usar esta función."), {}
        cmd = helper_command(self.args)
        logging.info("Helper: %s", " ".join(cmd[:3] + [self.args[0] if self.args else ""]))
        with self._lock:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True, bufsize=1,
                                         encoding="utf-8", errors="replace")
        if self._cancelled:
            self._send_cancel(self.proc)
        result = None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                logging.info("helper: %s", line)
                continue
            if ev.get("type") == "result":
                result = ev
            else:
                on_event(ev)
        stderr = self.proc.stderr.read().strip()
        rc = self.proc.wait()
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        if stderr:
            logging.warning("helper stderr: %s", stderr)
        if result is not None:
            data = result.get("data") or {}
            if result.get("cancelled"):
                data["cancelled"] = True
            return bool(result.get("ok")), str(result.get("msg", "")), data
        if rc in PKEXEC_DISMISSED:
            return False, "Autenticación cancelada o denegada. No se ha modificado nada.", {"auth": False}
        return False, f"El proceso privilegiado terminó inesperadamente (código {rc}).\n{stderr}", {}

    def cancel(self):
        with self._lock:
            self._cancelled = True
            p = self.proc
        self._send_cancel(p)

    @staticmethod
    def _send_cancel(p):
        if p and p.poll() is None:
            try:
                p.stdin.write("cancel\n")
                p.stdin.flush()
                p.stdin.close()
            except Exception:
                pass
