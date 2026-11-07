# welcome.py: the personal welcome link of a new hire (onboarding PR 2). HR issues a link; the app has no
# email yet, so the email is queued in the outbox for HR to copy. The link is a random token (stored only as a
# hash) that works for 14 days, and it needs a 6-digit code that HR gives the person another way, so a
# forwarded email alone is not enough. After 5 wrong codes the link is locked; a new link replaces the old one.
# Opening the link signs the person in as a "new_hire": a session that can only read their own checklist.
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import db
import hr_queries
import onboarding
import outbox
from tools import get_employee

LINK_DAYS = 14
MAX_WRONG_CODES = 5
FORMAT = "%Y-%m-%d %H:%M:%S"          # UTC, same shape as SQLite's CURRENT_TIMESTAMP, so it sorts as text


def init_welcome():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS welcome_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id TEXT NOT NULL REFERENCES employees(id),
                token_hash TEXT NOT NULL UNIQUE,
                code_salt TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                wrong_codes INTEGER NOT NULL DEFAULT 0,
                last_used_at TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS welcome_link_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id INTEGER NOT NULL REFERENCES welcome_links(id),
                emp_id TEXT NOT NULL,
                event TEXT NOT NULL,
                at TEXT NOT NULL
            )""")
    conn.close()


def _now(now):
    return now or datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_code(salt, code):
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def _event(conn, link_id, emp_id, event, now):
    conn.execute("INSERT INTO welcome_link_events (link_id, emp_id, event, at) VALUES (?, ?, ?, ?)",
                 (link_id, emp_id, event, now.strftime(FORMAT)))


def _revoke_active(conn, emp_id, now):
    for row in conn.execute("SELECT id FROM welcome_links WHERE emp_id = ? AND revoked_at IS NULL", (emp_id,)).fetchall():
        conn.execute("UPDATE welcome_links SET revoked_at = ? WHERE id = ?", (now.strftime(FORMAT), row["id"]))
        _event(conn, row["id"], emp_id, "revoked", now)


def compose_email(first_name, link, expires, manager_name):
    return (f"Hi {first_name},\n\n"
            f"Welcome to CognivexAI! Before your first day we have a few things to prepare together. "
            f"Your personal page shows what is needed and who does what, and you can ask the assistant there.\n\n"
            f"Open it here: {link}\n\n"
            f"It asks for a 6-digit code. HR will give you that code separately (by phone or message), so it "
            f"is not in this email. The link works until {expires:%d %B %Y}.\n\n"
            + (f"Your manager will be {manager_name}.\n\n" if manager_name else "") +
            "See you soon,\nThe CognivexAI HR team\n")


def issue_link(hr_id, emp_id, base_url, now=None):
    now = _now(now)
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can create a welcome link."}
    hire = hr_queries.person(emp_id)
    if hire is None or not hire["active"]:
        return {"error": "No current person with that ID."}
    if onboarding.checklist(hr_id, emp_id) is None:
        return {"error": f"{hire['name']} has no onboarding checklist, so there is nothing to show them."}
    if not hire["email"]:
        return {"error": f"{hire['name']} has no email address."}
    token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(10 ** 6):06d}"
    salt = secrets.token_hex(8)
    expires = now + timedelta(days=LINK_DAYS)
    link = f"{base_url.rstrip('/')}/welcome/{token}"
    first_name = hire["name"].split()[0]
    conn = db.get_conn()
    try:
        with conn:     # the new link, the end of the old one, the email and the log line: all or nothing
            _revoke_active(conn, emp_id, now)
            link_id = conn.execute("""INSERT INTO welcome_links (emp_id, token_hash, code_salt, code_hash, created_by,
                                                                 created_at, expires_at)
                                      VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                   (emp_id, _hash_token(token), salt, _hash_code(salt, code), hr_id,
                                    now.strftime(FORMAT), expires.strftime(FORMAT))).lastrowid
            _event(conn, link_id, emp_id, "issued", now)
            message_id = outbox.queue(conn, emp_id, hire["email"], "Welcome to CognivexAI: your personal page",
                                      compose_email(first_name, link, expires, hire["manager_name"]),
                                      "welcome_link", hr_id)
            onboarding._log(conn, emp_id, hr_id, "welcome link", None, f"issued, valid until {expires:%Y-%m-%d}",
                            "New welcome link")
    finally:
        conn.close()
    return {"success": True, "code": code, "expires_at": expires.strftime(FORMAT), "outbox_id": message_id,
            "to": hire["email"]}


def _link(token):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM welcome_links WHERE token_hash = ?", (_hash_token(token or ""),)).fetchone()
    conn.close()
    return dict(row) if row else None


def _state(link, now):
    if link is None:
        return "invalid"
    if link["revoked_at"]:
        return "revoked"
    if link["expires_at"] <= now.strftime(FORMAT):
        return "expired"
    if link["wrong_codes"] >= MAX_WRONG_CODES:
        return "locked"
    return "ready"


MESSAGES = {
    "invalid": "This link isn't valid. Check you opened the newest link, or ask HR.",
    "revoked": "This link was replaced by a newer one. Use the latest link HR sent you, or ask HR.",
    "expired": "This link has expired. Ask HR for a new one.",
    "locked": "Too many wrong codes, so this link is locked. Ask HR for a new link.",
}


def link_state(token, now=None):
    # for the page, before any code is typed: nothing about the person is revealed
    return {"state": _state(_link(token), _now(now))}


def open_link(token, code, now=None):
    # Returns {"emp_id": ...} for the right token AND code, else {"error", "state"}. The person is the one the
    # link was made for: the caller never says who they are.
    now = _now(now)
    link = _link(token)
    state = _state(link, now)
    if state != "ready":
        return {"error": MESSAGES[state], "state": state}
    hire = hr_queries.person(link["emp_id"])
    if hire is None or not hire["active"] or get_employee(link["emp_id"]) is None:
        return {"error": MESSAGES["invalid"], "state": "invalid"}
    code = "".join((code or "").split())
    if not (code.isdigit() and len(code) == 6):
        return {"error": "The code has 6 digits.", "state": "ready"}        # a typo is not a guess: not counted
    conn = db.get_conn()
    try:
        with conn:
            if hmac.compare_digest(_hash_code(link["code_salt"], code), link["code_hash"]):
                conn.execute("UPDATE welcome_links SET wrong_codes = 0, last_used_at = ? WHERE id = ?",
                             (now.strftime(FORMAT), link["id"]))
                _event(conn, link["id"], link["emp_id"], "opened", now)
                return {"emp_id": link["emp_id"]}
            wrong = link["wrong_codes"] + 1
            conn.execute("UPDATE welcome_links SET wrong_codes = ? WHERE id = ?", (wrong, link["id"]))
            _event(conn, link["id"], link["emp_id"], "wrong_code", now)
            if wrong >= MAX_WRONG_CODES:
                _event(conn, link["id"], link["emp_id"], "locked", now)
                return {"error": MESSAGES["locked"], "state": "locked"}
    finally:
        conn.close()
    left = MAX_WRONG_CODES - wrong
    return {"error": f"That code isn't right. {left} {'try' if left == 1 else 'tries'} left.", "state": "ready"}


def link_status(hr_id, emp_id, now=None):
    # HR's view: where the link stands. Never the token or the code.
    now = _now(now)
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can see this."}
    conn = db.get_conn()
    link = conn.execute("""SELECT l.*, c.name AS created_by_name FROM welcome_links l
                           LEFT JOIN employees c ON c.id = l.created_by
                           WHERE l.emp_id = ? ORDER BY l.id DESC LIMIT 1""", (emp_id,)).fetchone()
    if link is None:
        conn.close()
        return None
    events = [dict(r) for r in conn.execute("""SELECT event, at FROM welcome_link_events WHERE emp_id = ?
                                               ORDER BY id DESC LIMIT 8""", (emp_id,)).fetchall()]
    conn.close()
    link = dict(link)
    return {"state": _state(link, now), "created_at": link["created_at"], "expires_at": link["expires_at"],
            "created_by": link["created_by_name"], "wrong_codes": link["wrong_codes"],
            "last_used_at": link["last_used_at"], "max_wrong_codes": MAX_WRONG_CODES, "events": events}


def revoke_link(hr_id, emp_id, reason, now=None):
    now = _now(now)
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can cancel a welcome link."}
    if not (reason or "").strip():
        return {"error": "Give a reason. It goes in the change history."}
    conn = db.get_conn()
    try:
        active = conn.execute("SELECT id FROM welcome_links WHERE emp_id = ? AND revoked_at IS NULL", (emp_id,)).fetchone()
        if active is None:
            return {"error": "There is no active link for this person."}
        with conn:
            _revoke_active(conn, emp_id, now)
            onboarding._log(conn, emp_id, hr_id, "welcome link", "active", "cancelled", reason.strip())
    finally:
        conn.close()
    return {"success": True}
