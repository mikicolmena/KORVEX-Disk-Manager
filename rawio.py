# -*- coding: utf-8 -*-
"""Entrada/salida en bruto sobre discos, sin Qt.

Lo usan las operaciones de Windows (la app ya es administrador) y el helper
de root en Linux. Las comprobaciones de seguridad NO están aquí: quien llame
debe haber validado antes origen y destino con las funciones de disks.py.

Todas las funciones largas aceptan:
  progress(fase, hecho, total)   fases: copy, verify, wipe, scan, seq, rand
  cancelled() -> bool            se consulta entre bloques
"""
import os
import sys
import time
import mmap
import random
import hashlib

CHUNK = 4 * 1024 * 1024            # 4 MiB, múltiplo de cualquier tamaño de sector
SYNC_EVERY = 128 * 1024 * 1024     # Linux: vacía caché cada 128 MiB (progreso realista)
BLKFLSBUF = 0x1261                 # ioctl Linux: descartar buffers del dispositivo
MAX_BAD = 2000                     # tope de sectores defectuosos que se listan

IS_WINDOWS = sys.platform.startswith("win")
O_BINARY = getattr(os, "O_BINARY", 0)


class OpCancelled(Exception):
    pass


class OpError(Exception):
    pass


# Nombres antiguos
CloneCancelled = OpCancelled
CloneError = OpError


def device_size(path: str):
    """Tamaño real leyendo hasta el final (None si no se puede)."""
    try:
        fd = os.open(path, os.O_RDONLY | O_BINARY)
        try:
            return os.lseek(fd, 0, os.SEEK_END)
        finally:
            os.close(fd)
    except OSError:
        return None


def _open_read(path):
    return os.open(path, os.O_RDONLY | O_BINARY)


def open_device_exclusive(path):
    """Abre un disco para escribir. En Linux, O_EXCL sobre un dispositivo de
    bloques = apertura exclusiva: falla con EBUSY si algo lo tiene montado o
    en uso, y mientras siga abierto nadie (p. ej. el automontaje) puede usarlo."""
    try:
        if IS_WINDOWS:
            return os.open(path, os.O_RDWR | O_BINARY)
        return os.open(path, os.O_RDWR | os.O_EXCL)
    except OSError as e:
        raise OpError(f"No se pudo abrir el disco en exclusiva ({e.strerror}). ¿Sigue montado o en uso?")


def _open_new_file(path):
    """Crea un archivo nuevo. O_EXCL: nunca sobrescribe ni sigue enlaces simbólicos."""
    try:
        return os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | O_BINARY, 0o644)
    except FileExistsError:
        raise OpError(f"El archivo ya existe: {path}")
    except OSError as e:
        raise OpError(f"No se pudo crear {path}: {e.strerror}")


def _read_exact(fd, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = os.read(fd, n - len(buf))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OpError("El destino no acepta más datos.")
        view = view[written:]


def _drop_cache(fd):
    """Descarta la caché para que la verificación lea el disco real."""
    try:
        os.fsync(fd)
    except OSError:
        pass
    if IS_WINDOWS:
        return
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except (AttributeError, OSError):
        pass
    try:
        import fcntl
        fcntl.ioctl(fd, BLKFLSBUF, 0)
    except Exception:
        pass


def _check(cancelled):
    if cancelled and cancelled():
        raise OpCancelled()


# ======================================================================
# COPIA GENÉRICA (clonar disco, crear imagen, restaurar imagen / ISO)
# ======================================================================
def copy_data(src, dst, total, dst_kind="device", verify=True, progress=None, cancelled=None):
    """Copia `total` bytes de src a dst y opcionalmente verifica releyendo.

    dst_kind: "device" (se abre en exclusiva) o "file" (archivo nuevo).
    Devuelve dict con sha_src, sha_dst (si se verificó), ok y first_diff.
    """
    if total <= 0:
        raise OpError("Tamaño de origen no válido.")
    fs = _open_read(src)
    try:
        fd = open_device_exclusive(dst) if dst_kind == "device" else _open_new_file(dst)
    except OpError:
        os.close(fs)
        raise
    try:
        hs = hashlib.sha256()
        done = since_sync = 0
        while done < total:
            _check(cancelled)
            n = min(CHUNK, total - done)
            try:
                data = _read_exact(fs, n)
            except OSError as e:
                raise OpError(f"Error de lectura en el origen en el byte {done}: {e.strerror}")
            if len(data) != n:
                raise OpError(f"El origen terminó antes de lo esperado (byte {done + len(data)}).")
            hs.update(data)
            try:
                _write_all(fd, data)
            except OSError as e:
                raise OpError(f"Error de escritura en el destino en el byte {done}: {e.strerror}")
            done += n
            since_sync += n
            if not IS_WINDOWS and since_sync >= SYNC_EVERY:
                os.fsync(fd)
                since_sync = 0
            if progress:
                progress("copy", done, total)
        os.fsync(fd)
        result = {"ok": True, "sha_src": hs.hexdigest(), "sha_dst": None, "first_diff": None}
        if not verify:
            return result

        # ---------------- Verificación ----------------
        _drop_cache(fd)
        os.lseek(fs, 0, os.SEEK_SET)
        os.lseek(fd, 0, os.SEEK_SET)
        hs2, hd = hashlib.sha256(), hashlib.sha256()
        first_diff = None
        done = 0
        while done < total:
            _check(cancelled)
            n = min(CHUNK, total - done)
            try:
                a = _read_exact(fs, n)
                b = _read_exact(fd, n)
            except OSError as e:
                raise OpError(f"Error de lectura durante la verificación (byte {done}): {e.strerror}")
            if len(a) != n or len(b) != n:
                raise OpError(f"Lectura incompleta durante la verificación (byte {done}).")
            hs2.update(a)
            hd.update(b)
            if first_diff is None and a != b:
                first_diff = done + next(i for i in range(n) if a[i] != b[i])
            done += n
            if progress:
                progress("verify", done, total)
        result.update(ok=first_diff is None, sha_src=hs2.hexdigest(), sha_dst=hd.hexdigest(),
                      first_diff=first_diff)
        return result
    finally:
        os.close(fs)
        os.close(fd)


def clone_device(src, dst, total, verify=True, progress=None, cancelled=None):
    res = copy_data(src, dst, total, "device", verify, progress, cancelled)
    return res if verify else None


def hash_file(path, progress=None, cancelled=None):
    total = os.path.getsize(path)
    h = hashlib.sha256()
    done = 0
    with open(path, "rb", buffering=0) as f:
        while True:
            _check(cancelled)
            data = f.read(CHUNK)
            if not data:
                break
            h.update(data)
            done += len(data)
            if progress:
                progress("check", done, total)
    return h.hexdigest()


def read_sidecar_sha(image_path):
    side = image_path + ".sha256"
    try:
        with open(side, encoding="utf-8") as f:
            return f.read().split()[0].lower()
    except (OSError, IndexError):
        return None


def write_sidecar_sha(image_path, sha):
    with open(image_path + ".sha256", "w", encoding="utf-8") as f:
        f.write(f"{sha}  {os.path.basename(image_path)}\n")


# ======================================================================
# BORRADO SEGURO
# ======================================================================
def wipe_device(dst, total, method="zero", verify=True, progress=None, cancelled=None):
    """Sobrescribe el disco entero con ceros o datos aleatorios."""
    fd = open_device_exclusive(dst)
    try:
        zero = bytes(CHUNK)
        h = hashlib.sha256()
        done = since_sync = 0
        while done < total:
            _check(cancelled)
            n = min(CHUNK, total - done)
            data = zero[:n] if method == "zero" else os.urandom(n)
            if method != "zero":
                h.update(data)
            try:
                _write_all(fd, data)
            except OSError as e:
                raise OpError(f"Error de escritura en el byte {done}: {e.strerror}")
            done += n
            since_sync += n
            if not IS_WINDOWS and since_sync >= SYNC_EVERY:
                os.fsync(fd)
                since_sync = 0
            if progress:
                progress("wipe", done, total)
        os.fsync(fd)
        if not verify:
            return {"ok": True, "verified": False}

        _drop_cache(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        h2 = hashlib.sha256()
        done = 0
        while done < total:
            _check(cancelled)
            n = min(CHUNK, total - done)
            b = _read_exact(fd, n)
            if len(b) != n:
                raise OpError(f"Lectura incompleta durante la verificación (byte {done}).")
            if method == "zero":
                if b != zero[:n]:
                    off = done + next(i for i in range(n) if b[i] != 0)
                    return {"ok": False, "verified": True, "first_diff": off}
            else:
                h2.update(b)
            done += n
            if progress:
                progress("verify", done, total)
        if method != "zero" and h.hexdigest() != h2.hexdigest():
            return {"ok": False, "verified": True, "first_diff": None}
        return {"ok": True, "verified": True}
    finally:
        os.close(fd)


# ======================================================================
# LECTURA SIN CACHÉ (escaneo de superficie y test de velocidad)
# ======================================================================
class DirectReader:
    """Lee el disco sin pasar por la caché del sistema (O_DIRECT en Linux;
    en Windows el acceso a \\\\.\\PhysicalDriveN ya no usa la caché)."""

    def __init__(self, path, sector=512):
        self.sector = max(512, sector)
        self.direct = False
        self.fd = None
        if not IS_WINDOWS and hasattr(os, "O_DIRECT"):
            try:
                self.fd = os.open(path, os.O_RDONLY | os.O_DIRECT)
                self.direct = True
            except OSError:
                self.fd = None
        if self.fd is None:
            self.fd = os.open(path, os.O_RDONLY | O_BINARY)
        self._buf = mmap.mmap(-1, CHUNK)   # alineado a página, válido para O_DIRECT

    def read(self, offset, n):
        """Lee n bytes en offset (ambos alineados al sector). Devuelve bytes leídos."""
        if self.direct:
            view = memoryview(self._buf)[:n]
            try:
                return os.preadv(self.fd, [view], offset)
            finally:
                view.release()
        os.lseek(self.fd, offset, os.SEEK_SET)
        return len(_read_exact(self.fd, n))

    def close(self):
        try:
            os.close(self.fd)
        finally:
            self._buf.close()


def surface_scan(dev, total, sector=512, progress=None, cancelled=None):
    """Lee el disco entero y devuelve los rangos que no se han podido leer."""
    r = DirectReader(dev, sector)
    sec = r.sector
    bad = []
    bad_bytes = 0
    done = 0
    t0 = time.monotonic()
    try:
        while done < total:
            _check(cancelled)
            n = min(CHUNK, total - done)
            ok = False
            try:
                ok = r.read(done, n) == n
            except OSError:
                ok = False
            if not ok:
                # Localizar los sectores concretos dentro del bloque
                off = done
                while off < done + n:
                    _check(cancelled)
                    try:
                        good = r.read(off, sec) == sec
                    except OSError:
                        good = False
                    if not good:
                        bad_bytes += sec
                        if len(bad) < MAX_BAD:
                            bad.append(off // sec)
                    off += sec
            done += n
            if progress:
                progress("scan", done, total)
    finally:
        r.close()
    return {"bad_sectors": bad, "bad_count": bad_bytes // sec, "sector": sec,
            "seconds": time.monotonic() - t0, "truncated": bad_bytes // sec > len(bad)}


def benchmark(dev, total, sector=512, seq_bytes=1024 ** 3, rand_seconds=8.0,
              progress=None, cancelled=None):
    """Test de lectura (no escribe nada): secuencial en 3 zonas y aleatorio 4K."""
    r = DirectReader(dev, sector)
    try:
        # ---- Secuencial: inicio, mitad y final del disco ----
        seq_bytes = min(seq_bytes, total)
        per_zone = max(CHUNK, (seq_bytes // 3) // CHUNK * CHUNK)
        zones = [0, max(0, (total // 2) // CHUNK * CHUNK),
                 max(0, (total - per_zone) // CHUNK * CHUNK)]
        zone_speeds = []
        done = 0
        grand = per_zone * len(zones)
        for z in zones:
            t = time.monotonic()
            read = 0
            while read < per_zone and z + read < total:
                _check(cancelled)
                n = min(CHUNK, per_zone - read, total - z - read)
                n -= n % r.sector
                if n <= 0:
                    break
                r.read(z + read, n)
                read += n
                done += n
                if progress:
                    progress("seq", done, grand)
            dt = max(time.monotonic() - t, 1e-6)
            zone_speeds.append(read / dt)

        # ---- Aleatorio 4K ----
        block = max(4096, r.sector)
        slots = max(1, total // block - 1)
        lat = []
        t_end = time.monotonic() + rand_seconds
        t_start = time.monotonic()
        while time.monotonic() < t_end:
            _check(cancelled)
            off = random.randrange(slots) * block
            t = time.perf_counter()
            r.read(off, block)
            lat.append(time.perf_counter() - t)
            if progress and len(lat) % 50 == 0:
                progress("rand", int((time.monotonic() - t_start) * 1000), int(rand_seconds * 1000))
        elapsed = max(time.monotonic() - t_start, 1e-6)
        lat.sort()
        return {
            "seq_start": zone_speeds[0], "seq_mid": zone_speeds[1] if len(zone_speeds) > 1 else 0,
            "seq_end": zone_speeds[-1], "seq_avg": sum(zone_speeds) / len(zone_speeds),
            "rand_iops": len(lat) / elapsed,
            "rand_mbs": len(lat) * block / elapsed,
            "lat_avg_ms": (sum(lat) / len(lat) * 1000) if lat else 0,
            "lat_p99_ms": (lat[int(len(lat) * 0.99) - 1] * 1000) if lat else 0,
            "direct": r.direct or IS_WINDOWS,
        }
    finally:
        r.close()


# ======================================================================
# Utilidades
# ======================================================================
class RateMeter:
    """Velocidad y tiempo restante para mostrar en la interfaz."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.t0 = time.monotonic()
        self.last_emit = 0.0

    def should_emit(self, done, total, every=0.25):
        now = time.monotonic()
        if done >= total or now - self.last_emit >= every:
            self.last_emit = now
            return True
        return False

    def stats(self, done, total):
        elapsed = max(time.monotonic() - self.t0, 1e-6)
        speed = done / elapsed
        eta = (total - done) / speed if speed > 0 else 0
        return speed, eta


def iso_info(path):
    """Información básica de una ISO: etiqueta y si es 'híbrida' (arrancable en USB)."""
    info = {"label": "", "hybrid": False, "windows": False, "size": 0}
    try:
        info["size"] = os.path.getsize(path)
        with open(path, "rb") as f:
            mbr = f.read(512)
            info["hybrid"] = len(mbr) == 512 and mbr[510:512] == b"\x55\xaa"
            f.seek(32768)
            pvd = f.read(2048)
            if pvd[1:6] == b"CD001":
                info["label"] = pvd[40:72].decode("ascii", "replace").strip()
        lab = info["label"].upper()
        info["windows"] = lab.startswith(("CCCOMA", "CCSA", "CPBA", "CENA", "ESD-ISO", "J_CCSA", "WIN"))
    except OSError:
        pass
    return info
