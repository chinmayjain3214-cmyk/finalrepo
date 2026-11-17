"""Users, password hashing and sign-in for the invoice agent.

Sign-in protections
- Passwords are stored as salted PBKDF2-SHA256 hashes and compared in
  constant time.
- Repeated wrong passwords pause sign-in for that username for a short time.
- Unknown usernames take as long to reject as known ones, so response time
  does not reveal which accounts exist.
- Over-long inputs are rejected before any hashing is done.

The attempt counter lives in this process's memory. That fits the single
Streamlit process used here and resets when the app restarts; a multi-server
deployment would keep it in the database or a shared cache.
"""

import hashlib
import hmac
import secrets
import threading
import time

from db import get_connection
from db_store import save_log


ITERATIONS = 200_000

# Sign-in throttling.
MAX_FAILED_ATTEMPTS = 5            # wrong passwords allowed inside the window
FAILURE_WINDOW_SECONDS = 10 * 60   # how long a wrong password is remembered
LOCKOUT_SECONDS = 2 * 60           # pause once the limit is reached

# Input limits (also stop very long passwords from tying up the hasher).
MAX_USERNAME_LENGTH = 64
MAX_PASSWORD_LENGTH = 256

_attempt_lock = threading.Lock()
_failed_attempts = {}   # username -> [monotonic time of each recent failure]
_locked_until = {}      # username -> monotonic time the pause ends
_dummy_hash = None      # built on first use; see _timing_dummy_hash()

ALLOWED_ROLES = {
    "vendor",
    "procurement_officer",
    "department_head",
    "finance_officer",
    "final_approver",
    "approver",  # Backward-compatible alias for the final approval stage.
    "admin",
}


def hash_password(password):
    """Generate a salted PBKDF2 password hash."""

    if not isinstance(password, str) or not password:
        raise ValueError("Password cannot be empty.")

    salt = secrets.token_hex(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        ITERATIONS,
    )

    return (
        "pbkdf2_sha256$"
        + str(ITERATIONS)
        + "$"
        + salt
        + "$"
        + digest.hex()
    )


def verify_password(password, stored):
    """Verify a password against its stored hash."""

    try:
        if not isinstance(password, str):
            return False

        if not isinstance(stored, str):
            return False

        algorithm, iterations, salt, expected = stored.split("$")

        if algorithm != "pbkdf2_sha256":
            return False

        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            int(iterations),
        )

        return hmac.compare_digest(
            digest.hex(),
            expected,
        )

    except (ValueError, TypeError, AttributeError):
        return False


def create_user(username, full_name, role, password):
    """Create a user or update an existing demo account."""

    username = str(username).strip().lower()
    full_name = str(full_name).strip()
    role = str(role).strip().lower()

    if not username:
        raise ValueError("Username cannot be empty.")

    if not full_name:
        raise ValueError("Full name cannot be empty.")

    if role not in ALLOWED_ROLES:
        raise ValueError("Invalid user role: " + role)

    password_hash = hash_password(password)

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users
                    (username, full_name, role, password_hash)
                VALUES (%s, %s, %s, %s)

                ON CONFLICT (username)
DO UPDATE SET
    full_name = EXCLUDED.full_name,
    role = EXCLUDED.role,
    password_hash = EXCLUDED.password_hash;
                """,
                (
                    username,
                    full_name,
                    role,
                    password_hash,
                ),
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def normalize_username(username):
    """Usernames are stored lower-case with no surrounding spaces."""

    return str(username or "").strip().lower()


def _timing_dummy_hash():
    """A real hash that no password matches, used for unknown usernames."""

    global _dummy_hash

    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_hex(16))

    return _dummy_hash


def authenticate(username, password):
    """Check one username/password pair. Return the account, or None.

    This is the credential check only. The login screen calls login(),
    which adds attempt limits around it.
    """

    username = normalize_username(username)

    if (
        not username
        or len(username) > MAX_USERNAME_LENGTH
        or not isinstance(password, str)
        or not password
        or len(password) > MAX_PASSWORD_LENGTH
    ):
        save_log(
            "auth:login_failed",
            {"user": username[:50], "reason": "invalid_input"},
        )
        return None

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT username, full_name, role, password_hash
                FROM users
                WHERE username = %s;
                """,
                (username,),
            )

            row = cur.fetchone()

    finally:
        conn.close()

    # Always run one password check, even for an unknown username, so the
    # response time does not reveal whether the account exists.
    stored_hash = row[3] if row else _timing_dummy_hash()
    password_ok = verify_password(password, stored_hash)

    if (
        row
        and row[2] in ALLOWED_ROLES
        and password_ok
    ):
        save_log(
            "auth:login",
            {
                "user": row[0],
                "role": row[2],
            },
        )

        return {
            "username": row[0],
            "full_name": row[1],
            "role": row[2],
        }

    save_log(
        "auth:login_failed",
        {"user": username[:50]},
    )

    return None


# ==================================================
# SIGN-IN WITH ATTEMPT LIMITS
# ==================================================

def _prune(username, now):
    """Forget failures older than the window and pauses that have ended."""

    recent = [
        moment
        for moment in _failed_attempts.get(username, [])
        if now - moment < FAILURE_WINDOW_SECONDS
    ]

    if recent:
        _failed_attempts[username] = recent
    else:
        _failed_attempts.pop(username, None)

    if _locked_until.get(username, 0) <= now:
        _locked_until.pop(username, None)


def _seconds_locked(username, now):
    return max(0, int(round(_locked_until.get(username, 0) - now)))


def reset_login_attempts(username=None):
    """Clear the attempt counter for one username, or for everyone."""

    with _attempt_lock:
        if username is None:
            _failed_attempts.clear()
            _locked_until.clear()
        else:
            username = normalize_username(username)
            _failed_attempts.pop(username, None)
            _locked_until.pop(username, None)


def login(username, password):
    """Sign in with attempt limits. Returns a dict describing the outcome.

    {"status": "ok", "user": {...}}
    {"status": "missing"}                         a field was left empty
    {"status": "invalid", "attempts_left": n}     wrong username or password
    {"status": "locked", "retry_after": seconds}  too many wrong attempts

    The same answers are given for unknown usernames as for known ones, so
    the screen never reveals which accounts exist.
    """

    username = normalize_username(username)

    if not username or not isinstance(password, str) or not password:
        return {"status": "missing"}

    # Key the counter on a bounded string so memory use stays small.
    key = username[:MAX_USERNAME_LENGTH]
    now = time.monotonic()

    with _attempt_lock:
        _prune(key, now)
        remaining = _seconds_locked(key, now)

    if remaining > 0:
        return {"status": "locked", "retry_after": remaining}

    user = authenticate(username, password)

    now = time.monotonic()

    if user:
        with _attempt_lock:
            _failed_attempts.pop(key, None)
            _locked_until.pop(key, None)
        return {"status": "ok", "user": user}

    with _attempt_lock:
        _prune(key, now)
        failures = _failed_attempts.setdefault(key, [])
        failures.append(now)
        attempts_left = MAX_FAILED_ATTEMPTS - len(failures)

        if attempts_left <= 0:
            _locked_until[key] = now + LOCKOUT_SECONDS
            _failed_attempts.pop(key, None)
            just_locked = True
        else:
            just_locked = False

        # Keep the table small if many different usernames are tried.
        if len(_failed_attempts) > 5000:
            for name in list(_failed_attempts):
                _prune(name, now)

    if just_locked:
        save_log(
            "auth:lockout",
            {
                "user": key[:50],
                "failed_attempts": MAX_FAILED_ATTEMPTS,
                "locked_seconds": LOCKOUT_SECONDS,
            },
        )
        return {"status": "locked", "retry_after": LOCKOUT_SECONDS}

    return {"status": "invalid", "attempts_left": attempts_left}
