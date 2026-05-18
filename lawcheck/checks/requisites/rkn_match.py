"""C2 — Владелец сайта зарегистрирован в реестре операторов ПДн РКН.

Если на сайте обнаружены формы сбора ПДн (B1) ИЛИ трекеры со ставящимися
идентификаторами (D1) — обработка ПДн идёт, регистрация в реестре операторов
обязательна (ст. 22 152-ФЗ). Если ничего из этого нет — регистрация может
не требоваться, эмитим INFO.

ВАЖНО: сервис pd.rkn.gov.ru плохо доступен извне РФ. При любых сетевых
проблемах эмитим INFO «не удалось проверить», а не CRITICAL — иначе ложно
заклеймим зарегистрированных операторов.
"""
from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.checks.cookies._tracker_matcher import has_pd_identifier_trackers, match_trackers
from lawcheck.checks.pd_152._form_classifier import is_pd_form
from lawcheck.checks.requisites.extract import extract, filter_valid_inns
from lawcheck.crawler.snapshot import SiteSnapshot
from lawcheck.external.rkn_operators import lookup_by_inn

CHECK_ID = "C2"
TITLE = "Регистрация в реестре операторов персональных данных РКН"
LAW_REF = "ст. 22 152-ФЗ"


def _site_processes_pd(snapshot: SiteSnapshot) -> bool:
    if any(is_pd_form(f) for f in snapshot.all_forms()):
        return True
    if has_pd_identifier_trackers(match_trackers(snapshot)):
        return True
    return False


class RknOperatorCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        req = extract(snapshot)
        valid_inns, _ = filter_valid_inns(req.inn)
        if not valid_inns:
            return []  # без ИНН проверять некого (зафиксировано в E1)

        inn = valid_inns[0]
        result = lookup_by_inn(inn)

        # Сначала смотрим, обязательна ли регистрация в принципе
        pd_required = _site_processes_pd(snapshot)

        if result.error:
            return [Finding(
                check_id=self.id, severity=Severity.INFO, title=self.title,
                evidence=f"Не удалось получить ответ от реестра операторов РКН для ИНН {inn} "
                         f"(причина: {result.error}). Сверку выполнить не удалось.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation=f"Проверьте вручную: https://pd.rkn.gov.ru/operators-registry/operators-list/?OrgInn={inn}",
            )]

        if result.operator is not None:
            return [Finding(
                check_id=self.id, severity=Severity.OK, title=self.title,
                evidence=f"Оператор зарегистрирован в реестре РКН: {result.operator.name} "
                         f"(рег. № {result.operator.registry_id}).",
                location=result.operator.detail_url or snapshot.start_url,
                law_reference=LAW_REF,
                extra={"registry_id": result.operator.registry_id, "name": result.operator.name},
            )]

        # operator is None, error пустой → not_found
        if result.not_found:
            if pd_required:
                return [Finding(
                    check_id=self.id, severity=Severity.CRITICAL, title=self.title,
                    evidence=f"Оператор с ИНН {inn} не найден в реестре РКН, но на сайте есть формы "
                             f"сбора ПДн и/или трекеры со ставящимися идентификаторами — обработка ПДн "
                             f"осуществляется. Регистрация обязательна.",
                    location=snapshot.start_url, law_reference=LAW_REF,
                    recommendation="Подайте уведомление о намерении осуществлять обработку ПДн через "
                                   "pd.rkn.gov.ru (раздел «Электронные сервисы»).",
                )]
            return [Finding(
                check_id=self.id, severity=Severity.INFO, title=self.title,
                evidence=f"Оператор с ИНН {inn} не найден в реестре РКН. На сайте также не обнаружено "
                         f"очевидных признаков обработки ПДн — регистрация может не требоваться.",
                location=snapshot.start_url, law_reference=LAW_REF,
            )]

        return [Finding(
            check_id=self.id, severity=Severity.INFO, title=self.title,
            evidence=f"Ответ реестра РКН для ИНН {inn} получен, но в неожиданном формате — сверку "
                     f"автоматически выполнить не удалось.",
            location=snapshot.start_url, law_reference=LAW_REF,
        )]
