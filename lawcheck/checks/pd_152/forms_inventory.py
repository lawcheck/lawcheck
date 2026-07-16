"""B1 — Инвентаризация форм, собирающих ПДн.

Сама по себе не «нарушение/норма», а информационная сводка для пользователя:
сколько форм, что собирают, на каких страницах. Если форм с ПДн вообще нет —
это тоже важный факт (тогда регистрация в реестре операторов РКН может не
требоваться, см. C3).
"""
from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.pd_152._form_classifier import form_pd_categories, is_pd_form
from lawcheck.crawler.snapshot import SiteSnapshot

CHECK_ID = "B1"
TITLE = "Формы сбора персональных данных"
LAW_REF = "ст. 3, ст. 18.1 152-ФЗ"


class FormsInventoryCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        pd_forms = [f for f in snapshot.unique_forms() if is_pd_form(f)]

        if not pd_forms:
            return [Finding(
                check_id=self.id, severity=Severity.INFO, title=self.title,
                evidence=f"На {len(snapshot.pages)} проверенных страницах не обнаружено форм, "
                         f"собирающих персональные данные.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Если сайт собирает ПДн через сторонние сервисы (например, сторонние формы), "
                               "это также требует соответствия 152-ФЗ.",
            )]

        findings: list[Finding] = []
        for form in pd_forms:
            cats = sorted(form_pd_categories(form))
            findings.append(Finding(
                check_id=self.id, severity=Severity.INFO, title=self.title,
                evidence=f"Форма ({form.method.upper()} {form.action or '—'}) собирает ПДн категорий: "
                         f"{', '.join(cats)}. Полей: {len(form.fields)}.",
                location=form.page_url, law_reference=LAW_REF,
                extra={"categories": cats, "field_count": len(form.fields), "action": form.action},
            ))
        return findings
