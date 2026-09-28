import base64
import hashlib
import hmac
import secrets


PASSWORD_SCHEME = 'pbkdf2_sha256'
PASSWORD_ROUNDS = 310_000
SESSION_TTL_SECONDS = 12 * 60 * 60


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PASSWORD_ROUNDS)
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip('=')
    return f'{PASSWORD_SCHEME}${PASSWORD_ROUNDS}${encode(salt)}${encode(digest)}'


def verify_password(password, encoded):
    try:
        scheme, rounds, salt_value, digest_value = encoded.split('$')
        if scheme != PASSWORD_SCHEME:
            return False
        rounds = int(rounds)
        if rounds < 100_000 or rounds > 2_000_000:
            return False
        padding = lambda value: value + '=' * (-len(value) % 4)
        salt = base64.urlsafe_b64decode(padding(salt_value))
        expected = base64.urlsafe_b64decode(padding(digest_value))
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, rounds)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def new_session_token():
    return secrets.token_urlsafe(32)


def session_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()
