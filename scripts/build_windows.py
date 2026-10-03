"""Build a Windows portable ZIP with replaceable DLLs, licenses, and Qt sources."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import ssl
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from windows_bundle import archive_tree, digest, fetch_sources, source_notices


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Windows EXE 必须在 Windows 上构建。")

    from PySide6.QtWidgets import QApplication

    from token_pulse import __version__
    from token_pulse.ui.theme import pulse_icon

    root = Path(__file__).resolve().parents[1]
    if platform.python_version() != "3.13.7" or ssl.OPENSSL_VERSION.split()[1] != "3.5.3":
        raise RuntimeError(
            "Re-audit runtime licenses before changing Python 3.13.7 / OpenSSL 3.5.3"
        )
    for name in ("PySide6-Essentials", "shiboken6"):
        if importlib.metadata.version(name) != "6.11.2":
            raise RuntimeError("Re-audit Qt sources and notices before changing Qt 6.11.2")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=root / "dist" / "windows")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    build_root = root / "build" / "windows"
    build_root.mkdir(parents=True, exist_ok=True)
    # Fresh staging prevents old dependency notices from leaking into a new build.
    staging = Path(tempfile.mkdtemp(prefix="package-", dir=build_root))
    inputs = fetch_sources(root / "scripts/windows_sources.json", root / "build/license-sources")
    app = QApplication.instance() or QApplication([])
    icon_path = staging / "TokenPulse.ico"
    if not pulse_icon().pixmap(64, 64).save(str(icon_path), "ICO"):
        raise RuntimeError("无法生成 Windows 图标。")

    # Copy only package license/metadata files, never local application data.
    notices = staging / "notices"
    notices.mkdir(exist_ok=True)
    for name in ("LICENSE", "THIRD_PARTY.md"):
        shutil.copy2(root / name, notices / name)
    notice_index = notices / "THIRD_PARTY.md"
    notice_index.write_text(
        notice_index.read_text(encoding="utf-8").replace(
            "(docs/windows-distribution.md)", "(WINDOWS-DISTRIBUTION.md)"
        ),
        encoding="utf-8",
    )
    for name in ("PySide6-Essentials", "shiboken6", "pyinstaller"):
        distribution = importlib.metadata.distribution(name)
        for entry in distribution.files or ():
            if ".dist-info/" not in str(entry):
                continue
            if "licenses" not in entry.parts and entry.name != "METADATA":
                continue
            destination = notices / name / Path(*entry.parts[1:])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(distribution.locate_file(entry), destination)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("未找到 Python 许可证。")
    shutil.copy2(python_license, notices / "Python-LICENSE.txt")
    for path in inputs:
        if path.name.endswith(".tar.xz"):
            source_notices(path, notices / "upstream")
        else:
            shutil.copy2(path, notices / path.name)
    shutil.copy2(root / "docs/windows-distribution.md", notices / "WINDOWS-DISTRIBUTION.md")

    version_tuple = tuple(int(part) for part in __version__.split(".")) + (0,)
    version_file = staging / "version.txt"
    version_file.write_text(
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers={version_tuple!r}, prodvers={version_tuple!r}, "
        "mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),\n"
        "  kids=[StringFileInfo([StringTable('040904B0', [\n"
        "    StringStruct('ProductName', 'TokenPulse 词脉'),\n"
        "    StringStruct('FileDescription', 'TokenPulse 本机模型输出监视器'),\n"
        f"    StringStruct('FileVersion', '{__version__}'),\n"
        f"    StringStruct('ProductVersion', '{__version__}'),\n"
        "    StringStruct('OriginalFilename', 'TokenPulse.exe')\n"
        "  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]\n"
        ")\n",
        encoding="utf-8",
    )
    # A host PATH may contain unrelated native libraries (for example another ICU).
    # Let the Qt hooks resolve wheel DLLs; only expose Python and Windows here.
    environment = dict(os.environ)
    windows = Path(os.environ["SYSTEMROOT"])
    environment["PATH"] = os.pathsep.join(
        str(path)
        for path in (
            Path(sys.executable).parent,
            Path(sys.base_prefix),
            Path(sys.base_prefix) / "DLLs",
            windows / "System32",
            windows,
        )
    )
    for name in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        environment.pop(name, None)
    environment["TOKEN_PULSE_BUILD_STAGING"] = str(staging)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            f"--distpath={staging / 'dist'}",
            f"--workpath={staging / 'work'}",
            str(root / "scripts/windows.spec"),
        ],
        cwd=root,
        env=environment,
        check=True,
    )
    portable = staging / "dist/TokenPulse"
    shutil.copytree(notices, portable / "notices")
    sources = portable / "sources"
    sources.mkdir()
    for path in inputs:
        if path.name.endswith(".tar.xz"):
            shutil.copy2(path, sources / path.name)
    shutil.copy2(root / "scripts/windows_sources.json", sources / "upstream-sources.json")
    # Include application sources needed to rebuild/relink this exact build.
    # Git's tracked-file inventory excludes local logs, preferences, and databases.
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    with zipfile.ZipFile(
        sources / f"token-pulse-{__version__}-source.zip", "w", compression=zipfile.ZIP_DEFLATED
    ) as source_zip:
        for name in sorted(filter(None, tracked)):
            path = root / name
            if path.is_file():
                source_zip.write(path, name)
    manifest = {
        "token_pulse": __version__,
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("PySide6-Essentials", "shiboken6", "pyinstaller")
        },
        "openssl": ssl.OPENSSL_VERSION,
        "signed": False,
        "format": "portable directory; external replaceable DLLs",
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root)
        .decode()
        .strip(),
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root)
        ),
        "files": {
            path.relative_to(portable).as_posix(): digest(path)
            for path in sorted(portable.rglob("*"))
            if path.is_file()
        },
    }
    (portable / "build-info.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (portable / "START-HERE.txt").write_text(
        "TokenPulse - Windows portable preview (unsigned)\n\n"
        "Extract the entire ZIP and open TokenPulse.exe. Keep _internal beside it.\n"
        "Python is bundled. For an isolated demo: TokenPulse.exe --demo\n"
        "For a normal window: TokenPulse.exe --no-tray\n"
        "Close other TokenPulse instances before collecting local data.\n"
        "Use the tray menu to quit when the tray is available.\n\n"
        "This application uses Qt, PySide6 and Shiboken6 under LGPLv3.\n"
        "License texts and upstream copyright notices are in notices/.\n"
        "Complete Qt/PySide source archives and application source are in sources/.\n"
        "You may modify/replace these libraries and reverse engineer for debugging\n"
        "your modifications. See notices/WINDOWS-DISTRIBUTION.md for instructions.\n"
        "Microsoft runtime terms in notices/Python-LICENSE.txt apply only to those DLLs.\n"
        "The upstream libraries have not been modified by TokenPulse.\n\n"
        "https://github.com/zzccchen/token-pulse\n"
        "Independent project; not affiliated with OpenAI.\n",
        encoding="utf-8",
    )
    archive = output / f"TokenPulse-{__version__}-windows-{platform.machine().lower()}.zip"
    archive_tree(portable, archive)
    artifacts = [archive]
    checksums = []
    for file in artifacts:
        with file.open("rb") as stream:
            checksums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {file.name}\n")
    (output / "SHA256SUMS.txt").write_text(
        "".join(checksums),
        encoding="utf-8",
    )
    print(f"Built portable ZIP with sources and SHA256SUMS.txt in {output}")
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
