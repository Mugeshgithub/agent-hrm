# employees_tests.py: checks the employee-record rules. No AI, no API keys, runs in CI.
#   python employees_tests.py
import os
import sys
import tempfile

import db
db.DB_PATH = os.path.join(tempfile.mkdtemp(), "employees_test.db")   # throwaway database
os.environ["HRM_DB"] = db.DB_PATH          # so demo_data.py can't switch back to web_demo.db
import demo_data
import employees as e

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def fresh():
    # every test starts from the same 6 people, nothing else
    demo_data.init_all(reset=True)
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM employee_changes")
        conn.execute("DELETE FROM profiles")
    conn.close()
    demo_data.hr_queries.init_profiles()     # the 6 original profiles again, unchanged


NEW_HIRE = {"name": "Anu", "email": "anu@cognivexai.eu", "role": "employee", "title": "QA Engineer",
            "department": "Engineering", "location": "Paris", "start_date": "2026-12-01", "manager_id": "M001"}


@test
def hr_can_hire_and_it_is_logged():
    fresh()
    r = e.create_employee("H001", NEW_HIRE)
    assert r == {"success": True, "id": "E004", "name": "Anu"}, r
    assert e.directory_entry("E004")["manager"] == "Lucas"
    log = e.history("H001", "E004")["changes"]
    assert len(log) == 1 and log[0]["new_value"] == "created"


@test
def edit_logs_each_changed_field_with_the_reason():
    fresh()
    r = e.update_employee("H001", "E001", {"title": "Senior Engineer", "location": "Lyon", "department": "Engineering"},
                          "Promotion and move")
    assert r["changed"] == ["location", "title"], r          # department didn't change, so it isn't logged
    log = e.history("H001", "E001")["changes"]
    assert {(c["field"], c["old_value"], c["new_value"]) for c in log} == {
        ("title", "Software Engineer", "Senior Engineer"), ("location", "Paris", "Lyon")}
    assert all(c["reason"] == "Promotion and move" and c["changed_by"] == "Narmadha" for c in log)


@test
def offboarding_sets_the_end_date_and_returns_the_final_balance():
    fresh()
    r = e.offboard_employee("H001", "E002", "2026-12-31", "Resigned")
    # Hugo's unused paid days (7.5) are in the final balance: they are paid out when someone leaves
    assert r["success"] and r["final_balance"] == {"casual": 2, "sick": 4, "paid": 7.5}, r
    assert e.is_active("E002")                              # still here today
    assert not e.is_active("E002", on=__import__("datetime").date(2027, 1, 1))   # gone after the end date


@test
def only_active_hr_can_change_records():
    fresh()
    assert "error" in e.create_employee("E001", NEW_HIRE)
    assert "error" in e.update_employee("M001", "E001", {"title": "Boss"}, "test")
    e.offboard_employee("H001", "E003", "2026-11-01", "Left")     # Clara has left
    e.update_employee("H001", "E003", {}, "x")                    # (no-op, just to show she's gone)
    assert not e.is_active("E003")


@test
def manager_loops_are_blocked():
    fresh()
    r = e.update_employee("H001", "M001", {"manager_id": "M001"}, "test")
    assert "own manager" in r["error"], r


@test
def a_failed_change_leaves_nothing_behind():
    fresh()
    e.update_employee("H001", "E001", {"email": "hugo@cognivexai.eu"}, "test")   # clash, refused
    assert e.directory_entry("E001")["email"] == "emma@cognivexai.eu"
    assert e.history("H001", "E001")["changes"] == []


@test
def colleague_lookup_is_safe_and_skips_people_who_left():
    fresh()
    r = e.find_colleagues("hug")
    assert r["found"] == 1 and r["people"][0]["manager"] == "Lucas", r
    assert not {"casual_balance", "sick_balance", "contract_type", "end_date"} & set(r["people"][0])
    e.offboard_employee("H001", "E003", "2026-01-01", "Left")          # Clara left in January
    assert e.find_colleagues("clara")["found"] == 0
    assert "error" in e.find_colleagues("c")                            # too short to search


@test
def my_record_shows_my_manager_and_my_reports():
    fresh()
    emma = e.my_record("E001")["people"][0]
    assert emma["manager"] == "Lucas" and emma["reports"] == []
    assert sorted(e.my_record("M001")["people"][0]["reports"]) == ["Clara", "Emma", "Hugo"]


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