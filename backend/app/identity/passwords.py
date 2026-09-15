"""Password hashing.

Argon2id, via ``argon2-cffi``, at the library's defaults -- the algorithm OWASP
recommends for new applications, and memory-hard so GPU attack economics are
much worse than for bcrypt or PBKDF2.

Two behaviours matter beyond "hash the password":

``verify_password`` never raises on a bad password
    It returns ``False``. Callers must not distinguish "wrong password" from
    "no such user" in what they return to the client.

``needs_rehash``
    Argon2 parameters are encoded in the stored hash. When the cost parameters
    are raised, existing hashes can be upgraded transparently at next login
    rather than requiring a reset.
"""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

#: Cost of a dummy verification, used to keep timing uniform when no user was
#: found. Computed once at import so the first unauthenticated request is not
#: measurably slower than the rest.
_DUMMY_HASH = _hasher.hash("apex-timing-equaliser")


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id."""
    if not password:
        raise ValueError("Password must not be empty")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a stored hash, returning False on mismatch."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether a stored hash was made with outdated cost parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def equalise_timing() -> None:
    """Spend the cost of a verification against a dummy hash.

    Called when no user matched, so that a request for a non-existent account
    takes about as long as one for a real account with a wrong password. Without
    this, response time enumerates valid email addresses.
    """
    verify_password("apex-timing-equaliser-miss", _DUMMY_HASH)


def generate_refresh_token() -> str:
    """Generate a cryptographically random refresh token."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Hash a refresh token for storage.

    SHA-256 rather than Argon2: the token is 384 bits of entropy from a CSPRNG,
    so it is not brute-forceable and needs no key stretching -- and refresh
    happens often enough that an expensive hash would be a real cost. Hashing at
    all is what stops a database disclosure yielding usable sessions.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
