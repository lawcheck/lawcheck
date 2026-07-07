"""После завершения скана мониторинга — разослать diff клиентам, подключившим
Telegram. Вызывается из обоих путей выполнения скана (worker и in-process)."""
import logging

from lawcheck.config import settings
from lawcheck.db import repo
from lawcheck.notify import telegram
from lawcheck.reporting import scandiff

log = logging.getLogger(__name__)


def notify_monitoring(url: str) -> None:
    """Если url мониторят клиенты с подключённым Telegram — посчитать diff между
    двумя последними завершёнными сканами и разослать изменения. Best-effort."""
    try:
        clients = repo.clients_subscribed_to_url(url)
        if not clients:
            return
        scans = repo.list_done_scans_for_url(url, 2)
        if len(scans) < 2:
            return  # не с чем сравнивать
        diff = scandiff.scan_diff(scans[1], scans[0])
        report_url = f"{settings.site_base_url}/report/{scans[0].id}"
        text = scandiff.format_for_telegram(url, diff, report_url)
        if not text:
            return  # изменений нет — не спамим
        for order_id, chat_id in clients:
            telegram.send_message(chat_id, text)
            log.info("monitoring diff отправлен клиенту (заказ %s, %s)", order_id[:8], url)
    except Exception:
        log.exception("notify_monitoring упал для %s", url)
