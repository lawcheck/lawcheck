import re

_WS_RE = re.compile(r"\s+")


def normalize_ru(text: str) -> str:
    if not text:
        return ""
    text = text.lower().replace("ё", "е")
    return _WS_RE.sub(" ", text).strip()
