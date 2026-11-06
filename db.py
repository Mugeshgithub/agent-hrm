# db.py
import sqlite3

DB_PATH = "hr.db"

# starting data
SEED_EMPLOYEES = [
    # id, name, role, manager_id, email, casual, sick, paid (congés payés, earned 2.5 days a month)
    ("C001", "Mugesh",  "manager",  None,   "mugesh@cognivexai.eu",  12, 6, 25),
    ("M001", "Lucas",   "manager",  "C001", "lucas@cognivexai.eu",   10, 6, 20),
    ("E001", "Emma",   "employee", "M001", "emma@cognivexai.eu",    8, 5, 12.5),
    ("E002", "Hugo", "employee", "M001", "hugo@cognivexai.eu",  2, 4, 7.5),
    ("E003", "Clara",   "employee", "M001", "clara@cognivexai.eu",    0, 1, 2.5),
    ("H001", "Narmadha", "hr",       "C001", "narmadha@cognivexai.eu", 10, 6, 15),   # NEW: the HR person
]


def add_column_if_missing(conn, table, column, definition):
    # for databases made before the column existed. The SQLite-only lookup is kept in this one
    # function, so moving to Postgres later means changing it here and nowhere else.
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
            sick_balance INTEGER NOT NULL,
            paid_balance REAL NOT NULL DEFAULT 0    -- congés payés: 2.5 days a month, so halves
        )
    """)
    add_column_if_missing(conn, "employees", "paid_balance", "REAL NOT NULL DEFAULT 0")

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
    conn.executemany("""INSERT OR IGNORE INTO employees
                        (id, name, role, manager_id, email, casual_balance, sick_balance, paid_balance)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", SEED_EMPLOYEES)

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