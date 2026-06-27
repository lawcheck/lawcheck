"""Сравнение двух сканов одного сайта — общая логика для кабинета и
клиентских Telegram-уведомлений мониторинга."""

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def scan_diff(prev, last) -> dict:
    """Что изменилось между двумя сканами. Ключ находки — (check_id, location).

    Возвращает {new: [...], fixed: [...], same: int, prev, last}.
    """
    def problems(scan):
        return {(f.check_id, f.location): f for f in scan.findings if f.severity != "ok"}

    p_prev, p_last = problems(prev), problems(last)
    new = [p_last[k] for k in p_last.keys() - p_prev.keys()]
    fixed = [p_prev[k] for k in p_prev.keys() - p_last.keys()]
    new.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.check_id))
    fixed.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.check_id))
    return {"new": new, "fixed": fixed,
            "same": len(p_last.keys() & p_prev.keys()), "prev": prev, "last": last}


def format_for_telegram(url: str, diff: dict, report_url: str | None = None) -> str | None:
    """Текст diff для Telegram-клиента. None — если изменений нет (не слать)."""
    if not diff["new"] and not diff["fixed"]:
        return None
    lines = [f"🔍 Изменения на <b>{url}</b> с прошлой проверки:"]
    if diff["new"]:
        lines.append(f"\n🔴 Новые ({len(diff['new'])}):")
        lines += [f"• {f.title}" for f in diff["new"][:8]]
    if diff["fixed"]:
        lines.append(f"\n✅ Исправлено ({len(diff['fixed'])}):")
        lines += [f"• {f.title}" for f in diff["fixed"][:8]]
    if report_url:
        lines.append(f"\nОтчёт: {report_url}")
    return "\n".join(lines)
