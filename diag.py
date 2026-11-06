# -*- coding: utf-8 -*-
"""Normalización de datos de salud (SMART) y comprobación de sistemas de
archivos. Sin Qt: lo usan el helper de Linux y las operaciones de Windows."""

# Atributos SMART ATA que indican problemas físicos si su valor bruto es > 0
ATA_CRITICAL = {5: "Sectores reasignados", 187: "Errores no corregibles",
                188: "Timeouts de comando", 197: "Sectores pendientes",
                198: "Sectores no corregibles offline", 10: "Reintentos de arranque"}


def _raw(attr):
    raw = attr.get("raw") or {}
    v = raw.get("value")
    return v if isinstance(v, int) else None


def smart_from_smartctl(js: dict) -> dict:
    """Convierte la salida de `smartctl -a -j` en un resumen común."""
    out = {"source": "smartctl", "model": js.get("model_name") or js.get("model_family") or "",
           "serial": js.get("serial_number", ""), "firmware": js.get("firmware_version", ""),
           "passed": None, "temperature": None, "power_on_hours": None, "power_cycles": None,
           "wear_pct": None, "warnings": [], "attributes": []}
    st = js.get("smart_status")
    if isinstance(st, dict) and "passed" in st:
        out["passed"] = bool(st["passed"])
    t = js.get("temperature") or {}
    out["temperature"] = t.get("current")
    out["power_on_hours"] = (js.get("power_on_time") or {}).get("hours")
    out["power_cycles"] = js.get("power_cycle_count")

    # ---- ATA / SATA ----
    for a in (js.get("ata_smart_attributes") or {}).get("table", []) or []:
        raw = _raw(a)
        out["attributes"].append({
            "id": a.get("id"), "name": a.get("name", ""), "value": a.get("value"),
            "worst": a.get("worst"), "thresh": a.get("thresh"),
            "raw": (a.get("raw") or {}).get("string", raw),
            "failing": bool(a.get("when_failed")),
        })
        aid = a.get("id")
        if aid in ATA_CRITICAL and raw:
            out["warnings"].append(f"{ATA_CRITICAL[aid]}: {raw}")
        if a.get("when_failed"):
            out["warnings"].append(f"Atributo {a.get('name')} por debajo del umbral ({a.get('when_failed')})")
        if aid in (177, 231, 233, 202) and out["wear_pct"] is None and a.get("value") is not None:
            # Wear leveling / SSD life left: el valor normalizado es la vida restante
            out["wear_pct"] = max(0, 100 - int(a["value"]))

    # ---- NVMe ----
    nv = js.get("nvme_smart_health_information_log")
    if nv:
        out["wear_pct"] = nv.get("percentage_used")
        if out["temperature"] is None:
            out["temperature"] = nv.get("temperature")
        if out["power_on_hours"] is None:
            out["power_on_hours"] = nv.get("power_on_hours")
        if out["power_cycles"] is None:
            out["power_cycles"] = nv.get("power_cycles")
        for k, label in (("critical_warning", "Aviso crítico"), ("media_errors", "Errores de medio"),
                         ("num_err_log_entries", "Entradas en el registro de errores"),
                         ("available_spare", "Reserva disponible (%)"),
                         ("unsafe_shutdowns", "Apagados no seguros"),
                         ("data_units_written", "Unidades escritas"),
                         ("data_units_read", "Unidades leídas")):
            if k in nv:
                out["attributes"].append({"id": "", "name": label, "value": "", "worst": "",
                                          "thresh": "", "raw": nv[k], "failing": False})
        if nv.get("critical_warning"):
            out["warnings"].append(f"El SSD NVMe informa de un aviso crítico ({nv['critical_warning']})")
        if nv.get("media_errors"):
            out["warnings"].append(f"Errores de medio: {nv['media_errors']}")
        spare, th = nv.get("available_spare"), nv.get("available_spare_threshold")
        if spare is not None and th is not None and spare <= th:
            out["warnings"].append("La reserva del SSD está por debajo del umbral")

    if out["wear_pct"] is not None and out["wear_pct"] >= 90:
        out["warnings"].append(f"Desgaste del SSD muy alto ({out['wear_pct']} %)")
    if out["passed"] is False:
        out["warnings"].insert(0, "El disco informa de que la autoevaluación SMART ha FALLADO")
    msgs = (js.get("smartctl") or {}).get("messages") or []
    out["notes"] = [m.get("string", "") for m in msgs if m.get("string")]
    return out


def smart_verdict(s: dict) -> str:
    """'good' | 'warn' | 'bad' | 'unknown'"""
    if s.get("passed") is False:
        return "bad"
    if s.get("warnings"):
        return "warn"
    if s.get("passed") is True:
        return "good"
    return "unknown"


# Comandos de comprobación/reparación por sistema de archivos (Linux)
FSCK_COMMANDS = {
    "ext2": (["e2fsck", "-f", "-n"], ["e2fsck", "-f", "-y"]),
    "ext3": (["e2fsck", "-f", "-n"], ["e2fsck", "-f", "-y"]),
    "ext4": (["e2fsck", "-f", "-n"], ["e2fsck", "-f", "-y"]),
    "ntfs": (["ntfsfix", "-n"], ["ntfsfix", "-d"]),
    "fat32": (["fsck.vfat", "-n"], ["fsck.vfat", "-a", "-w"]),
    "exfat": (["fsck.exfat", "-n"], ["fsck.exfat", "-y"]),
    "xfs": (["xfs_repair", "-n"], ["xfs_repair"]),
    "btrfs": (["btrfs", "check", "--readonly"], None),
}

# Cambio de etiqueta (Linux): (comando, necesita desmontar, longitud máxima)
LABEL_COMMANDS = {
    "ext2": (lambda d, l: ["e2label", d, l], False, 16),
    "ext3": (lambda d, l: ["e2label", d, l], False, 16),
    "ext4": (lambda d, l: ["e2label", d, l], False, 16),
    "ntfs": (lambda d, l: ["ntfslabel", d, l], True, 32),
    "exfat": (lambda d, l: ["exfatlabel", d, l], True, 11),
    "fat32": (lambda d, l: ["fatlabel", d, l.upper()], True, 11),
    "xfs": (lambda d, l: ["xfs_admin", "-L", l or "--", d], True, 12),
}
