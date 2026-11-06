# leave_admin_tests.py: checks public holidays, balance adjustments and the yearly reset.
# No AI, no API keys, runs in CI.   python leave_admin_tests.py
import os
import sys
import tempfile
from datetime import date

import db
db.DB_PATH = os.path.join(tempfile.mkdtemp(), "leave_admin_test.db")   # throwaway database
os.environ["HRM_DB"] = db.DB_PATH          # so demo_data.py can't switch back to web_demo.db
import demo_data
import leave_admin as la
import tools

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def fresh():
    demo_data.init_all(reset=True)
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM public_holidays")
        conn.executemany("INSERT INTO public_holidays VALUES (?, ?)", la.FRENCH_HOLIDAYS)
    conn.close()
    la.load_holidays()


def balance(emp_id, kind):
    return tools.get_employee(emp_id)[f"{kind}_balance"]


@test
def french_holidays_are_used_for_counting_leave():
    fresh()
    assert date(2026, 11, 11) in tools.HOLIDAYS and date(2026, 11, 8) not in tools.HOLIDAYS
    # Mon 9 to Fri 13 Nov 2026: Armistice Day (Wed 11) isn't counted
    assert tools.count_working_days(date(2026, 11, 9), date(2026, 11, 13)) == 4


@test
def hr_adds_and_removes_a_holiday_and_counting_follows():
    fresh()
    assert "error" in la.add_holiday("E001", "2026-12-24", "Christmas Eve")       # not HR
    r = la.add_holiday("H001", "2026-12-24", "Christmas Eve (company day off)")
    assert r["success"] and date(2026, 12, 24) in tools.HOLIDAYS
    assert tools.count_working_days(date(2026, 12, 21), date(2026, 12, 25)) == 3
    assert "error" in la.add_holiday("H001", "2026-12-24", "Again")               # no duplicates
    assert la.remove_holiday("H001", "2026-12-24")["success"]
    assert tools.count_working_days(date(2026, 12, 21), date(2026, 12, 25)) == 4


@test
def adjustment_changes_the_balance_and_explains_why():
    fresh()
    r = la.adjust_balance("H001", "E001", "casual", 2, "Worked on the Saturday release")
    assert r["balance_after"] == r["balance_before"] + 2 == balance("E001", "casual")
    line = la.balance_history("E001")["adjustments"][0]
    assert (line["days"], line["kind"], line["changed_by"]) == (2, "manual", "Narmadha")
    assert line["reason"] == "Worked on the Saturday release"


@test
def adjustment_rules():
    fresh()
    assert "error" in la.adjust_balance("M001", "E001", "casual", 1, "x")          # managers can't
    assert "error" in la.adjust_balance("H001", "H001", "casual", 1, "x")          # not your own
    assert "error" in la.adjust_balance("H001", "E001", "casual", 1, " ")          # reason needed
    assert "error" in la.adjust_balance("H001", "E001", "casual", 0, "x")
    assert "error" in la.adjust_balance("H001", "E001", "casual", 31, "x")         # too big
    assert "error" in la.adjust_balance("H001", "E001", "paid", 1, "x")            # unknown type
    # Emma has 8 casual days; with 2 waiting for approval the balance can't drop below 2
    tools.apply_leave("E001", "casual", "2027-03-01", "2027-03-02")
    assert "can't go below 2" in la.adjust_balance("H001", "E001", "casual", -7, "x")["error"]
    assert la.balance_history("E001")["adjustments"] == []                          # refusals leave no trace


@test
def year_start_carries_5_casual_days_and_resets_sick():
    fresh()
    # Emma: 8 casual, 5 sick. Clara: 0 casual, 1 sick.
    preview = la.year_start_preview(2027)
    assert preview["people"] == 6 and preview["already_done"] is None
    assert "error" in la.run_year_start(2027, today=date(2026, 12, 31))             # too early
    assert la.run_year_start(2027, today=date(2027, 1, 1))["success"]
    assert (balance("E001", "casual"), balance("E001", "sick")) == (5 + 12, 6)
    assert (balance("E003", "casual"), balance("E003", "sick")) == (0 + 12, 6)
    kinds = [(a["leave_type"], a["kind"], a["days"]) for a in la.balance_history("E001")["adjustments"]]
    assert ("casual", "lapse", -3) in kinds and ("casual", "year_start", 12) in kinds
    assert ("sick", "lapse", -5) in kinds and ("sick", "year_start", 6) in kinds


@test
def year_start_runs_only_once():
    fresh()
    assert la.run_year_start(2027, today=date(2027, 1, 1))["success"]
    assert "already run" in la.run_year_start(2027, today=date(2027, 1, 2))["error"]
    assert balance("E001", "casual") == 17                                           # not applied twice
    assert la.year_start_preview(2027)["already_done"]["ran_by"] == "system"


@test
def people_who_have_left_are_not_reset():
    fresh()
    import employees
    employees.offboard_employee("H001", "E002", "2026-12-31", "Resigned")
    assert la.run_year_start(2027, today=date(2027, 1, 1))["people"] == 5
    assert balance("E002", "casual") == 2                                            # Hugo's stays as it was


if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as ex:
            failed += 1
            print(f"FAIL {t.__name__}: {ex!r}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)
