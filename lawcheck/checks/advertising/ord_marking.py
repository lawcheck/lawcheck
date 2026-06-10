"""G3 — Маркировка интернет-рекламы (ОРД-токен erid).

С 01.09.2022 рекламный креатив в интернете обязан содержать токен ОРД
(erid=...), пометку «Реклама» и указание рекламодателя. Обязанность
возникает у того, кто РАЗМЕЩАЕТ рекламу.

Гейт применимости (см. _ad_signs.py):
- erid'ы найдены → INFO: реклама маркируется.
- Подключены сети, отдающие креативы (РСЯ, AdSense), а erid'ов нет →
  WARNING: на сайте, похоже, крутится немаркированная реклама.
- Только пиксели рекламодателя (VK/Meta/TikTok) → OK: сайт продвигает
  себя в чужих сетях, обязанность маркировки у владельца не возникает.
- Признаков рекламы нет вообще → OK: требования не применяются.
"""
from lawcheck.checks.advertising._ad_signs import detect_ad_signs, erid_from_url
from lawcheck.checks.base import Check, Finding, Severity
from lawcheck.crawler.snapshot import SiteSnapshot

CHECK_ID = "G3"
TITLE = "Маркировка интернет-рекламы (токен ОРД)"
LAW_REF = "ст. 18.1 ФЗ № 38-ФЗ «О рекламе» (с 01.09.2022)"

# Обратная совместимость: тесты и сторонний код импортируют _erid_from_url отсюда.
_erid_from_url = erid_from_url


class OrdMarkingCheck(Check):
    id = CHECK_ID
    title = TITLE

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        signs = detect_ad_signs(snapshot)

        if signs.erids:
            return [Finding(
                check_id=self.id, severity=Severity.INFO, title=self.title,
                evidence=f"Обнаружено {len(signs.erids)} уникальных токенов ОРД (erid=...). "
                         f"Это признак маркировки рекламы по требованию ФЗ «О рекламе».",
                location=snapshot.start_url, law_reference=LAW_REF,
                extra={"erid_count": len(signs.erids), "examples": sorted(signs.erids)[:5]},
            )]

        if signs.serving_networks:
            names = sorted({h.name for h in signs.serving_networks})
            return [Finding(
                check_id=self.id, severity=Severity.WARNING, title=self.title,
                evidence=f"На сайт подгружаются рекламные блоки ({', '.join(names[:5])}), "
                         f"но ни одного токена ОРД (erid=...) не найдено. Размещаемая на сайте "
                         f"реклама должна быть промаркирована.",
                location=snapshot.start_url, law_reference=LAW_REF,
                recommendation="Получите токены ОРД через одного из операторов рекламных данных "
                               "(Яндекс ОРД, ВК-Реклама, OZON ОРД и др.) и добавьте erid в URL "
                               "рекламных креативов + пометку «Реклама» рядом с ними.",
                extra={"serving_networks": names},
            )]

        if signs.advertiser_pixels:
            names = sorted({h.name for h in signs.advertiser_pixels})
            return [Finding(
                check_id=self.id, severity=Severity.OK, title=self.title,
                evidence=f"Найдены только пиксели для продвижения самого сайта "
                         f"({', '.join(names[:5])}) — это не размещение рекламы. Обязанность "
                         f"маркировки (erid) возникла бы при показе чужой рекламы на вашем сайте; "
                         f"признаков этого не обнаружено.",
                location=snapshot.start_url, law_reference=LAW_REF,
                extra={"advertiser_pixels": names},
            )]

        return [Finding(
            check_id=self.id, severity=Severity.OK, title=self.title,
            evidence="Признаков размещения рекламы на сайте не обнаружено — требования "
                     "о маркировке интернет-рекламы к сайту не применяются.",
            location=snapshot.start_url, law_reference=LAW_REF,
        )]
