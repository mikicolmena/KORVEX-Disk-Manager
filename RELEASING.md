# Publicar una versión

Todo se hace desde GitHub Actions, así que **sirve cualquier PC** con git y acceso al repo.

```bash
# 1. Cambia la versión (ÚNICO sitio donde está): platform_utils.py
APP_VERSION = "1.1.0"

# 2. Commit + tag con la misma versión
git commit -am "Versión 1.1.0"
git tag v1.1.0
git push && git push --tags
```

El workflow `release.yml` comprueba que el tag coincide con `APP_VERSION`, construye el
`.deb` y el `.exe`, firma `SHA256SUMS.txt` y crea la release. Las apps instaladas la verán
en «Ajustes → Actualizaciones» (o al abrirse) y se actualizarán con un clic.

## Firma de las actualizaciones

La app solo instala releases cuyo `SHA256SUMS.txt` esté firmado (Ed25519) con una clave
cuya parte pública está en `updater.py` (`UPDATE_PUBLIC_KEYS`).

| Dónde | Qué |
|---|---|
| Secreto del repo `UPDATE_SIGNING_KEY` | Clave privada (PEM). La usa el workflow para firmar. |
| Tu gestor de contraseñas / copia offline | **Copia de seguridad** de la misma clave privada. |
| `updater.py` | Clave pública (no es secreta). |

Los secretos de GitHub no se pueden volver a leer: si algún día hay que recrear el secreto
(otro repo, otra cuenta…), se hace desde la copia de seguridad:

```bash
gh secret set UPDATE_SIGNING_KEY < korvex-update-signing-key.pem
```

### Si se pierde la clave por completo

Las versiones instaladas ya no aceptarán actualizaciones automáticas firmadas con otra clave.
Genera una nueva (`python3 tools/release_keys.py generate nueva.pem`), pon su pública en
`UPDATE_PUBLIC_KEYS`, sube el secreto y publica: los usuarios tendrán que instalar esa versión
**a mano** una vez; a partir de ahí se actualiza sola otra vez.

### Rotar la clave (sin perderla)

1. Genera la nueva y **añade** su pública a `UPDATE_PUBLIC_KEYS` (sin quitar la antigua).
2. Publica esa versión firmada todavía con la clave antigua.
3. Cambia el secreto `UPDATE_SIGNING_KEY` por la nueva. Más adelante puedes quitar la pública antigua.

### Seguridad de la cuenta

Quien controle tu cuenta de GitHub puede publicar una release firmada (el workflow usa el
secreto). Protégela: **2FA activado** y, si quieres más, en *Settings → Environments* crea un
entorno `release` con *Required reviewers* y añade `environment: release` al job `publish`.
