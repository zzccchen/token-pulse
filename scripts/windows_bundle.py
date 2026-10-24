"""Pinned source/notice preparation for the Windows portable distribution."""

import hashlib
import json
import posixpath
import shutil
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch_sources(manifest: Path, cache: Path) -> list[Path]:
    """Download build inputs only; the running application never calls this."""
    cache.mkdir(parents=True, exist_ok=True)
    paths = []
    for entry in json.loads(manifest.read_text(encoding="utf-8")):
        name = entry["filename"]
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("Source filename must be a basename")
        path = cache / name
        if not path.exists():
            partial = path.with_suffix(path.suffix + ".part")
            with urllib.request.urlopen(entry["url"], timeout=60) as response:
                with partial.open("wb") as stream:
                    shutil.copyfileobj(response, stream)
            if digest(partial) != entry["sha256"]:
                raise ValueError(f"Source checksum mismatch: {name}")
            partial.replace(path)
        if digest(path) != entry["sha256"]:
            raise ValueError(f"Source checksum mismatch: {name}")
        paths.append(path)
    return paths


def source_notices(archive: Path, destination: Path) -> None:
    """Copy license texts and attribution records without extracting source trees."""
    with tarfile.open(archive) as source:
        members = {member.name: member for member in source.getmembers() if member.isfile()}
        selected = set()
        for name, member in members.items():
            parts = PurePosixPath(name).parts
            basename = parts[-1].lower()
            if (
                "LICENSES" in parts
                or any(word in basename for word in ("license", "licence", "copying", "copyright"))
                or basename.startswith("notice")
                or basename == "readme.ijg"
                or basename == "qt_attribution.json"
            ):
                selected.add(name)
            if basename == "qt_attribution.json":
                with source.extractfile(member) as stream:
                    records = json.loads(stream.read().decode("utf-8"), strict=False)
                for record in records if isinstance(records, list) else [records]:
                    if filename := record.get("LicenseFile"):
                        target = posixpath.normpath(
                            posixpath.join(posixpath.dirname(name), filename)
                        )
                        if target not in members:
                            raise ValueError(f"Missing attribution license: {target}")
                        selected.add(target)
        for name in sorted(selected):
            parts = PurePosixPath(name).parts
            if name.startswith("/") or ".." in parts or any(":" in part for part in parts):
                raise ValueError("Unsafe source notice path")
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(members[name]) as stream:
                target.write_bytes(stream.read())


# TokenPulse uses raster Qt Widgets and draws its own icons. No SVG, network,
# OpenGL software renderer, or extra image-format plugins are needed.
QT_PLUGINS = {
    "platforms/qwindows.dll",
    "platforms/qoffscreen.dll",
    "platforms/qminimal.dll",
    "styles/qmodernwindowsstyle.dll",
    "imageformats/qico.dll",
    "imageformats/qjpeg.dll",
    "imageformats/qgif.dll",
}
QT_LIBRARIES = {"qt6core.dll", "qt6gui.dll", "qt6widgets.dll"}


def keep_binary(name: str) -> bool:
    path = name.replace("\\", "/")
    lower = path.lower()
    if "/plugins/" in lower:
        return lower.split("/plugins/", 1)[1] in QT_PLUGINS
    if lower.endswith("opengl32sw.dll"):
        return False
    if Path(lower).name.startswith("qt6") and lower.endswith(".dll"):
        return Path(lower).name in QT_LIBRARIES
    return True


def archive_tree(directory: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(directory).as_posix())
