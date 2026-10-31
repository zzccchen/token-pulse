"""Recover intact diagnostic records from a consistent SQLite snapshot.

Only structural cells and freeblocks are inspected. Record headers and payloads
must survive intact; no overflow reconstruction, guessed fields or text carving.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from token_pulse.logs import LogEvidence

MAX_SNAPSHOT = 128 * 1024 * 1024
MAX_ROWS = 50000
TARGETS = {"codex_core::stream_events_utils", "codex_core::responses_retry"}
COLUMNS = (
    "id",
    "ts",
    "ts_nanos",
    "level",
    "target",
    "feedback_log_body",
    "module_path",
    "file",
    "line",
    "thread_id",
    "process_uuid",
    "estimated_bytes",
)


def _u(data: bytes, at: int, size: int) -> int:
    if at < 0 or at + size > len(data):
        raise ValueError("Truncated page")
    return int.from_bytes(data[at : at + size], "big")


def _varint(data: bytes, at: int, end: int) -> tuple[int, int]:
    value = 0
    for index in range(9):
        if at >= end:
            raise ValueError("Truncated varint")
        byte = data[at]
        at += 1
        if index == 8:
            return value << 8 | byte, at
        value = value << 7 | byte & 127
        if byte < 128:
            return value, at
    raise ValueError("Invalid varint")


def _length(kind: int) -> int:
    if kind < 0 or kind in (10, 11):
        raise ValueError("Reserved serial type")
    if kind < 8:
        return (0, 1, 2, 3, 4, 6, 8, 8)[kind]
    return 0 if kind < 10 else (kind - 12) // 2


def _row(data: bytes, at: int, end: int) -> tuple | None:
    size, position = _varint(data, at, end)
    if not 13 <= size <= 40 or at + size > end:
        return None
    types = []
    while position < at + size:
        kind, position = _varint(data, position, at + size)
        types.append(kind)
    if len(types) != len(COLUMNS) or types[0] != 0:
        return None
    if types[1] not in range(1, 7) or types[2] not in (*range(1, 7), 8, 9):
        return None
    if any(types[i] < 13 or types[i] % 2 == 0 for i in (3, 4, 5, 9, 10)):
        return None
    if any(types[i] != 0 and (types[i] < 13 or types[i] % 2 == 0) for i in (6, 7)):
        return None
    if types[8] not in (*range(7), 8, 9) or types[11] not in (*range(1, 7), 8, 9):
        return None
    lengths = [_length(kind) for kind in types]
    if at + size + sum(lengths) != end or lengths[5] > 16000:
        return None
    position = at + size
    values = []
    for index, (kind, length) in enumerate(zip(types, lengths, strict=True)):
        if index in (1, 2):
            value = (
                int.from_bytes(data[position : position + length], "big", signed=True)
                if kind < 8
                else kind - 8
            )
        elif index in (3, 4, 5, 9, 10):
            value = data[position : position + length].decode("utf-8")
        else:
            value = None
        values.append(value)
        position += length
    return tuple(values)


def _leaves(image: bytes, size: int, usable: int) -> set[int]:
    count = len(image) // size
    trunk = _u(image, 32, 4)
    trunks: set[int] = set()
    leaves: set[int] = set()
    while trunk:
        if not 1 < trunk <= count or trunk in trunks or trunk in leaves:
            raise ValueError("Invalid freelist chain")
        trunks.add(trunk)
        at = (trunk - 1) * size
        length = _u(image, at + 4, 4)
        if length > (usable - 8) // 4:
            raise ValueError("Invalid freelist size")
        for index in range(length):
            page = _u(image, at + 8 + index * 4, 4)
            if not 1 < page <= count or page in leaves or page in trunks:
                raise ValueError("Invalid free page")
            leaves.add(page)
        trunk = _u(image, at, 4)
    if len(leaves) + len(trunks) != _u(image, 36, 4):
        raise ValueError("Incomplete freelist")
    return leaves


def _page(
    image: bytes, page: int, size: int, usable: int
) -> tuple[list[tuple[int, int, int]], list[tuple[int, int]]]:
    base = (page - 1) * size
    if image[base] != 13:
        return [], []
    count = _u(image, base + 3, 2)
    first = _u(image, base + 5, 2) or 65536
    if not 8 + count * 2 <= first <= usable:
        return [], []
    result, extents = [], []
    previous_id = 0
    for index in range(count):
        offset = _u(image, base + 8 + index * 2, 2)
        if not first <= offset < usable:
            return [], []
        payload, start = _varint(image, base + offset, base + usable)
        row_id, start = _varint(image, start, base + usable)
        if not previous_id < row_id < 2**63:
            return [], []
        previous_id = row_id
        local = payload
        if payload > usable - 35:
            minimum = (usable - 12) * 32 // 255 - 23
            local = minimum + (payload - minimum) % (usable - 4)
            if local > usable - 35:
                local = minimum
        end = start + local + (4 if local != payload else 0)
        if end > base + usable:
            return [], []
        extents.append((base + offset, end))
        if local == payload:
            result.append((row_id, start, end))
    blocks = []
    free = _u(image, base + 1, 2)
    while free:
        if not first <= free <= usable - 4:
            return [], []
        following, length = _u(image, base + free, 2), _u(image, base + free + 2, 2)
        if length < 4 or free + length > usable or following and following < free + length:
            return [], []
        blocks.append((base + free, base + free + length))
        free = following
    extents.extend(blocks)
    extents.sort()
    if any(left[1] > right[0] for left, right in zip(extents, extents[1:], strict=False)):
        return [], []
    return result, blocks


def _table_leaves(image: bytes, root: int, size: int, usable: int) -> set[int]:
    """Follow only the current logs table, rejecting malformed or cyclic trees."""
    pending, seen, leaves = [root], set(), set()
    while pending:
        page = pending.pop()
        if not 1 < page <= len(image) // size or page in seen:
            raise ValueError("Invalid table tree")
        seen.add(page)
        base = (page - 1) * size
        if image[base] == 13:
            leaves.add(page)
            continue
        if image[base] != 5:
            raise ValueError("Unsupported table page")
        count = _u(image, base + 3, 2)
        first = _u(image, base + 5, 2) or 65536
        if not 12 + count * 2 <= first <= usable:
            raise ValueError("Invalid table header")
        pending.append(_u(image, base + 8, 4))
        spans = []
        previous = 0
        for index in range(count):
            offset = _u(image, base + 12 + index * 2, 2)
            if not first <= offset <= usable - 5:
                raise ValueError("Invalid table cell")
            key, end = _varint(image, base + offset + 4, base + usable)
            if not previous < key < 2**63:
                raise ValueError("Invalid table keys")
            previous = key
            spans.append((base + offset, end))
            pending.append(_u(image, base + offset, 4))
        spans.sort()
        if any(a[1] > b[0] for a, b in zip(spans, spans[1:], strict=False)):
            raise ValueError("Overlapping table cells")
    return leaves


def _free_record(image: bytes, begin: int, end: int, usable: int, sequence: int) -> tuple | None:
    # Freeing a cell replaces its first four bytes with the freeblock link/size.
    # Only accept a wholly surviving record header and payload at the block start.
    # The lost cell rowid is never reconstructed or used for measurement identity.
    matches = []
    max_id_bytes = min(9, max(1, (sequence.bit_length() + 6) // 7))
    for offset in range(4, 19):
        start = begin + offset
        try:
            header, position = _varint(image, start, end)
            if not 13 <= header <= 40 or start + header > end:
                continue
            types = []
            while position < start + header:
                kind, position = _varint(image, position, start + header)
                types.append(kind)
            if len(types) != len(COLUMNS):
                continue
            amount = header + sum(_length(kind) for kind in types)
            amount_bytes = max(1, (amount.bit_length() + 6) // 7)
            if not 1 <= offset - amount_bytes <= max_id_bytes:
                continue
            if amount > usable - 35 or start + amount > end:
                continue  # No overflow or partially surviving payloads.
            suffix = image[max(begin + 4, begin + amount_bytes) : start]
            if suffix and (suffix[-1] >= 128 or any(byte < 128 for byte in suffix[:-1])):
                continue
            row = _row(image, start, start + amount)
            if row is not None:
                matches.append(row)
        except (ValueError, UnicodeDecodeError):
            continue
    return matches[0] if len(matches) == 1 else None


def _supported(row: tuple | None, now: float) -> bool:
    return bool(
        row is not None
        and row[4] in TARGETS
        and now - 604800 <= row[1] <= now + 5
        and 0 <= row[2] < 1e9
        and row[3] in {"TRACE", "DEBUG", "INFO", "WARN", "ERROR"}
        and row[9]
        and row[10]
    )


def recover_origins(db: sqlite3.Connection, evidence: LogEvidence, *, now: float) -> int:
    """Read once per database reset; retain only normalized origins/retry evidence."""
    owned_transaction = not db.in_transaction
    try:
        if owned_transaction:
            db.execute("BEGIN")
        if tuple(r[1] for r in db.execute("PRAGMA table_info(logs)")) != COLUMNS:
            return 0
        size = db.execute("PRAGMA page_size").fetchone()[0]
        count = db.execute("PRAGMA page_count").fetchone()[0]
        if count * size > MAX_SNAPSHOT or not hasattr(db, "serialize"):
            return 0
        sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name='logs'").fetchone()
        if sequence is None or type(sequence[0]) is not int:
            return 0
        image = db.serialize()
        if image[:16] != b"SQLite format 3\x00" or len(image) != count * size:
            return 0
        if (_u(image, 16, 2) or 65536) != size and not (size == 65536 and _u(image, 16, 2) == 1):
            return 0
        if _u(image, 56, 4) != 1:
            return 0  # UTF-8 layouts only.
        usable = size - image[20]
        candidates: dict[int, tuple | None] = {}
        free_pages = _leaves(image, size, usable)
        root = db.execute("SELECT rootpage FROM sqlite_master WHERE type='table' AND name='logs'")
        active_pages = _table_leaves(image, root.fetchone()[0], size, usable)
        if free_pages & active_pages:
            return 0
        free_records: set[tuple] = set()
        for page in free_pages | active_pages:
            try:
                cells, blocks = _page(image, page, size, usable)
            except (ValueError, IndexError):
                continue
            for begin, end in blocks:
                row = _free_record(image, begin, end, usable, sequence[0])
                if _supported(row, now):
                    free_records.add(row)
                if len(free_records) + len(candidates) > MAX_ROWS:
                    return 0
            if page not in free_pages:
                continue  # Active cells are read through SQL, not recovered.
            for row_id, start, end in cells:
                if row_id > sequence[0]:
                    continue
                try:
                    row = _row(image, start, end)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not _supported(row, now):
                    continue
                if row_id in candidates and candidates[row_id] != row:
                    candidates[row_id] = None
                else:
                    candidates[row_id] = row
                if len(candidates) + len(free_records) > MAX_ROWS:
                    return 0
        accepted = 0
        for row in sorted(free_records, key=lambda row: (row[1], row[2])):
            evidence.accept(
                row[9], row[1] + row[2] / 1e9, row[4], row[5], origin="recovered-stream-log"
            )
            accepted += 1
        for row_id, row in sorted(candidates.items()):
            if row is None or db.execute("SELECT 1 FROM logs WHERE id=?", (row_id,)).fetchone():
                continue  # A reused/live row ID cannot establish an orphan observation.
            evidence.accept(
                row[9], row[1] + row[2] / 1e9, row[4], row[5], origin="recovered-stream-log"
            )
            accepted += 1
        return accepted
    except (sqlite3.Error, ValueError, OverflowError, AttributeError):
        return 0
    finally:
        if owned_transaction:
            db.rollback()
