#!/bin/bash
# build_deb.sh — Construye el paquete .deb de KORVEX Disk Manager
set -e

PKG_NAME="korvex-disk-manager"
PKG_VERSION="1.0.0"
PKG_ARCH="all"
BUILD_DIR="$(pwd)/build_deb"
ROOT="${BUILD_DIR}/${PKG_NAME}_${PKG_VERSION}_${PKG_ARCH}"
LIB_DIR="/usr/lib/${PKG_NAME}"
POLICY_ID="com.korvex.diskmanager.helper"

MODULES="main.py ui.py style.py disks.py operations.py rawio.py diag.py win_ops.py helper_client.py platform_utils.py korvex_helper.py"

echo "🔨 Construyendo ${PKG_NAME} ${PKG_VERSION}..."

# =============================================================================
# Comprobaciones previas
# =============================================================================
for f in ${MODULES}; do
    if [ ! -f "$f" ]; then
        echo "❌ Falta $f"
        exit 1
    fi
done

for f in KorvexLogo-256.png KorvexLogo-128.png; do
    if [ ! -f "$f" ]; then
        echo "⚠️  Falta $f"
        echo "    Genera primero los iconos reducidos:"
        echo "      sudo apt install imagemagick"
        echo "      convert KorvexLogo.png -resize 256x256 KorvexLogo-256.png"
        echo "      convert KorvexLogo.png -resize 128x128 KorvexLogo-128.png"
        exit 1
    fi
done

# =============================================================================
# Limpiar
# =============================================================================
rm -rf "${BUILD_DIR}"
mkdir -p "${ROOT}/DEBIAN"
mkdir -p "${ROOT}/usr/bin"
mkdir -p "${ROOT}${LIB_DIR}"
mkdir -p "${ROOT}/usr/share/applications"
mkdir -p "${ROOT}/usr/share/icons/hicolor/128x128/apps"
mkdir -p "${ROOT}/usr/share/icons/hicolor/256x256/apps"
mkdir -p "${ROOT}/usr/share/polkit-1/actions"
mkdir -p "${ROOT}/usr/share/doc/${PKG_NAME}"

# =============================================================================
# Código Python
# =============================================================================
for f in ${MODULES}; do
    install -m 644 "$f" "${ROOT}${LIB_DIR}/$f"
done
cp KorvexLogo-256.png KorvexLogo-128.png "${ROOT}${LIB_DIR}/"
chmod 644 "${ROOT}${LIB_DIR}"/*.png
# El helper se lanza con pkexec: tiene que ser ejecutable y propiedad de root
chmod 755 "${ROOT}${LIB_DIR}/korvex_helper.py"

# =============================================================================
# Launcher
# =============================================================================
cat > "${ROOT}/usr/bin/${PKG_NAME}" <<EOF
#!/bin/bash
exec python3 ${LIB_DIR}/main.py "\$@"
EOF
chmod 755 "${ROOT}/usr/bin/${PKG_NAME}"

# =============================================================================
# .desktop
# =============================================================================
cat > "${ROOT}/usr/share/applications/${PKG_NAME}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=KORVEX Disk Manager
GenericName=Disk Manager
GenericName[es]=Gestor de discos
Comment=View, format and clone disks
Comment[es]=Ver, formatear y clonar discos
Exec=${PKG_NAME}
Icon=${PKG_NAME}
Terminal=false
Categories=System;Utility;Filesystem;
Keywords=disk;partition;format;clone;usb;disco;particion;formatear;clonar;
StartupNotify=true
StartupWMClass=KORVEX Disk Manager
EOF
chmod 644 "${ROOT}/usr/share/applications/${PKG_NAME}.desktop"

# =============================================================================
# Política de polkit (mensaje propio al pedir la contraseña)
# =============================================================================
cat > "${ROOT}/usr/share/polkit-1/actions/${POLICY_ID}.policy" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>KORVEX</vendor>
  <vendor_url>https://github.com/mikicolmena/KORVEX-Disk-Manager</vendor_url>
  <action id="${POLICY_ID}">
    <description>Modify disks with KORVEX Disk Manager</description>
    <description xml:lang="es">Modificar discos con KORVEX Disk Manager</description>
    <message>Authentication is required to access or modify disks</message>
    <message xml:lang="es">Se necesita autenticación para acceder a los discos o modificarlos</message>
    <icon_name>${PKG_NAME}</icon_name>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">${LIB_DIR}/korvex_helper.py</annotate>
  </action>
</policyconfig>
EOF
chmod 644 "${ROOT}/usr/share/polkit-1/actions/${POLICY_ID}.policy"

# =============================================================================
# Iconos
# =============================================================================
cp KorvexLogo-256.png "${ROOT}/usr/share/icons/hicolor/256x256/apps/${PKG_NAME}.png"
cp KorvexLogo-128.png "${ROOT}/usr/share/icons/hicolor/128x128/apps/${PKG_NAME}.png"

# =============================================================================
# Copyright
# =============================================================================
cat > "${ROOT}/usr/share/doc/${PKG_NAME}/copyright" <<'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: KORVEX Disk Manager
Source: https://github.com/mikicolmena/KORVEX-Disk-Manager

Files: *
Copyright: 2026 mikicolmena
License: MIT
EOF
if [ -f LICENSE ]; then
    { echo; sed 's/^$/./; s/^/ /' LICENSE; } >> "${ROOT}/usr/share/doc/${PKG_NAME}/copyright"
fi
chmod 644 "${ROOT}/usr/share/doc/${PKG_NAME}/copyright"

# =============================================================================
# DEBIAN/control
# =============================================================================
cat > "${ROOT}/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${PKG_VERSION}
Section: admin
Priority: optional
Architecture: ${PKG_ARCH}
Depends: python3 (>= 3.10),
         python3-pyqt6,
         util-linux,
         pkexec | policykit-1,
         e2fsprogs
Recommends: udisks2,
            fdisk,
            smartmontools,
            dosfstools,
            exfatprogs,
            ntfs-3g
Suggests: gdisk, xfsprogs, btrfs-progs
Maintainer: mikicolmena <macolmena60@gmail.com>
Homepage: https://github.com/mikicolmena/KORVEX-Disk-Manager
Description: Disk manager: partitions, cloning, images and diagnostics
 KORVEX Disk Manager is a modern PyQt6 disk manager. It shows every
 disk with its partitions and usage and lets you format, relabel,
 create, delete, resize, check and repair partitions; clone disks,
 create and restore disk images, write bootable USB drives from ISO
 files, initialize (GPT/MBR) and securely erase disks; and check disk
 health (SMART), speed and surface.
 .
 Safety first: the disk that holds the running system is always
 protected and can never be formatted or used as a clone target.
 Privileged operations run through pkexec in a separate helper that
 re-validates every target before writing.
EOF
chmod 644 "${ROOT}/DEBIAN/control"

# =============================================================================
# postinst
# =============================================================================
cat > "${ROOT}/DEBIAN/postinst" <<'EOF'
#!/bin/bash
set -e

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
fi

echo ""
echo "  ✅ KORVEX Disk Manager instalado."
echo "  Búscalo en el menú como 'KORVEX Disk Manager' o ejecuta: korvex-disk-manager"
echo ""

exit 0
EOF
chmod 755 "${ROOT}/DEBIAN/postinst"

# =============================================================================
# Construir
# =============================================================================
# Permisos estándar de Debian: carpetas 755 (no heredar el umask del usuario)
find "${ROOT}" -type d -exec chmod 755 {} +
find "${ROOT}/usr/share/icons" -type f -exec chmod 644 {} +

echo "📦 Empaquetando..."
dpkg-deb --build --root-owner-group "${ROOT}"

mv "${BUILD_DIR}/${PKG_NAME}_${PKG_VERSION}_${PKG_ARCH}.deb" .

echo ""
echo "✅ Listo: ${PKG_NAME}_${PKG_VERSION}_${PKG_ARCH}.deb"
ls -lh "${PKG_NAME}_${PKG_VERSION}_${PKG_ARCH}.deb"
echo ""
echo "Para instalar:"
echo "  sudo apt install ./${PKG_NAME}_${PKG_VERSION}_${PKG_ARCH}.deb"
echo ""
echo "Para desinstalar:"
echo "  sudo apt remove ${PKG_NAME}"
echo ""
