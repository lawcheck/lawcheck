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


def new_token() -> str:
    """Криптостойкий одноразовый токен для ссылок verify/reset."""
    return secrets.token_urlsafe(32)
