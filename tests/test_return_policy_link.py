"""F3 — условия возврата должны быть findable, а не просто существовать.

Раздел в оферте был и раньше; сканер ставил warning потому, что на него не
вело ни одной ссылки с распознаваемым текстом или адресом. Проверяем ровно
это — теми же паттернами, что и сама проверка F3.
"""
import re
from pathlib import Path

from lawcheck.checks.zozpp._ecommerce import RETURN_TEXT_RE, RETURN_URL_RE
from lawcheck.utils.text import normalize_ru

TEMPLATES = Path(__file__).resolve().parents[1] / "lawcheck" / "web" / "templates"
_LINK_RE = re.compile(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', re.S)


def _links(name: str) -> list[tuple[str, str]]:
    src = (TEMPLATES / name).read_text(encoding="utf-8")
    src = re.sub(r"\{#.*?#\}", " ", src, flags=re.S)  # Jinja-комментарии не рендерятся
    return [(href, re.sub(r"<[^>]+>", "", text)) for href, text in _LINK_RE.findall(src)]


def test_na_tarifah_est_ssylka_na_usloviya_vozvrata():
    """Страница оплаты — то место, где покупатель ищет условия возврата."""
    hits = [(href, text) for href, text in _links("pricing.html")
            if RETURN_TEXT_RE.search(normalize_ru(text)) or RETURN_URL_RE.search(href)]
    assert hits, "на странице тарифов нет ссылки на условия возврата"


def test_yakor_v_oferte_na_meste():
    """Ссылка ведёт на #vozvrat — без якоря она приведёт в начало оферты."""
    assert 'id="vozvrat"' in (TEMPLATES / "oferta.html").read_text(encoding="utf-8")


def test_ssylka_s_tarifov_vedyot_na_sushchestvuyushchiy_yakor():
    targets = {href for href, _ in _links("pricing.html") if href.startswith("/oferta#")}
    oferta = (TEMPLATES / "oferta.html").read_text(encoding="utf-8")
    for t in targets:
        anchor = t.split("#", 1)[1]
        assert f'id="{anchor}"' in oferta, f"якоря #{anchor} в оферте нет"
