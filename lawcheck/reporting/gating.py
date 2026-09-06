"""Сколько исправлений закрыто замком — одна формула на отчёт, тарифы и письма.

Считаем не находки, а карточки, которые человек реально видит: отсутствующие
разделы Политики (A3.*) показываются одной свёрнутой карточкой, поэтому и в
замке, и на /pricing, и в письме это одно исправление, а не восемь. Пока
формула жила в трёх местах, отчёт обещал «18 исправлений», а страница тарифов
встречала числом 23 (вики lawcheck-otchet-put-do-oplaty).
"""

# Сколько рекомендаций «Как исправить» открыто в бесплатном отчёте.
# Диагноз (что сломано, цитата, штраф) открыт всегда; рецепты сверх лимита — в Pro.
FREE_RECIPES = 2

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}

# Отчёт «лёгкий», пока нарушений мало и риск не набрал полумиллиона: там пакет
# за 8 000 ₽ несоразмерен находкам (четыре пункта, два из них по 5–10 тыс ₽
# штрафа), и главным оффером идёт Pro за 990 ₽ — тот самый, которым оплачены
# все состоявшиеся продажи. Тяжёлому отчёту наоборот: чинить руками два десятка
# пунктов человек не станет, ему нужен пакет с документами под ключ.
HEAVY_RISK = 500_000
HEAVY_CRITICAL = 3

# На коротком отчёте два бесплатных рецепта — это половина товара: находок
# с рецептом всего четыре. Тизер оставляем, но одним.
SMALL_REPORT = 5

# Префикс проверок, чьи находки сворачиваются в одну карточку отчёта.
GROUPED_PREFIX = "A3"


def ordered_problems(findings) -> list:
    """Нарушения с рецептом, отсортированные critical→info (как в отчёте)."""
    return sorted(
        (f for f in findings if f.severity != "ok" and f.recommendation),
        key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.check_id),
    )


def is_grouped(finding) -> bool:
    """Уйдёт ли находка в свёрнутую карточку разделов Политики."""
    return finding.check_id.split(".")[0] == GROUPED_PREFIX


def will_collapse(findings) -> bool:
    """Свернётся ли блок Политики: одна карточка имеет смысл от двух разделов."""
    return sum(1 for f in ordered_problems(findings) if is_grouped(f)) >= 2


def free_recipes_for(problems: list) -> int:
    """Сколько рецептов открыть бесплатно при таком объёме находок."""
    return 1 if len(problems) <= SMALL_REPORT else FREE_RECIPES


def primary_offer(findings, risk: dict | None = None) -> str:
    """Какой продукт главный на этом отчёте: 'pro' (990 ₽) или 'docs' (8 000 ₽)."""
    criticals = sum(1 for f in findings if f.severity == "critical")
    risk_max = (risk or {}).get("max") or 0
    return "docs" if criticals >= HEAVY_CRITICAL or risk_max >= HEAVY_RISK else "pro"


def gate(findings) -> tuple[set, int]:
    """(id открытых рецептов, число закрытых исправлений) — один источник правды.

    Бесплатный тизер берётся среди находок, которые НЕ уедут в свёрнутую
    карточку: та открывается только целиком, поэтому попавший в неё бесплатный
    рецепт человек всё равно не увидит — на боевом скане skillbox так терялся
    один из двух (вики lawcheck-otchet-put-do-oplaty).
    """
    problems = ordered_problems(findings)
    collapsed = will_collapse(findings)
    # Хвост = наименее тяжёлые: тизер на мелочи, crown-jewel фиксы под замком.
    teaser_pool = [f for f in problems if not (collapsed and is_grouped(f))]
    free_n = free_recipes_for(problems)
    free_ids = {f.id for f in teaser_pool[-free_n:]} if free_n else set()
    locked = [f for f in problems if f.id not in free_ids]
    grouped_locked = [f for f in locked if is_grouped(f)]
    return free_ids, len(locked) - max(0, len(grouped_locked) - 1)


def locked_fix_count(findings) -> int:
    """Число закрытых замком исправлений — в тех же единицах, что карточки."""
    return gate(findings)[1]


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many
