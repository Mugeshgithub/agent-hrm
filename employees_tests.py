# employees_tests.py: checks the employee-record rules. No AI, no API keys, runs in CI.
#   python employees_tests.py
import os
import sys
import tempfile
from datetime import date, timedelta

import db
db.DB_PATH = os.path.join(tempfile.mkdtemp(), "employees_test.db")   # throwaway database
os.environ["HRM_DB"] = db.DB_PATH          # so demo_data.py can't switch back to web_demo.db
import demo_data
import employees as e
import needs_you
import onboarding

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


def in_days(n):
    return (date.today() + timedelta(days=n)).isoformat()


NEW_HIRE = {"name": "Anu", "email": "anu@cognivexai.eu", "role": "employee", "title": "QA Engineer",
            "department": "Engineering", "location": "Paris", "start_date": in_days(30), "manager_id": "M001"}


@test
def hr_can_hire_and_it_is_logged():
    fresh()
    r = e.create_employee("H001", NEW_HIRE)
    assert r == {"success": True, "id": "E004", "name": "Anu"}, r
    assert e.directory_entry("E004")["manager"] == "Lucas"
    log = e.history("H001", "E004")["changes"]
    assert sorted(c["new_value"] for c in log) == ["created", "started with 8 steps"]   # the record, then the checklist


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


# ---------- onboarding (onboarding.py) ----------

def hire(days_ahead=30, **changes):
    r = e.create_employee("H001", {**NEW_HIRE, "start_date": in_days(days_ahead), **changes})
    assert r.get("success"), r
    return r["id"]


def step(emp_id, key):
    return next(s for s in onboarding.checklist("H001", emp_id)["steps"] if s["step_key"] == key)


@test
def a_hire_who_starts_in_the_future_gets_the_checklist_from_the_template():
    fresh()
    emp_id = hire(30)
    box = onboarding.checklist("H001", emp_id)
    start = date.fromisoformat(in_days(30))
    assert [(s["step_key"], s["owner"], s["status"]) for s in box["steps"]] == [
        ("contract", "hr", "todo"), ("id", "new_hire", "todo"), ("iban", "new_hire", "todo"),
        ("social_security", "new_hire", "todo"), ("equipment", "manager", "todo"), ("accounts", "hr", "todo"),
        ("welcome", "manager", "todo"), ("dpae", "hr", "todo")]
    assert box["progress"] == {"done": 0, "skipped": 0, "total": 8, "complete": False}
    assert box["name"] == "Anu" and box["start_date"] == in_days(30) and box["manager_name"] == "Lucas"
    # due dates count back from the start date; the DPAE is the last thing before day one
    assert step(emp_id, "id")["due_date"] == (start - timedelta(days=7)).isoformat()
    assert step(emp_id, "dpae")["due_date"] == (start - timedelta(days=1)).isoformat()
    assert all(s["due_date"] < in_days(30) for s in box["steps"]) and not any(s["overdue"] for s in box["steps"])
    assert "L1221-10" in step(emp_id, "dpae")["detail"]            # the legal reason is written on the step


@test
def someone_who_starts_today_or_earlier_gets_no_checklist():
    fresh()
    for days in (0, -40):
        emp_id = hire(days, email=f"person{abs(days)}@cognivexai.eu")
        assert onboarding.checklist("H001", emp_id) is None
        assert [c["new_value"] for c in e.history("H001", emp_id)["changes"]] == ["created"]


@test
def due_dates_are_never_in_the_past_on_the_day_the_hire_is_entered():
    fresh()
    emp_id = hire(3)                                  # starts in 3 days, so "7 days before" would be 4 days ago
    dues = {s["step_key"]: s["due_date"] for s in onboarding.checklist("H001", emp_id)["steps"]}
    assert dues["id"] == dues["contract"] == in_days(0)             # as early as possible: today
    assert dues["dpae"] == in_days(2) and dues["equipment"] == in_days(1)
    assert all(in_days(0) <= d < in_days(3) for d in dues.values())
    assert not any(s["overdue"] for s in onboarding.checklist("H001", emp_id)["steps"])


@test
def the_template_covers_what_a_new_hire_needs():
    keys = [t[0] for t in onboarding.TEMPLATE]
    assert keys == ["contract", "id", "iban", "social_security", "equipment", "accounts", "welcome", "dpae"]
    assert len(set(keys)) == len(keys)
    assert {t[2] for t in onboarding.TEMPLATE} == {"new_hire", "hr", "manager"}
    assert all(t[3] >= 1 for t in onboarding.TEMPLATE)             # everything is due before day one


@test
def only_hr_sees_a_checklist():
    fresh()
    emp_id = hire()
    for viewer in ("E001", "M001", emp_id, "nobody"):
        assert "error" in onboarding.checklist(viewer, emp_id), viewer
    assert onboarding.checklist("H001", "E001") is None            # no checklist for someone who started long ago


@test
def hr_can_complete_skip_and_reopen_and_every_change_is_logged():
    fresh()
    emp_id = hire()
    contract, accounts, iban = (step(emp_id, k)["id"] for k in ("contract", "accounts", "iban"))
    assert onboarding.complete_step("H001", contract, "Signed copy received by post")["status"] == "done"
    done = step(emp_id, "contract")
    assert done["status"] == "done" and done["done_by_name"] == "Narmadha" and done["note"] == "Signed copy received by post"
    assert "already done" in onboarding.complete_step("H001", contract)["error"]
    assert "reason" in onboarding.skip_step("H001", accounts, " ")["error"]                  # skipping needs a reason
    assert onboarding.skip_step("H001", accounts, "Uses a personal laptop, no account yet")["status"] == "skipped"
    assert "reason" in onboarding.reopen_step("H001", contract, "")["error"]
    assert "not done" in onboarding.reopen_step("H001", iban, "oops")["error"]               # only done or skipped steps
    assert onboarding.checklist("H001", emp_id)["progress"] == {"done": 1, "skipped": 1, "total": 8, "complete": False}
    assert onboarding.reopen_step("H001", contract, "Wrong person's contract")["status"] == "todo"
    reopened = step(emp_id, "contract")
    assert reopened["status"] == "todo" and reopened["done_by_name"] is None and reopened["note"] == ""
    log = [(c["field"], c["old_value"], c["new_value"], c["reason"], c["changed_by"])
           for c in e.history("H001", emp_id)["changes"] if c["field"].startswith("onboarding:")]
    assert log == [("onboarding: Employment contract signed", "done", "todo", "Wrong person's contract", "Narmadha"),
                   ("onboarding: Email and tool accounts created", "todo", "skipped",
                    "Uses a personal laptop, no account yet", "Narmadha"),
                   ("onboarding: Employment contract signed", "todo", "done", "Signed copy received by post", "Narmadha")]


@test
def the_checklist_is_complete_when_nothing_is_left_to_do():
    fresh()
    emp_id = hire()
    for i, s in enumerate(onboarding.checklist("H001", emp_id)["steps"]):
        (onboarding.skip_step("H001", s["id"], "Not needed") if i % 2 else onboarding.complete_step("H001", s["id"]))
    assert onboarding.checklist("H001", emp_id)["progress"] == {"done": 4, "skipped": 4, "total": 8, "complete": True}
    assert needs_you.waiting_count("H001") == 0 and needs_you.inbox("M001")["onboarding_steps"] == []


@test
def a_manager_completes_only_the_manager_steps_of_their_own_reports():
    fresh()
    emp_id = hire()                                                # Anu reports to Lucas (M001)
    equipment, contract = step(emp_id, "equipment")["id"], step(emp_id, "contract")["id"]
    assert "error" in onboarding.complete_step("E001", equipment)  # an employee
    assert "error" in onboarding.complete_step("C001", equipment)  # a manager, but not Anu's
    assert "error" in onboarding.complete_step(emp_id, equipment)  # the new hire themselves
    assert "error" in onboarding.complete_step("M001", contract)   # Lucas, but it is an HR step
    assert "Only HR" in onboarding.skip_step("M001", equipment, "no")["error"]
    assert onboarding.complete_step("M001", equipment)["status"] == "done"
    assert step(emp_id, "equipment")["done_by_name"] == "Lucas"
    assert "Only HR" in onboarding.reopen_step("M001", equipment, "no")["error"]
    assert "error" in onboarding.complete_step("nobody", 999)


@test
def the_needs_you_inbox_lists_the_steps_each_person_owns_most_urgent_first():
    fresh()
    emp_id = hire(30)
    hr_before, hr_total = needs_you.inbox("H001"), needs_you.waiting_count("H001")
    assert [s["step_title"] for s in hr_before["onboarding_steps"]] == [
        "Employment contract signed", "Email and tool accounts created", "DPAE filed"]
    assert all(s["employee_name"] == "Anu" and s["owner"] == "hr" for s in hr_before["onboarding_steps"])
    lucas = needs_you.inbox("M001")
    assert [s["step_title"] for s in lucas["onboarding_steps"]] == ["Laptop and equipment ready", "Welcome message sent"]
    assert lucas["waiting_on_you"] == len(lucas["leave_requests"]) + 2 and needs_you.waiting_count("M001") == lucas["waiting_on_you"]
    assert needs_you.inbox("C001")["onboarding_steps"] == []        # the CEO is not Anu's manager
    assert "error" in needs_you.inbox("E001")                         # employees have no inbox, and no steps of their own
    assert hr_before["waiting_on_you"] == hr_total == len(hr_before["hr_requests"]) + 3
    # doing a step takes it out of the inbox and the count
    onboarding.complete_step("H001", step(emp_id, "dpae")["id"])
    assert needs_you.waiting_count("H001") == hr_total - 1
    assert [s["step_title"] for s in needs_you.inbox("H001")["onboarding_steps"]][-1] == "Email and tool accounts created"
    # the assistant gets the same list
    seen = needs_you.for_assistant("M001")["onboarding_steps"]
    assert [(s["employee_name"], s["step"]) for s in seen] == [("Anu", "Laptop and equipment ready"), ("Anu", "Welcome message sent")]


@test
def a_step_past_its_due_date_is_marked_overdue_and_comes_first():
    fresh()
    hire(30)
    later = date.today() + timedelta(days=40)                         # a step is due, and nobody did it
    items = onboarding.open_steps("H001", today=later)
    assert items and all(i["overdue"] for i in items)
    assert [i["due_date"] for i in items] == sorted(i["due_date"] for i in items)
    assert not any(i["overdue"] for i in onboarding.open_steps("H001"))


@test
def people_who_have_left_are_not_waiting_on_anyone_and_their_steps_cannot_change():
    fresh()
    emp_id = hire()
    contract = step(emp_id, "contract")["id"]
    conn = db.get_conn()
    with conn:
        conn.execute("UPDATE profiles SET end_date = ? WHERE emp_id = ?", (in_days(-1), emp_id))     # the offer fell through
    conn.close()
    assert onboarding.open_steps("H001") == [] and onboarding.open_steps("M001") == []
    assert "no longer works here" in onboarding.complete_step("H001", contract)["error"]


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