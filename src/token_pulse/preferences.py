import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from token_pulse.i18n import LANGUAGES


@dataclass(frozen=True)
class Preferences:
    source: str = ""
    tray_number: bool = False
    notifications: bool = True
    appearance: str = "light"
    reduce_transparency: bool = False
    reduce_motion: bool = False
    language: str = "system"


def load(path: Path) -> Preferences:
    try:
        if path.stat().st_size > 65536:
            return Preferences()
        value = json.loads(path.read_text(encoding="utf-8"))
        return Preferences(
            source=value.get("source") if isinstance(value.get("source"), str) else "",
            tray_number=value.get("tray_number") is True,
            notifications=value.get("notifications", True) is True,
            appearance="dark" if value.get("appearance") == "dark" else "light",
            reduce_transparency=value.get("reduce_transparency") is True,
            reduce_motion=value.get("reduce_motion") is True,
            language=value.get("language") if value.get("language") in LANGUAGES else "system",
        )
    except (OSError, ValueError, AttributeError):
        return Preferences()


def save(path: Path, preferences: Preferences) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(asdict(preferences), stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
