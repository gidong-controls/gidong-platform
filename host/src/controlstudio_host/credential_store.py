from __future__ import annotations

import ctypes
import os
import re
import uuid
from ctypes import wintypes
from pathlib import Path


class CredentialStoreError(RuntimeError):
    """A credential could not be protected, recovered, or removed safely."""


_REFERENCE_PREFIX = "windows-dpapi-file-v1:"
_REFERENCE_PATTERN = re.compile(
    rf"^{re.escape(_REFERENCE_PREFIX)}(?P<identifier>[0-9a-f]{{32}})$"
)
_MAX_PROTECTED_BYTES = 256 * 1024
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _input_blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_ubyte]]:
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    return _DataBlob(len(value), buffer), buffer


def _crypt_protect(value: bytes) -> bytes:
    if os.name != "nt":
        raise CredentialStoreError("Windows credential protection is unavailable")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source, source_buffer = _input_blob(value)
    protected = _DataBlob()
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "ControlStudio model credential",
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(protected),
    ):
        raise CredentialStoreError("Windows could not protect the model credential")
    try:
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        kernel32.LocalFree(protected.pbData)
        del source_buffer


def _crypt_unprotect(value: bytes) -> bytes:
    if os.name != "nt":
        raise CredentialStoreError("Windows credential protection is unavailable")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source, source_buffer = _input_blob(value)
    plaintext = _DataBlob()
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(plaintext),
    ):
        raise CredentialStoreError("Windows could not recover the model credential")
    try:
        return ctypes.string_at(plaintext.pbData, plaintext.cbData)
    finally:
        kernel32.LocalFree(plaintext.pbData)
        del source_buffer


class WindowsDpapiCredentialStore:
    """Store DPAPI ciphertext outside SQLite and expose only opaque references."""

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory).expanduser().resolve()

    @staticmethod
    def protect_for_quarantine(value: bytes) -> bytes:
        """Seal a retry payload for the Host's encrypted migration quarantine."""

        if not isinstance(value, bytes) or not value or len(value) > _MAX_PROTECTED_BYTES:
            raise CredentialStoreError("model credential payload is invalid")
        return _crypt_protect(value)

    @staticmethod
    def unprotect_quarantined(value: bytes) -> bytes:
        """Open a DPAPI-sealed migration payload from the quarantine table."""

        if not isinstance(value, bytes) or not value or len(value) > _MAX_PROTECTED_BYTES:
            raise CredentialStoreError("protected model credential is invalid")
        return _crypt_unprotect(value)

    def put(self, value: bytes) -> str:
        protected = _crypt_protect(value)
        identifier = uuid.uuid4().hex
        destination = self._directory / f"{identifier}.bin"
        temporary = self._directory / f".{identifier}.tmp"
        descriptor: int | None = None
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_BINARY,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(protected)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise CredentialStoreError("could not persist the protected credential") from exc
        return f"{_REFERENCE_PREFIX}{identifier}"

    def get(self, reference: str) -> bytes:
        path = self._path_for_reference(reference)
        try:
            protected = path.read_bytes()
        except OSError as exc:
            raise CredentialStoreError("protected model credential is unavailable") from exc
        if not protected or len(protected) > _MAX_PROTECTED_BYTES:
            raise CredentialStoreError("protected model credential is invalid")
        return _crypt_unprotect(protected)

    def delete(self, reference: str) -> None:
        path = self._path_for_reference(reference)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise CredentialStoreError("could not remove the superseded credential") from exc

    def _path_for_reference(self, reference: str) -> Path:
        if not isinstance(reference, str):
            raise CredentialStoreError("model credential reference is invalid")
        match = _REFERENCE_PATTERN.fullmatch(reference)
        if match is None:
            raise CredentialStoreError("model credential reference is invalid")
        return self._directory / f"{match.group('identifier')}.bin"
