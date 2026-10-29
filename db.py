# db.py
import sqlite3

DB_PATH = "hr.db"

# starting data: same people as data.py
SEED_EMPLOYEES = [
    # id,    name,      role,       manager_id, email,                  casual, sick
    ("C001", "Mugesh",  "manager",  None,       "mugesh@company.com",   12,     6),
    ("M001", "Arjun",   "manager",  "C001",     "arjun@company.com",    10,     6),
    ("E001", "Priya",   "employee", "M001",     "priya@company.com",    8,      5),
    ("E002", "Karthik", "employee", "M001",     "karthik@company.com",  2,      4),
    ("E003", "Divya",   "employee", "M001",     "divya@company.com",    0,      1),
]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # lets us read columns by name, like row["name"]
    return conn


def init_db(reset=False):
    conn = get_conn()
    if reset:
        conn.execute("DROP TABLE IF EXISTS leave_requests")
        conn.execute("DROP TABLE IF EXISTS employees")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id             TEXT PRIMARY KEY,
            name           TEXT NOT NULL,
            role           TEXT NOT NULL,
            manager_id     TEXT,
            email          TEXT,
            casual_balance INTEGER NOT NULL,
            sick_balance   INTEGER NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS leave_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT NOT NULL REFERENCES employees(id),
            manager_id  TEXT NOT NULL REFERENCES employees(id),
            leave_type  TEXT NOT NULL,
            start_date  TEXT NOT NULL,
            end_date    TEXT NOT NULL,
            days        INTEGER NOT NULL,
            status      TEXT NOT NULL,
            reason      TEXT DEFAULT ''
        )
    """)

    # only add the starting people if the table is empty
    count = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
    if count == 0:
        conn.executemany("INSERT INTO employees VALUES (?, ?, ?, ?, ?, ?, ?)", SEED_EMPLOYEES)

    conn.commit()
    conn.close()


# test block: build a fresh database and print who is in it
if __name__ == "__main__":
    init_db(reset=True)
    conn = get_conn()
    for row in conn.execute("SELECT * FROM employees"):
        print(dict(row))
    conn.close()