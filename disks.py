# -*- coding: utf-8 -*-
"""Lectura de discos y particiones para Windows y Linux, sin Qt.

Aquí vive la lógica que decide qué está PROTEGIDO. La usa la interfaz para
deshabilitar acciones y la vuelven a usar los workers / el helper de root
justo antes de escribir, por si algo ha cambiado entretanto.

Niveles de protección
---------------------
* disk.is_system      -> disco donde corre el sistema. Solo lectura: no se
                         formatea, no se tocan sus particiones, no es destino.
* part.critical       -> partición del sistema (C:, /, /boot, swap, paginación…).
                         Ni siquiera se cambia su letra o etiqueta.
* part.special        -> EFI, reservada, recuperación, swap… No se toca.
* part.protected      -> no admite operaciones destructivas (resumen de lo anterior).
"""
import os
import json
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from platform_utils import IS_WINDOWS, NO_WINDOW

MiB = 1024 * 1024
MIN_GAP = 16 * MiB          # espacio libre mínimo para ofrecer "crear partición"


# ======================================================================
# MODELO
# ======================================================================
@dataclass
class Partition:
    key: str                     # Windows "disco:particion" · Linux "/dev/sdb1"
    number: int
    disk_key: str
    device: str = ""             # Linux: /dev/sdb1
    letter: str = ""             # Windows: "D"
    mountpoints: List[str] = field(default_factory=list)
    start: int = 0               # bytes desde el inicio del disco
    size: int = 0
    free: Optional[int] = None
    fs: str = ""
    label: str = ""
    ptype: str = ""
    whole_disk: bool = False     # sistema de archivos directamente sobre el disco
    critical: bool = False
    critical_reason: str = ""
    special: bool = False
    protected: bool = False
    protect_reason: str = ""
    busy_reason: str = ""        # Linux: LVM/LUKS/RAID activos -> no se toca
    uuid: str = ""
    partuuid: str = ""
    partlabel: str = ""
    fstab: List[str] = field(default_factory=list)   # Linux: puntos de montaje en /etc/fstab

    @property
    def display_name(self) -> str:
        if IS_WINDOWS:
            return f"{self.letter}:" if self.letter else (self.label or f"Partición {self.number}")
        if self.mountpoints:
            return self.mountpoints[0]
        return os.path.basename(self.device) or self.key

    @property
    def short_name(self) -> str:
        """Identificador corto (para escribir en confirmaciones)."""
        if IS_WINDOWS:
            return f"{self.letter}:" if self.letter else f"Partición {self.number}"
        return os.path.basename(self.device)

    @property
    def openable_path(self) -> str:
        if IS_WINDOWS:
            return f"{self.letter}:\\" if self.letter else ""
        for m in self.mountpoints:
            if m.startswith("/"):
                return m
        return ""

    @property
    def fs_norm(self) -> str:
        """Sistema de archivos normalizado: ext4, ntfs, exfat, fat32, …"""
        f = (self.fs or "").lower()
        return {"vfat": "fat32", "fat": "fat32", "fat16": "fat32", "msdos": "fat32"}.get(f, f)


@dataclass
class Disk:
    key: str                     # Windows "0" · Linux "/dev/sda"
    number: int
    device: str                  # Windows \\.\PhysicalDrive0 · Linux /dev/sda
    model: str = ""
    serial: str = ""
    size: int = 0
    sector_size: int = 512
    status: str = ""
    online: bool = True
    read_only: bool = False
    removable: bool = False
    bus: str = ""
    style: str = ""              # GPT / MBR / RAW
    rotational: Optional[bool] = None
    discard: bool = False        # admite TRIM/discard
    is_system: bool = False
    system_reason: str = ""
    busy_reason: str = ""
    partitions: List[Partition] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"Disco {self.number}"

    @property
    def short_device(self) -> str:
        return self.device if IS_WINDOWS else os.path.basename(self.device)

    @property
    def confirm_token(self) -> str:
        """Lo que hay que escribir para confirmar una operación destructiva."""
        return self.title if IS_WINDOWS else self.short_device

    @property
    def is_usb(self) -> bool:
        return self.removable or self.bus in ("USB", "SD", "MMC")

    def describe(self) -> str:
        return f"{self.title} · {self.model or 'Desconocido'} · {format_bytes(self.size)}"

    def free_regions(self) -> List[Tuple[int, int]]:
        """Huecos sin asignar (inicio, tamaño), alineados a 1 MiB."""
        if self.style not in ("GPT", "MBR") or any(p.whole_disk for p in self.partitions):
            return []
        first = MiB
        last = self.size - (MiB if self.style == "GPT" else 0)   # cabecera GPT de respaldo
        out = []
        cursor = first
        for p in sorted(self.partitions, key=lambda p: p.start):
            if p.start > cursor:
                out.append((cursor, p.start))
            cursor = max(cursor, p.start + p.size)
        if last > cursor:
            out.append((cursor, last))
        regions = []
        for a, b in out:
            a = (a + MiB - 1) // MiB * MiB
            b = b // MiB * MiB
            if b - a >= MIN_GAP:
                regions.append((a, b - a))
        return regions

    def gap_after(self, part: "Partition") -> int:
        """Espacio libre contiguo justo detrás de una partición (para ampliarla)."""
        end = part.start + part.size
        for a, size in self.free_regions():
            if 0 <= a - end < MiB:
                return a + size - end
        return 0


def format_bytes(value) -> str:
    if value is None or value == "":
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    for unit, factor in (("TB", 1024 ** 4), ("GB", 1024 ** 3)):
        if v >= factor:
            return f"{v / factor:.2f} {unit}"
    if v >= 1024 ** 2:
        return f"{v / 1024 ** 2:.0f} MB"
    if v >= 1024:
        return f"{v / 1024:.0f} KB"
    return f"{int(v)} B"


def list_disks() -> List[Disk]:
    disks = _list_disks_windows() if IS_WINDOWS else _list_disks_linux()
    return sorted(disks, key=lambda d: d.number)


def find_disk(disks: List[Disk], key: str) -> Optional[Disk]:
    return next((d for d in disks if d.key == key), None)


def find_partition(disks: List[Disk], key: str):
    for d in disks:
        for p in d.partitions:
            if p.key == key:
                return d, p
    return None, None


# ======================================================================
# WINDOWS (PowerShell / Storage module)
# ======================================================================
_PS_READ = r"""
$ErrorActionPreference = 'Stop'
$phys = @{}
try { Get-PhysicalDisk | ForEach-Object { $phys[[string]$_.DeviceId] = $_ } } catch {}
$disks = @(Get-Disk | ForEach-Object {
    $pd = $phys[[string]$_.Number]
    [pscustomobject]@{
        Number = [int]$_.Number; FriendlyName = [string]$_.FriendlyName
        SerialNumber = [string]$_.SerialNumber; Size = [int64]$_.Size
        LogicalSectorSize = [int]$_.LogicalSectorSize
        OperationalStatus = [string]($_.OperationalStatus -join ',')
        IsOffline = [bool]$_.IsOffline; IsReadOnly = [bool]$_.IsReadOnly
        IsBoot = [bool]$_.IsBoot; IsSystem = [bool]$_.IsSystem
        BusType = [string]$_.BusType; PartitionStyle = [string]$_.PartitionStyle
        MediaType = if ($pd) { [string]$pd.MediaType } else { '' }
    }
})
$parts = @(Get-Partition -ErrorAction SilentlyContinue | ForEach-Object {
    $v = $null
    try { $v = $_ | Get-Volume -ErrorAction Stop } catch {}
    $dl = [string]$_.DriveLetter
    if ($dl -eq [string][char]0) { $dl = '' }
    [pscustomobject]@{
        DiskNumber = [int]$_.DiskNumber; PartitionNumber = [int]$_.PartitionNumber
        DriveLetter = $dl; Offset = [int64]$_.Offset; Size = [int64]$_.Size; Type = [string]$_.Type
        IsBoot = [bool]$_.IsBoot; IsSystem = [bool]$_.IsSystem
        FileSystem = if ($v) { [string]$v.FileSystem } else { '' }
        Label = if ($v) { [string]$v.FileSystemLabel } else { '' }
        SizeRemaining = if ($v) { [int64]$v.SizeRemaining } else { $null }
    }
})
$pf = @()
try { $pf = @(Get-CimInstance Win32_PageFileUsage | ForEach-Object { ([string]$_.Name).Substring(0,1) }) } catch {}
@{ disks = $disks; partitions = $parts; sysdrive = [string]$env:SystemDrive; pagefile = $pf } | ConvertTo-Json -Depth 4 -Compress
"""

WIN_SPECIAL_TYPES = {"system", "reserved", "recovery", "extended"}


def run_powershell(script: str, timeout: int = 120) -> subprocess.CompletedProcess:
    script = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n" + script
    cmd = ["powershell", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
    return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW,
                          encoding="utf-8", errors="replace")


def ps_quote(text: str) -> str:
    """Cadena PowerShell entre comillas simples (escapa ')."""
    return "'" + str(text).replace("'", "''") + "'"


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _list_disks_windows() -> List[Disk]:
    res = run_powershell(_PS_READ)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "PowerShell devolvió un error")
    out = res.stdout.strip().lstrip("\ufeff")
    data = json.loads(out) if out else {}
    sysdrive = str(data.get("sysdrive") or "C:")[:1].upper()
    pagefile = {str(x).upper() for x in _as_list(data.get("pagefile"))}
    raw_parts = _as_list(data.get("partitions"))

    disks = []
    for rd in _as_list(data.get("disks")):
        num = int(rd.get("Number", 0))
        bus = str(rd.get("BusType") or "")
        media = str(rd.get("MediaType") or "").upper()
        style = str(rd.get("PartitionStyle") or "").upper()
        disk = Disk(
            key=str(num), number=num, device=rf"\\.\PhysicalDrive{num}",
            model=(rd.get("FriendlyName") or "").strip(),
            serial=(rd.get("SerialNumber") or "").strip(),
            size=int(rd.get("Size") or 0), sector_size=int(rd.get("LogicalSectorSize") or 512),
            status=str(rd.get("OperationalStatus") or ""),
            online=not rd.get("IsOffline", False), read_only=bool(rd.get("IsReadOnly")),
            bus=bus.upper(), style="MBR" if style == "MBR" else style,
            removable=bus.upper() in ("USB", "SD", "MMC"),
            rotational=True if media == "HDD" else False if media == "SSD" else None,
            discard=media == "SSD",
        )
        reasons = []
        if rd.get("IsBoot"):
            reasons.append("disco de arranque")
        if rd.get("IsSystem"):
            reasons.append("disco del sistema")

        for rp in sorted((p for p in raw_parts if int(p.get("DiskNumber", -1)) == num),
                         key=lambda p: int(p.get("Offset") or 0)):
            letter = str(rp.get("DriveLetter") or "").replace("\x00", "").strip().upper()
            pnum = int(rp.get("PartitionNumber", 0))
            ptype = str(rp.get("Type") or "")
            part = Partition(
                key=f"{num}:{pnum}", number=pnum, disk_key=disk.key, letter=letter,
                start=int(rp.get("Offset") or 0), size=int(rp.get("Size") or 0),
                free=rp.get("SizeRemaining") if rp.get("SizeRemaining") is not None else None,
                fs=str(rp.get("FileSystem") or ""), label=str(rp.get("Label") or ""), ptype=ptype,
            )
            crit = []
            if rp.get("IsBoot") or rp.get("IsSystem"):
                crit.append("partición de arranque")
            if letter and letter == sysdrive:
                crit.append(f"contiene Windows ({letter}:)")
            if letter and letter in pagefile:
                crit.append(f"archivo de paginación ({letter}:)")
            if crit:
                part.critical = True
                part.critical_reason = "; ".join(crit)
                reasons.extend(crit)
            if ptype.lower() in WIN_SPECIAL_TYPES:
                part.special = True
                part.protected = True
                part.protect_reason = f"Partición {ptype}"
            disk.partitions.append(part)

        _apply_system_flag(disk, reasons)
        disks.append(disk)
    return disks


# ======================================================================
# LINUX (lsblk + sysfs)
# ======================================================================
LSBLK_COLS = ["NAME", "PATH", "TYPE", "SIZE", "MODEL", "SERIAL", "FSTYPE", "LABEL",
              "MOUNTPOINTS", "FSAVAIL", "PARTTYPENAME", "PARTN", "RM", "RO", "TRAN", "PTTYPE",
              "ROTA", "LOG-SEC", "DISC-MAX", "UUID", "PARTUUID", "PARTLABEL"]
# Para versiones antiguas de util-linux que no conocen todas las columnas
LSBLK_COLS_OLD = ["NAME", "TYPE", "SIZE", "MODEL", "SERIAL", "FSTYPE", "LABEL",
                  "MOUNTPOINT", "FSAVAIL", "RM", "RO", "TRAN", "PTTYPE", "ROTA", "UUID"]

# Puntos de montaje que hacen que un disco sea "del sistema"
CRITICAL_MOUNTS = {"/", "/boot", "/boot/efi", "/efi", "/usr", "/var", "/home", "/opt", "/srv",
                   "[SWAP]", "/run/live/medium", "/cdrom", "/run/initramfs/live",
                   "/lib/live/mount/medium", "/run/archiso/bootmnt"}
CRITICAL_PREFIXES = ("/usr/", "/var/", "/boot/")

# Tipos de dispositivo que, si están activos en un disco, impiden escribir en él
BUSY_TYPES = ("lvm", "crypt", "raid", "dm", "mpath")

LINUX_SPECIAL_PTYPES = ("efi system", "bios boot", "microsoft reserved", "recovery",
                        "linux swap", "linux reserved", "extended")


def is_critical_mount(m: str) -> bool:
    return m in CRITICAL_MOUNTS or any(m.startswith(p) for p in CRITICAL_PREFIXES)


def _run_lsblk(cols, extra=()):
    env = dict(os.environ, LC_ALL="C")
    cmd = ["lsblk", "-J", "-b", "-o", ",".join(cols)] + list(extra)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)


def lsblk_tree(extra=()) -> list:
    res = _run_lsblk(LSBLK_COLS, extra)
    if res.returncode != 0:
        res = _run_lsblk(LSBLK_COLS_OLD, extra)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "lsblk devolvió un error")
    return json.loads(res.stdout or "{}").get("blockdevices", [])


def _b(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true")


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _mounts(node) -> List[str]:
    mps = node.get("mountpoints")
    if mps is None:
        mps = [node.get("mountpoint")]
    return [m for m in mps if m]


def _path(node) -> str:
    return node.get("path") or f"/dev/{node.get('name')}"


def _walk(node):
    for ch in node.get("children") or []:
        yield ch
        yield from _walk(ch)


def _sysfs_read_int(path, default=0):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return default


def disks_backing_dev(st_dev: int) -> set:
    """Discos físicos (/dev/sdX) sobre los que está un dispositivo (sigue
    dm/LVM/LUKS/RAID a través de 'slaves' en sysfs)."""
    found = set()
    try:
        start = f"/sys/dev/block/{os.major(st_dev)}:{os.minor(st_dev)}"
        if not os.path.exists(start):
            return found
        stack, seen = [os.path.realpath(start)], set()
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            slaves = os.path.join(node, "slaves")
            if os.path.isdir(slaves) and os.listdir(slaves):
                stack.extend(os.path.realpath(os.path.join(slaves, s)) for s in os.listdir(slaves))
                continue
            if os.path.exists(os.path.join(node, "partition")):
                node = os.path.dirname(node)
            found.add("/dev/" + os.path.basename(node))
    except Exception:
        pass
    return found


def disk_keys_for_path(path: str, disks: List[Disk]) -> set:
    """En qué disco(s) está guardado un archivo o carpeta."""
    path = os.path.abspath(path)
    probe = path
    while not os.path.exists(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    if IS_WINDOWS:
        drive = os.path.splitdrive(probe)[0][:1].upper()
        return {d.key for d in disks for p in d.partitions if p.letter and p.letter == drive}
    try:
        return disks_backing_dev(os.stat(probe).st_dev)
    except OSError:
        return set()


def read_fstab(path="/etc/fstab"):
    """Entradas de fstab: lista de (especificación, punto de montaje, opciones)."""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    out.append((parts[0], parts[1], parts[3] if len(parts) > 3 else ""))
    except OSError:
        pass
    return out


def _fstab_refs(node, fstab) -> List[str]:
    """Puntos de montaje de fstab que apuntan a este dispositivo (o a sus hijos)."""
    nodes = [node] + list(_walk(node))
    keys = set()
    for n in nodes:
        for k, v in (("UUID", n.get("uuid")), ("PARTUUID", n.get("partuuid")),
                     ("LABEL", n.get("label")), ("PARTLABEL", n.get("partlabel"))):
            if v:
                keys.add(f"{k}={v}")
                keys.add(f'{k}="{v}"')
        keys.add(_path(n))
    refs = []
    for spec, mp, opts in fstab:
        target = spec
        if spec.startswith("/dev/"):
            try:
                target = os.path.realpath(spec)
            except OSError:
                pass
        if spec in keys or target in keys:
            refs.append(mp + (" (nofail)" if "nofail" in opts.split(",") else ""))
    return refs


def _list_disks_linux() -> List[Disk]:
    tree = lsblk_tree()
    fstab = read_fstab()
    root_disks = set()
    try:
        root_disks = disks_backing_dev(os.stat("/").st_dev)
    except OSError:
        pass
    disks = []
    candidates = [n for n in tree if n.get("type") == "disk"
                  and not str(n.get("name", "")).startswith(("zram", "ram"))]
    for idx, node in enumerate(sorted(candidates, key=lambda n: n.get("name", ""))):
        dev = _path(node)
        name = os.path.basename(dev)
        style = (node.get("pttype") or "").upper()
        disk = Disk(
            key=dev, number=idx, device=dev,
            model=(node.get("model") or "").strip(), serial=(node.get("serial") or "").strip(),
            size=_int(node.get("size")),
            sector_size=_int(node.get("log-sec")) or _sysfs_read_int(f"/sys/block/{name}/queue/logical_block_size", 512),
            removable=_b(node.get("rm")), read_only=_b(node.get("ro")),
            bus=(node.get("tran") or "").upper(),
            style="MBR" if style == "DOS" else style or "RAW",
            rotational=_b(node.get("rota")) if node.get("rota") is not None else None,
            discard=_int(node.get("disc-max")) > 0,
            status="En línea",
        )
        reasons, busy = [], []
        all_mounts = _mounts(node) + [m for d in _walk(node) for m in _mounts(d)]
        for m in all_mounts:
            if is_critical_mount(m):
                reasons.append("swap activa" if m == "[SWAP]" else f"montado en {m}")
        if dev in root_disks:
            reasons.append("contiene el sistema raíz")
        for d in _walk(node):
            t = str(d.get("type") or "")
            if t.startswith(BUSY_TYPES):
                busy.append(f"{d.get('name')} ({t})")

        children = [c for c in (node.get("children") or []) if c.get("type") == "part"]
        whole = False
        if not children and node.get("fstype"):
            # Disco sin tabla de particiones con el sistema de archivos directamente encima
            children = [dict(node, children=[], partn=1, parttypename="")]
            whole = True
        for i, ch in enumerate(sorted(children, key=lambda c: _int(c.get("partn"), 0)), start=1):
            pdev = _path(ch)
            pname = os.path.basename(pdev)
            mounts = _mounts(ch) + [m for d in _walk(ch) for m in _mounts(d)]
            free = ch.get("fsavail")
            if free is None:
                free = next((d.get("fsavail") for d in _walk(ch) if d.get("fsavail") is not None), None)
            ptype = ch.get("parttypename") or ""
            part = Partition(
                key=pdev, number=_int(ch.get("partn"), i), disk_key=disk.key, device=pdev,
                mountpoints=mounts, size=_int(ch.get("size")),
                start=0 if whole else _sysfs_read_int(f"/sys/class/block/{pname}/start") * 512,
                free=_int(free) if free is not None else None,
                fs=ch.get("fstype") or "", label=ch.get("label") or "", ptype=ptype, whole_disk=whole,
                uuid=ch.get("uuid") or "", partuuid=ch.get("partuuid") or "",
                partlabel=ch.get("partlabel") or "", fstab=_fstab_refs(ch, fstab),
            )
            crit = [("swap activa" if m == "[SWAP]" else f"montado en {m}") for m in mounts if is_critical_mount(m)]
            if crit:
                part.critical = True
                part.critical_reason = "; ".join(crit)
            if any(k in ptype.lower() for k in LINUX_SPECIAL_PTYPES) or part.fs == "swap":
                part.special = True
                part.protected = True
                part.protect_reason = f"Partición {ptype or part.fs}"
            pbusy = [f"{d.get('name')} ({d.get('type')})" for d in _walk(ch)
                     if str(d.get("type") or "").startswith(BUSY_TYPES)]
            if pbusy:
                part.busy_reason = "en uso por " + ", ".join(pbusy)
            disk.partitions.append(part)

        if busy:
            disk.busy_reason = "volúmenes activos: " + ", ".join(busy)
        _apply_system_flag(disk, reasons)
        disks.append(disk)
    return disks


def _apply_system_flag(disk: Disk, reasons: List[str]):
    if not reasons:
        return
    seen = []
    for r in reasons:
        if r not in seen:
            seen.append(r)
    disk.is_system = True
    disk.system_reason = "; ".join(seen)
    for p in disk.partitions:
        p.protected = True
        if not p.protect_reason:
            p.protect_reason = "Disco del sistema"


# ======================================================================
# COMPROBACIONES DE SEGURIDAD (reutilizadas justo antes de escribir)
# Todas devuelven None si se permite, o el motivo por el que no.
# ======================================================================
def check_disk_writable(disk: Optional[Disk]) -> Optional[str]:
    """Para escribir en el disco entero o cambiar su tabla de particiones."""
    if disk is None:
        return "El disco ya no existe. Actualiza la lista de discos."
    if disk.is_system:
        return f"Es el disco del sistema ({disk.system_reason}). Nunca se modifica."
    if disk.read_only:
        return "El disco es de solo lectura."
    if disk.busy_reason:
        return (f"El disco tiene {disk.busy_reason}. Desactívalos "
                f"(vgchange -an / cryptsetup close / mdadm --stop) antes.")
    for p in disk.partitions:
        if p.fstab:
            return f"{p.short_name}: " + fstab_reason(p)
    return None


def check_part_destructive(disk: Optional[Disk], part: Optional[Partition]) -> Optional[str]:
    """Formatear, eliminar, redimensionar, reparar…"""
    if disk is None or part is None:
        return "La partición ya no existe. Actualiza la lista de discos."
    if disk.is_system:
        return f"La partición está en el disco del sistema ({disk.system_reason})."
    if part.critical:
        return f"Partición del sistema: {part.critical_reason}."
    if part.protected:
        return f"Partición protegida: {part.protect_reason}."
    if part.busy_reason:
        return f"La partición está {part.busy_reason}. Desactívala manualmente antes."
    if disk.read_only:
        return "El disco es de solo lectura."
    if part.fstab:
        return fstab_reason(part)
    return None


def fstab_reason(part: Partition) -> str:
    return (f"La partición está en /etc/fstab (se monta al arrancar en {', '.join(part.fstab)}). "
            f"Si se borra o cambia, el arranque podría fallar. Quita o comenta esa línea "
            f"de /etc/fstab antes.")


def check_part_meta(disk: Optional[Disk], part: Optional[Partition]) -> Optional[str]:
    """Cambios que no borran datos (letra, etiqueta). Se permiten también en el
    disco del sistema, salvo en las particiones críticas o especiales."""
    if disk is None or part is None:
        return "La partición ya no existe. Actualiza la lista de discos."
    if part.critical:
        return f"Partición del sistema: {part.critical_reason}."
    if part.special:
        return f"Partición protegida: {part.protect_reason}."
    if part.busy_reason:
        return f"La partición está {part.busy_reason}."
    if disk.read_only:
        return "El disco es de solo lectura."
    if not part.fs:
        return "La partición no tiene sistema de archivos."
    return None


# Compatibilidad con el nombre anterior
check_format_allowed = check_part_destructive


def check_clone_allowed(src: Optional[Disk], dst: Optional[Disk]) -> Optional[str]:
    if src is None or dst is None:
        return "Alguno de los discos ya no existe. Actualiza la lista."
    if src.key == dst.key:
        return "El origen y el destino son el mismo disco."
    reason = check_disk_writable(dst)
    if reason:
        return "Destino: " + reason
    if src.size <= 0:
        return "No se pudo determinar el tamaño del disco origen."
    if dst.size < src.size:
        return (f"El destino ({format_bytes(dst.size)}) es más pequeño que el origen "
                f"({format_bytes(src.size)}). Debe ser igual o mayor.")
    return None


def check_image_target(disk: Optional[Disk], image_size: int, image_path: str,
                       disks: List[Disk]) -> Optional[str]:
    """Restaurar una imagen o grabar una ISO en un disco."""
    reason = check_disk_writable(disk)
    if reason:
        return reason
    if image_size <= 0:
        return "El archivo de imagen está vacío o no se puede leer."
    if disk.size < image_size:
        return (f"El disco ({format_bytes(disk.size)}) es más pequeño que la imagen "
                f"({format_bytes(image_size)}).")
    if disk.key in disk_keys_for_path(image_path, disks):
        return "El archivo de imagen está guardado en el propio disco destino."
    return None


def check_image_source(disk: Optional[Disk], out_path: str, disks: List[Disk]) -> Optional[str]:
    """Crear una imagen de un disco en un archivo."""
    if disk is None:
        return "El disco ya no existe. Actualiza la lista de discos."
    if disk.key in disk_keys_for_path(out_path, disks):
        return "No se puede guardar la imagen dentro del mismo disco que se está copiando."
    try:
        folder = os.path.dirname(os.path.abspath(out_path))
        import shutil
        free = shutil.disk_usage(folder).free
        if free < disk.size:
            return (f"No hay espacio suficiente en la carpeta destino: hacen falta "
                    f"{format_bytes(disk.size)} y hay {format_bytes(free)} libres.")
    except OSError as e:
        return f"No se puede acceder a la carpeta destino: {e}"
    return None
