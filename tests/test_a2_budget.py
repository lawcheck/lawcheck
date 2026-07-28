"""A2 не должна винить клиента за то, что краулер до документа не дошёл.

Дефолт веб-формы — 10 страниц. Если Политика в этот бюджет не попала, старое
поведение выдавало предупреждение «не удалось загрузить документ Политики»,
то есть претензию к сайту за наше собственное ограничение. В юридическом отчёте
ложное срабатывание дороже пропуска — см. lawcheck/checks/CLAUDE.md.
"""
from lawcheck.checks.base import Severity
from lawcheck.checks.pd_152.policy_validity import PolicyValidityCheck
from lawcheck.crawler.snapshot import Link, PageSnapshot, SiteSnapshot

POLICY_URL = "https://mysite.ru/policy"


def _snapshot(*, budget_reached: bool, policy_page: PageSnapshot | None) -> SiteSnapshot:
    home = PageSnapshot(url="https://mysite.ru/", status=200, text="главная",
                        links=[Link(url=POLICY_URL, text="Политика конфиденциальности")])
    pages = [home] + ([policy_page] if policy_page else [])
    return SiteSnapshot(start_url="https://mysite.ru/", pages=pages,
                        budget_reached=budget_reached)


def test_ne_doshli_po_byudzhetu_nahodki_net():
    snap = _snapshot(budget_reached=True, policy_page=None)
    assert PolicyValidityCheck().run(snap) == []


def test_doshli_i_ne_otkrylos_nahodka_est():
    """Бюджет не исчерпан, а страницы всё равно нет — это уже про сайт."""
    snap = _snapshot(budget_reached=False, policy_page=None)
    findings = PolicyValidityCheck().run(snap)
    assert len(findings) == 1
    assert findings[0].severity == Severity.WARNING
    assert "Не удалось загрузить" in findings[0].evidence


def test_dokument_otkrylsya_nahodka_ok():
    page = PageSnapshot(url=POLICY_URL, status=200, text="х" * 2000)
    findings = PolicyValidityCheck().run(_snapshot(budget_reached=True, policy_page=page))
    assert len(findings) == 1
    assert findings[0].severity == Severity.OK


def test_byudzhet_ne_pryachet_nastoyashchuyu_oshibku():
    """Страница дошла, но отдала 404 — исчерпанный бюджет это не оправдывает."""
    page = PageSnapshot(url=POLICY_URL, status=404, text="")
    findings = PolicyValidityCheck().run(_snapshot(budget_reached=True, policy_page=page))
    assert len(findings) == 1
    assert findings[0].severity == Severity.CRITICAL
    assert "404" in findings[0].evidence
