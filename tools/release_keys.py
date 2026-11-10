#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claves de firma de las actualizaciones (Ed25519). Ver RELEASING.md.

  python3 tools/release_keys.py generate CLAVE.pem   crea un par nuevo (imprime la pública)
  python3 tools/release_keys.py public   CLAVE.pem   muestra la clave pública de una privada
  python3 tools/release_keys.py sign     SHA256SUMS.txt
        firma con la clave de la variable de entorno UPDATE_SIGNING_KEY (PEM)
        o de --key CLAVE.pem; escribe SHA256SUMS.txt.sig
  python3 tools/release_keys.py verify   SHA256SUMS.txt
        comprueba la firma con las claves públicas incluidas en updater.py

Necesita: pip install cryptography
"""
import os
import sys
import base64
import argparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _public_b64(priv) -> str:
    raw = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def _load_private(path=None):
    if path:
        with open(path, "rb") as f:
            pem = f.read()
    else:
        pem = os.environ.get("UPDATE_SIGNING_KEY", "").encode()
    if not pem.strip():
        sys.exit("ERROR: no hay clave privada (variable UPDATE_SIGNING_KEY o --key).")
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        sys.exit("ERROR: la clave no es Ed25519.")
    return key


def cmd_generate(a):
    if os.path.exists(a.path):
        sys.exit(f"ERROR: {a.path} ya existe; no se sobrescribe.")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    fd = os.open(a.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    print(f"Clave privada guardada en {a.path} (permisos 600).")
    print(f"Clave pública (añádela a UPDATE_PUBLIC_KEYS en updater.py):\n{_public_b64(key)}")


def cmd_public(a):
    print(_public_b64(_load_private(a.path)))


def cmd_sign(a):
    key = _load_private(a.key)
    with open(a.file, "rb") as f:
        data = f.read()
    sig = base64.b64encode(key.sign(data)).decode()
    with open(a.file + ".sig", "w") as f:
        f.write(sig + "\n")
    print(f"Firmado: {a.file}.sig (clave pública {_public_b64(key)})")


def cmd_verify(a):
    sys.path.insert(0, ROOT)
    import updater
    with open(a.file, "rb") as f:
        data = f.read()
    with open(a.file + ".sig", "rb") as f:
        sig = base64.b64decode(f.read().strip())
    if not updater.verify_signature(data, sig):
        sys.exit("ERROR: la firma NO corresponde a ninguna clave pública de updater.py. "
                 "¿El secreto UPDATE_SIGNING_KEY es la clave correcta?")
    print("Firma válida.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate"); g.add_argument("path"); g.set_defaults(fn=cmd_generate)
    pu = sub.add_parser("public"); pu.add_argument("path"); pu.set_defaults(fn=cmd_public)
    s = sub.add_parser("sign"); s.add_argument("file"); s.add_argument("--key"); s.set_defaults(fn=cmd_sign)
    v = sub.add_parser("verify"); v.add_argument("file"); v.set_defaults(fn=cmd_verify)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
