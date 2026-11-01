"""What the call says and shows by itself, in the language of the person's phone. The model finds its own words in that language."""
from __future__ import annotations

from typing import Dict

WORDS: Dict[str, Dict[str, str]] = {
    "ru": {
        "share": "Включи демонстрацию экрана",
        "sharing": "Демонстрация экрана включена",
        "stopped": "Демонстрация экрана остановлена",
        "ended": "Сессия завершена",
        "expired": "Сессия истекла",
        "failed": "Не получилось ответить, повтори, пожалуйста.",
        "silent": "(молчит, экран обновился)",
        "unseen": "(экран не показан)",
        "unshown": "(обводка не показана)",
    },
    "en": {
        "share": "Share your screen",
        "sharing": "Screen sharing is on",
        "stopped": "Screen sharing stopped",
        "ended": "Call ended",
        "expired": "The call timed out",
        "failed": "Sorry, I couldn't answer. Please say that again.",
        "silent": "(silent, the screen updated)",
        "unseen": "(screen not shown)",
        "unshown": "(outline not shown)",
    },
    "es": {
        "share": "Comparte tu pantalla",
        "sharing": "Estás compartiendo la pantalla",
        "stopped": "Dejaste de compartir la pantalla",
        "ended": "Llamada terminada",
        "expired": "La llamada caducó",
        "failed": "No pude responder. Repítelo, por favor.",
        "silent": "(en silencio, la pantalla se actualizó)",
        "unseen": "(pantalla no mostrada)",
        "unshown": "(contorno no mostrado)",
    },
}


def code(tag: str) -> str:
    """The language of a tag, however the phone writes it: es-MX, es_MX and ES are all es."""
    return tag.strip().lower().replace("_", "-").split("-")[0]


def words(tag: str) -> Dict[str, str]:
    """A phone in any other language gets the English words."""
    return WORDS.get(code(tag), WORDS["en"])
