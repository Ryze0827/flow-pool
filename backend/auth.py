import hashlib
import secrets

SESSION_TTL_SECONDS = 12 * 60 * 60


def new_session_token():
    return secrets.token_urlsafe(32)


def session_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()
