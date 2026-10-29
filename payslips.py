# payslips.py: read-only payslips. Payroll writes them; the agent only reads.
import db
from datetime import date

NOT_FOUND = "No payslip found for that month."   # same message for "missing" and "not allowed"

KINDS = ("earning", "employee_contribution", "employer_contribution", "tax")


def init_payslips():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payslip_lines (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id       TEXT NOT NULL,
                month        TEXT NOT NULL,          -- '2026-10'
                kind         TEXT NOT NULL,          -- one of KINDS
                label        TEXT NOT NULL,
                amount_cents INTEGER NOT NULL        -- money in cents, never floats
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payslip_access_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                viewer_id  TEXT NOT NULL,
                emp_id     TEXT NOT NULL,
                month      TEXT,
                viewed_at  TEXT DEFAULT CURRENT_TIMESTAMP
            )""")
    conn.close()


def _month(emp_id, month, base, pension, health, csg, tax, employer, extra=None):
    rows = [(emp_id, month, "earning", "Base salary", base)]
    if extra:
        rows.append((emp_id, month, "earning", extra[0], extra[1]))
    rows += [
        (emp_id, month, "employee_contribution", "Pension contributions", pension),
        (emp_id, month, "employee_contribution", "Health and other contributions", health),
        (emp_id, month, "employee_contribution", "CSG/CRDS", csg),
        (emp_id, month, "tax", "Income tax withheld at source", tax),
        (emp_id, month, "employer_contribution", "Employer contributions", employer),
    ]
    return rows


def seed_payslips():
    # illustrative figures only, not real French rates
    lines = (
        _month("E001", "2026-09", 320000, 33000, 12000, 29000, 15000, 135000)
        + _month("E001", "2026-10", 320000, 32000, 11700, 28100, 14000, 129000,
                 extra=("Unpaid leave (1 day)", -14700))
        + _month("E002", "2026-09", 280000, 29000, 10000, 26000, 9000, 115000)
        + _month("E002", "2026-10", 280000, 29000, 10000, 26000, 9000, 115000)
        + _month("E003", "2026-09", 250000, 26000, 9000, 23000, 6000, 103000)
        + _month("E003", "2026-10", 250000, 26000, 9000, 23000, 6000, 103000)
        + _month("M001", "2026-09", 450000, 46000, 16000, 41000, 32000, 190000)
        + _month("M001", "2026-10", 450000, 46000, 16000, 41000, 32000, 190000,
                 extra=("Performance bonus", 50000))
        + _month("H001", "2026-09", 380000, 39000, 14000, 35000, 22000, 160000)
        + _month("H001", "2026-10", 380000, 39000, 14000, 35000, 22000, 160000)
        + _month("C001", "2026-09", 700000, 72000, 25000, 64000, 80000, 300000)
        + _month("C001", "2026-10", 700000, 72000, 25000, 64000, 80000, 300000)
    )
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM payslip_lines")
        conn.executemany(
            "INSERT INTO payslip_lines (emp_id, month, kind, label, amount_cents) "
            "VALUES (?, ?, ?, ?, ?)", lines)
    conn.close()


# ---------- helpers ----------

def euro(cents):
    # exact formatting from whole cents: 219500 -> "€2,195.00", -14700 -> "-€147.00"
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}€{cents // 100:,}.{cents % 100:02d}"


def _role(emp_id):
    conn = db.get_conn()
    row = conn.execute("SELECT role FROM employees WHERE id = ?", (emp_id,)).fetchone()
    conn.close()
    return row["role"] if row else None


def _can_view(viewer_id, emp_id, month_note):
    # the ONE permission rule for every payslip function:
    # you may see your own; HR may see anyone's, and every HR look is logged
    if viewer_id == emp_id:
        return True
    if _role(viewer_id) == "hr":
        conn = db.get_conn()
        with conn:
            conn.execute(
                "INSERT INTO payslip_access_log (viewer_id, emp_id, month) VALUES (?, ?, ?)",
                (viewer_id, emp_id, month_note))
        conn.close()
        return True
    return False


def _lines(emp_id, month):
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT kind, label, amount_cents FROM payslip_lines "
        "WHERE emp_id = ? AND month = ? ORDER BY id",
        (emp_id, month)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _totals(lines):
    def total(kind):
        return sum(l["amount_cents"] for l in lines if l["kind"] == kind)
    gross = total("earning")
    contributions = total("employee_contribution")
    tax = total("tax")
    net_before_tax = gross - contributions
    return {
        "gross_cents": gross,
        "employee_contributions_cents": contributions,
        "net_before_tax_cents": net_before_tax,
        "tax_cents": tax,
        "net_paid_cents": net_before_tax - tax,
        "employer_contributions_cents": total("employer_contribution"),
    }


def _with_euros(totals):
    # add ready-to-say euro text, so the model never does arithmetic or rounding
    return {**totals, **{k.replace("_cents", "_eur"): euro(v) for k, v in totals.items()}}


# ---------- the three read-only functions ----------

def get_payslip(viewer_id, emp_id, month):
    if not _can_view(viewer_id, emp_id, month):
        return {"error": NOT_FOUND}
    lines = _lines(emp_id, month)
    if not lines:
        if viewer_id != emp_id:
            return {"error": NOT_FOUND}          # others: never reveal why
        # own payslip: the CODE gives the reason, so the model never has to invent one
        if month >= date.today().strftime("%Y-%m"):
            return {"error": f"No payslip has been issued for {month} yet.",
                    "reason": "not_issued_yet"}
        return {"error": f"There is no payslip on record for {month}.",
                "reason": "no_record"}
    return {
        "month": month,
        "lines": [{**l, "amount_eur": euro(l["amount_cents"])} for l in lines],
        **_with_euros(_totals(lines)),
    }


def compare_payslips(viewer_id, emp_id, month_a, month_b):
    if not _can_view(viewer_id, emp_id, f"{month_a} vs {month_b}"):
        return {"error": NOT_FOUND}
    a, b = _lines(emp_id, month_a), _lines(emp_id, month_b)
    if not a or not b:
        return {"error": NOT_FOUND}

    # line-by-line differences, including lines that exist in only one month
    before = {(l["kind"], l["label"]): l["amount_cents"] for l in a}
    after = {(l["kind"], l["label"]): l["amount_cents"] for l in b}
    changes = []
    for key in list(before) + [k for k in after if k not in before]:
        old, new = before.get(key, 0), after.get(key, 0)
        if old != new:
            changes.append({
                "kind": key[0], "label": key[1],
                "before_eur": euro(old), "after_eur": euro(new),
                "change_cents": new - old, "change_eur": euro(new - old),
            })

    ta, tb = _totals(a), _totals(b)
    change = tb["net_paid_cents"] - ta["net_paid_cents"]
    return {
        "from_month": month_a,
        "to_month": month_b,
        "net_paid_before_eur": euro(ta["net_paid_cents"]),
        "net_paid_after_eur": euro(tb["net_paid_cents"]),
        "net_paid_change_cents": change,
        "net_paid_change_eur": euro(change),
        "changes": changes,
    }


def get_pay_summary(viewer_id, emp_id, year):
    if not _can_view(viewer_id, emp_id, f"year {year}"):
        return {"error": NOT_FOUND}
    conn = db.get_conn()
    months = [r["month"] for r in conn.execute(
        "SELECT DISTINCT month FROM payslip_lines WHERE emp_id = ? AND month LIKE ? ORDER BY month",
        (emp_id, f"{year}-%")).fetchall()]
    conn.close()
    if not months:
        return {"error": NOT_FOUND}

    # add up month by month, in code
    sums = {}
    for m in months:
        for k, v in _totals(_lines(emp_id, m)).items():
            sums[k] = sums.get(k, 0) + v
    return {"year": year, "months": len(months), "month_list": months, **_with_euros(sums)}