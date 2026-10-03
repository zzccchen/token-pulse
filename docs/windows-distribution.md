# Windows distribution and library replacement

TokenPulse's application code is MIT-licensed. The portable Windows ZIP uses **Qt, PySide6 Essentials, and Shiboken6 under LGPL version 3**. Their copyrights and license texts are in `notices/upstream/`. GPLv3 is included because LGPLv3 incorporates its terms. Upstream commercial-license references are retained as upstream material; TokenPulse uses the open-source license option.

## What the ZIP contains

- `TokenPulse.exe` and `_internal/`: the application and dynamically loaded runtime libraries. Keep them together after extraction.
- `START-HERE.txt`: startup instructions and a prominent LGPL notice.
- `notices/`: LGPL/GPL texts, upstream copyright notices, Python/runtime notices, the PyInstaller exception, and this guide.
- `sources/`: complete, unmodified Qt Base 6.11.2 and PySide/Shiboken 6.11.2 source archives, the corresponding TokenPulse source snapshot, and the pinned download URLs and SHA-256 hashes.
- `build-info.json`: runtime versions, application commit, and file hashes for auditing. The application does not enforce these hashes or prevent library replacement.

No library source has been patched by TokenPulse. The packaging selects components; it does not change their compiled code. The source archives include their upstream build files. Source code travels in the same ZIP as the binaries, without requiring a separate request or relying on a future source offer.

## Bundled components

| Component | Distribution terms and notices |
| --- | --- |
| Qt 6.11.2 Core, Gui, Widgets and selected Qt Base plugins | LGPL-3.0; full Qt Base sources, `LICENSES`, attribution records and their referenced license files are included |
| PySide6 Essentials / Shiboken6 6.11.2 runtime bindings | LGPL-3.0; full PySide setup sources include Shiboken; upstream Python-derived code notices are retained |
| Qt Base third-party code | Original terms remain applicable, including FreeType, HarfBuzz, PCRE2, libpng, libjpeg-turbo, zlib, Unicode data, and other components documented by upstream attribution records |
| Python 3.13.7 | PSF and incorporated-software terms; runtime `Python-LICENSE.txt` and versioned Python license documentation are included |
| OpenSSL 3.5.3 (`libcrypto`, used by Python hashing) | Apache-2.0; versioned license text and upstream README with copyright notices are included |
| SQLite (Python runtime) | Public domain; the SQLite authors disclaim copyright in their source code |
| Microsoft Visual C++ runtime DLLs | Microsoft Distributable Code terms reproduced in `Python-LICENSE.txt`; these terms apply only to those DLLs, not to TokenPulse or the LGPL libraries |
| PyInstaller bootloader and runtime hooks | GPL with the application-distribution exception; the complete upstream `COPYING.txt` is included |

The Qt notice collection is intentionally broader than the selected Windows components. Its inclusion of a notice for another platform or a development tool does not mean that component is shipped. The actual runtime file inventory is in `build-info.json`.

This software is based in part on the work of the [FreeType Team](https://freetype.org/) and the Independent JPEG Group. FreeType is used under the FreeType License (FTL) option; its full text and the IJG notices are included with the other upstream materials.

The build excludes Qt Network, SVG, additional image-format plugins, and the software OpenGL renderer. TokenPulse renders ordinary raster widgets and draws its own icons. It also omits the unused Python SSL, LZMA, and bzip2 modules from the frozen runtime. Source installations are unaffected.

## Replace the LGPL libraries

You may modify the LGPL libraries and reverse engineer the combined application to debug your modifications. TokenPulse imposes no additional restriction on these rights. No signing key, activation, or integrity-check bypass is needed to run a modified library.

1. Extract the complete portable ZIP to a writable directory and close TokenPulse.
2. Back up that directory. Build an ABI-compatible Windows x64 release version of the library you want to change. Use matching Qt/PySide versions and the MSVC runtime ABI for the existing bindings.
3. Replace the relevant files under `_internal/PySide6/` (for example `Qt6Core.dll`, `Qt6Gui.dll`, `Qt6Widgets.dll`, binding `.pyd` files, and `pyside6.abi3.dll`) or `_internal/shiboken6/`. Replace matching dependencies and plugins together when required. Qt plugins are under `_internal/PySide6/plugins/`.
4. Start `TokenPulse.exe --demo --no-tray`. Once the modified libraries work, launch without `--demo` for local collection.

A portable build uses these external DLLs directly. It does not restore the original DLLs on startup. Interface-breaking changes may require rebuilding PySide and/or TokenPulse; the sources for doing so are included.

## Rebuild from source

Extract the Qt Base and PySide archives from `sources/`. Qt Base includes its `configure.bat`, CMake files, and build documentation. Use a Windows x64 MSVC developer shell, CMake and Ninja to configure a shared release build. For example, in a separate Qt build directory:

```powershell
& '<qtbase-source>/configure.bat' -opensource -confirm-license -shared -release -nomake examples -nomake tests -prefix '<qt-install>'
cmake --build . --parallel
cmake --install .
```

Build PySide/Shiboken against that Qt installation following the versioned instructions in the PySide source tree. This may require the Clang development tools for the binding generator. Upstream guides: [Qt Windows builds](https://doc.qt.io/qt-6/windows-building.html) and [Qt for Python Windows builds](https://doc.qt.io/qtforpython-6/building_from_source/windows.html). The modified libraries must preserve the interfaces expected by the application, or the application must be rebuilt against them.

For TokenPulse, extract its source ZIP and run `uv sync --locked`, then `uv run token-pulse --demo`. To use a custom PySide wheel, install it into that environment with `uv pip install --python .venv/Scripts/python.exe <custom-wheel>` and run `.venv/Scripts/python.exe -m token_pulse --demo` directly so dependency synchronization does not replace your wheel.

To reproduce the published portable packaging, use Python 3.13.7 and the locked packaging group as described in [development](https://github.com/zzccchen/token-pulse/blob/main/docs/development.md#builds). If building from the included source ZIP, first run `git init` and `git add .` inside that extracted source directory, then make a local commit so the builder can record its source inventory. The packaging script pins audited runtime/source versions and will stop on an unreviewed version change; this build-time check can be updated for your modified build. The executable itself has no such check.

## Verification limits

The release is unsigned. Automated checks cover startup and exit in isolated demo mode, file integrity, source/license presence, and loading replaceable libraries outside the source checkout. These checks do not establish a fresh Windows installation test or a manual check of every desktop interaction. Keep those limitations separate from the included license and source materials.

Reference: [Qt's LGPL distribution guidance](https://www.qt.io/development/open-source-lgpl-obligations). The bundled license texts govern each component.
