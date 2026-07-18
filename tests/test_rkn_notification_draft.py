"""Черновик уведомления в РКН под сайт (reporting/rkn_notification_draft)."""
from lawcheck.reporting import rkn_notification_draft


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
        _F("C2", "critical"),
    ])


def test_render_fills_detected_facts():
    html = rkn_notification_draft.render(_full_scan())
    # реквизиты подставлены
    assert "7714819798" in html and "322774600250213" in html and "ООО «Фисташки»" in html
    # категории — человеческие формулировки
    assert "адрес электронной почты" in html and "номер телефона" in html
    # трансграничная передача из зарубежного трекера
    assert "Google Analytics 4" in html and "Осуществляется" in html
    # владельческие поля помечены к заполнению
    assert "[ЗАПОЛНИТЕ" in html
    # инструкция подачи на месте
    assert "pd.rkn.gov.ru" in html and "Госуслуги" in html


def test_render_c2_problem_warns_about_fine():
    html = rkn_notification_draft.render(_full_scan())
    assert "не нашёл оператора в реестре" in html
    assert "100–300 тыс ₽" in html


def test_render_c2_ok_switches_to_update_mode():
    scan = _full_scan()
    for f in scan.findings:
        if f.check_id == "C2":
            f.severity = "ok"
    html = rkn_notification_draft.render(scan)
    assert "уже есть в реестре РКН" in html
    assert "информационное письмо об изменениях" in html


def test_render_no_foreign_trackers():
    scan = _Scan("https://empty.ru", [
        _F("D1.Яндекс.Метрика", "info"),
    ])
    html = rkn_notification_draft.render(scan)
    assert "не осуществляется" in html
    assert html.count("[ЗАПОЛНИТЕ") >= 6  # реквизиты, адрес, цели, ответственный…
    assert "empty.ru" in html
