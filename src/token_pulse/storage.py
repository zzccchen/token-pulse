"""Keep a history database, journals and instance lock in one physical directory.

Windows app-data overlays can route individual files to different directories.
Path spelling alone therefore cannot establish ownership of a SQLite file set.
Only TokenPulse's own data is written here; Codex sources use separate readers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

SIDECARS = ("-wal", "-shm", "-journal")
MANIFEST = "storage-migration.json"


class StorageError(RuntimeError):
    """A storage layout cannot be opened or migrated without ambiguity."""


def _handle_path(file) -> Path:
    if os.name != "nt":
        return Path(file.name).resolve()
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    final_path = kernel.GetFinalPathNameByHandleW
    final_path.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    final_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = final_path(msvcrt.get_osfhandle(file.fileno()), buffer, len(buffer), 0)
    if not 0 < length < len(buffer):
        raise OSError(ctypes.get_last_error(), "Cannot resolve storage handle")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def physical_file(path: Path) -> Path:
    with path.open("rb") as stream:
        return _handle_path(stream)


def writable_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    # The opened write handle identifies the destination selected by the OS.
    # Resolving an existing directory alone misses per-file overlay redirection.
    with tempfile.NamedTemporaryFile(prefix=".token-pulse-probe-", dir=path, delete=False) as probe:
        name = Path(probe.name)
        try:
            actual = _handle_path(probe)
        except BaseException:
            probe.close()
            name.unlink(missing_ok=True)
            raise
    actual.unlink()
    return actual.parent


def database_path(path: Path) -> Path:
    """Reject split layouts before SQLite opens a writable connection."""
    directory = writable_directory(path.parent)
    target = directory / path.name
    if directory != path.parent.resolve():
        raise StorageError("Storage redirection requires initialization under the instance lock")
    if not target.exists() and any(Path(str(target) + suffix).exists() for suffix in SIDECARS):
        raise StorageError("Orphan journals cannot initialize a new database")
    for suffix in ("", *SIDECARS):
        entry = Path(str(target) + suffix)
        if entry.exists() and physical_file(entry) != entry:
            raise StorageError("Database and journals do not share a physical directory")
    return target


def _read_only(path: Path) -> sqlite3.Connection:
    # Keep the caller's complete logical view when taking a legacy snapshot.
    # Resolving only the main file could select a different journal namespace.
    db = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=1)
    db.execute("PRAGMA query_only=ON")
    return db


def _salt(db: sqlite3.Connection) -> bytes:
    row = db.execute("SELECT value FROM settings WHERE key='salt'").fetchone()
    if row is None or not isinstance(row[0], str) or len(row[0]) != 64:
        raise StorageError("Not a supported TokenPulse history")
    return bytes.fromhex(row[0])


def _digest(salt: bytes, value: object) -> str:
    return hmac.new(salt, json.dumps(value, sort_keys=True).encode(), hashlib.sha256).hexdigest()


def _stamp(path: Path, salt: bytes) -> str | None:
    if not path.exists():
        return None
    stat = path.stat()
    return _digest(salt, (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))


def _write_manifest(directory: Path, value: dict) -> None:
    temporary = directory / (MANIFEST + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, directory / MANIFEST)


def _snapshot(source: Path, destination: Path) -> bytes:
    with closing(_read_only(source)) as reader, closing(sqlite3.connect(destination)) as writer:
        reader.execute("BEGIN")
        if reader.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise StorageError("Legacy history is not intact")
        salt = _salt(reader)
        reader.backup(writer)
        # A portable snapshot is a closed single file, never a copied WAL set.
        writer.execute("PRAGMA journal_mode=DELETE")
        if writer.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise StorageError("History snapshot verification failed")
    return salt


def _finish(directory: Path, manifest: dict, snapshot: Path) -> None:
    target = directory / "history.sqlite"
    if manifest["state"] == "prepared":
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != manifest[
                "snapshot_digest"
            ] or any(Path(str(target) + suffix).exists() for suffix in SIDECARS):
                raise StorageError("Destination changed during interrupted migration")
        # The complete snapshot and intent are durable before retiring any file.
        # Source-side files are retained as evidence, never installed as journals.
        for suffix in SIDECARS:
            entry = Path(str(target) + suffix)
            if entry.exists():
                if physical_file(entry) != entry:
                    raise StorageError("Journal escaped the selected storage directory")
                retired = snapshot.parent / ("retired-history.sqlite" + suffix)
                if retired.exists():
                    raise StorageError("Unexpected journal after interrupted migration")
                os.replace(entry, retired)
        if not target.exists():
            candidate = snapshot.parent / "install.sqlite"
            shutil.copyfile(snapshot, candidate)
            with candidate.open("r+b") as stream:
                os.fsync(stream.fileno())
            os.replace(candidate, target)
        manifest["state"] = "complete"
        _write_manifest(directory, manifest)


def initialize_directory(requested: Path, directory: Path) -> None:
    """Migrate after the caller acquires the physical directory's instance lock.

    Replays finish from a verified snapshot. Independent destination histories
    are never selected by recency or overwritten. Markers store HMACs, not paths.
    """
    source, target = requested / "history.sqlite", directory / "history.sqlite"
    marker = directory / MANIFEST
    if marker.exists():
        if marker.stat().st_size > 4096:
            raise StorageError("Invalid storage migration marker")
        manifest = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise StorageError("Invalid storage migration marker")
        name = manifest.get("backup", "")
        if (
            manifest.get("version") != 1
            or manifest.get("state") not in ("prepared", "complete")
            or not isinstance(name, str)
            or not name.startswith("storage-backup-")
            or Path(name).name != name
        ):
            raise StorageError("Invalid storage migration marker")
        snapshot = directory / name / "history.sqlite"
        if not snapshot.resolve().is_relative_to(directory):
            raise StorageError("Migration snapshot escaped storage directory")
        with closing(_read_only(snapshot)) as db:
            salt = _salt(db)
        if _digest(salt, str(requested.absolute())) != manifest.get("logical_directory"):
            # Explicitly opening the canonical directory is also supported.
            if requested.resolve() != directory:
                raise StorageError("Different logical storage directory")
        if manifest["state"] == "prepared":
            if hashlib.sha256(snapshot.read_bytes()).hexdigest() != manifest.get("snapshot_digest"):
                raise StorageError("Migration snapshot changed")
            if source.exists() and physical_file(source) != target:
                if _stamp(source, salt) != manifest.get("source_stamp"):
                    raise StorageError("Legacy history changed during migration")
            _finish(directory, manifest, snapshot)
        elif not target.exists():
            raise StorageError("Completed storage migration lost its destination")
        elif source.exists() and physical_file(source) != target:
            if _stamp(source, salt) != manifest.get("source_stamp"):
                raise StorageError("Legacy history changed after migration")
    elif source.exists() and physical_file(source) != target:
        if target.exists():
            raise StorageError("Two independent history files require explicit selection")
        backup = Path(tempfile.mkdtemp(prefix="storage-backup-", dir=directory))
        snapshot = backup / "history.sqlite"
        salt = _snapshot(source, snapshot)
        manifest = {
            "version": 1,
            "state": "prepared",
            "backup": backup.name,
            "logical_directory": _digest(salt, str(requested.absolute())),
            "source_stamp": _stamp(source, salt),
            "snapshot_digest": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        }
        _write_manifest(directory, manifest)
        _finish(directory, manifest, snapshot)
    database_path(target)
    settings = requested / "settings.json"
    if settings.exists() and not (directory / "settings.json").exists():
        if settings.stat().st_size > 65536:
            raise StorageError("Settings exceed the supported size")
        value = settings.read_bytes()
        json.loads(value)
        temporary = directory / "settings.migration.tmp"
        temporary.write_bytes(value)
        os.replace(temporary, directory / "settings.json")
