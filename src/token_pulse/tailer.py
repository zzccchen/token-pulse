"""Bounded JSONL tailing; partial lines never become partial observations."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from pyzstd import DParameter, EndlessZstdDecompressor, ZstdError


@dataclass
class Batch:
    records: list[dict] = field(default_factory=list)
    reset: bool = False
    skipped_prefix: bool = False
    malformed: int = 0
    oversized: int = 0
    gaps: list[int] = field(default_factory=list)
    error: str | None = None
    size: int = 0
    eof: bool = False
    bytes_read: int = 0


class JsonlTail:
    def __init__(
        self,
        path: Path,
        *,
        budget: int = 2 * 1024 * 1024,
        max_line: int = 8 * 1024 * 1024,
        backfill: int | None = 4 * 1024 * 1024,
    ):
        if min(budget, max_line) <= 0 or (backfill is not None and backfill <= 0):
            raise ValueError("read limits must be positive")
        self.path = path
        self.budget = budget
        self.max_line = max_line
        self.backfill = backfill
        self.offset = 0
        self.pending = b""
        self.dropping = False
        self.identity: tuple[int, int] | None = None
        self.anchor = b""

    def read(self) -> Batch:
        batch = Batch()
        try:
            with self.path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                batch.size = stat.st_size
                identity = stat.st_dev, stat.st_ino
                replaced = self.identity is not None and identity != self.identity
                changed = False
                if self.anchor and stat.st_size >= self.offset:
                    stream.seek(self.offset - len(self.anchor))
                    changed = stream.read(len(self.anchor)) != self.anchor
                if replaced or changed or stat.st_size < self.offset:
                    self.offset = 0
                    self.pending = b""
                    self.dropping = False
                    self.anchor = b""
                    batch.reset = True
                if self.identity is None or batch.reset:
                    if self.backfill is not None and stat.st_size > self.backfill:
                        self.offset = stat.st_size - self.backfill
                        self.dropping = True
                        batch.skipped_prefix = True
                self.identity = identity
                stream.seek(self.offset)
                data = stream.read(self.budget)
                batch.bytes_read = len(data)
                self.offset += len(data)
                stream.seek(max(0, self.offset - 128))
                self.anchor = stream.read(min(128, self.offset))
                batch.eof = self.offset >= batch.size
        except OSError:
            # Do not return OS exception text, which can contain private paths.
            batch.error = "source_unavailable"
            return batch

        self._parse(data, batch)
        return batch

    def _parse(self, data: bytes, batch: Batch) -> None:
        for fragment in data.splitlines(keepends=True):
            # bytes.splitlines only splits CR/LF. JSONL records require LF completion.
            complete = fragment.endswith(b"\n")
            if not self.dropping:
                self.pending += fragment
                if len(self.pending) > self.max_line:
                    self.pending = b""
                    self.dropping = True
                    batch.oversized += 1
                    batch.gaps.append(len(batch.records))
            if complete:
                if not self.dropping and self.pending.strip():
                    try:
                        record = json.loads(self.pending)
                        if isinstance(record, dict):
                            batch.records.append(record)
                        else:
                            batch.malformed += 1
                            batch.gaps.append(len(batch.records))
                    except (ValueError, UnicodeError, RecursionError):
                        batch.malformed += 1
                        batch.gaps.append(len(batch.records))
                self.pending = b""
                self.dropping = False


class ZstdJsonlTail(JsonlTail):
    """Decode concatenated frames incrementally; offsets count decoded JSONL bytes.

    A changed compressed container restarts decoding. No decoded source is written
    to disk, and neither an incomplete frame nor an incomplete line is completion.
    """

    def __init__(self, path: Path, **limits):
        super().__init__(path, **limits)
        self.version: tuple[int, int, int, int] | None = None
        self.source_offset = 0
        self.decoder = None

    def read(self) -> Batch:
        batch = Batch()
        try:
            with self.path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                version = stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
                batch.size = stat.st_size
                if version != self.version:
                    batch.reset = self.version is not None
                    self.version = version
                    self.source_offset = self.offset = 0
                    self.pending = b""
                    self.dropping = False
                    # The decoder rejects larger windows instead of allocating
                    # without a bound. This is a resource limit, not a TPS cutoff.
                    self.decoder = EndlessZstdDecompressor(option={DParameter.windowLogMax: 27})
                stream.seek(self.source_offset)
                remaining = self.budget
                input_remaining = self.budget
                while remaining > 0:
                    data = b""
                    if self.decoder.needs_input:
                        if input_remaining <= 0:
                            break
                        data = stream.read(min(65536, input_remaining))
                        self.source_offset += len(data)
                        batch.bytes_read += len(data)
                        input_remaining -= len(data)
                        if not data:
                            if self.source_offset == 0 or not self.decoder.at_frame_edge:
                                batch.error = "source_unavailable"
                            else:
                                batch.eof = True
                            break
                    decoded = self.decoder.decompress(data, min(65536, remaining))
                    self.offset += len(decoded)
                    remaining -= len(decoded)
                    self._parse(decoded, batch)
        except (OSError, ZstdError):
            batch.error = "source_unavailable"
            self.decoder = None
            # Restart on the next call even if a transient read failure did not
            # change the file. Retained samples are deduplicated by source identity.
            self.version = None
        return batch


def open_tail(path: Path, **limits) -> JsonlTail:
    cls = ZstdJsonlTail if path.name.endswith(".jsonl.zst") else JsonlTail
    return cls(path, **limits)
