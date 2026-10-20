import hashlib
import secrets

# Idle timeout; successful upstream validation renews both storage and cookie.
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60


def new_session_token():
    return secrets.token_urlsafe(32)


def session_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()
