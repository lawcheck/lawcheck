"""E2 — Юр.лицо/ИП по ИНН существует в ЕГРЮЛ/ЕГРИП.

Берём валидный ИНН, найденный E1, и пробиваем по реестру ФНС.
Дополнительно сверяем: совпадает ли извлечённое наименование с реальным.
"""
from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.requisites.extract import extract, filter_valid_inns
from lawcheck.crawler.snapshot import SiteSnapshot
from lawcheck.external.egrul import lookup_by_inn
from lawcheck.utils.text import normalize_ru

CHECK_ID = "E2"
TITLE = "Сверка с ЕГРЮЛ/ЕГРИП"
LAW_REF = "ст. 51 ГК РФ; ст. 5 ФЗ № 129-ФЗ"


def _name_match(site_names: list[str], egrul_short: str, egrul_full: str) -> bool:
    """Хотя бы одно из извлечённых наименований присутствует в данных ЕГРЮЛ."""
    if not site_names:
        return False
    egrul_text = normalize_ru(f"{egrul_short} {egrul_full}")
    for site_name in site_names:
        # Берём только часть после формы (ООО/ИП/...)
        parts = site_name.split(" ", 1)
        name_part = parts[1] if len(parts) > 1 else site_name
        if normalize_ru(name_part) in egrul_text:
            return True
    return False


class EgrulMatchCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        req = extract(snapshot)
        valid_inns, _ = filter_valid_inns(req.inn)
        if not valid_inns:
            return []  # без валидного ИНН проверять нечего (зафиксировано в E1)

        inn = valid_inns[0]
        result = lookup_by_inn(inn)

        if result.error == "timeout":
            return [Finding(
                check_id=self.id, severity=Severity.INFO, title=self.title,
                evidence=f"ЕГРЮЛ не ответил вовремя при проверке ИНН {inn}. Сверку выполнить не удалось.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Повторите проверку позже.",
            )]
        if result.error == "captcha_required":
            return [Finding(
                check_id=self.id, severity=Severity.INFO, title=self.title,
                evidence=f"ЕГРЮЛ запросил капчу при проверке ИНН {inn}. Автоматическая сверка невозможна.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation=f"Проверьте вручную: https://egrul.nalog.ru/index.html (введите ИНН {inn}).",
            )]
        if result.error == "not_found":
            return [Finding(
                check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                evidence=f"ИНН {inn} не найден в ЕГРЮЛ/ЕГРИП. Либо опечатка, либо юр.лицо не существует.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Перепроверьте ИНН оператора на сайте.",
            )]
        if result.error or result.record is None:
            return [Finding(
                check_id=self.id, severity=Severity.INFO, title=self.title,
                evidence=f"При проверке ИНН {inn} в ЕГРЮЛ произошла ошибка: {result.error}.",
                location=snapshot.start_url, law_reference=LAW_REF,
            )]

        rec = result.record
        site_names = sorted({h.value for h in req.legal_names})
        name_ok = _name_match(site_names, rec.short_name, rec.full_name)

        evidence = (
            f"ИНН {rec.inn} зарегистрирован в ЕГРЮЛ: {rec.short_name} (ОГРН {rec.ogrn}, {rec.region}, "
            f"дата регистрации {rec.registered_at})."
        )

        if site_names and not name_ok:
            return [Finding(
                check_id=self.id, severity=Severity.WARNING, title=self.title,
                evidence=evidence + f" Но наименование на сайте ({', '.join(site_names[:3])}) не совпадает "
                                     f"с данными ЕГРЮЛ.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Приведите наименование в подвале сайта и Политике в соответствие с ЕГРЮЛ.",
                extra={"egrul": rec.__dict__, "site_names": site_names},
            )]

        return [Finding(
            check_id=self.id, severity=Severity.OK, title=self.title,
            evidence=evidence + (" Наименование на сайте совпадает с ЕГРЮЛ." if name_ok else ""),
            location=snapshot.start_url, law_reference=LAW_REF,
            extra={"egrul": rec.__dict__, "site_names": site_names},
        )]
