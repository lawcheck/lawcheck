"""Эвристический классификатор «это интернет-магазин или сервис продажи?» +
поиск ссылок на нужные документы (оферта, доставка, возврат).

Решает базовый вопрос: применимы ли требования ЗОЗПП и Правил продажи
(ПП РФ № 2463) к этому сайту вообще.
"""
import re

from lawcheck.crawler.snapshot import SiteSnapshot
from lawcheck.utils.text import normalize_ru

# === классификатор: «сайт продаёт?» ===

# Сильные сигналы — однозначно e-commerce / сервис продажи
_ECOM_STRONG = re.compile(
    r"(добавить в корзину|оформить заказ|корзин[аыеу]|чек[ао]ут|"
    r"купить (?:сейчас|со скидкой)?|в корзину|товаров? в корзине|"
    r"итого к оплате|способы доставки|способы оплаты|"
    r"в один клик|оформление заказа|добавлено в корзину)",
    re.I,
)
# Слабые сигналы — нужно >= 2
_ECOM_WEAK = re.compile(
    r"(\bкорзина\b|\bcart\b|\bcheckout\b|\bкупить\b|\bцена\b|"
    r"\bкатегори[ия][а-я]*\b|\bтовар[аыов]*\b|\bдоставк[аеуи]\b|"
    r"\bоплат[аеыу]\b|\bакци[ия]\b|\bскидк[аиу]\b)",
    re.I,
)

_ECOM_LINK_RE = re.compile(r"(cart|basket|checkout|/shop/|/store/|/order)", re.I)


def is_ecommerce_site(snapshot: SiteSnapshot) -> tuple[bool, str]:
    """Возвращает (is_ecommerce, reason)."""
    text_chunks: list[str] = []
    link_hits = 0
    for page in snapshot.pages:
        if page.error or page.status >= 400:
            continue
        text_chunks.append(page.text or "")
        for link in page.links:
            if _ECOM_LINK_RE.search(link.url):
                link_hits += 1
    combined = normalize_ru(" ".join(text_chunks))
    strong_hits = len(_ECOM_STRONG.findall(combined))
    weak_hits = len(_ECOM_WEAK.findall(combined))

    if strong_hits >= 1:
        return True, f"найдено {strong_hits} сильных маркеров (корзина/оформление заказа)"
    if weak_hits >= 4 and link_hits >= 1:
        return True, f"{weak_hits} слабых маркеров + {link_hits} cart/checkout-ссылок"
    return False, f"strong={strong_hits}, weak={weak_hits}, cart_links={link_hits}"


# === поиск документов ===

def find_doc_links(
    snapshot: SiteSnapshot, text_re: re.Pattern, url_re: re.Pattern,
) -> list[tuple[str, str]]:
    """(page_url, doc_url) для каждой страницы, где найдена ссылка матчащая паттерн."""
    out: list[tuple[str, str]] = []
    for page in snapshot.pages:
        if page.error or page.status >= 400:
            continue
        for link in page.links:
            if text_re.search(normalize_ru(link.text)) or url_re.search(link.url):
                out.append((page.url, link.url))
                break
    return out


# Паттерны для трёх типов документов
OFERTA_TEXT_RE = re.compile(
    r"(оферт|договор\s+(?:публичной\s+)?оферт|правил[ао]\s+(?:продаж|использован|оказан)|"
    r"пользовательск[ое]\s+соглашен)",
    re.I,
)
OFERTA_URL_RE = re.compile(r"(ofert|oferta|public-offer|terms|rules|agreement|usloviy[a-z]*-prodazh)", re.I)

DELIVERY_TEXT_RE = re.compile(
    r"(услови[яей]\s+доставк|способ[ыа]?\s+доставк|информаци[яюи]\s+о\s+доставк|"
    r"\bдоставк[аеу]\b|delivery|shipping)",
    re.I,
)
DELIVERY_URL_RE = re.compile(r"(delivery|shipping|dostavk|otpravk)", re.I)

RETURN_TEXT_RE = re.compile(
    r"(возврат\s+(?:товар|денеж|средств)|обмен\s+товар|политик[аи]\s+возврат|"
    r"услови[яей]\s+возврат|refund|return)",
    re.I,
)
RETURN_URL_RE = re.compile(r"(return|refund|vozvrat|obmen)", re.I)
