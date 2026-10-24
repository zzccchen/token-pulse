# Third-party notices

TokenPulse's own code is licensed under [MIT](LICENSE). That does not change the licenses of its dependencies or referenced materials.

## Runtime and packaging

- **Qt for Python / PySide6 Essentials / Shiboken6.** Qt for Python offers community LGPLv3/GPLv3 and commercial licensing options. TokenPulse uses Qt Core, Gui, and Widgets via the Python packages. Consult the [Qt for Python licensing documentation](https://doc.qt.io/qtforpython-6/licenses.html) and the notices shipped with the exact dependency versions.
- **Python.** Local standalone builds include a Python runtime and its applicable license files; see the [Python license](https://docs.python.org/3/license.html).
- **PyInstaller.** The local EXE builder uses PyInstaller, which has an exception for distributing applications built with it. See its [license and exception](https://pyinstaller.org/en/stable/license.html). This does not replace the licenses of bundled dependencies.
- Development tools retain their own licenses. They are not imported by the running application.

The source archive and wheel do not bundle Qt or Python; installation resolves dependencies through the package manager.

The Windows portable ZIP uses the LGPLv3 option for Qt/PySide/Shiboken. It includes the LGPLv3 and GPLv3 texts, upstream attribution records and referenced license files, Python and OpenSSL notices, and the PyInstaller exception. Complete, unmodified Qt Base and PySide/Shiboken source archives are included in `sources/` alongside the corresponding TokenPulse source snapshot. DLLs remain external and replaceable; no integrity enforcement prevents running modified libraries.

See [Windows distribution and library replacement](docs/windows-distribution.md) for the component inventory, library replacement/rebuild instructions, Microsoft runtime terms, and validation limits. The build remains unsigned. Every dependency version change requires a fresh source/notice review; the builder checks the audited versions and pinned source hashes.

## Design references

The UI uses original Qt rendering and system fonts, without downloaded fonts, desktop capture, or an imported third-party rendering implementation. Design research included:

- [ECC liquid-glass-design](https://github.com/affaan-m/ECC/blob/e04ea0b9cc8248686edf5ac751cadff550e162b8/skills/liquid-glass-design/SKILL.md): shared material surfaces and opt-in interaction.
- [bowen31337/apple-design](https://github.com/bowen31337/apple-design/blob/c95fa1324ca371d7a8df9564ce8a03ff4bed5465/SKILL.md): material hierarchy, typography, and accessibility fallback.
- [Apple: Meet Liquid Glass](https://developer.apple.com/videos/play/wwdc2025/219/): visual design context.

These are references, not bundled libraries or claims of affiliation. The application does not implement Apple's native material APIs. Any future copied code or assets require their own attribution and license review.
