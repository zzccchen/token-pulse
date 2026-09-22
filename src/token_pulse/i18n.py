"""Small, offline message catalog shared by the GUI and command line.

Chinese source messages are the fallback. Translate templates before formatting;
raw model names, evidence codes and exported data never pass through this module.
The controller refreshes existing views when the selected language changes.
"""

from token_pulse.locales.en import MESSAGES

LANGUAGES = ("system", "zh_CN", "en")
_language = "zh_CN"


def system_language() -> str:
    from PySide6.QtCore import QLocale

    name = QLocale.system().name().lower()
    return "zh_CN" if name.startswith("zh") else "en"


def set_language(language: str) -> str:
    global _language
    _language = language if language in ("zh_CN", "en") else system_language()
    return _language


def tr(message: str, **values) -> str:
    translated = MESSAGES.get(message, message) if _language == "en" else message
    return translated.format(**values) if values else translated
