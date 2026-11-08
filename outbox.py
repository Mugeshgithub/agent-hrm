# outbox.py: the messages the app would send by email. Nothing is sent yet: each one waits here, HR reads and
# copies it, and marks it as sent. A message that carries a welcome link is scrubbed of the link once it is
# marked as sent, so the database stops holding a working link. Only HR can see or change the outbox.
import re

import db
import onboarding


def init_outbox():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id TEXT NOT NULL REFERENCES employees(id),
                to_email TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_by TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT,
                sent_by TEXT
            )""")
    conn.close()


def queue(conn, emp_id, to_email, subject, body, kind, created_by):
    # called inside the caller's transaction (the link and its email are saved together)
    return conn.execute("""INSERT INTO outbox (emp_id, to_email, subject, body, kind, created_by)
                           VALUES (?, ?, ?, ?, ?, ?)""", (emp_id, to_email, subject, body, kind, created_by)).lastrowid


def scrub_links(conn, emp_id):
    # a cancelled hire: no email of theirs keeps a working link
    for row in conn.execute("SELECT id, body FROM outbox WHERE emp_id = ?", (emp_id,)).fetchall():
        conn.execute("UPDATE outbox SET body = ? WHERE id = ?",
                     (re.sub(r"(/welcome/)[A-Za-z0-9_-]+", r"\1[link removed after sending]", row["body"]), row["id"]))


def messages(hr_id, unsent_only=False, limit=100):
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can see the outbox."}
    where = "WHERE o.sent_at IS NULL" if unsent_only else ""
    conn = db.get_conn()
    rows = conn.execute(f"""SELECT o.id, o.emp_id, e.name AS employee_name, o.to_email, r.name AS to_name, o.subject, o.body, o.kind,
                                   o.created_at, o.sent_at, s.name AS sent_by_name
                            FROM outbox o JOIN employees e ON e.id = o.emp_id
                            LEFT JOIN employees r ON lower(r.email) = lower(o.to_email)
                            LEFT JOIN employees s ON s.id = o.sent_by
                            {where} ORDER BY o.id DESC LIMIT ?""", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_sent(hr_id, message_id):
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can change the outbox."}
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM outbox WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            return {"error": "No such message."}
        if row["sent_at"]:
            return {"error": "This message is already marked as sent."}
        scrubbed = re.sub(r"(/welcome/)[A-Za-z0-9_-]+", r"\1[link removed after sending]", row["body"])
        with conn:
            conn.execute("UPDATE outbox SET sent_at = CURRENT_TIMESTAMP, sent_by = ?, body = ? WHERE id = ?",
                         (hr_id, scrubbed, message_id))
            onboarding._log(conn, row["emp_id"], hr_id, "outbox", None, f"marked as sent: {row['subject']}",
                            "Copied and sent by HR")
    finally:
        conn.close()
    return {"success": True, "id": message_id}
