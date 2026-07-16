"""Авто-черновик документов под сайт (reporting/policy_draft)."""
from lawcheck.reporting import policy_draft


class _F:
    def __init__(self, check_id, severity="info", extra=None):
        self.check_id = check_id
        self.severity = severity
        self.extra = extra or {}


class _Scan:
    def __init__(self, url, findings):
        self.url = url
        self.findings = findings


def _full_scan():
    return _Scan("https://fistashki.org", [
        _F("E1.inn", "ok", {"inn": "7714819798"}),
        _F("E1.ogrn", "ok", {"ogrn": "322774600250213"}),
        _F("E1.name", "ok", {"names": ["ООО «Фисташки»"]}),
        _F("B1", "info", {"categories": ["email", "full_name", "phone"]}),
        _F("D1.Google Analytics 4", "critical"),
        _F("D1.Яндекс.Метрика", "info"),
    ])


def test_extract_facts():
    facts = policy_draft.extract_facts(_full_scan())
    assert facts["inn"] == "7714819798"
    assert facts["ogrn"] == "322774600250213"
    assert facts["operator_name"] == "ООО «Фисташки»"
    assert set(facts["categories"]) == {"email", "full_name", "phone"}
    assert facts["trackers_foreign"] == ["Google Analytics 4"]
    assert facts["trackers_ru"] == ["Яндекс.Метрика"]


def test_render_fills_detected_facts():
    html = policy_draft.render(_full_scan())
    # реквизиты подставлены
    assert "7714819798" in html and "322774600250213" in html and "ООО «Фисташки»" in html
    # категории — человеческие формулировки
    assert "адрес электронной почты" in html and "номер телефона" in html
    # трансграничная передача из зарубежного трекера
    assert "Google Analytics 4" in html and "трансгранич" in html.lower()
    # текст согласия под поля сайта
    assert "☐ Я даю согласие" in html
    # cookie-политика с реально обнаруженными сервисами
    assert "Политика в отношении файлов cookie" in html
    assert "Яндекс.Метрика" in html
    # владельческие поля помечены к заполнению
    assert "[ЗАПОЛНИТЕ" in html


def test_render_marks_blanks_when_nothing_detected():
    scan = _Scan("https://empty.ru", [])  # ничего не нашли
    html = policy_draft.render(scan)
    assert html.count("[ЗАПОЛНИТЕ") >= 4          # реквизиты, цели, сроки, контакты…
    assert "empty.ru" in html
    assert "трансграничная передача персональных данных не осуществляется" in html.lower()
