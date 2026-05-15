"""B2 — У каждой формы сбора ПДн есть чекбокс согласия + ссылка на Политику.

Логика по форме:
  - нет чекбокса с подходящим текстом      → CRITICAL (нарушение ст. 9)
  - чекбокс есть, но `checked` по умолчанию → CRITICAL (нарушение принципа
    активного действия — согласие не может быть подразумеваемым, см. ст. 9 ч. 1)
  - чекбокс есть, согласие явное, но в радиусе формы нет ссылки на Политику
    → WARNING
  - всё хорошо                             → OK
"""
from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.pd_152._form_classifier import find_consent_checkbox, is_pd_form
from lawcheck.crawler.snapshot import SiteSnapshot

CHECK_ID = "B2"
TITLE = "Согласие на обработку ПДн у формы"
LAW_REF = "ст. 9 152-ФЗ"


class FormConsentCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        pd_forms = [f for f in snapshot.all_forms() if is_pd_form(f)]
        if not pd_forms:
            return []

        findings: list[Finding] = []
        for idx, form in enumerate(pd_forms, start=1):
            form_label = f"форма #{idx} ({form.method.upper()} {form.action or '—'}) на {form.page_url}"
            checkbox = find_consent_checkbox(form)

            if checkbox is None:
                findings.append(Finding(
                    check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                    evidence=f"У формы нет чекбокса согласия на обработку ПДн. {form_label}",
                    location=form.page_url, law_reference=LAW_REF,
                    recommendation="Добавьте обязательный чекбокс с текстом «Согласен на обработку персональных данных» "
                                   "и ссылкой на Политику. Чекбокс должен быть НЕ отмечен по умолчанию.",
                ))
                continue

            if checkbox.checked:
                findings.append(Finding(
                    check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                    evidence=f"Чекбокс согласия отмечен по умолчанию (checked). {form_label}",
                    location=form.page_url, law_reference=LAW_REF,
                    recommendation="Снимите атрибут checked. Согласие должно быть результатом активного действия "
                                   "пользователя.",
                ))
                continue

            if not form.has_policy_link:
                findings.append(Finding(
                    check_id=self.id, severity=Severity.WARNING, title=self.title,
                    evidence=f"Согласие есть, но рядом с формой не найдена ссылка на Политику обработки ПДн. {form_label}",
                    location=form.page_url, law_reference=LAW_REF,
                    recommendation="Добавьте в текст чекбокса ссылку на Политику обработки персональных данных.",
                ))
                continue

            findings.append(Finding(
                check_id=self.id, severity=Severity.OK, title=self.title,
                evidence=f"Согласие реализовано корректно: чекбокс не отмечен по умолчанию, "
                         f"в радиусе формы есть ссылка на Политику. {form_label}",
                location=form.page_url, law_reference=LAW_REF,
            ))

        return findings
