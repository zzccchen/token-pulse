"""Preview or apply a bounded repair from an existing TokenPulse history backup."""

import argparse
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

from token_pulse.history import History
from token_pulse.history_repair import restore_preserved
from token_pulse.logs import read_only


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.backup.resolve() == args.history.resolve() or not args.history.is_file():
        parser.error("Use a distinct existing destination history file")
    with tempfile.TemporaryDirectory(prefix="token-pulse-repair-") as temporary:
        destination = args.history
        if not args.apply:
            destination = Path(temporary) / "preview.sqlite"
            with closing(read_only(args.history)) as source, sqlite3.connect(destination) as target:
                source.backup(target)
        history = History(destination)
        try:
            count = restore_preserved(history, args.backup, now=time.time(), apply=args.apply)
            print(
                f"{'Restored' if args.apply else 'Recoverable'} independent measurements: {count}"
            )
        finally:
            history.close()


if __name__ == "__main__":
    main()
