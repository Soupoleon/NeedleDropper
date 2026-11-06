"""Per-user secret storage for NeedleDropper (Spotify tokens).

On Windows the data is encrypted with DPAPI (``CryptProtectData`` in the
current user's scope, ``CRYPTPROTECT_UI_FORBIDDEN``) through ``ctypes``, so the
blob can only be decrypted by the same Windows account on the same machine.

On other operating systems (tests, development on Linux/macOS) there is no
DPAPI; the data is stored base64-encoded with a marker prefix and a warning is
logged once. That fallback is obfuscation, not encryption.

Note: this module is ``needledropper.secrets``; it deliberately does not use the
standard-library ``secrets`` module so the name clash can never matter.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional, Union

log = logging.getLogger("needledropper.secrets")

__all__ = [
    "SecretError", "protect", "unprotect", "save_secret", "load_secret", "delete_secret",
    "dpapi_available", "DEFAULT_ENTROPY",
]

CRYPTPROTECT_UI_FORBIDDEN = 0x01
DEFAULT_ENTROPY = b"NeedleDropper.v1"
_DESCRIPTION = "NeedleDropper secret"
_FALLBACK_MAGIC = b"NDB64:"  # prefix of non-DPAPI (base64) blobs

PathLike = Union[str, "os.PathLike[str]"]


class SecretError(Exception):
    """Encrypting or decrypting a secret failed."""


# ------------------------------------------------------------------ DPAPI ----

_dpapi: Optional[tuple[Any, Any, Any, Any]] = None  # (crypt32, kernel32, DATA_BLOB, wintypes)
_dpapi_error: Optional[str] = None
_warned_fallback = False


def _load_dpapi() -> Optional[tuple[Any, Any, Any, Any]]:
    """Bind crypt32/kernel32 lazily; returns None when DPAPI is unavailable."""
    global _dpapi, _dpapi_error
    if _dpapi is not None or _dpapi_error is not None:
        return _dpapi
    if sys.platform != "win32":
        _dpapi_error = f"DPAPI is only available on Windows (platform: {sys.platform})"
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        blob_p = ctypes.POINTER(DATA_BLOB)
        crypt32.CryptProtectData.argtypes = [blob_p, wintypes.LPCWSTR, blob_p, ctypes.c_void_p,
                                             ctypes.c_void_p, wintypes.DWORD, blob_p]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [blob_p, ctypes.POINTER(wintypes.LPWSTR), blob_p,
                                               ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_p]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        _dpapi = (crypt32, kernel32, DATA_BLOB, ctypes)
    except (OSError, AttributeError, ImportError) as exc:  # pragma: no cover - broken Windows install
        _dpapi_error = f"could not load DPAPI: {exc}"
        _dpapi = None
    return _dpapi


def dpapi_available() -> bool:
    """True when Windows DPAPI can be used on this machine."""
    return _load_dpapi() is not None


def _blob_in(api: tuple[Any, Any, Any, Any], data: bytes) -> tuple[Any, Any]:
    _, _, DATA_BLOB, ctypes = api
    buf = (ctypes.c_ubyte * max(1, len(data))).from_buffer_copy(data or b"\0")
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf


def _dpapi_call(data: bytes, entropy: bytes, encrypt: bool) -> bytes:
    api = _load_dpapi()
    if api is None:  # pragma: no cover - guarded by callers
        raise SecretError(_dpapi_error or "DPAPI unavailable")
    crypt32, kernel32, DATA_BLOB, ctypes = api
    blob_in, _keep1 = _blob_in(api, data)
    ent_blob, _keep2 = _blob_in(api, entropy)
    ent_ptr = ctypes.byref(ent_blob) if entropy else None
    blob_out = DATA_BLOB()
    if encrypt:
        ok = crypt32.CryptProtectData(ctypes.byref(blob_in), _DESCRIPTION, ent_ptr, None, None,
                                      CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, ent_ptr, None, None,
                                        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        err = ctypes.get_last_error()
        what = "CryptProtectData" if encrypt else "CryptUnprotectData"
        raise SecretError(f"{what} failed (Windows error {err}: {ctypes.FormatError(err).strip()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        if blob_out.pbData:
            kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def _warn_fallback() -> None:
    global _warned_fallback
    if not _warned_fallback:
        _warned_fallback = True
        log.warning("Windows DPAPI is not available (%s); secrets are stored base64-encoded, "
                    "NOT encrypted. Protect the NeedleDropper app folder yourself.", _dpapi_error)


# ---------------------------------------------------------------- public ----

def protect(data: bytes, *, entropy: bytes = DEFAULT_ENTROPY) -> bytes:
    """Encrypt ``data`` for the current Windows user (DPAPI); base64 fallback elsewhere."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("protect() expects bytes")
    if dpapi_available():
        return _dpapi_call(bytes(data), entropy, encrypt=True)
    _warn_fallback()
    return _FALLBACK_MAGIC + base64.b64encode(bytes(data))


def unprotect(blob: bytes, *, entropy: bytes = DEFAULT_ENTROPY) -> bytes:
    """Reverse :func:`protect`. Raises :class:`SecretError` if the blob cannot be decrypted."""
    if not isinstance(blob, (bytes, bytearray)):
        raise TypeError("unprotect() expects bytes")
    blob = bytes(blob)
    if blob.startswith(_FALLBACK_MAGIC):
        try:
            return base64.b64decode(blob[len(_FALLBACK_MAGIC):], validate=True)
        except (ValueError, TypeError) as exc:
            raise SecretError(f"corrupt secret blob: {exc}") from exc
    if not dpapi_available():
        raise SecretError(f"this secret was encrypted with Windows DPAPI and cannot be read here "
                          f"({_dpapi_error})")
    return _dpapi_call(blob, entropy, encrypt=False)


def save_secret(path: PathLike, value: dict[str, Any]) -> None:
    """Serialize ``value`` as JSON, protect it and write it atomically to ``path``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = protect(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_secret(path: PathLike) -> Optional[dict[str, Any]]:
    """Read a secret written by :func:`save_secret`.

    Returns None if the file does not exist or cannot be decrypted/parsed (for
    example it was copied from another Windows account); a warning is logged in
    the latter case.
    """
    p = Path(path)
    try:
        blob = p.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("Could not read secret file %s: %s", p, exc)
        return None
    try:
        value = json.loads(unprotect(blob).decode("utf-8"))
    except (SecretError, ValueError, UnicodeDecodeError) as exc:
        log.warning("Could not decrypt %s (%s). It was probably created by another Windows user "
                    "or is corrupt; log in again to replace it.", p, exc)
        return None
    if not isinstance(value, dict):
        log.warning("Secret file %s does not contain a JSON object; ignoring it.", p)
        return None
    return value


def delete_secret(path: PathLike) -> bool:
    """Remove a stored secret. Returns True if a file was removed."""
    try:
        Path(path).unlink()
        return True
    except FileNotFoundError:
        return False
