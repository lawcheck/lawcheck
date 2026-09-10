"""F4 — Иностранные слова в сведениях для потребителя.

Ст. 10.1 ЗОЗПП (введена ФЗ от 24.06.2025 № 168-ФЗ, действует с 01.03.2026):
сведения для потребителя предоставляются на русском языке. Фирменные
наименования, товарные знаки и знаки обслуживания выведены из-под требования
(п. 3 ст. 3 53-ФЗ «О государственном языке»).

Ищем не латиницу вообще, а закрытый список слов с однозначным русским аналогом
(dictionaries/foreign_words.yaml) и только там, где надпись обращена к
покупателю: заголовок страницы, пункты меню и ссылки, кнопки, подписи и
подсказки полей формы. Сплошной поиск латиницы дал бы находку почти на каждом
сайте — домен, почта, Wi-Fi — и отчёт врал бы в лицо владельцу.

Товарные знаки. Прогон по живым магазинам 10.09.2026 показал, что главный
источник ложных обвинений — названия брендов в меню: «New Balance», «Free
Lance», «No Name» на rendez-vous.ru дали три находки против одной настоящей
(«SPECIAL OFFER SALE -80%»). Поэтому надпись не вменяем, если ссылка ведёт в
раздел брендов или если словарное слово стоит в паре с соседним с заглавной
буквы. Правило заодно съедает часть настоящих нарушений («About Us», «Free
Shipping»), и это осознанный размен: ложное обвинение в отчёте стоит дороже
пропущенной надписи.

Гейт применимости: требование про сведения ДЛЯ ПОТРЕБИТЕЛЯ. Интернет-магазин
получает WARNING. Сайт с формой заявки, но без признаков продажи, — INFO с
условной формулировкой: продаёт ли он потребителям, по одной форме не видно.
Сайт без того и другого проверку не проходит вовсе.

Штраф намеренно не считаем: F4 не заведён в map: словаря fines.yaml, пока
санкция не сверена с первоисточником (см. шапку foreign_words.yaml).
"""
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.zozpp._ecommerce import is_ecommerce_site
from lawcheck.crawler.snapshot import SiteSnapshot
from lawcheck.dictionaries.loader import foreign_words

CHECK_ID = "F4"
TITLE = "Иностранные слова в сведениях для потребителя"
LAW_REF = "ст. 10.1 ЗОЗПП (ФЗ от 24.06.2025 № 168-ФЗ)"

MAX_EXAMPLES = 8

FIELD_KIND = "поле формы"

_BUTTON_RE = re.compile(r"<button[^>]*>(.*?)</button>", re.I | re.S)
_SUBMIT_RE = re.compile(r"<input[^>]+type=[\"']?(?:submit|button)[\"']?[^>]*>", re.I)
_VALUE_RE = re.compile(r"value=[\"']([^\"']+)[\"']", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)*")
_CAPITALIZED_RE = re.compile(r"^[A-Z][a-z'-]+$")

# Надпись целиком является адресом: ссылка, домен или почта. Слово внутри адреса —
# идентификатор, а не сведения для потребителя.
_ADDRESS_RE = re.compile(
    r"(https?://|www\.|@|\b[a-z0-9-]+\.(?:ru|com|net|org|io|рф)\b)", re.I,
)
# Знак ™/® рядом — прямое исключение п. 3 ст. 3 53-ФЗ, надпись не трогаем.
_TRADEMARK_RE = re.compile(r"[™®]")
# Ссылка в раздел брендов: что бы там ни было написано, это товарные знаки.
# Ищем только в пути, а не во всём адресе: у краулера ссылки абсолютные, и на
# домене вроде brandshop.ru совпадал бы КАЖДЫЙ href — проверка ослепла бы целиком.
_BRAND_LINK_RE = re.compile(r"(brand|vendor|manufactur|proizvoditel)", re.I)


def _brand_section(href: str) -> bool:
    parts = urlsplit(href)
    return bool(_BRAND_LINK_RE.search(f"{parts.path}?{parts.query}"))

_SKIP_DOMAIN_PARTS = {"www", "ru", "com", "net", "org", "io", "xn--p1ai"}


@dataclass(frozen=True)
class _Snippet:
    kind: str   # где нашли — попадает в текст находки
    page: str
    text: str
    href: str = ""


def _brand_names(snapshot: SiteSnapshot) -> set[str]:
    """Имя сайта по домену, склеенное в одно слово: «new-style.ru» -> «newstyle».

    Надпись, которая целиком повторяет имя сайта, — фирменное наименование
    (п. 3 ст. 3 53-ФЗ), её не вменяем. Сравниваем всю надпись, а не отдельные
    слова: иначе на sale-shop.ru проверка ослепла бы на «SALE» — там, где это
    как раз распродажа. Название сайта из <title> не берём: заголовок вида
    «SALE | Магазин» выключил бы проверку целиком.
    """
    host = urlsplit(snapshot.start_url).hostname or ""
    names: set[str] = set()
    for part in host.lower().split("."):
        if part in _SKIP_DOMAIN_PARTS:
            continue
        joined = re.sub(r"[^a-z]", "", part)
        if joined:
            names.add(joined)
    return names


def _snippets(snapshot: SiteSnapshot) -> list[_Snippet]:
    out: list[_Snippet] = []
    for page in snapshot.pages:
        if page.error or page.status >= 400:
            continue
        if page.title:
            out.append(_Snippet("заголовок страницы", page.url, page.title))
        for link in page.links:
            if link.text.strip():
                out.append(_Snippet("ссылка", page.url, link.text, link.url))
        for raw in _BUTTON_RE.findall(page.html or ""):
            label = _TAG_RE.sub(" ", raw).strip()
            if label:
                out.append(_Snippet("кнопка", page.url, label))
        for tag in _SUBMIT_RE.findall(page.html or ""):
            m = _VALUE_RE.search(tag)
            if m:
                out.append(_Snippet("кнопка", page.url, m.group(1)))
    for form in snapshot.all_forms():
        for field in form.fields:
            for label in (field.label, field.placeholder):
                if label.strip():
                    out.append(_Snippet(FIELD_KIND, form.page_url, label))
    return out


def _is_proper_name(words: list[str], i: int) -> bool:
    """«New Balance», «No Name» — слово с заглавной рядом с таким же соседом."""
    if not _CAPITALIZED_RE.match(words[i]):
        return False
    return any(_CAPITALIZED_RE.match(words[j])
               for j in (i - 1, i + 1) if 0 <= j < len(words))


def _scan(
    snippets: list[_Snippet], vocab: dict[str, str], brand: set[str],
) -> list[tuple[_Snippet, str, str]]:
    """(где, иностранное слово, русский аналог) — по разу на пару слово+место."""
    seen: set[tuple[str, str]] = set()
    hits: list[tuple[_Snippet, str, str]] = []
    for sn in snippets:
        if (_ADDRESS_RE.search(sn.text) or _TRADEMARK_RE.search(sn.text)
                or (sn.href and _brand_section(sn.href))):
            continue
        words = _WORD_RE.findall(sn.text)
        if "".join(re.sub(r"[^a-z]", "", w.lower()) for w in words) in brand:
            continue
        for i, word in enumerate(words):
            token = word.lower()
            ru = vocab.get(token)
            if ru is None or (token, sn.kind) in seen or _is_proper_name(words, i):
                continue
            seen.add((token, sn.kind))
            hits.append((sn, token, ru))
    return hits


def _sample(hits: list[tuple[_Snippet, str, str]]) -> str:
    return ", ".join(f"«{token}» ({ru}) — {sn.kind}" for sn, token, ru in hits[:3])


class StateLanguageCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        is_ecommerce, _ = is_ecommerce_site(snapshot)
        has_forms = bool(snapshot.all_forms())
        if not is_ecommerce and not has_forms:
            return []

        vocab = foreign_words()
        brand = _brand_names(snapshot)
        snippets = _snippets(snapshot)
        # Словарь подписей полей — только к полям: слово «name» в тексте ссылки
        # это чей-то «No Name», а не подпись поля.
        interface = _scan([s for s in snippets if s.kind != FIELD_KIND],
                          vocab["interface"], brand)
        fields = _scan([s for s in snippets if s.kind == FIELD_KIND],
                       vocab["form_fields"], brand)

        if not interface and not fields:
            return [Finding(
                check_id=self.id, severity=Severity.OK, title=self.title,
                evidence="Меню, кнопки и подписи полей — на русском.",
                location=snapshot.start_url, law_reference=LAW_REF,
            )]

        hits = interface + fields
        extra = {"examples": [
            {"page": sn.page, "place": sn.kind, "word": token, "russian": ru}
            for sn, token, ru in hits[:MAX_EXAMPLES]
        ]}
        recommendation = (
            "Замените эти надписи русскими. Фирменное наименование и товарный знак "
            "переводить не нужно — они выведены из-под требования (п. 3 ст. 3 53-ФЗ). "
            "Иностранный вариант можно оставить вторым, рядом с русским."
        )

        if interface and is_ecommerce:
            return [Finding(
                check_id=self.id, severity=Severity.WARNING, title=self.title,
                evidence=f"Найдено {len(hits)} надписей для покупателя иностранными словами "
                         f"при наличии русского аналога: {_sample(hits)}. Требование "
                         f"ст. 10.1 ЗОЗПП действует с 01.03.2026.",
                location=hits[0][0].page, law_reference=LAW_REF,
                recommendation=recommendation, extra=extra,
            )]

        if interface:
            return [Finding(
                check_id=self.id, severity=Severity.INFO, title=self.title,
                evidence=f"Найдено {len(hits)} надписей иностранными словами при наличии "
                         f"русского аналога: {_sample(hits)}. Признаков продажи потребителям "
                         f"на сайте нет — ст. 10.1 ЗОЗПП применяется к сведениям для "
                         f"потребителя, и вменять её этому сайту рано.",
                location=hits[0][0].page, law_reference=LAW_REF,
                recommendation="Если через сайт продаёте или оказываете услуги физлицам — "
                               + recommendation[0].lower() + recommendation[1:],
                extra=extra,
            )]

        return [Finding(
            check_id=self.id, severity=Severity.INFO, title=self.title,
            evidence=f"Меню и кнопки на русском, но подписи полей формы — иностранные: "
                     f"{_sample(fields)}.",
            location=fields[0][0].page, law_reference=LAW_REF,
            recommendation=recommendation, extra=extra,
        )]
