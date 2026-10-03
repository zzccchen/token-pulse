"""Build a standalone Windows GUI executable from the locked packaging environment."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Windows EXE 必须在 Windows 上构建。")

    from PySide6.QtWidgets import QApplication

    from token_pulse import __version__
    from token_pulse.ui.theme import pulse_icon

    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=root / "dist" / "windows")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    build_root = root / "build" / "windows"
    build_root.mkdir(parents=True, exist_ok=True)
    # Fresh staging prevents old dependency notices from leaking into a new build.
    staging = Path(tempfile.mkdtemp(prefix="package-", dir=build_root))
    app = QApplication.instance() or QApplication([])
    icon_path = staging / "TokenPulse.ico"
    if not pulse_icon().pixmap(64, 64).save(str(icon_path), "ICO"):
        raise RuntimeError("无法生成 Windows 图标。")

    # Copy only package license/metadata files, never local application data.
    notices = staging / "notices"
    notices.mkdir(exist_ok=True)
    for name in ("LICENSE", "THIRD_PARTY.md"):
        shutil.copy2(root / name, notices / name)
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
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--windowed",
            "--noupx",
            "--name=TokenPulse",
            f"--paths={root / 'src'}",
            f"--icon={icon_path}",
            f"--version-file={version_file}",
            f"--add-data={notices}:notices",
            f"--distpath={output}",
            f"--workpath={staging / 'work'}",
            f"--specpath={staging}",
            str(root / "src" / "token_pulse" / "__main__.py"),
        ],
        cwd=root,
        env=environment,
        check=True,
    )
    manifest = {
        "token_pulse": __version__,
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("PySide6-Essentials", "shiboken6", "pyinstaller")
        },
        "signed": False,
        "status": "Local validation build; public distribution review pending.",
    }
    archive = output / f"TokenPulse-{__version__}-windows-{platform.machine().lower()}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(output / "TokenPulse.exe", "TokenPulse.exe")
        for file in sorted(notices.rglob("*")):
            if file.is_file():
                bundle.write(file, f"notices/{file.relative_to(notices).as_posix()}")
        bundle.writestr("build-info.json", json.dumps(manifest, indent=2) + "\n")
        bundle.writestr(
            "START-HERE.txt",
            "TokenPulse - local validation build (unsigned)\n\n"
            "Extract the ZIP and open TokenPulse.exe. Python is bundled.\n"
            "For an isolated demo: TokenPulse.exe --demo\n"
            "For a normal window: TokenPulse.exe --no-tray\n"
            "Close other TokenPulse instances before collecting local data.\n"
            "Use the tray menu to quit when the tray is available.\n\n"
            "Public distribution review and clean-machine testing are pending.\n"
            "https://github.com/zzccchen/token-pulse\n"
            "Independent project; not affiliated with OpenAI.\n",
        )
    artifacts = [output / "TokenPulse.exe", archive]
    checksums = []
    for file in artifacts:
        with file.open("rb") as stream:
            checksums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {file.name}\n")
    (output / "SHA256SUMS.txt").write_text(
        "".join(checksums),
        encoding="utf-8",
    )
    print(f"Built executable, ZIP and SHA256SUMS.txt in {output}")
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
