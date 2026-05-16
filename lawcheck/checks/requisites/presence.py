"""E1 — Наличие, валидность и непротиворечивость юридических реквизитов.

Проверяем:
  - есть ли на сайте ИНН (без него оператора нельзя идентифицировать в реестре РКН);
  - есть ли ОГРН/ОГРНИП;
  - все ли найденные значения валидны по контрольной сумме (защита от опечаток);
  - не противоречат ли значения между страницами (например, в футере один ИНН,
    в Политике другой — типичная ошибка при копипасте шаблона).
"""
from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.requisites.extract import (
    extract, filter_valid_inns, filter_valid_ogrns,
)
from lawcheck.crawler.snapshot import SiteSnapshot

CHECK_ID = "E1"
TITLE = "Юридические реквизиты владельца сайта"
LAW_REF = "ст. 18.1 ч. 2 152-ФЗ; п. 2 ст. 8 ЗоЗПП; п. 9 Правил продажи (ПП РФ № 2463)"


class RequisitesPresenceCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        req = extract(snapshot)
        findings: list[Finding] = []

        # --- ИНН ---
        if not req.inn:
            findings.append(Finding(
                check_id=f"{self.id}.inn", severity=Severity.CRITICAL, title=f"{TITLE}: ИНН",
                evidence="ИНН на сайте не найден ни на одной из проверенных страниц.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Укажите ИНН оператора в подвале сайта и в Политике обработки ПДн.",
            ))
        else:
            valid, invalid = filter_valid_inns(req.inn)
            if invalid and not valid:
                findings.append(Finding(
                    check_id=f"{self.id}.inn", severity=Severity.WARNING, title=f"{TITLE}: ИНН",
                    evidence=f"Найдены ИНН, но все провалили проверку контрольной суммы: {', '.join(invalid)}. "
                             f"Скорее всего, опечатка.",
                    location=req.inn[0].source_url, law_reference=LAW_REF,
                    recommendation="Перепроверьте ИНН — текущее значение не валидно.",
                ))
            elif len(valid) > 1:
                findings.append(Finding(
                    check_id=f"{self.id}.inn", severity=Severity.WARNING, title=f"{TITLE}: ИНН",
                    evidence=f"На сайте указано несколько разных ИНН: {', '.join(valid)}. "
                             f"Это противоречие — оператор должен быть единственным.",
                    location=req.inn[0].source_url, law_reference=LAW_REF,
                    recommendation="Приведите ИНН к единому значению (футер, Политика, страница контактов).",
                    extra={"all_values": valid},
                ))
            else:
                inn_val = valid[0]
                # сколько страниц упомянули ИНН
                pages_with = {h.source_url for h in req.inn if h.value == inn_val}
                findings.append(Finding(
                    check_id=f"{self.id}.inn", severity=Severity.OK, title=f"{TITLE}: ИНН",
                    evidence=f"ИНН {inn_val} валиден, указан на {len(pages_with)} страницах.",
                    location=next(iter(pages_with)), law_reference=LAW_REF,
                    extra={"inn": inn_val, "page_count": len(pages_with)},
                ))

        # --- ОГРН ---
        if not req.ogrn:
            findings.append(Finding(
                check_id=f"{self.id}.ogrn", severity=Severity.WARNING, title=f"{TITLE}: ОГРН/ОГРНИП",
                evidence="ОГРН/ОГРНИП на сайте не найден.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Укажите ОГРН (для юр.лиц) или ОГРНИП (для ИП) рядом с ИНН.",
            ))
        else:
            valid, invalid = filter_valid_ogrns(req.ogrn)
            if invalid and not valid:
                findings.append(Finding(
                    check_id=f"{self.id}.ogrn", severity=Severity.WARNING, title=f"{TITLE}: ОГРН/ОГРНИП",
                    evidence=f"Найдены ОГРН, но все провалили проверку контрольной суммы: {', '.join(invalid)}.",
                    location=req.ogrn[0].source_url, law_reference=LAW_REF,
                    recommendation="Перепроверьте ОГРН — текущее значение не валидно.",
                ))
            elif len(valid) > 1:
                findings.append(Finding(
                    check_id=f"{self.id}.ogrn", severity=Severity.WARNING, title=f"{TITLE}: ОГРН/ОГРНИП",
                    evidence=f"На сайте указано несколько разных ОГРН: {', '.join(valid)}.",
                    location=req.ogrn[0].source_url, law_reference=LAW_REF,
                    recommendation="Приведите ОГРН к единому значению.",
                    extra={"all_values": valid},
                ))
            else:
                ogrn_val = valid[0]
                findings.append(Finding(
                    check_id=f"{self.id}.ogrn", severity=Severity.OK, title=f"{TITLE}: ОГРН/ОГРНИП",
                    evidence=f"ОГРН {ogrn_val} валиден.",
                    location=req.ogrn[0].source_url, law_reference=LAW_REF,
                    extra={"ogrn": ogrn_val},
                ))

        # --- Наименование (информационно) ---
        if req.legal_names:
            unique_names = sorted({h.value for h in req.legal_names})[:3]
            findings.append(Finding(
                check_id=f"{self.id}.name", severity=Severity.INFO, title=f"{TITLE}: Наименование",
                evidence=f"Обнаружены формы наименования: {', '.join(unique_names)}.",
                location=req.legal_names[0].source_url, law_reference=LAW_REF,
                extra={"names": unique_names},
            ))

        return findings
