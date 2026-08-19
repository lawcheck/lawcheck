"""Веб-зависимости: текущий пользователь из cookie-сессии.

Сессия живёт в request.scope["session"] (ставит Starlette SessionMiddleware).
Если SessionMiddleware не подключён (session_secret пуст — аккаунты выключены),
обращения безопасно возвращают «не залогинен», сайт работает как раньше.

В самой cookie лежит минимум: id пользователя и версия сессий. Starlette
подписывает cookie, но не шифрует — всё, что туда положено, читается base64
в браузере и в любом логе, куда cookie попадает. Email и статус подтверждения
поэтому берём из БД: их читает `load_user` один раз за запрос и кладёт в
`request.state`, откуда их и берут шаблоны.
"""
import asyncio
import time

from fastapi import Request

from lawcheck.db import repo
from lawcheck.db.models import User


def _session(request: Request) -> dict | None:
    return request.scope.get("session")


def session_uid(request: Request) -> int | None:
    sess = _session(request)
    return sess.get("uid") if sess else None


# Купленные заказы, предъявленные в этой cookie-сессии. Нужны, чтобы отчёт
# открывался и без ?order= в ссылке — например, когда клиент вернулся из истории
# браузера или пришёл по ссылке из ленты на свой же оплаченный отчёт.
_ORDERS_KEY = "orders"


def remember_order(request: Request, order_id: str) -> None:
    sess = _session(request)
    if sess is None or not order_id:
        return
    known = [o for o in sess.get(_ORDERS_KEY, []) if isinstance(o, str)]
    if order_id not in known:
        # Ограничиваем длину: сессия едет в cookie, она не резиновая.
        sess[_ORDERS_KEY] = (known + [order_id])[-20:]


def session_order_ids(request: Request) -> list[str]:
    sess = _session(request) or {}
    return [o for o in sess.get(_ORDERS_KEY, []) if isinstance(o, str)]


def csp_nonce(request: Request) -> str:
    """Nonce для инлайн-скрипта. Ставит middleware, читают шаблоны."""
    return getattr(request.state, "csp_nonce", "")


# Адрес входа с рекламной меткой. Метрика включается только после «Принять»
# в cookie-баннере, поэтому посетитель, ушедший вглубь сайта раньше, даёт
# счётчику стартовать уже без yclid — и Метрика записывает рекламный визит как
# прямой заход (проверено 2026-08-10: первый хит уходил с page-url без метки).
# Запоминаем адрес входа здесь, шаблон отдаёт его скрипту счётчика.
_AD_ENTRY_KEY = "ad"
_AD_ENTRY_TTL = 30 * 60  # столько же длится визит Метрики без активности
_AD_PARAMS = ("yclid", "ymclid", "gclid", "_openstat", "utm_source")
_AD_URL_MAX = 500  # сессия едет в cookie, она не резиновая


def remember_ad_entry(request: Request) -> None:
    """Запомнить адрес входа с рекламной меткой (см. комментарий выше).

    Храним путь с запросом, без схемы и хоста: приложение живёт за Caddy и
    прокси-заголовкам не доверяет, поэтому `request.url` отдаёт внутреннюю
    схему `http`, и в Метрику уезжал бы `http://lawchek.ru/?yclid=…` — тот же
    адрес, но отдельной строкой в отчётах по страницам. Схему подставит
    браузер из `location.origin`, он её знает точно.
    """
    sess = _session(request)
    if sess is None or request.method != "GET":
        return
    if not any(p in request.query_params for p in _AD_PARAMS):
        return
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    # Перезаписываем: последний клик по рекламе важнее предыдущего.
    sess[_AD_ENTRY_KEY] = {"u": path[:_AD_URL_MAX], "t": int(time.time())}


def ad_entry(request: Request) -> str:
    """Путь входа с меткой, пока жив визит. Пусто — восстанавливать нечего."""
    entry = (_session(request) or {}).get(_AD_ENTRY_KEY)
    if not isinstance(entry, dict):
        return ""
    if time.time() - int(entry.get("t", 0)) > _AD_ENTRY_TTL:
        return ""
    return str(entry.get("u", ""))


# Источник заказа: откуда человек пришёл на сайт. Отдельно от `_AD_ENTRY_KEY` —
# у того своя задача, 30-минутное окно на восстановление хита Метрики.
# Храним ПОСЛЕДНЕЕ НЕПРЯМОЕ касание: переходы внутри сайта источник не трогают,
# а новый заход со стороны — трогает. Не «первое за всё время»: сессия живёт
# 30 дней и переживает визит, поэтому у вернувшегося по письму-напоминанию или
# по второму клику в рекламе источником осталась бы органика месячной давности,
# то есть ровно тот случай, ради которого всё и писалось, терялся бы.
# Внешний реферер важен не меньше метки: без него Инстаграм, Телеграм и
# органика неотличимы от прямого захода — меток-то в них нет.
# Прямой заход в сессию не пишем вовсе: пустой источник и означает «прямой»,
# а иначе cookie заводилась бы каждому посетителю, включая ботов.
_ENTRY_KEY = "src"
_ENTRY_MAX = 300  # сессия едет в cookie, она не резиновая
# `/account/{id}` — ссылка-пропуск в кабинет: в источнике другого заказа ей
# делать нечего. `/pay/success` — возврат с кассы банка: реферер там чужой,
# и без этого платёжный шлюз стал бы «источником» следующего заказа в той же
# сессии, а она живёт 30 дней. Весь `/pay` скрывать нельзя: `/pay/retry`
# приходит из письма-напоминания с меткой и атрибуцироваться должен.
_ENTRY_SKIP = ("/static", "/api", "/webhooks", "/internal", "/favicon", "/account",
               "/pay/success")


def remember_entry(request: Request) -> None:
    """Запомнить заход со стороны: внешний реферер и адрес входа с метками."""
    sess = _session(request)
    if sess is None or request.method != "GET":
        return
    path = request.url.path
    if path.startswith(_ENTRY_SKIP):
        return
    # Свой же реферер источником не считаем: интересен вход на сайт, а не
    # переход внутри него. Хост берём из заголовка Host (его Caddy сохраняет),
    # схему не сравниваем — за прокси она всегда внутренний http.
    ref = request.headers.get("referer", "")
    if request.url.hostname and f"//{request.url.hostname}" in ref:
        ref = ""
    if not ref and not any(p in request.query_params for p in _AD_PARAMS):
        return  # ни реферера, ни метки — заход прямой, записывать нечего
    url = path + (f"?{request.url.query}" if request.url.query else "")
    sess[_ENTRY_KEY] = {"r": ref[:_ENTRY_MAX], "u": url[:_ENTRY_MAX]}


def entry_source(request: Request) -> tuple[str, str]:
    """Источник заказа: (внешний реферер, адрес входа с метками).

    Адрес — путь с запросом, без схемы и хоста: за Caddy `request.url` отдаёт
    внутренний `http`, и в БД уезжал бы неверный абсолютный адрес.
    """
    entry = (_session(request) or {}).get(_ENTRY_KEY)
    if not isinstance(entry, dict):
        return "", ""
    return str(entry.get("r", "")), str(entry.get("u", ""))


def _loaded_user(request: Request) -> User | None:
    return getattr(request.state, "user", None)


def session_email(request: Request) -> str | None:
    """Email залогиненного — для навигации в шаблонах."""
    user = _loaded_user(request)
    return user.email if user else None


def session_unverified(request: Request) -> bool:
    """Залогинен, но email ещё не подтверждён — для баннера в шаблонах."""
    user = _loaded_user(request)
    return bool(user and user.email_verified_at is None)


def login_user(request: Request, user: User) -> None:
    sess = _session(request)
    if sess is not None:
        sess["uid"] = user.id
        sess["epoch"] = user.session_epoch or 0
    # Дальше в этом же запросе шаблоны спрашивают email и статус подтверждения —
    # отдаём уже загруженного пользователя, второй раз в БД не идём.
    request.state.user = user


def logout_user(request: Request) -> None:
    sess = _session(request)
    if sess is not None:
        sess.clear()
    request.state.user = None


async def load_user(request: Request) -> User | None:
    """Пользователь сессии, ровно один поход в БД за запрос.

    Вызывается из middleware до обработчика, поэтому шаблоны и обработчики
    берут результат из `request.state` бесплатно.
    """
    cached = getattr(request.state, "user", "missing")
    if cached != "missing":
        return cached  # type: ignore[return-value]
    user = None
    uid = session_uid(request)
    if uid:
        user = await asyncio.to_thread(repo.get_user_by_id, uid)
        # Смена пароля увеличивает epoch — старые cookie перестают пускать.
        # Сессии, выданные до появления поля, несут 0 и совпадают с дефолтом,
        # поэтому уже вошедшие пользователи не разлогиниваются при выкате.
        if user is None or (_session(request) or {}).get("epoch", 0) != (user.session_epoch or 0):
            logout_user(request)
            user = None
    request.state.user = user
    return user


async def current_user(request: Request) -> User | None:
    return await load_user(request)
