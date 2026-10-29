# notifications.py: messages for employees, e.g. "your leave was approved"
from db import get_conn


def init_notifications():
    conn = get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT NOT NULL,
                message TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.close()


def notify_decision(request_id, decision, reason, manager_name):
    # find who the request belongs to, and its dates
    conn = get_conn()
    row = conn.execute(
        "SELECT employee_id, leave_type, start_date, end_date FROM leave_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    if row is None:
        conn.close()
        return
    word = "approved" if decision == "approve" else "rejected"
    message = (f"Your {row['leave_type']} leave from {row['start_date']} to {row['end_date']} "
               f"was {word} by {manager_name}.")
    if reason:
        message += f" Reason: {reason}"
    with conn:
        conn.execute(
            "INSERT INTO notifications (employee_id, message) VALUES (?, ?)",
            (row["employee_id"], message),
        )
    conn.close()


def show_unread(employee_id):
    # print unread notifications, then mark them as read
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, message, created_at FROM notifications "
        "WHERE employee_id = ? AND is_read = 0 ORDER BY id",
        (employee_id,),
    ).fetchall()
    if rows:
        print(f"\n  You have {len(rows)} new notification(s):")
        for r in rows:
            print(f"   - {r['message']}  ({r['created_at']})")
        with conn:
            conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE employee_id = ? AND is_read = 0",
                (employee_id,),
            )
    conn.close()

def notify_ticket_reply(ticket_id, hr_name):
    # tell the employee that HR answered their ticket
    from db import ticket_number
    conn = get_conn()
    row = conn.execute(
        "SELECT employee_id, hr_reply FROM hr_tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if row:
        with conn:
            conn.execute(
                "INSERT INTO notifications (employee_id, message) VALUES (?, ?)",
                (row["employee_id"],
                 f"{hr_name} from HR replied to ticket {ticket_number(ticket_id)}: {row['hr_reply']}"),
            )
    conn.close()