"""Ориентировочная оценка штрафов по найденным нарушениям.

Суммы — порядковая оценка риска по КоАП РФ (см. dictionaries/fines.yaml),
а не юридическая консультация. Суммарный риск считается по НАБОРУ затронутых
статей: несколько нарушений по одной статье не суммируются.
"""
from lawcheck.dictionaries.loader import load


def _data() -> dict:
    return load("fines")


def group_for(check_id: str) -> dict | None:
    """Группа штрафа для check_id (по префиксу до точки) или None."""
    prefix = check_id.split(".")[0]
    key = _data()["map"].get(prefix)
    if key is None:
        return None
    return {"key": key, **_data()["groups"][key]}


def risk_total(findings) -> dict | None:
    """Суммарный риск по уникальным статьям для нарушений (severity != ok).

    Возвращает {min, max, groups:[{koap,min,max}...]} или None, если нарушений нет.
    """
    keys: dict[str, dict] = {}
    for f in findings:
        if f.severity == "ok":
            continue
        g = group_for(f.check_id)
        if g is not None:
            keys[g["key"]] = g
    if not keys:
        return None
    groups = sorted(keys.values(), key=lambda g: g["max"], reverse=True)
    return {
        "min": sum(g["min"] for g in groups),
        "max": sum(g["max"] for g in groups),
        "groups": groups,
    }
