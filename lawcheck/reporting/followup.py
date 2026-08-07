"""Письмо-догонялка: дожимаем узкое место воронки scan_submit → оплата.

Лид оставил email на странице отчёта, но не оплатил Pro. Через 1–3 дня шлём
одно письмо про ЕГО отчёт: что нашли на его сайте, статьи закона, сколько
готовых текстов «Как исправить» ждёт в Pro. Тон — помощь, не давление.

Рамка по ст. 18 ФЗ «О рекламе»: тема и первый экран — про отчёт (сервисное,
ожидаемое письмо), Pro — вторым CTA; в футере обязательна ссылка отписки.

Запуск батча — через CLI-инструмент `lawcheck.tools.send_followups`.
"""
from __future__ import annotations

import html
import logging
from urllib.parse import urlencode, urlparse

from lawcheck.config import settings
from lawcheck.db import repo
from lawcheck.db.models import Lead, Scan
from lawcheck.notify import mailer
from lawcheck.reporting import fines
from lawcheck.utils.contact import mask_contact

log = logging.getLogger(__name__)

_FREE_RECIPES = 2
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _host(url: str) -> str:
    netloc = urlparse(url).netloc or url
    return netloc[4:] if netloc.startswith("www.") else netloc


def _with_utm(url: str, campaign: str = "followup") -> str:
    params = urlencode({"utm_source": "email", "utm_medium": "email",
                        "utm_campaign": campaign})
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{params}"


def _unique_laws(findings) -> str:
    seen: list[str] = []
    for f in findings:
        ref = (f.law_reference or "").strip()
        if ref and ref not in seen:
            seen.append(ref)
    return ", ".join(seen[:4])


def build_context(lead: Lead, scan: Scan) -> dict:
    problems = sorted(
        (f for f in scan.findings if f.severity != "ok"),
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.check_id),
    )
    critical = [f for f in problems if f.severity == "critical"]
    with_recipe = [f for f in problems if f.recommendation]
    locked = max(0, len(with_recipe) - _FREE_RECIPES)
    base = settings.site_base_url.rstrip("/")
    contact = settings.reply_to or settings.smtp_user or "maxim@lawchek.ru"
    return {
        "site": _host(lead.url or scan.url),
        "problems": len(problems),
        "critical": len(critical),
        "top3": [f.title for f in problems[:3]],
        "laws": _unique_laws(problems),
        "locked": locked,
        "report_url": _with_utm(f"{base}/report/{scan.id}"),
        "pricing_url": _with_utm(f"{base}/pricing?scan={scan.id}"),
        "unsub_url": f"{base}/unsubscribe/{lead.unsub_token}",
        "risk": fines.risk_total(scan.findings),
        "contact_email": contact,
        "contact_subject": f"Аудит {_host(lead.url or scan.url)}",
    }


def render(ctx: dict) -> tuple[str, str, str]:
    site = ctx["site"]
    n_prob = ctx["problems"]
    n_find = _plural(n_prob, "находка", "находки", "находок")
    subject = f"Ваш отчёт по {site}: {n_prob} {n_find} и как их закрыть"

    top3 = ctx["top3"]
    laws = ctx["laws"]
    locked = ctx["locked"]
    risk = ctx["risk"]
    prob_word = _plural(n_prob, "нарушение", "нарушения", "нарушений")
    n_crit = ctx["critical"]
    crit_word = _plural(n_crit, "критичное", "критичных", "критичных")
    crit_line = (f", из них {n_crit} {crit_word}" if n_crit else "")
    locked_word = _plural(locked, "готовый текст", "готовых текста", "готовых текстов")
    risk_line = ""
    if risk and risk.get("max"):
        risk_line = (f"Суммарный риск штрафа по найденным нарушениям — "
                     f"до {int(risk['max']):,} ₽.".replace(",", " "))

    # --- text/plain ---
    text_lines = [
        "Здравствуйте!",
        "",
        f"Вы запускали проверку сайта {site} на LawCheck. Собрали для вас итог,",
        "чтобы не потерялся.",
        "",
        f"Что нашли: {n_prob} {prob_word}{crit_line}. Самое важное:",
    ]
    text_lines += [f"  — {t}" for t in top3]
    text_lines.append("")
    if laws:
        text_lines.append(
            f"Это зона {laws} — по ней Роскомнадзор штрафует бизнес. "
            "Проверка сама по себе штраф не убирает — нарушения надо закрыть.")
    if risk_line:
        text_lines.append(risk_line)
    text_lines.append("")
    if locked:
        text_lines.append(
            f"В бесплатном отчёте открыты первые рекомендации. Ещё {locked} "
            f"{locked_word} «Как исправить» (политика ПДн, тексты согласий, "
            f"cookie-баннер под {site}) откроются на Pro.")
    text_lines += [
        "",
        "Как закрыть найденное — два варианта.",
        "",
        f"Pro, 990 ₽/мес — готовые тексты исправлений под {site}, "
        f"шаблоны Политики, согласий и уведомления в РКН, "
        f"еженедельный мониторинг и PDF-заключение с подписью юриста: "
        f"{ctx['pricing_url']}",
        "",
        f"Персональный аудит, 35 000 ₽ разово — беру проект руками: "
        f"разбираю формы, метрики и сторонние скрипты, готовлю документы "
        f"под ваши процессы, уведомление в РКН и час консультации. "
        f"Напишите на {ctx['contact_email']}, и я расскажу что войдёт в аудит "
        f"{site}. Или сразу на странице тарифов: {ctx['pricing_url']}",
        "",
        f"Открыть отчёт: {ctx['report_url']}",
        "",
        "Не готовы платить — тоже ответьте: подскажу, с чего начать, бесплатно.",
        "",
        "— Максим Подольский, LawCheck · проверка сайтов на 152-ФЗ и смежные законы",
        f"Вы получили письмо, потому что оставили email для отчёта по {site}.",
        f"Отписаться: {ctx['unsub_url']}",
    ]
    text_body = "\n".join(text_lines)

    # --- text/html (брендированный шаблон) ---
    e = html.escape
    top3_html = "".join(f"<li>{e(t)}</li>" for t in top3)

    html_body = f"""\
<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F0F4F8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F0F4F8;padding:40px 0">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)">

  <!-- Логотип -->
  <tr><td style="padding:32px 40px 24px">
    <table cellpadding="0" cellspacing="0"><tr>
      <td style="width:36px;height:36px;background:#0B5CFF;border-radius:10px;text-align:center;line-height:36px">
        <span style="color:#fff;font-size:18px;font-weight:700">⚖</span>
      </td>
      <td style="padding-left:12px">
        <span style="font-size:20px;font-weight:700;color:#00053D;letter-spacing:-0.3px">LawCheck</span>
        <br><span style="font-size:12px;color:#94A3B8">проверка сайтов на 152-ФЗ</span>
      </td>
    </tr></table>
  </td></tr>

  <!-- Приветствие -->
  <tr><td style="padding:0 40px 12px;font-size:15px;line-height:1.65;color:#1E293B">
    <p style="margin:0 0 12px">Здравствуйте!</p>
    <p style="margin:0">Вы запускали проверку сайта <b style="color:#0B5CFF">{e(site)}</b> на LawCheck. Собрали для вас итог, чтобы не потерялся.</p>
  </td></tr>

  <!-- Блок: что нашли -->
  <tr><td style="padding:20px 40px;background:#F8FAFC;border-top:1px solid #E2E8F0;border-bottom:1px solid #E2E8F0">
    <p style="margin:0 0 12px;font-size:15px;font-weight:600;color:#00053D">Найдено: {n_prob} {prob_word}{e(crit_line)}</p>
    <ul style="margin:0;padding:0 0 0 20px;font-size:14px;color:#475569;line-height:1.7">{top3_html}</ul>
  </td></tr>
"""

    if laws or risk_line:
        html_body += """\
  <tr><td style="padding:20px 40px 0;font-size:14px;line-height:1.65;color:#475569">"""
        if laws:
            html_body += f"""\
    <p style="margin:0 0 8px">Это зона {e(laws)} — по ней Роскомнадзор штрафует бизнес. Проверка сама по себе штраф не убирает — нарушения надо закрыть.</p>"""
        if risk_line:
            html_body += f"""\
    <p style="margin:0;font-size:14px;font-weight:600;color:#1E293B">⚠ {e(risk_line)}</p>"""
        html_body += """\
  </td></tr>"""

    if locked:
        html_body += f"""\
  <tr><td style="padding:16px 40px 0;font-size:14px;line-height:1.65;color:#1E293B">
    <p style="margin:0">В бесплатном отчёте открыты первые рекомендации. Ещё <b>{locked}</b> {locked_word} «Как исправить» (политика ПДн, тексты согласий, cookie-баннер под {e(site)}) откроются на <b style="color:#0B5CFF">Pro</b>.</p>
  </td></tr>"""

    html_body += f"""\
  <!-- Тарифы -->
  <tr><td style="padding:28px 40px 8px">
    <p style="margin:0 0 16px;font-size:16px;font-weight:600;color:#00053D">Как закрыть найденное — два варианта</p>

    <!-- Pro -->
    <a href="{e(ctx['pricing_url'])}" style="display:block;text-decoration:none;color:inherit">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#F0F5FF;border-radius:12px;border:2px solid #0B5CFF;margin-bottom:12px">
    <tr><td style="padding:20px 24px">
      <table cellpadding="0" cellspacing="0" width="100%"><tr>
        <td style="vertical-align:top;padding-right:16px">
          <span style="display:inline-block;width:40px;height:40px;background:#0B5CFF;border-radius:10px;text-align:center;line-height:40px;font-size:20px">⚡</span>
        </td>
        <td>
          <p style="margin:0 0 4px;font-size:17px;font-weight:700;color:#0B5CFF">Pro</p>
          <p style="margin:0 0 6px;font-size:26px;font-weight:700;color:#1E293B">990 ₽/мес</p>
          <p style="margin:0 0 12px;font-size:14px;line-height:1.6;color:#475569">Готовые тексты исправлений под {e(site)}, шаблоны Политики, согласий и уведомления в РКН, еженедельный мониторинг и PDF-заключение с подписью юриста.</p>
          <span style="font-size:13px;color:#0B5CFF;font-weight:600">Подключить за 990 ₽ →</span>
        </td>
      </tr></table>
    </td></tr>
    </table>
    </a>

    <!-- Аудит -->
    <a href="{e(ctx['pricing_url'])}" style="display:block;text-decoration:none;color:inherit">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#F8FAFC;border-radius:12px;border:1px solid #E2E8F0">
    <tr><td style="padding:20px 24px">
      <table cellpadding="0" cellspacing="0" width="100%"><tr>
        <td style="vertical-align:top;padding-right:16px">
          <span style="display:inline-block;width:40px;height:40px;background:#00053D;border-radius:10px;text-align:center;line-height:40px;font-size:20px">✍</span>
        </td>
        <td>
          <p style="margin:0 0 4px;font-size:17px;font-weight:700;color:#00053D">Персональный аудит</p>
          <p style="margin:0 0 6px;font-size:26px;font-weight:700;color:#1E293B">35 000 ₽ разово</p>
          <p style="margin:0;font-size:14px;line-height:1.6;color:#475569">Беру проект руками: разбираю формы, метрики и сторонние скрипты, готовлю документы под ваши процессы, уведомление в РКН и час консультации.</p>
        </td>
      </tr></table>
    </td></tr>
    </table>
    </a>
  </td></tr>

  <!-- Кнопки -->
  <tr><td align="center" style="padding:24px 40px 8px">
    <a href="{e(ctx['report_url'])}" style="display:inline-block;background:#0B5CFF;color:#fff;text-decoration:none;padding:14px 32px;border-radius:12px;font-size:15px;font-weight:600;margin:0 8px 12px">Открыть отчёт →</a>
    <a href="mailto:{e(ctx['contact_email'])}?subject={e(ctx['contact_subject'])}" style="display:inline-block;background:#fff;color:#0B5CFF;text-decoration:none;padding:14px 32px;border-radius:12px;font-size:15px;font-weight:600;border:2px solid #0B5CFF;margin:0 8px 12px">Написать Максиму</a>
  </td></tr>

  <!-- Мягкий CTA -->
  <tr><td style="padding:0 40px 32px;font-size:14px;color:#64748B;text-align:center">
    Не готовы платить — тоже ответьте: подскажу, с чего начать, бесплатно.
  </td></tr>

  <!-- Разделитель -->
  <tr><td style="border-top:1px solid #E2E8F0;padding:0 40px"></td></tr>

  <!-- Футер -->
  <tr><td style="padding:20px 40px 32px;font-size:12px;color:#94A3B8;line-height:1.6">
    — Максим Подольский, LawCheck · проверка сайтов на 152-ФЗ и смежные законы<br>
    Вы получили письмо, потому что оставили email для отчёта по {e(site)}.
    <a href="{e(ctx['unsub_url'])}" style="color:#94A3B8">Отписаться</a>.
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""

    return subject, html_body, text_body


def send_one(lead: Lead, scan: Scan) -> bool:
    subject, html_body, text_body = render(build_context(lead, scan))
    ok = mailer.send_email(lead.email, subject, html_body, text_body)
    if ok:
        repo.mark_lead_mailed(lead.id)
    else:
        log.warning("followup: письмо лиду %s не ушло — mailed_at не ставим",
                    mask_contact(lead.email))
    return ok


def run(limit: int = 50, delay_hours: int = 20, max_age_days: int = 14,
        dry_run: bool = False) -> dict:
    """Батч: разослать письма-догонялки подходящим лидам."""
    leads = repo.leads_to_followup(delay_hours, max_age_days, limit)
    sent = skipped = 0
    for lead in leads:
        scan = repo.get_scan(lead.scan_id)
        if scan is None:
            skipped += 1
            continue
        if dry_run:
            subject, _, _ = render(build_context(lead, scan))
            log.info("followup[dry] → %s | %s", mask_contact(lead.email), subject)
            continue
        if send_one(lead, scan):
            sent += 1
        else:
            skipped += 1
    return {"candidates": len(leads), "sent": sent, "skipped": skipped,
            "dry_run": dry_run}