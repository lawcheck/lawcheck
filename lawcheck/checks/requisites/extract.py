"""Извлечение реквизитов (ИНН, ОГРН) со страниц сайта.

Ищем по контекстным паттернам — рядом должно быть слово «ИНН», «ОГРН», «ОГРНИП».
Это сильно сокращает false-positives (10-значное число может быть номером телефона,
заказа или артикула; 13-значное — артикул, штрихкод и пр.).
"""
import re
from dataclasses import dataclass

from lawcheck.crawler.snapshot import SiteSnapshot
from lawcheck.utils.inn_ogrn import is_valid_inn, is_valid_ogrn

_INN_RE = re.compile(r"\bинн\s*[:№#]?\s*(\d{10}|\d{12})\b", re.I)
_OGRN_RE = re.compile(r"\bогрн(?:ип)?\s*[:№#]?\s*(\d{13}|\d{15})\b", re.I)
# «ООО «Название»» / «АО "Название"» / «ИП Иванов И. И.»
_LEGAL_NAME_RE = re.compile(
    r'(?:\b(ООО|ОАО|ЗАО|АО|ПАО|НКО|ИП)\b)\s*[«"“«]?([^«»"”»\n;,.]{2,120})[»"”»]?',
)


@dataclass
class RequisiteHit:
    value: str
    source_url: str  # страница, где найдено
    context: str = ""  # ±60 символов вокруг


@dataclass
class ExtractedRequisites:
    inn: list[RequisiteHit]      # все найденные ИНН (могут быть дубли с разных страниц)
    ogrn: list[RequisiteHit]
    legal_names: list[RequisiteHit]  # форма «ООО Х», «ИП Y» — для отображения

    @property
    def unique_inns(self) -> list[str]:
        return sorted({h.value for h in self.inn})

    @property
    def unique_ogrns(self) -> list[str]:
        return sorted({h.value for h in self.ogrn})


def _context(text: str, start: int, end: int, radius: int = 60) -> str:
    a = max(0, start - radius)
    b = min(len(text), end + radius)
    return re.sub(r"\s+", " ", text[a:b]).strip()


def extract(snapshot: SiteSnapshot) -> ExtractedRequisites:
    inn_hits: list[RequisiteHit] = []
    ogrn_hits: list[RequisiteHit] = []
    legal_hits: list[RequisiteHit] = []

    for page in snapshot.pages:
        if page.error or page.status >= 400 or not page.text:
            continue
        text = page.text
        for m in _INN_RE.finditer(text):
            val = m.group(1)
            inn_hits.append(RequisiteHit(value=val, source_url=page.url,
                                          context=_context(text, m.start(), m.end())))
        for m in _OGRN_RE.finditer(text):
            val = m.group(1)
            ogrn_hits.append(RequisiteHit(value=val, source_url=page.url,
                                           context=_context(text, m.start(), m.end())))
        for m in _LEGAL_NAME_RE.finditer(text):
            form, name = m.group(1), m.group(2).strip()
            legal_hits.append(RequisiteHit(value=f"{form} {name}", source_url=page.url,
                                            context=_context(text, m.start(), m.end())))

    return ExtractedRequisites(inn=inn_hits, ogrn=ogrn_hits, legal_names=legal_hits)


def filter_valid_inns(hits: list[RequisiteHit]) -> tuple[list[str], list[str]]:
    """Разделяет ИНН на валидные и невалидные по контрольной сумме."""
    valid, invalid = set(), set()
    for h in hits:
        (valid if is_valid_inn(h.value) else invalid).add(h.value)
    return sorted(valid), sorted(invalid)


def filter_valid_ogrns(hits: list[RequisiteHit]) -> tuple[list[str], list[str]]:
    valid, invalid = set(), set()
    for h in hits:
        (valid if is_valid_ogrn(h.value) else invalid).add(h.value)
    return sorted(valid), sorted(invalid)
