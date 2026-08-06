"""Находки собственного сканера по своему же сайту: G1 и H1.

Проверяем ИСХОДНИКИ шаблонов, а не отрендеренные страницы: и маркировка, и
формулировки — статический текст, из данных ничего не подставляется. Заодно
тест не зависит от seo_enabled, который подключает роутер блога на импорте.

Регулярки берём прямо из проверок — тогда тест ломается ровно тогда, когда
находка вернётся, а не когда кто-то переформулирует ожидание.
"""
import re
from pathlib import Path

import pytest

from lawcheck.checks.advertising.superlatives import _SUPERLATIVE_RE
from lawcheck.checks.media.age_marking import _AGE_LABEL_RE

TEMPLATES = Path(__file__).resolve().parents[1] / "lawcheck" / "web" / "templates"

_JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}", re.S)


def _src(name: str) -> str:
    """Текст шаблона без Jinja-комментариев.

    Комментарии до браузера не доезжают, а объяснять в них находку приходится
    её же словами — иначе тест ловит собственную документацию.
    """
    return _JINJA_COMMENT_RE.sub(" ", (TEMPLATES / name).read_text(encoding="utf-8"))


# === H1: возрастная маркировка (ст. 11, 13 ФЗ № 436-ФЗ) ===

@pytest.mark.parametrize("template", ["blog_index.html", "blog_article.html"])
def test_vozrastnaya_markirovka_est(template):
    """Сайт классифицируется как медиа из-за блога — маркировка обязана быть
    и на листинге, и у каждого материала."""
    labels = _AGE_LABEL_RE.findall(_src(template))
    assert labels, f"в {template} нет возрастной маркировки"


# === G1: превосходная степень (ст. 5 ч. 3 ФЗ «О рекламе») ===

def test_bez_prevoshodnoy_stepeni_na_tarifah():
    """«Единственный сервис» — утверждение о превосходстве без критерия
    сравнения и источника. Формулировка уходит и в объявления Директа."""
    hits = _SUPERLATIVE_RE.findall(_src("pricing.html"))
    assert not hits, f"на странице тарифов снова превосходная степень: {hits}"
