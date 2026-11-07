# leave_admin_tests.py: checks public holidays, balance adjustments and the yearly reset.
# No AI, no API keys, runs in CI.   python leave_admin_tests.py
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date

import db
db.DB_PATH = os.path.join(tempfile.mkdtemp(), "leave_admin_test.db")   # throwaway database
os.environ["HRM_DB"] = db.DB_PATH          # so demo_data.py can't switch back to web_demo.db
import demo_data
import hr_queries
import leave_admin as la
import tools

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def fresh():
    demo_data.init_all(reset=True)
    conn = db.get_conn()
    with conn:   # holiday plans back to the built-in two
        conn.execute("DELETE FROM holidays")
        conn.execute("DELETE FROM holiday_plans")
        conn.execute("UPDATE profiles SET holiday_plan_id = NULL")
        # profiles survive a reset, so put back everyone's start date and clear any end date a test set
        conn.execute("UPDATE profiles SET end_date = NULL")
        for emp_id, _title, _dept, _loc, start in hr_queries.SEED_PROFILES:
            conn.execute("UPDATE profiles SET start_date = ? WHERE emp_id = ?", (start, emp_id))
    conn.close()
    la.init_leave_admin()


def balance(emp_id, kind):
    return tools.get_employee(emp_id)[f"{kind}_balance"]


def plan_id(name):
    return next(p["id"] for p in la.list_plans() if p["name"] == name)


@test
def leave_is_counted_with_each_persons_holiday_plan():
    fresh()
    assert date(2026, 11, 11) in tools.HOLIDAYS and date(2026, 11, 8) not in tools.HOLIDAYS
    # Mon 9 to Fri 13 Nov 2026: Armistice Day (Wed 11) isn't counted
    assert tools.count_working_days(date(2026, 11, 9), date(2026, 11, 13), tools.holidays_of("E001")) == 4
    # Good Friday 2027 is a public holiday in Alsace-Moselle only
    friday = (date(2027, 3, 26), date(2027, 3, 26))
    assert tools.count_working_days(*friday, tools.holidays_of("E001")) == 1
    la.assign_plan("H001", plan_id("Alsace-Moselle"), emp_ids=["E001"])
    assert tools.count_working_days(*friday, tools.holidays_of("E001")) == 0
    r = tools.apply_leave("E001", "casual", "2027-03-25", "2027-03-26")      # Thu + Good Friday
    assert r["request"]["days"] == 1, r


@test
def moving_people_between_plans_is_logged_and_can_go_by_office():
    fresh()
    import employees
    r = la.assign_plan("H001", plan_id("Alsace-Moselle"), location="Lyon")   # Hugo works in Lyon
    assert r["moved"] == 1 and la.plan_id_of("E002") == plan_id("Alsace-Moselle")
    line = employees.history("H001", "E002")["changes"][0]
    assert (line["field"], line["old_value"], line["new_value"]) == ("holiday_plan", "France", "Alsace-Moselle")
    assert "error" in la.assign_plan("E001", plan_id("France"), emp_ids=["E002"])   # not HR


@test
def holiday_editing_rules():
    fresh()
    france = plan_id("France")
    assert "error" in la.add_holiday("E001", france, "2026-12-28", "x")                    # not HR
    assert "passed" in la.add_holiday("H001", france, "2026-01-02", "Too late")["error"]   # past dates
    assert "already" in la.add_holiday("H001", france, "2026-12-25", "Again")["error"]
    r = la.add_holiday("H001", france, "2026-12-28", "Company day off")
    assert r["success"] and date(2026, 12, 28) in tools.holidays_of("E001")
    assert la.change_holiday_kind("H001", france, "2026-12-28", "floater")["success"]
    assert date(2026, 12, 28) not in tools.holidays_of("E001")                        # floaters aren't days off for everyone
    assert la.remove_holiday("H001", france, "2026-12-28")["success"]
    assert "passed" in la.remove_holiday("H001", france, "2026-01-01")["error"]       # history stays


@test
def floating_holidays_follow_the_quota_and_need_approval():
    fresh()
    r = la.request_floater("E001", "2026-12-24")                     # Christmas Eve, a floater in France
    assert r["success"] and r["floaters_left"] == 1, r
    assert "error" in la.request_floater("E001", "2026-12-25")       # mandatory, not a floater
    assert la.request_floater("E001", "2026-12-31")["success"]
    assert la.my_holidays("E001")["floaters"] == {"quota": 2, "used": 2, "left": 0}
    # Lucas approves; a floater doesn't touch the casual balance
    before = balance("E001", "casual")
    assert tools.decide_leave("M001", r["request_id"], "approve")["success"]
    assert balance("E001", "casual") == before
    # a chosen floater can't be removed or made mandatory until the request is withdrawn
    france = plan_id("France")
    assert "chosen" in la.remove_holiday("H001", france, "2026-12-24")["error"]
    assert tools.cancel_leave("E001", r["request_id"])["cancelled"]
    assert la.my_holidays("E001")["floaters"]["left"] == 1


@test
def floater_quota_is_per_year():
    fresh()
    assert la.request_floater("E001", "2026-12-24")["success"]
    assert la.request_floater("E001", "2026-12-31")["success"]
    la.add_holiday("H001", plan_id("France"), "2026-12-30", "Extra floater", "floater")
    assert "already used your 2" in la.request_floater("E001", "2026-12-30")["error"]
    assert la.request_floater("E001", "2027-01-06")["success"]       # 2027 has its own 2


@test
def plans_can_be_created_copied_and_only_deleted_when_empty():
    fresh()
    r = la.create_plan("H001", "Belgium office", "Copied from France for now", 1, copy_from=plan_id("France"))
    assert r["success"]
    assert len(la.plan_holidays(r["id"])["holidays"]) == len(la.plan_holidays(plan_id("France"))["holidays"])
    assert "already exists" in la.create_plan("H001", "france")["error"]
    la.assign_plan("H001", r["id"], emp_ids=["E003"])
    assert "Move them" in la.delete_plan("H001", r["id"])["error"]
    la.assign_plan("H001", plan_id("France"), emp_ids=["E003"])
    assert la.delete_plan("H001", r["id"])["success"]
    assert "default" in la.delete_plan("H001", plan_id("France"))["error"]


@test
def csv_import_checks_every_line_before_saving():
    fresh()
    france = plan_id("France")
    csv = "date,name,kind\n2027-06-21,Fête de la Musique,floater\n2027-13-01,Bad date,\n2027-12-25,Christmas again,\n2027-07-15,Summer day off,mandatory\n"
    preview = la.import_holidays("H001", france, csv, dry_run=True)
    assert preview["will_add"] == 2 and preview["skipped"] == 2
    assert [r["ok"] for r in preview["rows"]] == [True, False, False, True]
    assert date(2027, 7, 15) not in tools.holidays_of("E001")            # nothing saved yet
    done = la.import_holidays("H001", france, csv, dry_run=False)
    assert done["added"] == 2 and date(2027, 7, 15) in tools.holidays_of("E001")
    assert "column names" in la.import_holidays("H001", france, "when,what\n2027-01-01,x")["error"]


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
    assert "error" in la.adjust_balance("H001", "E001", "unpaid", 1, "x")          # unknown type
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


# ---------- paid leave (congés payés): 2.5 days earned every month ----------

def paid_lines(emp_id):
    return [(a["kind"], a["days"]) for a in la.balance_history(emp_id)["adjustments"] if a["leave_type"] == "paid"]


@test
def monthly_accrual_adds_2_5_paid_days_and_logs_why():
    fresh()
    result = la.run_month_accrual(2026, 10, today=date(2026, 11, 1))
    assert result == {"success": True, "month": "October 2026", "people": 6, "days_in_total": 15}, result
    assert balance("E001", "paid") == 15 and balance("E003", "paid") == 5      # 12.5 + 2.5, 2.5 + 2.5
    line = next(a for a in la.balance_history("E001")["adjustments"] if a["kind"] == "accrual")
    assert (line["leave_type"], line["days"], line["reason"], line["changed_by"]) == (
        "paid", 2.5, "Paid leave earned in October 2026", "Automatic update")


@test
def a_month_is_credited_only_once():
    fresh()
    assert la.run_month_accrual(2026, 10, today=date(2026, 11, 1))["success"]
    assert "already been credited" in la.run_month_accrual(2026, 10, today=date(2026, 11, 2))["error"]
    assert balance("E001", "paid") == 15                                         # not applied twice
    assert la.month_accrual_preview(2026, 10)["already_done"]["ran_by"] == "system"
    assert la.run_month_accrual(2026, 11, today=date(2026, 12, 1))["success"]    # next month is its own run
    assert balance("E001", "paid") == 17.5


@test
def a_month_cannot_be_credited_before_it_ends():
    fresh()
    assert "from 2026-10-31" in la.run_month_accrual(2026, 10, today=date(2026, 10, 30))["error"]
    assert balance("E001", "paid") == 12.5                                       # nothing happened
    assert la.run_month_accrual(2026, 10, today=date(2026, 10, 31))["success"]   # the last day itself is fine
    assert la.last_finished_month(date(2026, 11, 1)) == (2026, 10)
    assert la.last_finished_month(date(2027, 1, 15)) == (2026, 12)


@test
def only_hr_can_run_the_accrual_by_hand():
    fresh()
    for who in ("E001", "M001", "C001"):
        assert "error" in la.run_month_accrual(2026, 10, ran_by=who, today=date(2026, 11, 1))
    assert balance("E001", "paid") == 12.5
    assert la.run_month_accrual(2026, 10, ran_by="H001", today=date(2026, 11, 1))["success"]
    line = next(a for a in la.balance_history("E001")["adjustments"] if a["kind"] == "accrual")
    assert line["changed_by"] == "Narmadha"                                      # the log says who


@test
def a_part_month_earns_a_share_in_proportion_to_the_days_employed():
    fresh()
    conn = db.get_conn()
    with conn:
        conn.execute("UPDATE profiles SET start_date = '2026-10-03' WHERE emp_id = 'E001'")   # 29 of 31 days
        conn.execute("UPDATE profiles SET start_date = '2026-10-04' WHERE emp_id = 'E002'")   # 28 of 31 days
        conn.execute("UPDATE profiles SET start_date = '2026-10-05' WHERE emp_id = 'E003'")   # 27 of 31 days
        conn.execute("UPDATE profiles SET end_date = '2026-10-15' WHERE emp_id = 'H001'")     # left half way: 15 of 31
        conn.execute("UPDATE profiles SET end_date = '2026-09-30' WHERE emp_id = 'M001'")     # left the month before
    conn.close()
    preview = la.month_accrual_preview(2026, 10)
    assert preview["people_earning"] == 5 and preview["days_in_total"] == 10.49   # 2.5+2.34+2.26+2.18+1.21
    assert set(preview["part_month"]) == {"Emma", "Hugo", "Clara", "Narmadha"} and preview["not_earning"] == ["Lucas"]
    assert la.run_month_accrual(2026, 10, today=date(2026, 11, 1))["people"] == 5
    # 2.5 x 29/31 = 2.3387 -> 2.34, 2.5 x 28/31 = 2.2581 -> 2.26, 2.5 x 27/31 = 2.1774 -> 2.18, 2.5 x 15/31 = 1.2097 -> 1.21
    assert [round(balance(e, "paid"), 2) for e in ("E001", "E002", "E003", "H001")] == [14.84, 9.76, 4.68, 16.21]
    assert (balance("C001", "paid"), balance("M001", "paid")) == (27.5, 20)          # a full month is exactly 2.5; Lucas had left
    line = next(a for a in la.balance_history("E001")["adjustments"] if a["kind"] == "accrual")
    assert (line["days"], line["reason"]) == (2.34, "Paid leave earned in October 2026, pro-rated: 29 of 31 days")
    assert tools.get_leave_balance("E001")["leave_balance"]["paid"] == 14.84         # no 14.839999 on screen
    # February has 28 days: joining on the 15th is 14 of 28, exactly half
    plan = {p["id"]: p["days"] for p in la._accrual_plan(2027, 2)}
    assert plan["C001"] == 2.5
    assert la._days_employed_in_month("2027-02-15", None, 2027, 2) == 14 and round(2.5 * 14 / 28, 2) == 1.25


@test
def year_start_carries_every_paid_day_and_rounds_the_total_up():
    fresh()
    preview = la.year_start_preview(2027)
    # 25, 20, 12.5, 7.5, 2.5, 15 become 25, 20, 13, 8, 3, 15
    assert preview["paid_days_carried"] == 84 and preview["paid_days_rounded_up"] == 1.5
    assert la.run_year_start(2027, today=date(2027, 1, 1))["success"]
    assert [balance(e, "paid") for e in ("C001", "E001", "E002", "E003")] == [25, 13, 8, 3]
    assert paid_lines("E001") == [("round_up", 0.5)]                             # the rounding is explained
    assert paid_lines("C001") == []                                              # a whole number needs none
    reason = next(a["reason"] for a in la.balance_history("E001")["adjustments"] if a["kind"] == "round_up")
    assert "L3141-7" in reason


@test
def year_start_rounds_up_by_the_exact_fraction_and_ignores_float_noise():
    fresh()
    conn = db.get_conn()
    with conn:
        conn.execute("UPDATE employees SET paid_balance = 14.01 WHERE id = 'E001'")           # up to 15
        conn.execute("UPDATE employees SET paid_balance = 14.0000000001 WHERE id = 'E002'")   # noise: stays 14
        conn.execute("UPDATE employees SET paid_balance = 14.84 WHERE id = 'E003'")
    conn.close()
    assert la.run_year_start(2027, today=date(2027, 1, 1))["success"]
    assert [balance(e, "paid") for e in ("E001", "E002", "E003")] == [15, 14, 15]
    assert paid_lines("E001") == [("round_up", 0.99)] and paid_lines("E003") == [("round_up", 0.16)]
    assert paid_lines("E002") == []


@test
def paid_leave_adjustments_may_be_half_days_but_casual_and_sick_may_not():
    fresh()
    assert la.adjust_balance("H001", "E001", "paid", 2.5, "Days brought from a previous employer")["balance_after"] == 15
    assert la.adjust_balance("H001", "E001", "paid", -0.5, "Correction")["balance_after"] == 14.5
    assert "half days" in la.adjust_balance("H001", "E001", "paid", 0.3, "x")["error"]
    assert "whole number" in la.adjust_balance("H001", "E001", "casual", 0.5, "x")["error"]
    assert balance("E001", "paid") == 14.5 and balance("E001", "casual") == 8
    assert la.balance_history("E001")["balance"] == {"casual": 8, "sick": 5, "paid": 14.5}


@test
def accrual_status_names_the_last_month_and_the_next_one_with_its_date():
    fresh()
    s = la.accrual_status(today=date(2026, 11, 7))
    assert s["last_credited"] is None                                     # nothing credited yet: start with last month
    assert (s["next"]["label"], s["next"]["can_run_from"], s["next"]["can_run_now"]) == ("October 2026", "2026-10-31", True)
    assert la.run_month_accrual(2026, 10, ran_by="H001", today=date(2026, 11, 7))["success"]
    s = la.accrual_status(today=date(2026, 11, 7))
    assert (s["last_credited"]["month"], s["last_credited"]["ran_by"]) == ("October 2026", "Narmadha")
    assert (s["next"]["label"], s["next"]["can_run_from"], s["next"]["can_run_now"]) == ("November 2026", "2026-11-30", False)
    assert la.accrual_status(today=date(2026, 11, 30))["next"]["can_run_now"]      # the last day itself is enough
    assert la.run_month_accrual(2026, 11, today=date(2026, 12, 1))["success"]
    assert la.run_month_accrual(2026, 12, today=date(2027, 1, 1))["success"]
    assert la.accrual_status(today=date(2027, 1, 2))["next"]["label"] == "January 2027"   # December rolls over


@test
def databases_made_before_paid_leave_get_the_column():
    old_path = db.DB_PATH
    db.DB_PATH = os.path.join(tempfile.mkdtemp(), "before_paid_leave.db")
    try:
        conn = sqlite3.connect(db.DB_PATH)     # the employees table as it was before this feature
        conn.execute("""CREATE TABLE employees (id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
                        manager_id TEXT, email TEXT, casual_balance INTEGER NOT NULL, sick_balance INTEGER NOT NULL)""")
        conn.execute("INSERT INTO employees VALUES ('E009', 'Old Timer', 'employee', NULL, 'o@x.eu', 3, 2)")
        conn.commit()
        conn.close()
        db.init_db()
        db.init_db()                                                     # running it twice is harmless
        assert tools.get_employee("E009")["paid_balance"] == 0           # existing people start at 0
        assert tools.get_employee("E001")["paid_balance"] == 12.5        # seed people are added as before
    finally:
        db.DB_PATH = old_path


@test
def the_monthly_job_runs_from_the_command_line():
    fresh()
    env = {**os.environ, "HRM_DB": db.DB_PATH}
    here = os.path.dirname(os.path.abspath(__file__))
    def job(*args):
        return subprocess.run([sys.executable, "leave_jobs.py", *args], cwd=here, env=env,
                              capture_output=True, text=True)
    first = job("month-end", "2026-10")
    assert first.returncode == 0 and "'success': True" in first.stdout, first.stdout + first.stderr
    assert balance("E001", "paid") == 15
    again = job("month-end", "2026-10")
    assert again.returncode == 0 and "already been credited" in again.stdout      # a daily cron is safe
    assert balance("E001", "paid") == 15
    assert job("month-end", "banana").returncode != 0                              # bad input is a failure
    assert job("month-end", "2999-01").returncode != 0                             # a month that hasn't ended is too
    assert balance("E001", "paid") == 15


# ---------- the Needs you inbox (needs_you.py) ----------
import needs_you


def a_floating_holiday(emp_id):
    # the next floating holiday still to come that falls on a weekday
    for year in (date.today().year, date.today().year + 1):
        for h in la.my_holidays(emp_id, year)["holidays"]:
            if h["kind"] == "floater" and not h["past"] and not h["weekend"]:
                return h["day"]


def set_paid(emp_id, days):
    conn = db.get_conn()
    with conn:
        conn.execute("UPDATE employees SET paid_balance = ? WHERE id = ?", (days, emp_id))
    conn.close()


@test
def a_manager_sees_leave_paid_leave_and_floating_holidays_of_the_team_oldest_first():
    fresh()
    paid = tools.apply_leave("E001", "paid", "2030-03-04", "2030-03-09")["request"]["id"]       # Emma, 6 days
    casual = tools.apply_leave("E002", "casual", "2030-03-05", "2030-03-05")["request"]["id"]   # Hugo
    floater = la.request_floater("E003", a_floating_holiday("E003"))["request_id"]               # Clara
    box = needs_you.inbox("M001")
    assert [r["request_id"] for r in box["leave_requests"]] == [paid, casual, floater]           # oldest first
    assert [r["leave_type"] for r in box["leave_requests"]] == ["paid", "casual", "floater"]
    assert box["waiting_on_you"] == 3 and box["hr_requests"] == [] and box["role"] == "manager"
    first = box["leave_requests"][0]
    assert first["days"] == 6 and first["balances"]["paid"] == {"balance": 12.5, "pending": 6, "available": 6.5}
    assert [o["name"] for o in first["overlaps"]] == ["Hugo"]                                    # who else is off then


@test
def only_employer_decisions_are_in_the_inbox_and_employees_have_none():
    fresh()
    tools.apply_leave("E001", "casual", "2030-03-04", "2030-03-04")                              # Emma asks Lucas
    lucas = tools.apply_leave("M001", "casual", "2030-03-11", "2030-03-11")["request"]["id"]      # Lucas asks the CEO
    assert [r["request_id"] for r in needs_you.inbox("C001")["leave_requests"]] == [lucas]      # the CEO decides Lucas's
    assert [r["employee_id"] for r in needs_you.inbox("M001")["leave_requests"]] == ["E001"]     # not his own request
    assert "error" in needs_you.inbox("E001") and "error" in needs_you.inbox("nobody")          # employees: no inbox
    assert needs_you.waiting_count("E001") == 0
    assert needs_you.inbox("H001")["leave_requests"] == []                                       # HR does not decide leave


@test
def hr_sees_open_hr_requests_oldest_first_and_closed_ones_leave_the_inbox():
    fresh()
    first = tools.create_hr_ticket("E002", "Can I get a parking allowance?", "Parking allowance")["id"]
    second = tools.create_hr_ticket("E001", "Laptop stand?", "Laptop stand")["id"]
    box = needs_you.inbox("H001")
    assert [t["id"] for t in box["hr_requests"]] == [first, second] and box["waiting_on_you"] == 2
    assert box["hr_requests"][0]["ticket"] == "HR-0001"
    assert tools.reply_to_ticket("H001", first, "We will look at it.")["success"]
    assert [t["id"] for t in needs_you.inbox("H001")["hr_requests"]] == [second]
    assert needs_you.waiting_count("H001") == 1 and needs_you.inbox("M001")["hr_requests"] == []


@test
def more_than_30_unused_paid_days_is_a_warning_for_the_team_manager_and_for_hr():
    fresh()
    set_paid("E001", 31)          # Emma: warned
    set_paid("E002", 30)          # Hugo: exactly 30 is fine
    set_paid("E003", 30.5)        # Clara: warned
    lucas = needs_you.inbox("M001")["paid_leave_warnings"]
    assert [(w["name"], w["paid_days"]) for w in lucas] == [("Emma", 31), ("Clara", 30.5)]   # most days first
    assert lucas[0]["message"] == "Emma has 31 unused paid days, more than 30. Worth planning some leave."
    assert needs_you.inbox("M001")["waiting_on_you"] == 0                                    # a heads-up, not a decision
    assert needs_you.inbox("C001")["paid_leave_warnings"] == []                              # Lucas 20, Narmadha 15: his team only
    assert [w["name"] for w in needs_you.inbox("H001")["paid_leave_warnings"]] == ["Emma", "Clara"]


@test
def people_who_have_left_are_not_warned_about():
    fresh()
    set_paid("E001", 40)
    conn = db.get_conn()
    with conn:
        conn.execute("UPDATE profiles SET end_date = '2020-01-01' WHERE emp_id = 'E001'")
    conn.close()
    assert needs_you.inbox("M001")["paid_leave_warnings"] == []
    assert needs_you.inbox("H001")["paid_leave_warnings"] == []


@test
def a_decided_request_leaves_the_inbox_and_the_assistant_view_matches():
    fresh()
    rid = tools.apply_leave("E001", "paid", "2030-03-04", "2030-03-09")["request"]["id"]
    tools.create_hr_ticket("E002", "Parking?", "Parking")
    set_paid("E003", 33)
    seen = needs_you.for_assistant("M001")
    assert seen["waiting_on_you"] == 1 and seen["hr_requests"] == []
    assert seen["leave_requests"] == [{"request_id": rid, "employee_name": "Emma", "leave_type": "paid",
                                       "start": "2030-03-04", "end": "2030-03-09", "days": 6, "also_off": []}]
    assert seen["paid_leave_warnings"] == ["Clara has 33 unused paid days, more than 30. Worth planning some leave."]
    assert "error" in needs_you.for_assistant("E001")
    assert [t["summary"] for t in needs_you.for_assistant("H001")["hr_requests"]] == ["Parking"]
    assert tools.decide_leave("M001", rid, "approve")["success"]                                 # same rule as the Approvals page
    assert needs_you.inbox("M001")["leave_requests"] == [] and needs_you.waiting_count("M001") == 0
    assert "error" in tools.decide_leave("C001", rid, "approve")                                 # still only the right manager


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
