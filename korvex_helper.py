#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""Helper privilegiado de KORVEX Disk Manager para Linux.

La interfaz se ejecuta como usuario normal y lanza este script con pkexec
solo para las operaciones que necesitan root. El helper NO se fía de lo que
le diga la interfaz: vuelve a leer los discos como root y repite todas las
comprobaciones de seguridad antes de tocar nada.

Uso:   korvex_helper.py <operación> '<parámetros JSON>'

Comunicación: una línea JSON por evento en stdout (progress / log / result).
Cancelar: la interfaz escribe "cancel" en stdin o lo cierra. Solo las
operaciones que se pueden interrumpir sin dañar nada (copias, borrado,
lecturas) hacen caso; el resto (mkfs, redimensionar, tablas de particiones)
terminan siempre lo que han empezado, aunque la interfaz se cierre.
"""
import sys
sys.dont_write_bytecode = True   # No dejar __pycache__ de root en /usr/lib
import os
import json
import time
import shutil
import signal
import threading
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import disks as D            # noqa: E402
import rawio as R            # noqa: E402
import diag                  # noqa: E402

MiB = D.MiB
ROOT_LOG = "/var/log/korvex-disk-manager.log"
TABLE_BACKUPS = "/var/lib/korvex-disk-manager/partition-tables"

_cancel = threading.Event()
_pipe_ok = True

FS_ALLOWED = ("ext4", "ntfs", "exfat", "fat32")
LABEL_MAX = {"ext4": 16, "ntfs": 32, "exfat": 11, "fat32": 11}
# Tipo de partición acorde al sistema de archivos (para que Windows la reconozca)
GPT_TYPE = {"ext4": "0FC63DAF-8483-4772-8E79-3D69D8477DE4",
            "ntfs": "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7",
            "exfat": "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7",
            "fat32": "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"}
MBR_TYPE = {"ext4": "83", "ntfs": "7", "exfat": "7", "fat32": "c"}


class Fail(Exception):
    pass


# ======================================================================
# COMUNICACIÓN
# ======================================================================
def _root_log(line):
    try:
        with open(ROOT_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{os.getpid()}] {line}\n")
    except OSError:
        pass


def emit(kind, **data):
    """Envía un evento. Si la interfaz se ha cerrado, sigue trabajando igual
    (nunca abandonar una operación a medias por no poder informar)."""
    global _pipe_ok
    data["type"] = kind
    if kind != "progress":
        _root_log(json.dumps(data, ensure_ascii=False))
    if not _pipe_ok:
        return
    try:
        sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        _pipe_ok = False


def log(msg):
    emit("log", msg=msg)


def _watch_stdin():
    try:
        for line in sys.stdin:
            if line.strip() == "cancel":
                break
    except Exception:
        pass
    _cancel.set()   # "cancel" recibido o la interfaz se ha cerrado


def run(cmd, input_text=None, check=False, what=None):
    env = dict(os.environ, LC_ALL="C")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, input=input_text)
    if check and r.returncode != 0:
        raise Fail(f"{what or cmd[0]} ha fallado:\n{(r.stderr or r.stdout).strip()}")
    return r


def need_tool(name, package):
    if not shutil.which(name):
        raise Fail(f"No está instalado '{name}'. Instala el paquete {package}.")


class Progress:
    def __init__(self):
        self.meter = R.RateMeter()
        self.phase = None

    def __call__(self, phase, done, total):
        if phase != self.phase:
            self.phase = phase
            self.meter.reset()
        if self.meter.should_emit(done, total):
            speed, eta = self.meter.stats(done, total)
            emit("progress", phase=phase, done=done, total=total, speed=speed, eta=eta)


# ======================================================================
# COMPROBACIONES COMUNES
# ======================================================================
def fresh_disks():
    return D.list_disks()


def get_disk(key, exp_size=None):
    disks = fresh_disks()
    d = D.find_disk(disks, key)
    if d is None:
        raise Fail("El disco ya no existe. Actualiza la lista de discos.")
    if exp_size is not None and d.size != int(exp_size):
        raise Fail("La lista de discos ha cambiado desde que la viste (¿se ha conectado o "
                   "desconectado algo?). Actualiza y vuelve a seleccionarlo.")
    real = R.device_size(d.device)
    if not real or real != d.size:
        raise Fail("No se pudo confirmar el tamaño real del disco.")
    return disks, d


def get_part(dev):
    disks = fresh_disks()
    d, p = D.find_partition(disks, dev)
    if p is None:
        raise Fail("La partición ya no existe. Actualiza la lista de discos.")
    return disks, d, p


def allow(reason):
    if reason:
        raise Fail(reason)


def unmount_all(mountpoints):
    """Desmonta (los más profundos primero). Devuelve la lista desmontada."""
    done = []
    for mp in sorted({m for m in mountpoints if m.startswith("/")}, key=len, reverse=True):
        if D.is_critical_mount(mp):
            raise Fail(f"Se ha negado a desmontar {mp}: es un punto de montaje del sistema.")
        log(f"Desmontando {mp}…")
        r = run(["umount", "--", mp])
        if r.returncode != 0:
            raise Fail(f"No se pudo desmontar {mp}: {r.stderr.strip()}\n"
                       f"Cierra los programas que estén usando esa unidad e inténtalo de nuevo.")
        done.append(mp)
    return done


def ensure_exclusive(dev):
    """Comprueba que nadie más tiene el dispositivo abierto (montado, swap, dm…)."""
    try:
        fd = os.open(dev, os.O_RDONLY | os.O_EXCL)
        os.close(fd)
    except OSError as e:
        raise Fail(f"{dev} sigue en uso por el sistema ({e.strerror}). No se ha tocado nada.")


def settle(disk_dev=None):
    if disk_dev:
        run(["partx", "-u", disk_dev])
    run(["udevadm", "settle", "--timeout=15"])


def backup_table(disk):
    """Guarda la tabla de particiones actual antes de cambiarla."""
    if disk.style not in ("GPT", "MBR") or not shutil.which("sfdisk"):
        return None
    r = run(["sfdisk", "-d", disk.device])
    if r.returncode != 0 or not r.stdout.strip():
        return None
    os.makedirs(TABLE_BACKUPS, exist_ok=True)
    ident = (disk.serial or disk.model or "disco").replace("/", "_").replace(" ", "_")
    path = os.path.join(TABLE_BACKUPS,
                        f"{os.path.basename(disk.device)}_{ident}_{datetime.now():%Y%m%d-%H%M%S}.sfdisk")
    with open(path, "w") as f:
        f.write(r.stdout)
    log(f"Copia de la tabla de particiones guardada en {path} "
        f"(restaurable con: sudo sfdisk {disk.device} < {path})")
    return path


def chown_to_user(path):
    """Los archivos creados por root pasan a ser del usuario que lanzó la app."""
    uid = os.environ.get("PKEXEC_UID")
    if not uid:
        return
    try:
        import pwd
        pw = pwd.getpwuid(int(uid))
        os.chown(path, pw.pw_uid, pw.pw_gid)
    except Exception:
        pass


def mkfs_command(fs, dev, label):
    if fs == "ext4":
        cmd = ["mkfs.ext4", "-F"] + (["-L", label] if label else []) + [dev]
    elif fs == "ntfs":
        cmd = ["mkfs.ntfs", "-Q", "-F"] + (["-L", label] if label else []) + [dev]
    elif fs == "exfat":
        cmd = ["mkfs.exfat"]
        if label:
            h = run(["mkfs.exfat", "--help"])
            uses_L = "volume-label" in (h.stdout + h.stderr)
            cmd += ["-L" if uses_L else "-n", label]
        cmd += [dev]
    else:  # fat32
        cmd = ["mkfs.vfat", "-F", "32"] + (["-n", label.upper()] if label else []) + [dev]
    need_tool(cmd[0], "e2fsprogs / ntfs-3g / exfatprogs / dosfstools")
    return cmd


def clean_label(label, fs):
    label = "".join(ch for ch in str(label) if ch.isprintable() and ch not in "\"'\\/")
    return label[:LABEL_MAX.get(fs, 11)]


def make_fs(dev, fs, label):
    if fs not in FS_ALLOWED:
        raise Fail(f"Sistema de archivos no permitido: {fs}")
    cmd = mkfs_command(fs, dev, clean_label(label, fs))
    log("Ejecutando: " + " ".join(cmd))
    run(cmd, check=True, what="El formateo")


def set_part_type(disk, number, fs):
    if not shutil.which("sfdisk"):
        return
    ptype = GPT_TYPE[fs] if disk.style == "GPT" else MBR_TYPE[fs] if disk.style == "MBR" else None
    if ptype:
        t = run(["sfdisk", "--part-type", disk.device, str(number), ptype])
        if t.returncode != 0:
            log(f"Aviso: no se pudo ajustar el tipo de partición ({t.stderr.strip()})")


def disk_has_no_users(disk):
    unmount_all([m for p in disk.partitions for m in p.mountpoints])
    for p in disk.partitions:
        if not p.whole_disk:
            ensure_exclusive(p.device)
    ensure_exclusive(disk.device)


# ======================================================================
# OPERACIONES DE PARTICIÓN
# ======================================================================
def op_format(p):
    fs = p["fs"]
    _, disk, part = get_part(p["dev"])
    allow(D.check_part_destructive(disk, part))
    log(f"Formateando {part.device} ({D.format_bytes(part.size)}) en {disk.describe()}")
    unmount_all(part.mountpoints)
    ensure_exclusive(part.device)
    make_fs(part.device, fs, p.get("label", ""))
    if not part.whole_disk:
        set_part_type(disk, part.number, fs)
    settle()
    return f"{part.device} se ha formateado correctamente en {fs.upper()}.", {}


def op_label(p):
    _, disk, part = get_part(p["dev"])
    allow(D.check_part_meta(disk, part))
    fs = part.fs_norm
    if fs not in diag.LABEL_COMMANDS:
        raise Fail(f"No se puede cambiar la etiqueta de un sistema de archivos {part.fs}.")
    if part.label and any(f"LABEL={part.label}" in spec or f'LABEL="{part.label}"' in spec
                          for spec, _mp, _o in D.read_fstab()):
        raise Fail(f"La etiqueta actual «{part.label}» se usa en /etc/fstab. Cambiarla podría "
                   f"impedir el arranque. Edita /etc/fstab antes.")
    build, needs_umount, mx = diag.LABEL_COMMANDS[fs]
    label = "".join(ch for ch in str(p.get("label", "")) if ch.isprintable() and ch not in "\"'\\/")[:mx]
    unmounted = []
    if needs_umount:
        unmounted = unmount_all(part.mountpoints)
    cmd = build(part.device, label)
    need_tool(cmd[0], "e2fsprogs / ntfs-3g / exfatprogs / dosfstools / xfsprogs")
    run(cmd, check=True, what="El cambio de etiqueta")
    settle()
    return f"Etiqueta cambiada a «{label}».", {"remount": part.device if unmounted else ""}


def op_fsck(p):
    repair = bool(p.get("repair"))
    _, disk, part = get_part(p["dev"])
    allow(D.check_part_destructive(disk, part) if repair else D.check_part_meta(disk, part))
    fs = part.fs_norm
    cmds = diag.FSCK_COMMANDS.get(fs)
    if not cmds or (repair and not cmds[1]):
        raise Fail(f"No hay herramienta para {'reparar' if repair else 'comprobar'} {part.fs}.")
    cmd = list(cmds[1] if repair else cmds[0]) + [part.device]
    need_tool(cmd[0], "e2fsprogs / ntfs-3g / dosfstools / exfatprogs / xfsprogs / btrfs-progs")
    unmounted = unmount_all(part.mountpoints)
    ensure_exclusive(part.device)
    log("Ejecutando: " + " ".join(cmd))
    r = run(cmd)
    out = ((r.stdout or "") + ("\n" + r.stderr if r.stderr else "")).strip()
    rc = r.returncode
    if fs.startswith("ext"):
        # e2fsck: 0 limpio · 1/2 corregido · 4 errores sin corregir · 8+ error de uso
        if rc >= 8:
            raise Fail(f"e2fsck no ha podido ejecutarse (código {rc}):\n{out[-2000:]}")
        clean, fixed, pending = rc == 0, rc in (1, 2, 3), bool(rc & 4)
    else:
        clean = rc == 0
        fixed = repair and rc == 0
        pending = rc != 0
    if repair:
        msg = ("No se han encontrado errores." if clean else
               "Sistema de archivos reparado." if fixed and not pending else
               f"Quedan errores que no se han podido reparar (código {rc}). Haz copia de seguridad.")
    else:
        msg = ("No se han encontrado errores." if clean else
               "Se han encontrado errores. Usa «Reparar» para corregirlos.")
    return msg, {"output": out[-6000:], "clean": clean,
                 "remount": part.device if unmounted else "",
                 "success": not (repair and pending)}


def op_part_create(p):
    fs = p["fs"]
    disks, disk = get_disk(p["disk"], p.get("exp_size"))
    allow(D.check_disk_writable(disk))
    need_tool("sfdisk", "fdisk / util-linux")
    start, size = int(p["start"]), int(p["size"])
    start = (start + MiB - 1) // MiB * MiB
    size = size // MiB * MiB
    if size < D.MIN_GAP:
        raise Fail("La partición es demasiado pequeña.")
    if not any(a <= start and start + size <= a + gsz for a, gsz in disk.free_regions()):
        raise Fail("El espacio elegido ya no está libre. Actualiza la lista de discos.")
    lss = disk.sector_size
    ptype = GPT_TYPE[fs] if disk.style == "GPT" else MBR_TYPE[fs]
    before = {x.device for x in disk.partitions}
    backup_table(disk)
    log(f"Creando partición de {D.format_bytes(size)} en {disk.device}…")
    # --wipe-partitions: borra firmas antiguas en la nueva partición para que no se automonte
    run(["sfdisk", "--append", "--wipe-partitions", "always", disk.device], check=True, what="La creación de la partición",
        input_text=f"start={start // lss}, size={size // lss}, type={ptype}\n")
    settle(disk.device)
    _, d2 = get_disk(disk.key)
    new = [x for x in d2.partitions if x.device not in before]
    if len(new) != 1:
        raise Fail("La partición se ha creado pero no se ha podido identificar. Actualiza y "
                   "formatéala manualmente.")
    newp = new[0]
    ensure_exclusive(newp.device)
    make_fs(newp.device, fs, p.get("label", ""))
    settle()
    return f"Partición {newp.device} creada y formateada en {fs.upper()}.", {}


def op_part_delete(p):
    _, disk, part = get_part(p["dev"])
    allow(D.check_part_destructive(disk, part))
    allow(D.check_disk_writable(disk))
    if part.whole_disk:
        raise Fail("El disco no tiene tabla de particiones. Usa «Inicializar disco».")
    need_tool("sfdisk", "fdisk / util-linux")
    unmount_all(part.mountpoints)
    ensure_exclusive(part.device)
    backup_table(disk)
    log(f"Eliminando {part.device}…")
    run(["sfdisk", "--delete", disk.device, str(part.number)], check=True, what="La eliminación")
    settle(disk.device)
    return f"Partición {part.device} eliminada.", {}


def _set_part_size(disk, part, new_size):
    lss = disk.sector_size
    run(["sfdisk", "-N", str(part.number), disk.device], check=True,
        what="El cambio de tamaño de la partición",
        input_text=f"{part.start // lss},{new_size // lss}\n")
    settle(disk.device)
    real = D._sysfs_read_int(f"/sys/class/block/{os.path.basename(part.device)}/size") * 512
    if real != new_size:
        raise Fail(f"La partición no tiene el tamaño esperado tras el cambio "
                   f"({D.format_bytes(real)} en lugar de {D.format_bytes(new_size)}).")


def op_part_resize(p):
    _, disk, part = get_part(p["dev"])
    allow(D.check_part_destructive(disk, part))
    allow(D.check_disk_writable(disk))
    fs = part.fs_norm
    if fs not in ("ext4", "ext3", "ext2", "ntfs"):
        raise Fail(f"Solo se pueden redimensionar particiones ext4 y NTFS (esta es {part.fs or 'sin formato'}).")
    if part.whole_disk:
        raise Fail("El disco no tiene tabla de particiones.")
    need_tool("sfdisk", "fdisk / util-linux")
    need_tool("resize2fs" if fs.startswith("ext") else "ntfsresize", "e2fsprogs / ntfs-3g")
    new_size = int(p["new_size"]) // MiB * MiB
    max_size = part.size + disk.gap_after(part)
    if new_size < D.MIN_GAP:
        raise Fail("Tamaño demasiado pequeño.")
    if new_size > max_size:
        raise Fail(f"No hay tanto espacio libre detrás de la partición (máximo {D.format_bytes(max_size)}).")
    if abs(new_size - part.size) < MiB:
        raise Fail("El tamaño nuevo es igual al actual.")
    unmount_all(part.mountpoints)
    ensure_exclusive(part.device)
    backup_table(disk)
    dev = part.device
    shrink = new_size < part.size

    if fs.startswith("ext"):
        log("Comprobando el sistema de archivos (e2fsck)…")
        r = run(["e2fsck", "-f", "-p", dev])
        if r.returncode not in (0, 1):
            raise Fail("El sistema de archivos tiene errores. Usa «Reparar» antes de redimensionar.\n"
                       + (r.stdout + r.stderr).strip()[-2000:])
    else:
        log("Comprobando el sistema de archivos NTFS…")
        r = run(["ntfsresize", "--no-action", "-s", str(new_size), dev], input_text="y\n")
        if r.returncode != 0:
            raise Fail("ntfsresize no permite este cambio (no se ha modificado nada):\n"
                       + (r.stdout + r.stderr).strip()[-2000:])

    if shrink:
        # Primero el sistema de archivos, después la partición
        log(f"Reduciendo el sistema de archivos a {D.format_bytes(new_size)}…")
        if fs.startswith("ext"):
            run(["resize2fs", dev, f"{new_size // 1024}K"], check=True, what="resize2fs")
        else:
            run(["ntfsresize", "-s", str(new_size), dev], input_text="y\n", check=True, what="ntfsresize")
        log("Reduciendo la partición…")
        _set_part_size(disk, part, new_size)
    else:
        # Primero la partición, después el sistema de archivos
        log("Ampliando la partición…")
        _set_part_size(disk, part, new_size)
        log("Ampliando el sistema de archivos…")
        if fs.startswith("ext"):
            run(["resize2fs", dev], check=True, what="resize2fs")
        else:
            run(["ntfsresize", dev], input_text="y\n", check=True, what="ntfsresize")
    extra = (" Windows revisará el volumen (chkdsk) la próxima vez que arranque." if fs == "ntfs" else "")
    return f"{dev} redimensionada a {D.format_bytes(new_size)}.{extra}", {}


# ======================================================================
# OPERACIONES DE DISCO COMPLETO
# ======================================================================
def op_init(p):
    style = p.get("style", "gpt").lower()
    if style not in ("gpt", "mbr"):
        raise Fail("Tipo de tabla no válido.")
    disks, disk = get_disk(p["disk"], p.get("exp_size"))
    allow(D.check_disk_writable(disk))
    need_tool("sfdisk", "fdisk / util-linux")
    disk_has_no_users(disk)
    backup_table(disk)
    log(f"Creando tabla {style.upper()} nueva en {disk.device}…")
    if shutil.which("wipefs"):
        run(["wipefs", "-a", disk.device])
    run(["sfdisk", "--wipe", "always", disk.device], check=True, what="La creación de la tabla",
        input_text=f"label: {'gpt' if style == 'gpt' else 'dos'}\n")
    run(["blockdev", "--rereadpt", disk.device])
    settle(disk.device)
    create = p.get("create")
    if create:
        _, d2 = get_disk(disk.key)
        regions = d2.free_regions()
        if not regions:
            raise Fail("La tabla se ha creado, pero no se encuentra espacio libre para la partición.")
        a, size = max(regions, key=lambda r: r[1])
        return op_part_create({"disk": d2.key, "exp_size": d2.size, "start": a, "size": size,
                               "fs": create["fs"], "label": create.get("label", "")})[0] \
            .replace("Partición", f"Tabla {style.upper()} creada. Partición"), {}
    return f"Tabla de particiones {style.upper()} creada en {disk.device}. El disco está vacío.", {}


def op_clone(p):
    verify = bool(p.get("verify", True))
    disks = fresh_disks()
    src, dst = D.find_disk(disks, p["src"]), D.find_disk(disks, p["dst"])
    allow(D.check_clone_allowed(src, dst))
    if src.size != int(p["src_size"]) or dst.size != int(p["dst_size"]):
        raise Fail("La lista de discos ha cambiado desde que la viste. Actualiza y vuelve a "
                   "seleccionar los discos.")
    real_src, real_dst = R.device_size(src.device), R.device_size(dst.device)
    if not real_src or real_src != src.size:
        raise Fail("No se pudo confirmar el tamaño real del disco origen.")
    if not real_dst or real_dst < real_src:
        raise Fail("El disco destino es más pequeño que el origen.")
    log(f"Origen:  {src.describe()} ({src.device})")
    log(f"Destino: {dst.describe()} ({dst.device})")
    if src.is_system:
        log("Aviso: el origen es el disco del sistema en uso; la copia puede no ser 100 % "
            "consistente si hay escrituras durante el proceso.")
    disk_has_no_users(dst)
    res = R.copy_data(src.device, dst.device, real_src, "device", verify, Progress(), _cancel.is_set)
    run(["blockdev", "--rereadpt", dst.device])
    settle()
    extra = ""
    if dst.size > src.size:
        extra = ("\n\nEl destino es mayor que el origen: el espacio sobrante queda sin asignar."
                 + (f" Para mover la cabecera GPT de respaldo al final: sudo sgdisk -e {dst.device}"
                    if src.style == "GPT" else ""))
    extra += ("\n\nAmbos discos tienen ahora los mismos UUID: si los dejas conectados a la vez, "
              "el sistema podría confundirlos al arrancar.")
    return _verify_msg("Clonación", res, verify, src.is_system) + extra, {"verify_ok": res["ok"]}


def _verify_msg(what, res, verify, live_source=False):
    if not verify:
        return f"{what} completada (sin verificación)."
    if res["ok"]:
        return f"{what} completada y verificada.\nSHA-256: {res['sha_dst']}"
    hint = (" El origen es el disco del sistema en uso: probablemente se escribió en él durante "
            "la copia." if live_source else "")
    raise Fail(f"La verificación ha encontrado diferencias (primera en el byte {res['first_diff']}).{hint}\n"
               f"SHA-256 origen:  {res['sha_src']}\nSHA-256 destino: {res['sha_dst']}")


def op_erase(p):
    method = p.get("method", "zero")
    verify = bool(p.get("verify", True))
    disks, disk = get_disk(p["disk"], p.get("exp_size"))
    allow(D.check_disk_writable(disk))
    disk_has_no_users(disk)
    log(f"Borrado seguro de {disk.describe()} ({disk.device}) · método: {method}")
    if method == "discard":
        need_tool("blkdiscard", "util-linux")
        if not disk.discard:
            raise Fail("Este disco no admite TRIM/discard.")
        run(["blkdiscard", "-f", disk.device], check=True, what="blkdiscard")
        result = {"ok": True, "verified": False}
    elif method in ("zero", "random"):
        result = R.wipe_device(disk.device, disk.size, method, verify, Progress(), _cancel.is_set)
    else:
        raise Fail("Método de borrado no válido.")
    run(["blockdev", "--rereadpt", disk.device])
    settle()
    if not result["ok"]:
        raise Fail("La verificación ha encontrado datos que no se han borrado"
                   + (f" (byte {result.get('first_diff')})." if result.get("first_diff") is not None else ".")
                   + " El disco podría estar fallando.")
    return ("Disco borrado por completo" + (" y verificado." if result.get("verified") else ".")
            + " Ahora no tiene particiones: usa «Inicializar disco» para volver a usarlo."), {}


def _check_out_path(path):
    path = os.path.abspath(path)
    if any(path.startswith(x) for x in ("/dev/", "/proc/", "/sys/", "/run/udev")):
        raise Fail("Ruta de destino no válida.")
    if not os.path.isdir(os.path.dirname(path)):
        raise Fail("La carpeta de destino no existe.")
    return path


def op_image_create(p):
    verify = bool(p.get("verify", True))
    path = _check_out_path(p["path"])
    disks, disk = get_disk(p["disk"], p.get("exp_size"))
    allow(D.check_image_source(disk, path, disks))
    log(f"Creando imagen de {disk.describe()} en {path}")
    if disk.is_system:
        log("Aviso: es el disco del sistema en uso; la imagen puede no ser 100 % consistente.")
    try:
        res = R.copy_data(disk.device, path, disk.size, "file", verify, Progress(), _cancel.is_set)
    except (R.OpCancelled, R.OpError):
        try:
            os.remove(path)     # no dejar imágenes a medias que parezcan buenas
        except OSError:
            pass
        raise
    finally:
        if os.path.exists(path):
            chown_to_user(path)
    msg = _verify_msg("Imagen", res, verify, disk.is_system)
    R.write_sidecar_sha(path, res["sha_src"])
    chown_to_user(path + ".sha256")
    return msg + f"\n\nArchivo: {path}\nSuma SHA-256 guardada en {os.path.basename(path)}.sha256", {}


def op_image_restore(p):
    verify = bool(p.get("verify", True))
    path = os.path.abspath(p["path"])
    if not os.path.isfile(path):
        raise Fail("El archivo de imagen no existe.")
    size = os.path.getsize(path)
    disks, disk = get_disk(p["disk"], p.get("exp_size"))
    allow(D.check_image_target(disk, size, path, disks))
    kind = p.get("kind", "imagen")
    expected = R.read_sidecar_sha(path) if p.get("precheck", True) else None
    if expected:
        log("Comprobando la integridad de la imagen antes de escribir…")
        got = R.hash_file(path, Progress(), _cancel.is_set)
        if got != expected:
            raise Fail("La imagen está dañada: su SHA-256 no coincide con el archivo .sha256. "
                       "No se ha escrito nada en el disco.")
        log("Imagen íntegra.")
    log(f"Escribiendo {os.path.basename(path)} en {disk.describe()} ({disk.device})")
    disk_has_no_users(disk)
    res = R.copy_data(path, disk.device, size, "device", verify, Progress(), _cancel.is_set)
    run(["blockdev", "--rereadpt", disk.device])
    settle()
    what = "Grabación de la ISO" if kind == "iso" else "Restauración"
    return _verify_msg(what, res, verify), {}


# ======================================================================
# DIAGNÓSTICO (solo lectura)
# ======================================================================
def op_smart(p):
    disks, disk = get_disk(p["disk"])
    need_tool("smartctl", "smartmontools")
    r = run(["smartctl", "-a", "-j", disk.device])
    try:
        js = json.loads(r.stdout or "{}")
    except ValueError:
        raise Fail("smartctl no ha devuelto datos válidos.\n" + r.stderr.strip())
    if (r.returncode & 0b11) and not js.get("smart_status") and not js.get("ata_smart_attributes") \
            and not js.get("nvme_smart_health_information_log"):
        # Algunas cajas USB necesitan "-d sat"
        r2 = run(["smartctl", "-a", "-j", "-d", "sat", disk.device])
        try:
            js2 = json.loads(r2.stdout or "{}")
            if js2.get("smart_status") or js2.get("ata_smart_attributes"):
                js = js2
        except ValueError:
            pass
    s = diag.smart_from_smartctl(js)
    if s["passed"] is None and not s["attributes"]:
        raise Fail("Este disco no proporciona datos SMART (es habitual en memorias USB y algunas "
                   "cajas externas).")
    return "Datos de salud leídos.", {"smart": s}


def op_bench(p):
    disks, disk = get_disk(p["disk"], p.get("exp_size"))
    log(f"Test de lectura en {disk.describe()} (no se escribe nada)")
    res = R.benchmark(disk.device, disk.size, disk.sector_size, progress=Progress(), cancelled=_cancel.is_set)
    return "Test de velocidad completado.", {"bench": res}


def op_scan(p):
    disks, disk = get_disk(p["disk"], p.get("exp_size"))
    log(f"Escaneo de superficie de {disk.describe()} (solo lectura)")
    res = R.surface_scan(disk.device, disk.size, disk.sector_size, Progress(), _cancel.is_set)
    msg = ("No se han encontrado sectores defectuosos." if not res["bad_count"] else
           f"Se han encontrado {res['bad_count']} sectores ilegibles. Haz copia de seguridad cuanto antes.")
    return msg, {"scan": res}


# name: (función, cancelable)
OPS = {
    "format": (op_format, False), "label": (op_label, False), "fsck": (op_fsck, False),
    "part_create": (op_part_create, False), "part_delete": (op_part_delete, False),
    "part_resize": (op_part_resize, False), "init": (op_init, False),
    "clone": (op_clone, True), "erase": (op_erase, True),
    "image_create": (op_image_create, True), "image_restore": (op_image_restore, True),
    "smart": (op_smart, True), "bench": (op_bench, True), "scan": (op_scan, True),
}


def main():
    if os.geteuid() != 0:
        emit("result", ok=False, msg="El helper debe ejecutarse como root (pkexec).")
        sys.exit(1)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN) if hasattr(signal, "SIGPIPE") else None
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, lambda *_: _cancel.set())
    threading.Thread(target=_watch_stdin, daemon=True).start()

    if len(sys.argv) != 3 or sys.argv[1] not in OPS:
        emit("result", ok=False, msg="Argumentos no válidos.")
        sys.exit(1)
    name = sys.argv[1]
    try:
        params = json.loads(sys.argv[2])
    except ValueError:
        emit("result", ok=False, msg="Parámetros no válidos.")
        sys.exit(1)
    fn, _cancelable = OPS[name]
    _root_log(f"INICIO {name} {json.dumps(params, ensure_ascii=False)} (uid usuario {os.environ.get('PKEXEC_UID')})")
    try:
        msg, data = fn(params)
        emit("result", ok=data.pop("success", True), msg=msg, data=data)
    except R.OpCancelled:
        emit("result", ok=False, cancelled=True,
             msg="Operación cancelada." + ("" if name in ("smart", "bench", "scan", "image_create") else
                  " El disco destino ha quedado incompleto y no es utilizable hasta que se vuelva "
                  "a escribir o formatear."))
        sys.exit(2)
    except (Fail, R.OpError) as e:
        emit("result", ok=False, msg=str(e))
        sys.exit(1)
    except Exception as e:
        emit("result", ok=False, msg=f"Error inesperado: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
