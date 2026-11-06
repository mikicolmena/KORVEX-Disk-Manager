# -*- coding: utf-8 -*-
"""Operaciones de disco en Windows (la app ya se ejecuta como administrador).

Misma interfaz que el helper de Linux: cada operación recibe (params, ctx)
y devuelve (mensaje, datos) o lanza Fail. ctx ofrece progress(), log() y
cancelled(). Todas vuelven a leer los discos y repiten las comprobaciones
de seguridad antes de escribir.
"""
import os
import json
import shutil
import string
import subprocess
from contextlib import contextmanager

import disks as D
import rawio as R
import diag
from platform_utils import NO_WINDOW, journal_update

WIN_FS = {"ntfs": "NTFS", "exfat": "exFAT", "fat32": "FAT32"}
LABEL_MAX = {"NTFS": 32, "exFAT": 11, "FAT32": 11}


class Fail(Exception):
    pass


# ======================================================================
# UTILIDADES
# ======================================================================
def ps(script, timeout=3600, what="La operación"):
    res = D.run_powershell("$ErrorActionPreference='Stop'\n" + script, timeout=timeout)
    if res.returncode != 0:
        raise Fail(f"{what} ha fallado:\n{(res.stderr or res.stdout).strip()}")
    return res.stdout.strip()


def run_console(cmd, input_text="", timeout=6 * 3600):
    """Herramientas de consola clásicas (convert, chkdsk): salida en la página OEM."""
    r = subprocess.run(cmd, input=input_text.encode("ascii", "replace"), capture_output=True,
                       timeout=timeout, creationflags=NO_WINDOW)
    try:
        out = (r.stdout + r.stderr).decode("oem", "replace")   # códec solo disponible en Windows
    except LookupError:
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
    return r.returncode, out


def allow(reason):
    if reason:
        raise Fail(reason)


def get_disk(key, exp_size=None):
    disks = D.list_disks()
    d = D.find_disk(disks, str(key))
    if d is None:
        raise Fail("El disco ya no existe. Actualiza la lista de discos.")
    if exp_size is not None and d.size != int(exp_size):
        raise Fail("La lista de discos ha cambiado desde que la viste (¿se ha conectado o "
                   "desconectado algo?). Actualiza y vuelve a seleccionarlo.")
    return disks, d


def get_part(key):
    disks = D.list_disks()
    d, p = D.find_partition(disks, key)
    if p is None:
        raise Fail("La partición ya no existe. Actualiza la lista de discos.")
    return disks, d, p


def part_ref(d, p):
    return f"Get-Partition -DiskNumber {int(d.number)} -PartitionNumber {int(p.number)}"


def clean_label(label, fs):
    bad = "\"'\\/*?<>|:"
    label = "".join(ch for ch in str(label) if ch.isprintable() and ch not in bad)
    return label[:LABEL_MAX.get(fs, 11)]


def win_fs(fs):
    f = WIN_FS.get(str(fs).lower())
    if not f:
        raise Fail(f"Sistema de archivos no permitido en Windows: {fs}")
    return f


def _set_offline(number, offline):
    val = "$true" if offline else "$false"
    res = D.run_powershell(f"$ErrorActionPreference='Stop'; Set-Disk -Number {int(number)} -IsOffline {val}")
    return res.returncode == 0, (res.stderr or res.stdout).strip()


@contextmanager
def disk_offline(disk, ctx):
    """Pone el disco offline (desmonta todos sus volúmenes) mientras dura la
    escritura y lo restaura al terminar. Queda anotado en el registro de
    operaciones por si la app se cierra a la fuerza."""
    was_offline = not disk.online
    if not was_offline:
        ctx.log("Poniendo el disco offline (desmonta sus volúmenes)…")
        ok, err = _set_offline(disk.number, True)
        if not ok:
            raise Fail(f"No se pudo poner el disco offline. No se ha tocado nada.\n{err}")
        journal_update(offline_disk=disk.number)
    try:
        now = D.find_disk(D.list_disks(), disk.key)
        if now is None or now.online or now.is_system:
            raise Fail("El disco no ha quedado offline. Operación abortada sin escribir nada.")
        yield
    finally:
        if not was_offline:
            ok, err = _set_offline(disk.number, False)
            if ok:
                journal_update(offline_disk=None)
            else:
                ctx.log(f"Aviso: no se pudo volver a poner online el disco: {err}")
        D.run_powershell("Update-HostStorageCache")


def _verify_msg(what, res, verify, live_source=False):
    if not verify:
        return f"{what} completada (sin verificación)."
    if res["ok"]:
        return f"{what} completada y verificada.\nSHA-256: {res['sha_dst']}"
    hint = (" El origen es el disco del sistema en uso: probablemente se escribió en él durante "
            "la copia." if live_source else "")
    raise Fail(f"La verificación ha encontrado diferencias (primera en el byte {res['first_diff']}).{hint}\n"
               f"SHA-256 origen:  {res['sha_src']}\nSHA-256 destino: {res['sha_dst']}")


def used_letters():
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return {string.ascii_uppercase[i] for i in range(26) if mask & (1 << i)}
    except Exception:
        return {L for L in string.ascii_uppercase if os.path.exists(f"{L}:\\")}


def free_letters():
    used = used_letters()
    return [L for L in string.ascii_uppercase[3:] if L not in used]   # desde la D


# ======================================================================
# OPERACIONES DE PARTICIÓN
# ======================================================================
def op_format(p, ctx):
    fs = win_fs(p["fs"])
    _, d, part = get_part(p["part"])
    allow(D.check_part_destructive(d, part))
    label = clean_label(p.get("label", ""), fs)
    full = "$false" if p.get("quick", True) else "$true"
    ctx.log(f"Formateando {part.display_name} en {fs}…")
    ps(f"{part_ref(d, part)} | Format-Volume -FileSystem {fs} -NewFileSystemLabel {D.ps_quote(label)} "
       f"-Full:{full} -Force -Confirm:$false | Out-Null", timeout=24 * 3600, what="El formateo")
    return f"{part.display_name} se ha formateado correctamente en {fs}.", {}


def op_label(p, ctx):
    _, d, part = get_part(p["part"])
    allow(D.check_part_meta(d, part))
    fs = part.fs.upper().replace("EXFAT", "exFAT")
    label = clean_label(p.get("label", ""), fs if fs in LABEL_MAX else "NTFS")
    ps(f"{part_ref(d, part)} | Get-Volume | Set-Volume -NewFileSystemLabel {D.ps_quote(label)}",
       what="El cambio de etiqueta")
    return f"Etiqueta cambiada a «{label}».", {}


def op_letter(p, ctx):
    _, d, part = get_part(p["part"])
    allow(D.check_part_meta(d, part))
    new = str(p.get("letter") or "").upper()[:1]
    if new:
        if new not in string.ascii_uppercase or new in ("A", "B", "C"):
            raise Fail("Letra no válida.")
        if new in used_letters():
            raise Fail(f"La letra {new}: ya está en uso.")
        ps(f"Set-Partition -DiskNumber {d.number} -PartitionNumber {part.number} -NewDriveLetter {new}",
           what="El cambio de letra")
        return f"La partición ahora es {new}:", {}
    if not part.letter:
        raise Fail("La partición no tiene letra.")
    ps(f"Remove-PartitionAccessPath -DiskNumber {d.number} -PartitionNumber {part.number} "
       f"-AccessPath {D.ps_quote(part.letter + ':\\')}", what="Quitar la letra")
    return f"Se ha quitado la letra {part.letter}: (los datos siguen ahí).", {}


def op_convert(p, ctx):
    """FAT32 -> NTFS sin perder datos (convert.exe)."""
    _, d, part = get_part(p["part"])
    allow(D.check_part_destructive(d, part))
    if part.fs.upper() not in ("FAT32", "FAT"):
        raise Fail("Solo se puede convertir sin pérdida de FAT32 a NTFS.")
    if not part.letter:
        raise Fail("La partición necesita una letra para convertirla. Asígnale una antes.")
    ctx.log(f"Convirtiendo {part.letter}: a NTFS (conserva los datos)…")
    rc, out = run_console(["convert", f"{part.letter}:", "/FS:NTFS", "/X"],
                          input_text=f"{part.label}\r\nS\r\nY\r\n")
    if rc != 0:
        raise Fail(f"convert ha fallado (código {rc}). No se ha modificado el volumen.\n{out.strip()[-2000:]}")
    return f"{part.letter}: convertido a NTFS conservando los datos.", {"output": out[-6000:]}


def op_fsck(p, ctx):
    repair = bool(p.get("repair"))
    _, d, part = get_part(p["part"])
    if repair:
        allow(D.check_part_destructive(d, part))
    elif part.special or not part.letter:
        allow(D.check_part_meta(d, part))   # sin letra: mismas reglas que un cambio no destructivo
    if part.letter:
        cmd = ["chkdsk", f"{part.letter}:"] + (["/f", "/x"] if repair else [])
        ctx.log("Ejecutando: " + " ".join(cmd))
        rc, out = run_console(cmd, input_text="N\r\n")
        clean = rc == 0
        if repair:
            if rc >= 3:
                raise Fail(f"chkdsk no ha podido reparar el volumen (código {rc}).\n{out.strip()[-3000:]}")
            msg = "No se han encontrado errores." if clean else "Errores corregidos."
        else:
            msg = "No se han encontrado errores." if clean else "Se han encontrado errores. Usa «Reparar»."
        return msg, {"output": out[-6000:], "clean": clean}
    mode = "-OfflineScanAndFix" if repair else "-Scan"
    out = ps(f"{part_ref(d, part)} | Get-Volume | Repair-Volume {mode}", timeout=24 * 3600,
             what="La comprobación")
    clean = "NoErrorsFound" in out
    return ("No se han encontrado errores." if clean else f"Resultado: {out}"), {"output": out, "clean": clean}


def op_part_create(p, ctx):
    fs = win_fs(p["fs"])
    _, d = get_disk(p["disk"], p.get("exp_size"))
    allow(D.check_disk_writable(d))
    start, size = int(p["start"]), int(p["size"]) // D.MiB * D.MiB
    if not any(a <= start and start + size <= a + g for a, g in d.free_regions()):
        raise Fail("El espacio elegido ya no está libre. Actualiza la lista de discos.")
    label = clean_label(p.get("label", ""), fs)
    ctx.log(f"Creando partición de {D.format_bytes(size)}…")
    ps(f"$p = New-Partition -DiskNumber {d.number} -Offset {start} -Size {size} -AssignDriveLetter\n"
       f"$p | Format-Volume -FileSystem {fs} -NewFileSystemLabel {D.ps_quote(label)} -Force -Confirm:$false | Out-Null",
       what="La creación de la partición")
    return f"Partición de {D.format_bytes(size)} creada y formateada en {fs}.", {}


def op_part_delete(p, ctx):
    _, d, part = get_part(p["part"])
    allow(D.check_part_destructive(d, part))
    allow(D.check_disk_writable(d))
    ps(f"Remove-Partition -DiskNumber {d.number} -PartitionNumber {part.number} -Confirm:$false",
       what="La eliminación")
    return f"Partición {part.display_name} eliminada.", {}


def op_part_resize(p, ctx):
    _, d, part = get_part(p["part"])
    allow(D.check_part_destructive(d, part))
    allow(D.check_disk_writable(d))
    if part.fs.upper() not in ("NTFS", "REFS"):
        raise Fail("En Windows solo se pueden redimensionar volúmenes NTFS.")
    new_size = int(p["new_size"]) // D.MiB * D.MiB
    js = ps(f"Get-PartitionSupportedSize -DiskNumber {d.number} -PartitionNumber {part.number} | "
            f"Select-Object SizeMin, SizeMax | ConvertTo-Json -Compress", what="La consulta de tamaños")
    lim = json.loads(js)
    mn, mx = int(lim["SizeMin"]), int(lim["SizeMax"])
    if not (mn <= new_size <= mx):
        raise Fail(f"Tamaño fuera de los límites permitidos por Windows: entre "
                   f"{D.format_bytes(mn)} y {D.format_bytes(mx)}. No se ha modificado nada.")
    ctx.log(f"Redimensionando a {D.format_bytes(new_size)}…")
    ps(f"Resize-Partition -DiskNumber {d.number} -PartitionNumber {part.number} -Size {new_size}",
       timeout=24 * 3600, what="El redimensionado")
    return f"{part.display_name} redimensionada a {D.format_bytes(new_size)}.", {}


# ======================================================================
# OPERACIONES DE DISCO COMPLETO
# ======================================================================
def op_init(p, ctx):
    style = "GPT" if str(p.get("style", "gpt")).lower() == "gpt" else "MBR"
    _, d = get_disk(p["disk"], p.get("exp_size"))
    allow(D.check_disk_writable(d))
    script = (f"$n = {d.number}\n"
              f"if ((Get-Disk -Number $n).IsOffline) {{ Set-Disk -Number $n -IsOffline $false }}\n"
              f"if ((Get-Disk -Number $n).PartitionStyle -ne 'RAW') {{ Clear-Disk -Number $n -RemoveData -RemoveOEM -Confirm:$false }}\n"
              f"Initialize-Disk -Number $n -PartitionStyle {style}\n")
    create = p.get("create")
    if create:
        fs = win_fs(create["fs"])
        label = clean_label(create.get("label", ""), fs)
        script += (f"New-Partition -DiskNumber $n -UseMaximumSize -AssignDriveLetter | "
                   f"Format-Volume -FileSystem {fs} -NewFileSystemLabel {D.ps_quote(label)} -Force -Confirm:$false | Out-Null\n")
    ctx.log(f"Inicializando {d.title} como {style}…")
    ps(script, what="La inicialización")
    if create:
        return f"Disco inicializado ({style}) con una partición {create['fs'].upper()}.", {}
    return f"Disco inicializado como {style}. Está vacío.", {}


def op_clone(p, ctx):
    verify = bool(p.get("verify", True))
    disks = D.list_disks()
    src, dst = D.find_disk(disks, p["src"]), D.find_disk(disks, p["dst"])
    allow(D.check_clone_allowed(src, dst))
    if src.size != int(p["src_size"]) or dst.size != int(p["dst_size"]):
        raise Fail("La lista de discos ha cambiado desde que la viste. Actualiza y vuelve a "
                   "seleccionar los discos.")
    ctx.log(f"Origen:  {src.describe()}")
    ctx.log(f"Destino: {dst.describe()}")
    if src.is_system:
        ctx.log("Aviso: el origen es el disco del sistema en uso; la copia puede no ser 100 % "
                "consistente si hay escrituras durante el proceso.")
    with disk_offline(dst, ctx):
        res = R.copy_data(src.device, dst.device, src.size, "device", verify, ctx.progress, ctx.cancelled)
    extra = ("\n\nSi Windows mantiene el disco destino offline es por la «colisión de firma» con el "
             "origen (ambos son idénticos): desconecta uno de los dos o ponlo online desde "
             "Administración de discos.")
    if dst.size > src.size:
        extra += "\n\nEl destino es mayor que el origen: el espacio sobrante queda sin asignar."
    return _verify_msg("Clonación", res, verify, src.is_system) + extra, {}


def op_erase(p, ctx):
    method = p.get("method", "zero")
    if method not in ("zero", "random"):
        raise Fail("Método de borrado no disponible en Windows.")
    verify = bool(p.get("verify", True))
    _, d = get_disk(p["disk"], p.get("exp_size"))
    allow(D.check_disk_writable(d))
    ctx.log(f"Borrado seguro de {d.describe()} · método: {method}")
    with disk_offline(d, ctx):
        res = R.wipe_device(d.device, d.size, method, verify, ctx.progress, ctx.cancelled)
    if not res["ok"]:
        raise Fail("La verificación ha encontrado datos que no se han borrado. El disco podría estar fallando.")
    return ("Disco borrado por completo" + (" y verificado." if res.get("verified") else ".")
            + " Usa «Inicializar disco» para volver a usarlo."), {}


def op_image_create(p, ctx):
    verify = bool(p.get("verify", True))
    path = os.path.abspath(p["path"])
    disks, d = get_disk(p["disk"], p.get("exp_size"))
    allow(D.check_image_source(d, path, disks))
    ctx.log(f"Creando imagen de {d.describe()} en {path}")
    try:
        res = R.copy_data(d.device, path, d.size, "file", verify, ctx.progress, ctx.cancelled)
    except (R.OpCancelled, R.OpError):
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    msg = _verify_msg("Imagen", res, verify, d.is_system)
    R.write_sidecar_sha(path, res["sha_src"])
    return msg + f"\n\nArchivo: {path}\nSuma SHA-256 guardada en {os.path.basename(path)}.sha256", {}


def op_image_restore(p, ctx):
    verify = bool(p.get("verify", True))
    path = os.path.abspath(p["path"])
    if not os.path.isfile(path):
        raise Fail("El archivo de imagen no existe.")
    size = os.path.getsize(path)
    disks, d = get_disk(p["disk"], p.get("exp_size"))
    allow(D.check_image_target(d, size, path, disks))
    expected = R.read_sidecar_sha(path) if p.get("precheck", True) else None
    if expected:
        ctx.log("Comprobando la integridad de la imagen antes de escribir…")
        if R.hash_file(path, ctx.progress, ctx.cancelled) != expected:
            raise Fail("La imagen está dañada: su SHA-256 no coincide con el archivo .sha256. "
                       "No se ha escrito nada en el disco.")
    ctx.log(f"Escribiendo {os.path.basename(path)} en {d.describe()}")
    with disk_offline(d, ctx):
        res = R.copy_data(path, d.device, size, "device", verify, ctx.progress, ctx.cancelled)
    return _verify_msg("Grabación de la ISO" if p.get("kind") == "iso" else "Restauración", res, verify), {}


# ======================================================================
# DIAGNÓSTICO (solo lectura)
# ======================================================================
def op_smart(p, ctx):
    _, d = get_disk(p["disk"])
    smartctl = shutil.which("smartctl")
    if smartctl:
        r = subprocess.run([smartctl, "-a", "-j", f"/dev/pd{d.number}"], capture_output=True,
                           creationflags=NO_WINDOW)
        try:
            s = diag.smart_from_smartctl(json.loads(r.stdout.decode("utf-8", "replace") or "{}"))
            if s["passed"] is not None or s["attributes"]:
                return "Datos de salud leídos (smartctl).", {"smart": s}
        except ValueError:
            pass
    out = ps(f"""
$pd = Get-PhysicalDisk | Where-Object {{ [string]$_.DeviceId -eq '{d.number}' }} | Select-Object -First 1
if (-not $pd) {{ throw 'No se encontró el disco físico' }}
$rc = $null
try {{ $rc = $pd | Get-StorageReliabilityCounter }} catch {{}}
[pscustomobject]@{{
  health = [string]$pd.HealthStatus; op = [string]($pd.OperationalStatus -join ',')
  model = [string]$pd.FriendlyName; serial = [string]$pd.SerialNumber; fw = [string]$pd.FirmwareVersion
  temp = $rc.Temperature; tempmax = $rc.TemperatureMax; wear = $rc.Wear; hours = $rc.PowerOnHours
  cycles = $rc.StartStopCycleCount; rerr = $rc.ReadErrorsTotal; rerru = $rc.ReadErrorsUncorrected
  werr = $rc.WriteErrorsTotal; werru = $rc.WriteErrorsUncorrected; latr = $rc.ReadLatencyMax; latw = $rc.WriteLatencyMax
}} | ConvertTo-Json -Compress""", what="La lectura de salud")
    w = json.loads(out)
    health = str(w.get("health") or "")
    s = {"source": "Windows", "model": w.get("model", ""), "serial": w.get("serial", ""),
         "firmware": w.get("fw", ""), "passed": True if health == "Healthy" else False if health == "Unhealthy" else None,
         "temperature": w.get("temp") or None, "power_on_hours": w.get("hours"),
         "power_cycles": w.get("cycles"), "wear_pct": w.get("wear"), "warnings": [], "notes": [],
         "attributes": []}
    labels = [("health", "Estado (Windows)"), ("op", "Estado operativo"), ("tempmax", "Temperatura máxima (°C)"),
              ("rerr", "Errores de lectura"), ("rerru", "Errores de lectura no corregidos"),
              ("werr", "Errores de escritura"), ("werru", "Errores de escritura no corregidos"),
              ("latr", "Latencia máx. lectura (ms)"), ("latw", "Latencia máx. escritura (ms)")]
    for k, name in labels:
        if w.get(k) not in (None, ""):
            s["attributes"].append({"id": "", "name": name, "value": "", "worst": "", "thresh": "",
                                    "raw": w[k], "failing": False})
    if health == "Warning":
        s["warnings"].append("Windows indica que el disco tiene problemas (estado: Warning)")
    if w.get("rerru"):
        s["warnings"].append(f"Errores de lectura no corregidos: {w['rerru']}")
    if w.get("werru"):
        s["warnings"].append(f"Errores de escritura no corregidos: {w['werru']}")
    if s["temperature"] is None and not s["attributes"]:
        s["notes"].append("Windows no ofrece contadores detallados para este disco. "
                          "Instala smartmontools para ver todos los atributos SMART.")
    return "Datos de salud leídos.", {"smart": s}


def op_bench(p, ctx):
    _, d = get_disk(p["disk"], p.get("exp_size"))
    ctx.log(f"Test de lectura en {d.describe()} (no se escribe nada)")
    return "Test de velocidad completado.", {
        "bench": R.benchmark(d.device, d.size, d.sector_size, progress=ctx.progress, cancelled=ctx.cancelled)}


def op_scan(p, ctx):
    _, d = get_disk(p["disk"], p.get("exp_size"))
    ctx.log(f"Escaneo de superficie de {d.describe()} (solo lectura)")
    res = R.surface_scan(d.device, d.size, d.sector_size, ctx.progress, ctx.cancelled)
    msg = ("No se han encontrado sectores defectuosos." if not res["bad_count"] else
           f"Se han encontrado {res['bad_count']} sectores ilegibles. Haz copia de seguridad cuanto antes.")
    return msg, {"scan": res}


OPS = {
    "format": op_format, "label": op_label, "letter": op_letter, "convert": op_convert,
    "fsck": op_fsck, "part_create": op_part_create, "part_delete": op_part_delete,
    "part_resize": op_part_resize, "init": op_init, "clone": op_clone, "erase": op_erase,
    "image_create": op_image_create, "image_restore": op_image_restore,
    "smart": op_smart, "bench": op_bench, "scan": op_scan,
}
