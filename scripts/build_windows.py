"""Build a standalone Windows GUI executable from the locked packaging environment."""

import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Windows EXE 必须在 Windows 上构建。")

    from PySide6.QtWidgets import QApplication

    from token_pulse import __version__
    from token_pulse.ui.theme import pulse_icon

    root = Path(__file__).resolve().parents[1]
    staging = root / "build" / "windows"
    staging.mkdir(parents=True, exist_ok=True)
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
            f"--distpath={root / 'dist'}",
            f"--workpath={staging / 'work'}",
            f"--specpath={staging}",
            str(root / "src" / "token_pulse" / "__main__.py"),
        ],
        cwd=root,
        env=environment,
        check=True,
    )
    # Keep notices accessible alongside the single-file executable as well.
    shutil.copytree(notices, root / "dist" / "notices", dirs_exist_ok=True)
    print(f"已生成：{root / 'dist' / 'TokenPulse.exe'}")
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
