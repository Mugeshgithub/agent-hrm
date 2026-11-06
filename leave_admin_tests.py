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
    with conn:   # holiday plans back to the built-in two
        conn.execute("DELETE FROM holidays")
        conn.execute("DELETE FROM holiday_plans")
        conn.execute("UPDATE profiles SET holiday_plan_id = NULL")
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
