# db.py
import sqlite3

DB_PATH = "hr.db"

# starting data
SEED_EMPLOYEES = [
    # id, name, role, manager_id, email, casual, sick
    ("C001", "Mugesh",  "manager",  None,   "mugesh@company.com",  12, 6),
    ("M001", "Arjun",   "manager",  "C001", "arjun@company.com",   10, 6),
    ("E001", "Priya",   "employee", "M001", "priya@company.com",    8, 5),
    ("E002", "Karthik", "employee", "M001", "karthik@company.com",  2, 4),
    ("E003", "Divya",   "employee", "M001", "divya@company.com",    0, 1),
    ("H001", "Lakshmi", "hr",       "C001", "lakshmi@company.com", 10, 6),   # NEW: the HR person
]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us read columns by name, like row["name"]
    return conn


def init_db(reset=False):
    conn = get_conn()

    if reset:
        conn.execute("DROP TABLE IF EXISTS hr_tickets")       # NEW
        conn.execute("DROP TABLE IF EXISTS leave_requests")
        conn.execute("DROP TABLE IF EXISTS employees")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            manager_id TEXT,
            email TEXT,
            casual_balance INTEGER NOT NULL,
            sick_balance INTEGER NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS leave_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT NOT NULL REFERENCES employees(id),
            manager_id TEXT NOT NULL REFERENCES employees(id),
            leave_type TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            days INTEGER NOT NULL,
            status TEXT NOT NULL,
            reason TEXT DEFAULT ''
        )
    """)

    # NEW: one row per question the agent could not solve
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hr_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT NOT NULL REFERENCES employees(id),
            question TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            hr_reply TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # CHANGED: adds any seed person who is missing, never overwrites existing people
    conn.executemany("INSERT OR IGNORE INTO employees VALUES (?, ?, ?, ?, ?, ?, ?)",
                     SEED_EMPLOYEES)

    conn.commit()
    conn.close()


def ticket_number(ticket_id):
    # turns 1 into "HR-0001"
    return f"HR-{ticket_id:04d}"


# test block: build a fresh database and print who is in it
if __name__ == "__main__":
    init_db(reset=True)
    conn = get_conn()
    for row in conn.execute("SELECT * FROM employees"):
        print(dict(row))
    conn.close()