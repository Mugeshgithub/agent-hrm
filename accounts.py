# accounts.py: signing in with an email and a password.
#
# HR sends each person a link to set their own password. The app has no email yet, so the email waits in the outbox for HR to send, as a
# welcome link does; the link is taken out of the outbox once HR marks it sent. The same link sets a forgotten password again.
# The first HR person has nobody to send them a link: `python accounts.py link H001` prints one, for whoever runs the server.
#
# What is kept, and what is not:
#   - the password's scrypt hash, with its salt and settings: never the password
#   - the link's SHA-256 hash: never the link. A link works once, for LINK_DAYS days; a newer one replaces it
#   - each sign-in, failed try, lock and password change, by person and time (login_events): no address, no browser
# Five wrong passwords lock the account for LOCK_MINUTES. Setting a password signs the person out everywhere: the session
# carries the account's session_version, and a session with an older one is refused.
#
# The demo picker (sign in as anyone, no password) works only when HRM_DEMO_LOGIN=1, for local demos and tests: never on a real deployment.
import hashlib
import hmac
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

import db
import hr_queries
import onboarding
import outbox

LINK_DAYS = 7
MAX_WRONG = 5
LOCK_MINUTES = 15
MIN_LENGTH = 12
MAX_LENGTH = 128
FORMAT = "%Y-%m-%d %H:%M:%S"          # UTC, same shape as SQLite's CURRENT_TIMESTAMP, so it sorts as text
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}  # about 50 ms and 32 MB a check: slow for someone guessing, quick for a person
WRONG = "That email and password don't match."   # the same words whether the email is unknown, has no password yet, or the password is wrong


def demo_login():
    return os.getenv("HRM_DEMO_LOGIN") == "1"


def init_accounts():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logins (
                emp_id TEXT PRIMARY KEY REFERENCES employees(id),
                password_hash TEXT,                  -- scrypt$n$r$p$salt$hash; NULL until the person sets one
                password_set_at TEXT,
                wrong_tries INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT,
                session_version INTEGER NOT NULL DEFAULT 1,
                last_login_at TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id TEXT NOT NULL REFERENCES employees(id),
                token_hash TEXT NOT NULL UNIQUE,
                created_by TEXT NOT NULL,           -- an HR person's id, or 'server' for the first link from the command line
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                revoked_at TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS login_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id TEXT NOT NULL,
                event TEXT NOT NULL,                 -- link_issued, password_set, signed_in, wrong_password, locked
                at TEXT NOT NULL
            )""")
    conn.close()


def _now(now):
    return now or datetime.now(timezone.utc).replace(tzinfo=None)


def _event(conn, emp_id, event, now):
    conn.execute("INSERT INTO login_events (emp_id, event, at) VALUES (?, ?, ?)", (emp_id, event, now.strftime(FORMAT)))


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, maxmem=64 * 1024 * 1024, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${digest.hex()}"


def password_matches(password, stored):
    try:
        _, n, r, p, salt, digest = stored.split("$")
        mine = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                              maxmem=64 * 1024 * 1024, dklen=32)
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(mine.hex(), digest)


_DUMMY = hash_password("not a password anyone has")    # checked when there is no account, so a wrong email takes as long as a wrong password


def password_problem(password, email=""):
    """Why this password can't be used, or None."""
    password = password or ""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters. A few words you will remember work well."
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    if email and email.split("@")[0].lower() in password.lower():
        return "Don't use your email address in your password."
    return None


def compose_email(first_name, link, expires):
    return (f"Hi {first_name},\n\n"
            f"Here is your link to set your password for the CognivexAI HR app:\n\n{link}\n\n"
            f"It works once, until {expires:%d %B %Y}. After that, sign in with your work email and the password you chose. "
            f"If you didn't expect this email, tell HR.\n\n"
            "The CognivexAI HR team\n")


def _issue(conn, emp_id, created_by, base_url, now):
    token = secrets.token_urlsafe(32)
    expires = now + timedelta(days=LINK_DAYS)
    stamp = now.strftime(FORMAT)
    conn.execute("UPDATE password_links SET revoked_at = ? WHERE emp_id = ? AND used_at IS NULL AND revoked_at IS NULL", (stamp, emp_id))
    conn.execute("INSERT INTO password_links (emp_id, token_hash, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                 (emp_id, _hash_token(token), created_by, stamp, expires.strftime(FORMAT)))
    _event(conn, emp_id, "link_issued", now)
    return f"{base_url.rstrip('/')}/set-password/{token}", expires


def issue_link(hr_id, emp_id, base_url, now=None):
    """HR sends a person their link (a first password, or a forgotten one). The email goes to the outbox."""
    now = _now(now)
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can send a sign-in link."}
    person = hr_queries.person(emp_id)
    if person is None or not person["active"]:
        return {"error": "No current person with that ID."}
    if not person["email"]:
        return {"error": f"{person['name']} has no email address."}
    conn = db.get_conn()
    try:
        with conn:      # the link, the end of the older one, the email and the change history: all or nothing
            link, expires = _issue(conn, emp_id, hr_id, base_url, now)
            message_id = outbox.queue(conn, emp_id, person["email"], "Set your password for the CognivexAI HR app",
                                      compose_email(person["name"].split()[0], link, expires), "password_link", hr_id)
            onboarding._log(conn, emp_id, hr_id, "sign-in link", None, f"sent, valid until {expires:%Y-%m-%d}", "New sign-in link")
    finally:
        conn.close()
    return {"success": True, "to": person["email"], "expires_at": expires.strftime(FORMAT), "outbox_id": message_id}


def first_link(emp_id, base_url, now=None):
    """For the command line only: the first HR person's link, printed for whoever runs the server. Nothing goes to the outbox."""
    now = _now(now)
    person = hr_queries.person(emp_id)
    if person is None or not person["active"]:
        return {"error": "No current person with that ID."}
    conn = db.get_conn()
    try:
        with conn:
            link, expires = _issue(conn, emp_id, "server", base_url, now)
    finally:
        conn.close()
    return {"link": link, "expires_at": expires.strftime(FORMAT)}


def _link(token):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM password_links WHERE token_hash = ?", (_hash_token(token or ""),)).fetchone()
    conn.close()
    return dict(row) if row else None


def _link_state(link, now):
    if link is None:
        return "invalid"
    if link["used_at"]:
        return "used"
    if link["revoked_at"]:
        return "replaced"
    if link["expires_at"] <= now.strftime(FORMAT):
        return "expired"
    return "ready"


LINK_MESSAGES = {
    "invalid": "This link isn't valid. Check you opened the newest link, or ask HR.",
    "used": "This link was already used. Sign in with your password, or ask HR for a new link.",
    "replaced": "This link was replaced by a newer one. Use the latest link HR sent you.",
    "expired": "This link has expired. Ask HR for a new one.",
}


def link_state(token, now=None):
    # for the page, before a password is typed: nothing about the person is revealed
    return {"state": _link_state(_link(token), _now(now))}


def set_password(token, password, now=None):
    """Returns {"emp_id", "session_version"} for a ready link and a usable password, else {"error"}. The person is the one the link was made for."""
    now = _now(now)
    link = _link(token)
    state = _link_state(link, now)
    if state != "ready":
        return {"error": LINK_MESSAGES[state]}
    person = hr_queries.person(link["emp_id"])
    if person is None or not person["active"]:
        return {"error": LINK_MESSAGES["invalid"]}
    if (problem := password_problem(password, person["email"] or "")):
        return {"error": problem}
    stamp = now.strftime(FORMAT)
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("UPDATE password_links SET used_at = ? WHERE id = ?", (stamp, link["id"]))
            conn.execute("INSERT OR IGNORE INTO logins (emp_id) VALUES (?)", (link["emp_id"],))
            # a new password: unlocked, and every older session ends
            conn.execute("""UPDATE logins SET password_hash = ?, password_set_at = ?, wrong_tries = 0, locked_until = NULL,
                            session_version = session_version + 1, last_login_at = ? WHERE emp_id = ?""",
                         (hash_password(password), stamp, stamp, link["emp_id"]))
            _event(conn, link["emp_id"], "password_set", now)
            version = conn.execute("SELECT session_version FROM logins WHERE emp_id = ?", (link["emp_id"],)).fetchone()[0]
    finally:
        conn.close()
    return {"emp_id": link["emp_id"], "session_version": version}


def sign_in(email, password, now=None):
    """Returns {"emp_id", "session_version"} for the right email and password, else {"error", "status"}."""
    now = _now(now)
    email = (email or "").strip().lower()
    conn = db.get_conn()
    try:
        rows = conn.execute("""SELECT e.id, l.password_hash, l.wrong_tries, l.locked_until, l.session_version FROM employees e
                              LEFT JOIN logins l ON l.emp_id = e.id WHERE lower(e.email) = ?""", (email,)).fetchall() if email else []
        if len(rows) != 1 or not rows[0]["password_hash"]:
            password_matches(password or "", _DUMMY)       # the same time as a real check
            return {"error": WRONG, "status": 401}
        account = rows[0]
        if account["locked_until"] and account["locked_until"] > now.strftime(FORMAT):
            return {"error": f"Too many wrong passwords. Try again after {account['locked_until'][11:16]} UTC, or ask HR for a new link.",
                    "status": 429}
        with conn:
            if not password_matches(password or "", account["password_hash"]):
                wrong = account["wrong_tries"] + 1
                locked = (now + timedelta(minutes=LOCK_MINUTES)).strftime(FORMAT) if wrong >= MAX_WRONG else None
                conn.execute("UPDATE logins SET wrong_tries = ?, locked_until = ? WHERE emp_id = ?",
                             (0 if locked else wrong, locked, account["id"]))
                _event(conn, account["id"], "locked" if locked else "wrong_password", now)
                return {"error": WRONG, "status": 401}
            if not hr_queries.person(account["id"])["active"]:
                return {"error": "Your access to the HR app has ended.", "status": 403}
            conn.execute("UPDATE logins SET wrong_tries = 0, locked_until = NULL, last_login_at = ? WHERE emp_id = ?",
                         (now.strftime(FORMAT), account["id"]))
            _event(conn, account["id"], "signed_in", now)
    finally:
        conn.close()
    return {"emp_id": account["id"], "session_version": account["session_version"]}


def session_version(emp_id):
    """The account's current session version, or None when it has no password: a session made before the last password change is refused."""
    conn = db.get_conn()
    row = conn.execute("SELECT session_version FROM logins WHERE emp_id = ? AND password_hash IS NOT NULL", (emp_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def status(hr_id, emp_id, now=None):
    """HR's view of a person's sign-in: never the link or the password."""
    now = _now(now)
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can see this."}
    conn = db.get_conn()
    account = conn.execute("SELECT * FROM logins WHERE emp_id = ?", (emp_id,)).fetchone()
    link = conn.execute("SELECT * FROM password_links WHERE emp_id = ? ORDER BY id DESC LIMIT 1", (emp_id,)).fetchone()
    events = [dict(r) for r in conn.execute("SELECT event, at FROM login_events WHERE emp_id = ? ORDER BY id DESC LIMIT 8", (emp_id,)).fetchall()]
    conn.close()
    has_password = bool(account and account["password_hash"])
    locked = bool(account and account["locked_until"] and account["locked_until"] > now.strftime(FORMAT))
    return {"has_password": has_password, "password_set_at": account["password_set_at"] if has_password else None,
            "last_login_at": account["last_login_at"] if account else None, "locked_until": account["locked_until"] if locked else None,
            "link": None if link is None else {"state": _link_state(dict(link), now), "created_at": link["created_at"],
                                               "expires_at": link["expires_at"]},
            "events": events}


if __name__ == "__main__":
    # python accounts.py link H001: the first HR person's link, printed here (HRM_DB picks the database, HRM_APP_URL the address)
    if len(sys.argv) != 3 or sys.argv[1] != "link":
        sys.exit("Usage: python accounts.py link <employee id>")
    db.DB_PATH = os.getenv("HRM_DB", db.DB_PATH)
    init_accounts()
    result = first_link(sys.argv[2].upper(), os.getenv("HRM_APP_URL", "http://localhost:3000"))
    if "error" in result:
        sys.exit(result["error"])
    print(f"Open this link to set the password (once, until {result['expires_at']} UTC):\n{result['link']}")
