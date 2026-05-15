"""Классификация полей форм: какие из них собирают ПДн и каких категорий.

Используется проверками B1 (инвентаризация) и B2 (согласие у формы),
а в дальнейшем — B3 (отдельное согласие на маркетинг) и B4 (HTTPS).
"""
from lawcheck.crawler.snapshot import Form, FormField
from lawcheck.dictionaries import loader

# Поля, которые однозначно НЕ являются ПДн — служебные
_NON_PD_TYPES = {"hidden", "submit", "button", "reset", "image", "file", "checkbox", "radio", "search"}


def _haystacks(field: FormField) -> str:
    return " ".join([field.name, field.id, field.placeholder, field.label]).lower()


def field_pd_categories(field: FormField) -> list[str]:
    """Категории ПДн, которые предположительно собирает поле. Пустой список — не ПДн."""
    if field.type in _NON_PD_TYPES:
        return []
    haystack = _haystacks(field)
    found: list[str] = []
    for category, patterns in loader.pd_field_patterns().items():
        types = [t.lower() for t in patterns.get("types") or []]
        names = [n.lower() for n in patterns.get("name_patterns") or []]
        if field.type in types or any(n in haystack for n in names):
            found.append(category)
    return found


def form_pd_categories(form: Form) -> set[str]:
    out: set[str] = set()
    for f in form.fields:
        out.update(field_pd_categories(f))
    return out


def is_pd_form(form: Form) -> bool:
    return bool(form_pd_categories(form))


def find_consent_checkbox(form: Form) -> FormField | None:
    """Чекбокс с текстом, похожим на согласие на обработку ПДн."""
    markers = [m.lower() for m in loader.consent_markers().get("consent_text_markers") or []]
    for field in form.fields:
        if field.type != "checkbox":
            continue
        label = (field.label or "").lower().replace("ё", "е")
        if any(m in label for m in markers):
            return field
    return None
