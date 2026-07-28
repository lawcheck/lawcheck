"""Пароли и токены аккаунтов: argon2-хеширование + одноразовые токены."""
import secrets

from passlib.context import CryptContext

_pwd = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(raw: str) -> str:
    return _pwd.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _pwd.verify(raw, hashed)
    except Exception:
        # Битый/пустой хеш — считаем неверным паролем, не роняем запрос.
        return False


# Хеш заведомо недостижимого пароля. Нужен, чтобы вход по несуществующему email
# стоил столько же времени, сколько по существующему.
_DUMMY_HASH = _pwd.hash(secrets.token_urlsafe(32))


def waste_time_like_verify(raw: str) -> None:
    """Посчитать argon2 впустую — против перечисления адресов по времени ответа.

    Без этого ответ на несуществующий email возвращается заметно быстрее: пароль
    не с чем сверять, argon2 не считается. Форма восстановления пароля от
    перечисления защищена явно, вход должен вести себя так же.
    """
    _pwd.verify(raw, _DUMMY_HASH)


def new_token() -> str:
    """Криптостойкий одноразовый токен для ссылок verify/reset."""
    return secrets.token_urlsafe(32)
