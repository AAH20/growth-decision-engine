"""Private, local snapshots for replaying a pilot against stable copied inputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

from .core import DataError, MAX_INPUT_BYTES, canonical_json
from .pilot import pilot_packet, verify_pilot
from .proposals import _unique_keys

NAMES = ("assignments", "outcomes", "costs", "billing", "spend", "plan", "manifest")
FILENAMES = {name: f"{name}.{'json' if name in ('plan', 'manifest') else 'csv'}" for name in NAMES}
FILE_LIMITS = {name: 64 * 1024 if name in ("plan", "manifest") else MAX_INPUT_BYTES for name in NAMES}
SCHEMA = "growth-decision-local-snapshot/v1"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _private_write(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _copy_source(source: Path, target: Path, limit: int) -> dict:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise DataError(f"cannot open regular source file: {source.name}") from exc
    try:
        with os.fdopen(descriptor, "rb") as input_file:
            before = os.fstat(input_file.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise DataError(f"source is not a regular file within limit: {source.name}")
            digest = hashlib.sha256()
            size = 0
            output_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(output_fd, "wb") as output_file:
                while chunk := input_file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise DataError(f"source exceeds size limit: {source.name}")
                    digest.update(chunk)
                    output_file.write(chunk)
                output_file.flush()
                os.fsync(output_file.fileno())
            after = os.fstat(input_file.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != after.st_size:
                raise DataError(f"source changed during snapshot: {source.name}")
            return {"filename": target.name, "bytes": size, "sha256": digest.hexdigest()}
    except OSError as exc:
        raise DataError(f"could not copy source file: {source.name}") from exc


def _private_directory(destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise DataError("snapshot destination already exists")
    if not destination.parent.is_dir():
        raise DataError("snapshot destination parent must already exist")
    resolved_parent = destination.parent.resolve()
    if any((ancestor / ".git").exists() for ancestor in (resolved_parent, *resolved_parent.parents)):
        raise DataError("snapshot destination must be outside a Git repository")


def snapshot_pilot(destination: str | Path, *sources: str | Path, seed: int = 1729, resamples: int = 2000) -> dict:
    """Copy seven explicit files, validate them, and atomically publish a private bundle."""
    if len(sources) != len(NAMES):
        raise DataError("snapshot requires seven source files")
    directory = Path(destination)
    _private_directory(directory)
    temporary = Path(tempfile.mkdtemp(prefix=f".{directory.name}.partial-", dir=directory.parent))
    try:
        copied = {}
        for name, source in zip(NAMES, sources):
            target = temporary / FILENAMES[name]
            copied[name] = _copy_source(Path(source), target, FILE_LIMITS[name])
        source_paths = [temporary / FILENAMES[name] for name in NAMES]
        packet = pilot_packet(*source_paths, seed=seed, resamples=resamples)
        packet_bytes = canonical_json(packet).encode("utf-8")
        _private_write(temporary / "packet.json", packet_bytes)
        inventory = {"schema_version": SCHEMA, "inputs": copied,
                     "packet_sha256": hashlib.sha256(packet_bytes).hexdigest(),
                     "seed": seed, "resamples": resamples, "claim": packet["claim"]}
        _private_write(temporary / "snapshot.json", canonical_json(inventory).encode("utf-8"))
        _private_directory(directory)
        os.rename(temporary, directory)
        return {"directory": str(directory.resolve()), "snapshot": inventory}
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def verify_snapshot(directory: str | Path) -> dict:
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise DataError("snapshot directory must be a real directory")
    expected_files = set(FILENAMES.values()) | {"packet.json", "snapshot.json"}
    if {item.name for item in root.iterdir()} != expected_files:
        raise DataError("snapshot directory has missing or unexpected files")
    inventory_path = root / "snapshot.json"
    if inventory_path.is_symlink() or not inventory_path.is_file() or inventory_path.stat().st_size > 64 * 1024:
        raise DataError("snapshot inventory must be a regular JSON file within limit")
    try:
        inventory = json.loads(inventory_path.read_bytes(), object_pairs_hook=_unique_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataError("snapshot inventory must be UTF-8 JSON with unique keys") from exc
    if (not isinstance(inventory, dict) or set(inventory) != {"schema_version", "inputs", "packet_sha256", "seed", "resamples", "claim"}
            or inventory["schema_version"] != SCHEMA or not isinstance(inventory["inputs"], dict)
            or set(inventory["inputs"]) != set(NAMES)):
        raise DataError("unsupported snapshot inventory")
    seed, resamples = inventory["seed"], inventory["resamples"]
    if type(seed) is not int or type(resamples) is not int or not 100 <= resamples <= 10000:
        raise DataError("snapshot inventory has invalid score parameters")
    if not isinstance(inventory["packet_sha256"], str) or not SHA256.fullmatch(inventory["packet_sha256"]):
        raise DataError("snapshot inventory has invalid packet digest")
    for name in NAMES:
        item = inventory["inputs"][name]
        if (not isinstance(item, dict) or set(item) != {"filename", "bytes", "sha256"}
                or item["filename"] != FILENAMES[name] or type(item["bytes"]) is not int
                or not 0 <= item["bytes"] <= FILE_LIMITS[name]
                or not isinstance(item["sha256"], str) or not SHA256.fullmatch(item["sha256"])):
            raise DataError(f"snapshot inventory has invalid {name} entry")
        file = root / FILENAMES[name]
        if file.is_symlink() or not file.is_file() or file.stat().st_size > FILE_LIMITS[name]:
            raise DataError(f"snapshot {name} must be a bounded regular file")
        content = file.read_bytes()
        if item["bytes"] != len(content) or item["sha256"] != hashlib.sha256(content).hexdigest():
            raise DataError(f"snapshot {name} differs from inventory")
    packet_path = root / "packet.json"
    if packet_path.is_symlink() or not packet_path.is_file() or packet_path.stat().st_size > 64 * 1024 * 1024:
        raise DataError("snapshot packet must be a bounded regular file")
    packet_raw = packet_path.read_bytes()
    if inventory["packet_sha256"] != hashlib.sha256(packet_raw).hexdigest():
        raise DataError("snapshot packet differs from inventory")
    packet = verify_pilot(packet_path, *(root / FILENAMES[name] for name in NAMES), seed=seed, resamples=resamples)
    if packet["claim"] != inventory["claim"]:
        raise DataError("snapshot claim differs from replayed packet")
    return {"status": "verified_against_local_copy", "schema_version": SCHEMA,
            "packet_sha256": inventory["packet_sha256"], "claim": packet["claim"],
            "limits": ["Local hashes detect changed copies but do not authenticate the original source system.",
                       "A local directory can be replaced by someone with filesystem access; no external signature or anchor is used."]}
