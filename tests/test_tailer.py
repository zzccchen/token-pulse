import json

from token_pulse.tailer import JsonlTail


def test_partial_utf8_and_crlf_are_completed_once(tmp_path):
    path = tmp_path / "events.jsonl"
    data = (json.dumps({"word": "词脉"}, ensure_ascii=False) + "\r\n").encode()
    path.write_bytes(data[:12])
    tail = JsonlTail(path)
    assert not tail.read().records
    with path.open("ab") as stream:
        stream.write(data[12:])
    assert tail.read().records == [{"word": "词脉"}]
    assert not tail.read().records


def test_read_is_bounded_and_recovers_from_bad_line(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"n":1}\ninvalid\n{"n":2}\n')
    tail = JsonlTail(path, budget=8)
    batches = [tail.read() for _ in range(4)]
    assert [r for b in batches for r in b.records] == [{"n": 1}, {"n": 2}]
    assert sum(b.malformed for b in batches) == 1


def test_truncate_and_rewrite_larger_than_original(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"n":1}\n')
    tail = JsonlTail(path)
    tail.read()
    path.write_text('{"n":2}\n{"n":3}\n')
    batch = tail.read()
    assert batch.reset
    assert batch.records == [{"n": 2}, {"n": 3}]
    path.write_text("{}\n")
    assert tail.read().reset


def test_replacement_and_missing_file_recovery(tmp_path):
    path = tmp_path / "events.jsonl"
    tail = JsonlTail(path)
    assert tail.read().error == "source_unavailable"
    path.write_text('{"n":1}\n')
    tail.read()
    path.rename(tmp_path / "old.jsonl")
    path.write_text('{"n":2}\n')
    batch = tail.read()
    assert batch.reset
    assert batch.records == [{"n": 2}]


def test_oversized_line_is_discarded_without_losing_next_record(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"x" * 100 + b'\n{"n":1}\n')
    tail = JsonlTail(path, budget=13, max_line=20)
    batches = [tail.read() for _ in range(10)]
    assert sum(b.oversized for b in batches) == 1
    assert [r for b in batches for r in b.records] == [{"n": 1}]
    assert len(tail.pending) <= 20


def test_bounded_backfill_skips_first_fragment(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"n":1}\n{"n":2}\n{"n":3}\n')
    tail = JsonlTail(path, backfill=14)
    batch = tail.read()
    assert batch.skipped_prefix
    assert batch.records == [{"n": 3}]


def test_excessive_nesting_records_a_gap_and_recovers(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"deep":' + "[" * 10000 + "0" + "]" * 10000 + '}\n{"n":1}\n')
    batch = JsonlTail(path).read()
    assert batch.malformed == 1
    assert batch.gaps == [0]
    assert batch.records == [{"n": 1}]
