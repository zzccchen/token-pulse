"""Offline checks for the distribution's source and notice boundaries."""

import hashlib
import io
import json
import runpy
import tarfile
from pathlib import Path

import pytest

helpers = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/windows_bundle.py"))
fetch_sources = helpers["fetch_sources"]
source_notices = helpers["source_notices"]
keep_binary = helpers["keep_binary"]


def write_tar(path, contents):
    with tarfile.open(path, "w:xz") as archive:
        for name, text in contents.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def test_cached_source_must_match_hash(tmp_path):
    payload = tmp_path / "source.tar.xz"
    payload.write_bytes(b"original")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "filename": payload.name,
                    "url": "https://invalid.example/never-requested",
                    "sha256": hashlib.sha256(b"original").hexdigest(),
                }
            ]
        )
    )
    assert fetch_sources(manifest, tmp_path) == [payload]
    payload.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        fetch_sources(manifest, tmp_path)


def test_notices_include_referenced_headers_and_parent_licenses(tmp_path):
    archive = tmp_path / "source.tar.xz"
    write_tar(
        archive,
        {
            "upstream/LICENSES/LGPL-3.0-only.txt": "LGPL text",
            "upstream/src/license-header.h": "upstream copyright",
            "upstream/src/part/qt_attribution.json": json.dumps(
                {
                    "LicenseFile": "../license-header.h",
                    "Copyright": "Upstream author",
                }
            ),
            "upstream/src/implementation.cpp": "not a notice",
        },
    )
    target = tmp_path / "notices"
    source_notices(archive, target)
    assert (target / "upstream/LICENSES/LGPL-3.0-only.txt").read_text() == "LGPL text"
    assert (target / "upstream/src/license-header.h").read_text() == "upstream copyright"
    assert not (target / "upstream/src/implementation.cpp").exists()


def test_missing_attribution_file_stops_build(tmp_path):
    archive = tmp_path / "source.tar.xz"
    write_tar(
        archive,
        {
            "upstream/qt_attribution.json": '{"LicenseFile": "missing.h"}',
        },
    )
    with pytest.raises(ValueError, match="Missing attribution license"):
        source_notices(archive, tmp_path / "notices")


def test_notice_path_cannot_escape_destination(tmp_path):
    archive = tmp_path / "source.tar.xz"
    write_tar(archive, {"../COPYING.txt": "outside"})
    with pytest.raises(ValueError, match="Unsafe source notice path"):
        source_notices(archive, tmp_path / "notices")
    assert not (tmp_path / "COPYING.txt").exists()


@pytest.mark.parametrize(
    "name",
    [
        "PySide6\\plugins\\platforms\\qwindows.dll",
        "PySide6/Qt6Core.dll",
        "PySide6/Qt6Gui.dll",
        "PySide6/Qt6Widgets.dll",
    ],
)
def test_required_raster_runtime_is_retained(name):
    assert keep_binary(name)


@pytest.mark.parametrize(
    "name",
    [
        "PySide6/opengl32sw.dll",
        "PySide6/Qt6Svg.dll",
        "PySide6/Qt6Network.dll",
        "PySide6/plugins/imageformats/qwebp.dll",
        "PySide6/plugins/tls/qopensslbackend.dll",
    ],
)
def test_unused_components_are_not_distributed(name):
    assert not keep_binary(name)
