# employees_tests.py: checks the employee-record rules. No AI, no API keys, runs in CI.
#   python employees_tests.py
import os
import sys
import tempfile
import re
from datetime import date, datetime, timedelta

import db
db.DB_PATH = os.path.join(tempfile.mkdtemp(), "employees_test.db")   # throwaway database
os.environ["HRM_FILES_DIR"] = tempfile.mkdtemp()                       # and a throwaway folder for the documents
os.environ["HRM_DB"] = db.DB_PATH          # so demo_data.py can't switch back to web_demo.db
import demo_data
import employees as e
import checkins
import careers
import collective_agreement
import contracts
import documents
import employee_files
import jobs
import needs_you
import onboarding
import pdf_text
import outbox
import policy_gaps
import retention
import policy_store
import tools
import welcome

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
        conn.execute("DELETE FROM notifications")
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
    assert sorted(c["new_value"] for c in log) == ["created", "started with 9 steps"]   # the record, then the checklist


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
        ("welcome", "manager", "todo"), ("dpae", "hr", "todo"), ("checkin", "manager", "todo")]
    assert box["progress"] == {"done": 0, "skipped": 0, "total": 9, "complete": False}
    assert box["name"] == "Anu" and box["start_date"] == in_days(30) and box["manager_name"] == "Lucas"
    # due dates count back from the start date; the DPAE is the last thing before day one
    assert step(emp_id, "id")["due_date"] == (start - timedelta(days=7)).isoformat()
    assert step(emp_id, "dpae")["due_date"] == (start - timedelta(days=1)).isoformat()
    assert all(s["due_date"] < in_days(30) for s in box["steps"] if s["step_key"] != "checkin") and not any(s["overdue"] for s in box["steps"])
    assert step(emp_id, "checkin")["due_date"] == (start + timedelta(days=7)).isoformat()      # a week into the job: the first-week check-in
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
    assert all(in_days(0) <= d < in_days(3) for k, d in dues.items() if k != "checkin") and dues["checkin"] == in_days(10)
    assert not any(s["overdue"] for s in onboarding.checklist("H001", emp_id)["steps"])


@test
def the_template_covers_what_a_new_hire_needs():
    keys = [t[0] for t in onboarding.TEMPLATE]
    assert keys == ["contract", "id", "iban", "social_security", "equipment", "accounts", "welcome", "dpae", "checkin"]
    assert len(set(keys)) == len(keys)
    assert {t[2] for t in onboarding.TEMPLATE} == {"new_hire", "hr", "manager"}
    assert all(t[3] >= 1 for t in onboarding.TEMPLATE if t[0] != "checkin")      # everything is due before day one...
    assert [t[3] for t in onboarding.TEMPLATE if t[0] == "checkin"] == [-7]       # ...except the first-week check-in, a week after


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
    assert onboarding.checklist("H001", emp_id)["progress"] == {"done": 1, "skipped": 1, "total": 9, "complete": False}
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
    assert onboarding.checklist("H001", emp_id)["progress"] == {"done": 5, "skipped": 4, "total": 9, "complete": True}
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


# ---------- the new hire's welcome link, the outbox, and the new hire's own view ----------

NOW = datetime(2030, 1, 7, 9, 0, 0)
APP = "http://app.test"


def issue(emp_id, now=NOW):
    r = welcome.issue_link("H001", emp_id, APP, now=now)
    assert r.get("success"), r
    body = outbox.messages("H001")[0]["body"]
    return re.search(r"/welcome/([A-Za-z0-9_-]+)", body).group(1), r["code"], r


def events(emp_id):
    conn = db.get_conn()
    rows = [r["event"] for r in conn.execute("SELECT event FROM welcome_link_events WHERE emp_id = ? ORDER BY id", (emp_id,))]
    conn.close()
    return rows


@test
def only_hr_issues_a_welcome_link_and_only_for_someone_with_a_checklist():
    fresh()
    emp_id = hire()
    for who in ("E001", "M001", emp_id, "nobody"):
        assert "Only HR" in welcome.issue_link(who, emp_id, APP, now=NOW)["error"], who
    assert "no onboarding checklist" in welcome.issue_link("H001", "E001", APP, now=NOW)["error"]     # started long ago
    assert "No current person" in welcome.issue_link("H001", "E999", APP, now=NOW)["error"]
    r = welcome.issue_link("H001", emp_id, APP, now=NOW)
    assert r["success"] and re.fullmatch(r"\d{6}", r["code"]) and r["to"] == "anu@cognivexai.eu"
    assert r["expires_at"] == "2030-01-21 09:00:00"                                                  # 14 days


@test
def the_token_is_stored_only_as_a_hash_and_the_code_never_in_the_email():
    fresh()
    emp_id = hire()
    token, code, _ = issue(emp_id)
    assert len(token) >= 40                                                  # random, long enough to never be guessed
    conn = db.get_conn()
    stored = " ".join(str(v) for row in conn.execute("SELECT * FROM welcome_links").fetchall() for v in tuple(row))
    conn.close()
    assert token not in stored and code not in stored.split()               # only hashes are kept
    mail = outbox.messages("H001")[0]
    assert f"{APP}/welcome/{token}" in mail["body"] and code not in mail["body"]      # the code goes another way
    assert mail["to_email"] == "anu@cognivexai.eu" and mail["kind"] == "welcome_link" and mail["sent_at"] is None
    assert token != issue(emp_id)[0]                                         # every link is different


@test
def the_right_token_and_code_open_the_link_and_five_wrong_codes_lock_it():
    fresh()
    emp_id = hire()
    token, code, _ = issue(emp_id)
    assert welcome.link_state(token, now=NOW) == {"state": "ready"}
    assert welcome.link_state("not-a-token", now=NOW) == {"state": "invalid"}
    assert welcome.open_link("not-a-token", code, now=NOW)["state"] == "invalid"
    wrong = "000000" if code != "000000" else "111111"
    assert "The code has 6 digits" in welcome.open_link(token, "12ab", now=NOW)["error"]        # a typo is not a guess
    assert "4 tries left" in welcome.open_link(token, wrong, now=NOW)["error"]
    assert "3 tries left" in welcome.open_link(token, wrong, now=NOW)["error"]
    assert welcome.open_link(token, f" {code[:3]} {code[3:]} ", now=NOW) == {"emp_id": emp_id}   # spaces are fine; resets the count
    for tries_left in (4, 3, 2, 1):
        assert f"{tries_left} {'try' if tries_left == 1 else 'tries'} left" in welcome.open_link(token, wrong, now=NOW)["error"]
    locked = welcome.open_link(token, wrong, now=NOW)
    assert locked["state"] == "locked" and "Ask HR" in locked["error"]
    assert welcome.open_link(token, code, now=NOW)["state"] == "locked"      # even the right code, now
    assert welcome.link_state(token, now=NOW) == {"state": "locked"}
    assert welcome.link_status("H001", emp_id, now=NOW)["wrong_codes"] == 5
    assert events(emp_id).count("wrong_code") == 7 and "locked" in events(emp_id) and "opened" in events(emp_id)


@test
def a_link_works_for_14_days_and_a_new_link_replaces_the_old_one():
    fresh()
    emp_id = hire()
    token, code, _ = issue(emp_id)
    assert welcome.open_link(token, code, now=NOW + timedelta(days=13, hours=23)) == {"emp_id": emp_id}
    expired = welcome.open_link(token, code, now=NOW + timedelta(days=14))
    assert expired["state"] == "expired" and "expired" in expired["error"]
    new_token, new_code, _ = issue(emp_id, now=NOW + timedelta(days=15))
    assert welcome.open_link(token, code, now=NOW + timedelta(days=15))["state"] == "revoked"      # replaced
    assert welcome.open_link(new_token, new_code, now=NOW + timedelta(days=15)) == {"emp_id": emp_id}
    assert welcome.link_status("H001", emp_id, now=NOW + timedelta(days=15))["state"] == "ready"


@test
def a_link_for_someone_who_left_does_not_open_and_hr_can_cancel_one_with_a_reason():
    fresh()
    emp_id = hire()
    token, code, _ = issue(emp_id)
    assert "reason" in welcome.revoke_link("H001", emp_id, " ", now=NOW)["error"]
    assert "Only HR" in welcome.revoke_link("M001", emp_id, "x", now=NOW)["error"]
    assert welcome.revoke_link("H001", emp_id, "Offer withdrawn", now=NOW)["success"]
    assert welcome.open_link(token, code, now=NOW)["state"] == "revoked"
    assert "no active link" in welcome.revoke_link("H001", emp_id, "again", now=NOW)["error"]
    other = hire(email="bea@cognivexai.eu", name="Bea")
    token2, code2, _ = issue(other)
    conn = db.get_conn()
    with conn:
        conn.execute("UPDATE profiles SET end_date = ? WHERE emp_id = ?", (in_days(-1), other))      # the offer fell through
    conn.close()
    assert welcome.open_link(token2, code2, now=NOW)["state"] == "invalid"


@test
def the_email_waits_in_the_outbox_only_hr_sees_it_and_the_link_is_scrubbed_once_sent():
    fresh()
    emp_id = hire()
    token, code, issued = issue(emp_id)
    for who in ("E001", "M001", emp_id):
        assert "Only HR" in outbox.messages(who)["error"] and "Only HR" in outbox.mark_sent(who, issued["outbox_id"])["error"]
    assert "No such message" in outbox.mark_sent("H001", 9999)["error"]
    assert outbox.mark_sent("H001", issued["outbox_id"])["success"]
    mail = outbox.messages("H001")[0]
    assert mail["sent_at"] and mail["sent_by_name"] == "Narmadha"
    assert token not in mail["body"] and "[link removed after sending]" in mail["body"]       # no working link is kept
    assert "already marked" in outbox.mark_sent("H001", issued["outbox_id"])["error"]
    assert welcome.open_link(token, code, now=NOW) == {"emp_id": emp_id}                     # the person's link still works
    assert outbox.messages("H001", unsent_only=True) == []


@test
def issuing_cancelling_and_sending_are_in_the_change_history():
    fresh()
    emp_id = hire()
    _, _, issued = issue(emp_id)
    outbox.mark_sent("H001", issued["outbox_id"])
    welcome.revoke_link("H001", emp_id, "Wrong email address", now=NOW)
    log = [(c["field"], c["new_value"], c["reason"], c["changed_by"]) for c in e.history("H001", emp_id)["changes"]
           if c["field"] in ("welcome link", "outbox")]
    assert ("welcome link", "cancelled", "Wrong email address", "Narmadha") in log
    assert ("outbox", "marked as sent: Welcome to CognivexAI: your personal page", "Copied and sent by HR", "Narmadha") in log
    assert any(f == "welcome link" and v.startswith("issued, valid until") for f, v, _, _ in log)


@test
def a_new_hire_sees_only_their_own_checklist_without_notes_or_names():
    fresh()
    emp_id = hire()
    box = onboarding.my_checklist(emp_id)
    assert box["name"] == "Anu" and box["manager"] == "Lucas" and box["total"] == 9 and box["done"] == 0
    assert box["next_for_you"] == "Identity document"
    assert {s["who"] for s in box["steps"]} == {"you", "HR", "your manager"}
    base = {"key", "step", "what", "who", "due_date", "status", "overdue"}
    assert all(set(s) == base | ({"document"} if s["key"] in ("id", "iban", "social_security") else set()) for s in box["steps"])   # no notes, no names
    assert next(s for s in box["steps"] if s["key"] == "iban")["document"] == {"status": "not sent yet"}
    id_step = step(emp_id, "id")["id"]
    onboarding.complete_step("H001", id_step, "Passport copy checked, number 12AB34567")
    box = onboarding.my_checklist(emp_id)
    assert box["next_for_you"] == "Bank details (IBAN)" and box["done"] == 1
    assert "12AB34567" not in str(box) and "Narmadha" not in str(box)                        # HR's note and name stay with HR
    assert "no onboarding checklist" in onboarding.my_checklist("E001")["error"]            # started long ago
    assert "error" in onboarding.my_checklist("nobody")


# ---------- documents: the checks around Claude, who opens a file, and cancelling a hire ----------

GOOD_ID = {"readable": True, "looks_off": False, "document_type": "passport", "holder_name": "Anu Patel", "expiry_date": "2099-01-01"}
GOOD_BANK = {"readable": True, "looks_off": False, "holder_name": "PATEL Anu", "iban": "FR76 3000 6000 0112 3456 7890 189"}
FILE = pdf_text.make_pdf([("p", "a document")])


def reads(fields):
    # a stand-in for Claude: it "reads" exactly these fields, whatever the file is
    return lambda kind, data, file_type: dict(fields)


def stored_files():
    return sorted(os.listdir(documents.files_dir())) if os.path.isdir(documents.files_dir()) else []


def send(emp_id, step_key, fields, data=FILE, name="scan.pdf"):
    documents.READER = reads(fields)
    try:
        return documents.submit(emp_id, step_key, data, name)
    finally:
        documents.READER = None


def clear_files():
    for f in stored_files():
        os.remove(os.path.join(documents.files_dir(), f))


@test
def a_good_document_is_read_checked_kept_privately_and_waits_for_the_new_hire_to_confirm():
    fresh(); clear_files()
    emp_id = hire()
    r = send(emp_id, "id", GOOD_ID, name="my passport (1).pdf")
    assert r["accepted"] is True and r["flagged"] is False
    assert ("Name on the document", "Anu Patel") in [(x["label"], x["value"]) for x in r["read"]]
    files = stored_files()
    assert len(files) == 1 and re.fullmatch(r"[0-9a-f]{32}\.pdf", files[0])                  # a random name, never the person's
    assert os.stat(os.path.join(documents.files_dir(), files[0])).st_mode & 0o777 == 0o600   # only the owner of the process reads it
    assert step(emp_id, "id")["status"] == "todo"                                              # not done until they confirm
    seen = onboarding.my_checklist(emp_id)
    assert next(s for s in seen["steps"] if s["key"] == "id")["document"]["status"] == "waiting for you to confirm"
    assert "cannot tell whether a document is genuine" in next(s for s in seen["steps"] if s["key"] == "id")["document"]["what_was_checked"]
    assert documents.confirm(emp_id, r["document_id"]) == {"success": True}
    assert step(emp_id, "id")["status"] == "done"
    box = onboarding.my_checklist(emp_id)
    assert next(s for s in box["steps"] if s["key"] == "id")["document"]["status"] == "received" and box["next_for_you"] == "Bank details (IBAN)"
    assert "error" in documents.confirm(emp_id, r["document_id"])                              # not twice
    log = [(c["field"], c["new_value"]) for c in e.history("H001", emp_id)["changes"] if c["field"].startswith("document:")]
    assert ("document: identity document", "confirmed") in log and all("FR76" not in str(v) for _, v in log)     # no numbers in the log


@test
def a_bad_iban_is_refused_by_code_and_nothing_is_kept():
    fresh(); clear_files()
    emp_id = hire()
    r = send(emp_id, "iban", {**GOOD_BANK, "iban": "FR76 3000 6000 0112 3456 7890 188"})       # one digit off
    assert r["accepted"] is False and any("check digits" in p for p in r["problems"])
    assert stored_files() == []                                                                # no file
    conn = db.get_conn()
    row = dict(conn.execute("SELECT * FROM documents WHERE id = ?", (r["document_id"],)).fetchone())
    conn.close()
    assert row["status"] == "rejected" and row["file_name"] is None and row["fields_json"] is None   # and not what was read
    assert step(emp_id, "iban")["status"] == "todo"
    shown = next(s for s in onboarding.my_checklist(emp_id)["steps"] if s["key"] == "iban")["document"]
    assert shown["status"] == "needs resending" and any("check digits" in p for p in shown["problems"])
    assert send(emp_id, "iban", GOOD_BANK)["accepted"] is True                                 # sent again, it passes
    assert next(s for s in onboarding.my_checklist(emp_id)["steps"] if s["key"] == "iban")["document"]["status"] == "waiting for you to confirm"


@test
def someone_elses_document_an_expired_one_and_an_unreadable_one_are_all_sent_back():
    fresh(); clear_files()
    emp_id = hire()
    wrong = send(emp_id, "id", {**GOOD_ID, "holder_name": "Lea Durand"})
    assert wrong["accepted"] is False and "doesn't match your name" in wrong["problems"][0]
    expired = send(emp_id, "id", {**GOOD_ID, "expiry_date": "2020-01-01"})
    assert expired["accepted"] is False and "expired" in expired["problems"][0]
    assert send(emp_id, "id", {"readable": False, "looks_off": False})["problems"][0].startswith("I couldn't read the document")
    documents.READER = lambda *a: (_ for _ in ()).throw(RuntimeError("the reader is down"))    # an API failure is "unreadable" too
    try:
        assert documents.submit(emp_id, "id", FILE, "x.pdf")["accepted"] is False
    finally:
        documents.READER = None
    assert stored_files() == [] and step(emp_id, "id")["status"] == "todo"


@test
def the_file_type_the_size_and_the_step_are_checked_before_claude_reads_anything():
    fresh(); clear_files()
    emp_id = hire()
    calls = []
    documents.READER = lambda *a: calls.append(1) or dict(GOOD_ID)
    try:
        assert "PDF, JPG or PNG" in documents.submit(emp_id, "id", b"MZ a program", "passport.pdf")["error"]       # the name lies
        assert "too big" in documents.submit(emp_id, "id", b"%PDF-" + b"0" * documents.MAX_BYTES, "big.pdf")["error"]
        assert "empty" in documents.submit(emp_id, "id", b"", "x.pdf")["error"]
        assert "doesn't take a document" in documents.submit(emp_id, "welcome", FILE, "x.pdf")["error"]
        assert "doesn't take a document" in documents.submit(emp_id, "contract", FILE, "x.pdf")["error"]           # an HR step
        assert documents.submit("E001", "id", FILE, "x.pdf")["error"] == "There is no such step in your checklist."     # started long ago
        assert documents.submit("nobody", "id", FILE, "x.pdf")["error"] == "There is no onboarding for you."
        assert calls == []                                                                                        # Claude was never asked
        assert documents.submit(emp_id, "id", FILE, "ok.jpg")["accepted"] is True and calls == [1]                # the name doesn't matter, the file does
        onboarding.complete_step("H001", step(emp_id, "iban")["id"], "HR did it")
        assert "already done" in documents.submit(emp_id, "iban", FILE, "x.pdf")["error"]
    finally:
        documents.READER = None


@test
def a_document_that_waits_for_confirmation_can_be_asked_for_again_with_what_was_read():
    fresh(); clear_files()
    emp_id, other = hire(), hire(email="bea@cognivexai.eu", name="Bea Roy")
    assert documents.waiting_for(emp_id) == []
    first = send(emp_id, "id", GOOD_ID)
    send(other, "iban", {**GOOD_BANK, "holder_name": "Bea Roy"})
    waiting = documents.waiting_for(emp_id)
    assert [(w["document_id"], w["step_key"]) for w in waiting] == [(first["document_id"], "id")]
    assert ("Name on the document", "Anu Patel") in [(r["label"], r["value"]) for r in waiting[0]["read"]]      # in full: it is their own
    assert [w["step_key"] for w in documents.waiting_for(other)] == ["iban"]                                    # each person sees only theirs
    documents.confirm(emp_id, first["document_id"])
    assert documents.waiting_for(emp_id) == []                                                                  # confirmed: no longer waiting
    again = send(emp_id, "iban", GOOD_BANK)
    documents.decline(emp_id, again["document_id"])
    assert documents.waiting_for(emp_id) == []                                                                  # declined: gone too
    assert documents.waiting_for("nobody") == []


@test
def a_new_file_replaces_one_that_waits_and_saying_it_is_wrong_removes_it():
    fresh(); clear_files()
    emp_id, other = hire(), hire(email="bea@cognivexai.eu", name="Bea Roy")
    first = send(emp_id, "id", GOOD_ID)
    second = send(emp_id, "id", GOOD_ID)
    assert len(stored_files()) == 1                                                            # the first one is gone
    assert "isn't waiting" in documents.confirm(emp_id, first["document_id"])["error"]
    assert "No such document" in documents.confirm(other, second["document_id"])["error"]      # not someone else's
    assert "No such document" in documents.decline(other, second["document_id"])["error"]
    assert documents.decline(emp_id, second["document_id"]) == {"success": True}
    assert stored_files() == [] and step(emp_id, "id")["status"] == "todo"
    assert next(s for s in onboarding.my_checklist(emp_id)["steps"] if s["key"] == "id")["document"]["status"] == "not sent yet"


@test
def only_the_person_and_hr_open_a_file_and_every_opening_is_logged():
    fresh(); clear_files()
    emp_id, other = hire(), hire(email="bea@cognivexai.eu", name="Bea Roy")
    doc = send(emp_id, "id", GOOD_ID)["document_id"]
    documents.confirm(emp_id, doc)
    for who in ("M001", "E001", "C001", other, "nobody"):                                       # the manager too
        assert documents.open_file(who, doc) == {"error": "No such document."}, who             # and "not yours" sounds like "missing"
    assert documents.open_file("H001", 9999) == {"error": "No such document."}
    mine, hr = documents.open_file(emp_id, doc), documents.open_file("H001", doc)
    assert open(hr["path"], "rb").read() == FILE and hr["media_type"] == "application/pdf" and mine["path"] == hr["path"]
    assert hr["filename"] == "identity-document.pdf"
    listing = documents.hr_documents("H001", emp_id)
    assert [v["viewer_name"] for v in listing[0]["views"]] == ["Narmadha", "Anu"]                # newest first
    assert "Only HR" in documents.hr_documents("M001", emp_id)["error"] and "Only HR" in documents.hr_documents(emp_id, emp_id)["error"]


@test
def a_cancelled_hire_loses_access_and_every_file_and_what_was_read_is_deleted():
    fresh(); clear_files()
    emp_id = hire()
    token, code, issued = issue(emp_id)
    done = send(emp_id, "id", GOOD_ID)["document_id"]; documents.confirm(emp_id, done)
    send(emp_id, "iban", GOOD_BANK)                                                              # one still waiting
    assert len(stored_files()) == 2
    for who in ("M001", "E001", emp_id):
        assert "Only HR" in onboarding.cancel(who, emp_id, "x")["error"]
    assert "reason" in onboarding.cancel("H001", emp_id, " ")["error"]
    assert "no onboarding" in onboarding.cancel("H001", "E001", "x")["error"]
    assert onboarding.cancel("H001", emp_id, "Offer withdrawn") == {"success": True, "files_deleted": 2}
    assert stored_files() == []                                                                   # gone from disk
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute("SELECT status, file_name, fields_json, problems_json FROM documents WHERE emp_id = ?", (emp_id,))]
    conn.close()
    assert rows and all(r["status"] == "deleted" and r["file_name"] is None and r["fields_json"] is None for r in rows)
    assert welcome.open_link(token, code, now=NOW)["state"] == "revoked" and not e.is_active(emp_id)    # no access, no link
    assert "[link removed after sending]" in outbox.messages("H001")[0]["body"] and token not in outbox.messages("H001")[0]["body"]
    assert onboarding.open_steps("H001") == [] and onboarding.open_steps("M001") == []           # nobody waits on a cancelled hire
    log = [(c["field"], c["new_value"], c["reason"]) for c in e.history("H001", emp_id)["changes"]]
    assert ("onboarding", "cancelled", "Offer withdrawn") in log and any(f == "end_date" and "Hire cancelled" in r for f, _, r in log)
    assert onboarding.cancel("H001", emp_id, "again")["error"] == "Anu's hire was already cancelled."
    started = hire(0, email="old@cognivexai.eu", name="Old Hand")                                 # started today: no checklist at all
    assert "no onboarding" in onboarding.cancel("H001", started, "x")["error"]


@test
def a_document_something_looked_off_about_goes_to_hr_and_stays_until_a_person_has_looked():
    fresh(); clear_files()
    emp_id = hire()
    r = send(emp_id, "id", {**GOOD_ID, "looks_off": True, "looks_off_reason": "The name's font differs from the rest of the page."})
    assert r["accepted"] is True and r["flagged"] is True
    waiting = needs_you.waiting_count("H001")
    flags = needs_you.inbox("H001")["document_flags"]
    assert flags == [{"document_id": r["document_id"], "employee_id": emp_id, "employee_name": "Anu", "label": "identity document",
                      "reason": "The name's font differs from the rest of the page."}]
    assert needs_you.inbox("M001")["document_flags"] == [] and "error" in needs_you.inbox("E001")                # only HR
    assert needs_you.for_assistant("H001")["document_flags"][0]["reason"].startswith("The name's font")
    assert "font" not in str(onboarding.my_checklist(emp_id)) and "looks off" not in str(onboarding.my_checklist(emp_id))   # the person is not told
    assert "Write what you found" in documents.mark_reviewed("H001", r["document_id"], " ")["error"]
    assert "Only HR" in documents.mark_reviewed("M001", r["document_id"], "ok")["error"]
    assert documents.mark_reviewed("H001", r["document_id"], "Looked at the original: fine")["success"]
    assert needs_you.inbox("H001")["document_flags"] == [] and needs_you.waiting_count("H001") == waiting - 1
    assert "already looked" in documents.mark_reviewed("H001", r["document_id"], "again")["error"]
    assert documents.hr_documents("H001", emp_id)[0]["flag_reviewed_by"] == "Narmadha"


# ---------- the contract: drafting, the law, who approves, and what leaves the app ----------

TERMS = {"salary_eur": "3200", "category": "executive", "trial": 4}


def drafted(emp_id=None, **changes):
    emp_id = emp_id or hire()
    r = contracts.create_draft("H001", emp_id, {**TERMS, **changes})
    assert r.get("success"), r
    return emp_id, r["draft_id"]


def approved():
    emp_id, draft = drafted()
    assert contracts.submit("H001", draft)["success"]
    assert contracts.decide("M001", draft, "approve")["status"] == "approved"
    return emp_id, draft


@test
def hr_drafts_a_contract_from_the_record_and_the_law_limits_the_trial_period():
    fresh()
    emp_id = hire()
    for who in ("E001", "M001", emp_id, "nobody"):
        assert "Only HR" in contracts.create_draft(who, emp_id, TERMS)["error"], who
    long_trial = contracts.create_draft("H001", emp_id, {**TERMS, "trial": 5})["error"]
    assert "L1221-19" in long_trial and "4 months" in long_trial                                  # the law is cited, with the number
    assert "L1221-19" in contracts.create_draft("H001", emp_id, {**TERMS, "category": "employee", "trial": 3})["error"]
    assert "salary" in contracts.create_draft("H001", emp_id, {**TERMS, "salary_eur": ""})["error"]
    assert "no onboarding" in contracts.create_draft("H001", "E001", TERMS)["error"]               # started long ago
    assert "No current person" in contracts.create_draft("H001", "E999", TERMS)["error"]
    intern = hire(email="int@cognivexai.eu", name="Ines Roy", contract_type="Internship")
    assert "isn't drafted here" in contracts.create_draft("H001", intern, TERMS)["error"]
    r = contracts.create_draft("H001", emp_id, TERMS)
    assert r["success"] and "already has a contract" in contracts.create_draft("H001", emp_id, TERMS)["error"]
    view = contracts.hr_view("H001", emp_id)
    assert view["status"] == "draft" and view["fields"] == {"salary_eur": 3200.0, "weekly_hours": 35, "category": "executive", "trial": 4}
    assert view["person"]["title"] == "QA Engineer" and view["person"]["contract_type"] == "CDI"   # from the record, not the form
    assert view["approver"] == "Lucas" and view["created_by"] == "Narmadha" and "Only HR" in contracts.hr_view("M001", emp_id)["error"]
    cdd = hire(email="cdd@cognivexai.eu", name="Cleo Fox", contract_type="CDD")
    problems = contracts.create_draft("H001", cdd, {"salary_eur": 2500, "trial": 12, "end_date": in_days(30 + 89), "cdd_reason": "Remplacement"})
    assert problems["success"] is True                                                             # 89 days: 12 days of trial is the maximum
    assert "L1242-10" in contracts.update_draft("H001", problems["draft_id"], {"salary_eur": 2500, "trial": 13, "end_date": in_days(30 + 89), "cdd_reason": "x"})["error"]


@test
def a_draft_goes_to_the_new_hires_own_manager_who_approves_or_rejects_it_with_a_reason():
    fresh()
    emp_id, draft = drafted()
    for who in ("E001", "M001", "C001"):
        assert "Only HR" in contracts.submit(who, draft)["error"], who
    assert "nothing to decide" in contracts.decide("M001", draft, "approve")["error"]               # nobody decides before it is sent
    for who in ("H001", "C001", "E001", emp_id):
        assert "Only the new hire's manager" in contracts.decide(who, draft, "approve")["error"], who
    assert contracts.submit("H001", draft) == {"success": True, "approver": "Lucas"}
    assert "is waiting for approval" in contracts.submit("H001", draft)["error"]                  # not twice
    for who in ("H001", "C001", "E001", emp_id, "nobody"):                                         # HR, another manager, the hire, anyone
        assert "Only the new hire's manager" in contracts.decide(who, draft, "approve")["error"], who
    assert "Give a reason" in contracts.decide("M001", draft, "reject", " ")["error"]
    assert "approve or reject" in contracts.decide("M001", draft, "maybe")["error"]
    assert contracts.decide("M001", draft, "reject", "The salary should be 3400")["status"] == "rejected"
    view = contracts.hr_view("H001", emp_id)
    assert view["status"] == "rejected" and view["decision_note"] == "The salary should be 3400" and view["decided_by"] == "Lucas"
    assert "rejected" in contracts.submit("H001", draft)["error"]                                  # HR has to change it first
    assert contracts.update_draft("H001", draft, {**TERMS, "salary_eur": "3400"})["success"]
    assert contracts.hr_view("H001", emp_id)["status"] == "draft"
    contracts.submit("H001", draft)
    assert contracts.decide("M001", draft, "approve")["status"] == "approved"
    assert "can't be changed" in contracts.update_draft("H001", draft, TERMS)["error"]              # approved is final
    log = [(c["field"], c["old_value"], c["new_value"], c["changed_by"]) for c in e.history("H001", emp_id)["changes"] if c["field"] == "contract"]
    assert ("contract", "pending_approval", "rejected", "Lucas") in log and ("contract", "pending_approval", "approved", "Lucas") in log


@test
def the_pdf_only_exists_after_the_approval_and_every_download_is_logged():
    fresh()
    emp_id, draft = drafted()
    assert "only be downloaded once it is approved" in contracts.pdf_for("H001", draft)["error"]   # a draft
    contracts.submit("H001", draft)
    assert "waiting for approval" in contracts.pdf_for("H001", draft)["error"]                    # not even when sent
    contracts.decide("M001", draft, "reject", "Change it")
    assert "rejected" in contracts.pdf_for("H001", draft)["error"]
    contracts.update_draft("H001", draft, TERMS); contracts.submit("H001", draft); contracts.decide("M001", draft, "approve")
    for who in ("M001", "C001", "E001", emp_id):                                                  # the approver cannot download it either
        assert "Only HR" in contracts.pdf_for(who, draft)["error"], who
    pdf = contracts.pdf_for("H001", draft)
    assert pdf["pdf"].startswith(b"%PDF-1.4") and b"CONTRAT DE TRAVAIL" in pdf["pdf"] and pdf["filename"] == f"contrat-anu-{draft}.pdf"
    assert b"approuv" in pdf["pdf"]                                                               # who approved it, on the document
    contracts.pdf_for("H001", draft)
    assert len(contracts.hr_view("H001", emp_id)["downloads"]) == 2 and contracts.hr_view("H001", emp_id)["can_download"] is True
    assert contracts.preview("H001", draft)["title"].startswith("CONTRAT DE TRAVAIL") and contracts.preview("M001", draft)["status"] == "approved"
    for who in ("C001", "E001", emp_id):
        assert contracts.preview(who, draft) == {"error": "No such contract."}, who               # only HR and the approving manager read it


@test
def signing_needs_an_approved_contract_and_completes_the_checklist_step():
    fresh()
    emp_id, draft = drafted()
    assert "Only an approved contract" in contracts.mark_signed("H001", draft, in_days(0))["error"]
    contracts.submit("H001", draft)
    assert "Only an approved contract" in contracts.mark_signed("H001", draft, in_days(0))["error"]
    contracts.decide("M001", draft, "approve")
    assert "Only HR" in contracts.mark_signed("M001", draft, in_days(0))["error"]
    assert "future" in contracts.mark_signed("H001", draft, in_days(3))["error"]
    assert "before it was approved" in contracts.mark_signed("H001", draft, in_days(-5))["error"]
    assert "Give the date" in contracts.mark_signed("H001", draft, "yesterday")["error"]
    assert step(emp_id, "contract")["status"] == "todo"
    assert contracts.mark_signed("H001", draft, in_days(0), "Both copies received") == {"success": True}
    assert step(emp_id, "contract")["status"] == "done" and "Contract signed" in step(emp_id, "contract")["note"]       # the checklist follows
    view = contracts.hr_view("H001", emp_id)
    assert view["status"] == "signed" and view["signed_on"] == in_days(0) and view["signed_by"] == "Narmadha"
    assert "signed" in contracts.cancel_draft("H001", draft, "oops")["error"] and "can't be changed" in contracts.update_draft("H001", draft, TERMS)["error"]
    assert contracts.pdf_for("H001", draft)["pdf"].startswith(b"%PDF")                              # still downloadable


@test
def a_record_that_changed_after_the_draft_makes_it_stale_and_blocks_sending_approving_and_downloading():
    fresh()
    emp_id, draft = drafted()
    e.update_employee("H001", emp_id, {"title": "Lead QA Engineer"}, "Promotion before the start")
    assert contracts.hr_view("H001", emp_id)["stale"] is True
    assert "record changed" in contracts.submit("H001", draft)["error"]
    assert contracts.update_draft("H001", draft, TERMS)["success"]                                  # saving again brings it up to date
    assert contracts.hr_view("H001", emp_id)["stale"] is False and contracts.hr_view("H001", emp_id)["person"]["title"] == "Lead QA Engineer"
    contracts.submit("H001", draft)
    e.update_employee("H001", emp_id, {"title": "Principal QA Engineer"}, "Another change")
    assert "record changed" in contracts.decide("M001", draft, "approve")["error"]                  # the manager can't approve something out of date
    assert contracts.update_draft("H001", draft, TERMS)["success"]                                  # HR pulls it back from approval...
    assert contracts.hr_view("H001", emp_id)["status"] == "draft" and contracts.pending_for("M001") == []     # ...and the manager no longer sees it
    contracts.submit("H001", draft); contracts.decide("M001", draft, "approve")
    e.update_employee("H001", emp_id, {"title": "Staff QA Engineer"}, "And another")
    assert "record changed" in contracts.pdf_for("H001", draft)["error"]


@test
def a_contract_can_be_cancelled_with_a_reason_and_written_again():
    fresh()
    emp_id, draft = drafted()
    contracts.submit("H001", draft)
    assert "Only HR" in contracts.cancel_draft("M001", draft, "x")["error"] and "reason" in contracts.cancel_draft("H001", draft, " ")["error"]
    assert contracts.cancel_draft("H001", draft, "Wrong terms") == {"success": True}
    assert contracts.hr_view("H001", emp_id)["status"] == "cancelled" and contracts.pending_for("M001") == []
    assert "cancelled" in contracts.submit("H001", draft)["error"] and "cancelled" in contracts.decide("M001", draft, "approve")["error"]
    again = contracts.create_draft("H001", emp_id, TERMS)
    assert again["success"] and contracts.hr_view("H001", emp_id)["id"] == again["draft_id"]       # the live one is shown, not the cancelled one


@test
def a_pending_contract_waits_in_the_needs_you_inbox_of_the_hires_manager_and_nobody_else():
    fresh()
    emp_id, draft = drafted()
    assert needs_you.inbox("M001")["contract_approvals"] == []                                      # a draft is not waiting for anyone yet
    contracts.submit("H001", draft)
    box = needs_you.inbox("M001")
    assert [(c["employee_name"], c["contract_type"], c["salary_eur"], c["trial_text"], c["submitted_by"]) for c in box["contract_approvals"]] == [
        ("Anu", "CDI", 3200.0, "4 months", "Narmadha")]
    assert box["waiting_on_you"] == len(box["leave_requests"]) + len(box["onboarding_steps"]) + 1 and needs_you.waiting_count("M001") == box["waiting_on_you"]
    assert needs_you.inbox("C001")["contract_approvals"] == [] and needs_you.inbox("H001")["contract_approvals"] == []
    assert needs_you.for_assistant("M001")["contracts_to_approve"] == [
        {"employee_name": "Anu", "contract_type": "CDI", "start_date": in_days(30), "salary_eur": 3200.0, "trial": "4 months", "sent_by": "Narmadha"}]
    contracts.decide("M001", draft, "approve")
    assert needs_you.inbox("M001")["contract_approvals"] == [] and needs_you.waiting_count("M001") == box["waiting_on_you"] - 1


@test
def the_assistant_tool_only_reports_where_a_contract_stands_and_only_to_hr():
    fresh()
    emp_id = hire()
    status = contracts.status_for_assistant
    assert "Only HR" in status("M001", "Anu")["error"] and "Only HR" in status(emp_id, "Anu")["error"]
    assert "No current person" in status("H001", "Zebra")["error"]
    assert status("H001", "Anu")["status"] == "no contract has been drafted yet"
    assert "never sends a contract" in status("H001", "Anu")["how_a_contract_leaves_the_app"]
    _, draft = drafted(emp_id)
    assert status("H001", "Anu")["status"] == "a draft" and status("H001", "Anu")["pdf_can_be_downloaded"] is False
    contracts.submit("H001", draft)
    waiting = status("H001", "Anu")
    assert waiting["status"] == "waiting for approval" and waiting["waiting_for"] == "approval by Lucas" and waiting["pdf_can_be_downloaded"] is False
    contracts.decide("M001", draft, "approve")
    ok = status("H001", "Anu")
    assert ok["status"] == "approved" and ok["approved_by"] == "Lucas" and ok["pdf_can_be_downloaded"] is True and ok["waiting_for"] is None
    hire(email="anya@cognivexai.eu", name="Anya Ng")
    assert status("H001", "Anu")["employee"] == "Anu"
    assert status("H001", "An")["several"] == ["Anu", "Anya Ng"] and "error" in status("H001", "A")        # a name that fits two people asks which


# ---------- the daily check-ins (checkins.py): what is due, when, and never twice ----------

def mails(kind=None):
    return [m for m in outbox.messages("H001") if kind in (None, m["kind"])]


def day_of(offset, emp_id):
    return date.fromisoformat(hr_start(emp_id)) + timedelta(days=offset)


def hr_start(emp_id):
    return onboarding.checklist("H001", emp_id)["start_date"]


def finish_new_hire_steps(emp_id):
    for key in ("id", "iban", "social_security"):
        onboarding.complete_step("H001", step(emp_id, key)["id"], "done")


@test
def a_new_hire_is_chased_for_their_own_items_once_they_are_due_and_at_most_every_two_days():
    fresh()
    emp_id = hire(30)
    due = day_of(-7, emp_id)                                                        # the three documents are due a week before the start
    assert checkins.run_daily(today=due - timedelta(days=1))["chase"] == [] and mails() == []
    assert checkins.run_daily(today=due)["chase"] == ["Anu"]
    mail = mails("chase")[0]
    assert mail["to_email"] == "anu@cognivexai.eu" and mail["sent_at"] is None and mail["to_name"] == "Anu"
    for wanted in ("Identity document", "Bank details (IBAN)", "Social security number"):
        assert wanted in mail["body"], wanted
    for not_theirs in ("Employment contract signed", "Laptop and equipment ready", "DPAE filed"):
        assert not_theirs not in mail["body"], not_theirs                          # HR's and the manager's steps are not chased
    assert [n["message"] for n in checkins.reminders_for(emp_id)] == [
        "Reminder: we still need your identity document, bank details (IBAN), social security number. Your checklist shows what to send."]
    assert checkins.run_daily(today=due)["chase"] == [] and len(mails("chase")) == 1                  # not twice the same day
    assert checkins.run_daily(today=due + timedelta(days=1))["chase"] == []                            # one day later: too soon
    assert checkins.run_daily(today=due + timedelta(days=2))["chase"] == ["Anu"] and len(mails("chase")) == 2
    onboarding.complete_step("H001", step(emp_id, "id")["id"], "Passport received")
    assert checkins.run_daily(today=due + timedelta(days=4))["chase"] == ["Anu"]
    assert "Identity document" not in mails("chase")[0]["body"] and "Bank details (IBAN)" in mails("chase")[0]["body"]   # only what is still missing
    finish_new_hire_steps(emp_id)
    assert checkins.run_daily(today=due + timedelta(days=9))["chase"] == [] and len(mails("chase")) == 3     # nothing missing: nothing sent
    log = [(c["new_value"], c["changed_by"]) for c in e.history("H001", emp_id)["changes"] if c["field"] == "reminder"]
    assert log.count(("chase", "system")) == 3                                                         # in the history, as "system"


@test
def nobody_is_chased_or_told_anything_for_a_cancelled_hire_or_someone_who_left():
    fresh()
    cancelled, left = hire(30), hire(30, email="bea@cognivexai.eu", name="Bea Roy")
    onboarding.cancel("H001", cancelled, "Offer withdrawn")
    conn = db.get_conn()
    with conn:
        conn.execute("UPDATE profiles SET end_date = ? WHERE emp_id = ?", (in_days(-1), left))      # the offer fell through
    conn.close()
    done = checkins.run_daily(today=date.fromisoformat(in_days(40)))
    assert done == {"chase": [], "before_day_one": [], "first_week": [], "trial_end": []} and mails() == []


@test
def the_first_day_information_goes_out_once_three_days_before_the_start_with_what_is_still_missing():
    fresh()
    emp_id = hire(30)
    start = day_of(0, emp_id)
    assert checkins.run_daily(today=start - timedelta(days=4))["before_day_one"] == []
    assert checkins.run_daily(today=start - timedelta(days=3))["before_day_one"] == ["Anu"]
    mail = mails("before_day_one")[0]
    assert mail["subject"].startswith("Your first day at CognivexAI") and f"{start:%A %d %B}" in mail["subject"]
    assert "QA Engineer in our Paris office" in mail["body"] and "Lucas will welcome you" in mail["body"]
    assert "Narmadha (narmadha@cognivexai.eu)" in mail["body"] and "Identity document" in mail["body"]       # what is still missing
    assert checkins.run_daily(today=start - timedelta(days=2))["before_day_one"] == [] and len(mails("before_day_one")) == 1
    soon = hire(2, email="cy@cognivexai.eu", name="Cy Doe")                          # hired with less than 3 days to go: the first run sends it
    assert checkins.run_daily(today=date.today())["before_day_one"] == ["Cy Doe"]
    finish_new_hire_steps(soon)
    assert checkins.run_daily(today=date.today())["before_day_one"] == []
    third = hire(3, email="di@cognivexai.eu", name="Di Fox")
    finish_new_hire_steps(third)
    assert checkins.run_daily(today=date.today())["before_day_one"] == ["Di Fox"]
    assert "Everything we asked for is in" in next(m for m in mails("before_day_one") if m["employee_name"] == "Di Fox")["body"]
    assert checkins.run_daily(today=start + timedelta(days=1))["before_day_one"] == []              # never after the start


@test
def the_first_week_check_in_goes_to_the_hire_and_the_manager_each_with_their_own_line():
    fresh()
    emp_id = hire(30)
    finish_new_hire_steps(emp_id)                                                    # nothing missing, so no chase muddies the messages
    start = day_of(0, emp_id)
    assert "First-week check-in" not in [s["step_title"] for s in onboarding.open_steps("M001")]                    # not at hiring
    assert "First-week check-in" not in [s["step_title"] for s in onboarding.open_steps("M001", today=start + timedelta(days=6))]
    assert checkins.run_daily(today=start + timedelta(days=6))["first_week"] == []
    assert checkins.run_daily(today=start + timedelta(days=7))["first_week"] == ["Anu"]
    assert [s["step_title"] for s in onboarding.open_steps("M001", today=start + timedelta(days=7))][-1] == "First-week check-in"   # now it waits
    mine = [n["message"] for n in checkins.reminders_for(emp_id)]
    lucas = [n["message"] for n in checkins.reminders_for("M001")]
    assert mine == ["It has been a week: how is it going? Lucas will check in with you."]
    assert lucas == ["Anu started a week ago: time for the first-week check-in."]                                 # not the hire's line
    mail = mails("first_week")[0]
    assert mail["to_email"] == "anu@cognivexai.eu" and "How is it going" in mail["body"] and "Narmadha" in mail["body"]
    assert checkins.run_daily(today=start + timedelta(days=8))["first_week"] == [] and len(mails("first_week")) == 1
    late = hire(30, email="eve@cognivexai.eu", name="Eve Ash")
    assert checkins.run_daily(today=day_of(40, late))["first_week"] == []                                           # never months late


@test
def the_manager_is_reminded_two_weeks_before_the_trial_period_of_an_approved_contract_ends():
    fresh()
    emp_id = hire(30)
    finish_new_hire_steps(emp_id)
    start = date.fromisoformat(hr_start(emp_id))
    draft = contracts.create_draft("H001", emp_id, {**TERMS, "trial": 2})["draft_id"]
    end = contracts.trial_end("CDI", start, 2)
    assert checkins.run_daily(today=end - timedelta(days=14))["trial_end"] == []                                    # nothing: still a draft
    contracts.submit("H001", draft)
    assert checkins.run_daily(today=end - timedelta(days=14))["trial_end"] == []                                    # nor while waiting
    contracts.decide("M001", draft, "approve")
    assert checkins.run_daily(today=end - timedelta(days=15))["trial_end"] == []
    assert checkins.run_daily(today=end - timedelta(days=14))["trial_end"] == ["Anu"]
    mail = mails("trial_end")[0]
    assert mail["to_email"] == "lucas@cognivexai.eu" and mail["to_name"] == "Lucas" and mail["employee_name"] == "Anu"       # to the manager
    assert f"ends on {end:%d %B}" in mail["subject"] and "L1221-25" in mail["body"] and "in 14 days" in mail["body"]
    assert [n["message"] for n in checkins.reminders_for("M001")][0].startswith("Anu's trial period ends on") and checkins.reminders_for(emp_id) == []
    assert checkins.run_daily(today=end - timedelta(days=13))["trial_end"] == [] and len(mails("trial_end")) == 1       # once
    assert checkins.run_daily(today=end + timedelta(days=1))["trial_end"] == []                                      # not after it ended
    none = hire(30, email="fay@cognivexai.eu", name="Fay Lo")
    none_draft = contracts.create_draft("H001", none, {**TERMS, "trial": 0})["draft_id"]
    contracts.submit("H001", none_draft); contracts.decide("M001", none_draft, "approve")
    assert checkins.run_daily(today=date.fromisoformat(hr_start(none)) + timedelta(days=30))["trial_end"] == []      # no trial period: no reminder


@test
def a_new_hire_reads_and_dismisses_their_own_reminders_and_the_outbox_shows_who_each_is_to():
    fresh()
    emp_id, other = hire(30), hire(30, email="gus@cognivexai.eu", name="Gus Pei")
    checkins.run_daily(today=day_of(-7, emp_id))
    assert len(checkins.reminders_for(emp_id)) == 1 and len(checkins.reminders_for(other)) == 1 and checkins.reminders_for("E001") == []
    checkins.mark_read(emp_id)
    assert checkins.reminders_for(emp_id) == [] and len(checkins.reminders_for(other)) == 1                         # only their own
    assert {m["to_name"] for m in mails("chase")} == {"Anu", "Gus Pei"}
    assert "Only HR" in outbox.messages("M001")["error"]


# ---------- everyone's private documents: HR files, a person sees their own, the assistant reads only those ----------

def sample(name, notice="deux mois"):
    return demo_data.sample_contract(name, "Engineer", notice)


def filed(emp_id, name, notice="deux mois", kind="contract", title=None):
    pdf, text = sample(name, notice)
    r = employee_files.add("H001", emp_id, kind, title or f"Employment contract of {name}", pdf, "contract.pdf", text=text)
    assert r["success"], r
    return r["file_id"]


def reset_files():
    fresh(); clear_files()
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM employee_file_views")
        conn.execute("DELETE FROM employee_files")
    conn.close()


@test
def hr_files_a_document_privately_and_nobody_else_can_file_one():
    reset_files()
    pdf, text = sample("Emma")
    for who in ("E001", "M001", "C001", "nobody"):
        assert "Only HR" in employee_files.add(who, "E001", "contract", "Contract", pdf, text=text)["error"], who
    assert "Choose what it is" in employee_files.add("H001", "E001", "memo", "Contract", pdf, text=text)["error"]
    assert "title" in employee_files.add("H001", "E001", "contract", "  ", pdf, text=text)["error"]
    assert "under 120" in employee_files.add("H001", "E001", "contract", "x" * 121, pdf, text=text)["error"]
    assert "PDF, JPG or PNG" in employee_files.add("H001", "E001", "contract", "Contract", b"MZ a program", "c.pdf", text=text)["error"]
    assert "too big" in employee_files.add("H001", "E001", "contract", "Contract", b"%PDF-" + b"0" * documents.MAX_BYTES, text=text)["error"]
    assert "No such person" in employee_files.add("H001", "E999", "contract", "Contract", pdf, text=text)["error"]
    assert stored_files() == []                                                               # nothing was kept for a refused one
    r = employee_files.add("H001", "E001", "contract", "  Employment   contract, 2024 ", pdf, "../../etc/my contract.pdf", text=text)
    assert r == {"success": True, "file_id": r["file_id"], "assistant": "ready"}
    files = stored_files()
    assert len(files) == 2 and sorted(re.sub(r"[0-9a-f]{32}", "X", f) for f in files) == ["X.pdf", "X.txt"]   # a random name, the file and its text
    assert all(os.stat(os.path.join(documents.files_dir(), f)).st_mode & 0o777 == 0o600 for f in files)
    conn = db.get_conn()
    row = dict(conn.execute("SELECT * FROM employee_files WHERE id = ?", (r["file_id"],)).fetchone())
    conn.close()
    assert row["title"] == "Employment contract, 2024" and row["original_name"] == "my contract.pdf" and row["uploaded_by"] == "H001"
    assert "préavis" not in " ".join(str(v) for v in row.values())                           # none of the text is in the database
    log = [(c["field"], c["new_value"], c["changed_by"]) for c in e.history("H001", "E001")["changes"] if c["field"].startswith("file:")]
    assert log == [("file: Employment contract, 2024", "added", "Narmadha")]


@test
def when_the_transcription_fails_or_is_cut_the_file_is_still_kept_and_says_so():
    reset_files()
    pdf, _ = sample("Emma")
    calls = []
    employee_files.TRANSCRIBER = lambda data, ft: calls.append(ft) or ("Article 1 – la moitié du texte", False)
    try:
        assert employee_files.add("H001", "E001", "contract", "Cut", pdf, "c.pdf")["assistant"] == "partial" and calls == ["pdf"]
        employee_files.TRANSCRIBER = lambda data, ft: (_ for _ in ()).throw(RuntimeError("the reader is down"))
        down = employee_files.add("H001", "E001", "certificate", "Unread", pdf, "c.pdf")
        assert down["success"] and down["assistant"] == "unreadable"                           # filed anyway
        employee_files.TRANSCRIBER = lambda data, ft: ("   ", True)
        assert employee_files.add("H001", "E001", "certificate", "Blank", pdf, "c.pdf")["assistant"] == "unreadable"
    finally:
        employee_files.TRANSCRIBER = None
    shown = {f["title"]: f["assistant"] for f in employee_files.own_list("E001")}
    assert shown == {"Cut": "partial", "Unread": "unreadable", "Blank": "unreadable"}
    cut_id = next(f["id"] for f in employee_files.own_list("E001") if f["title"] == "Cut")
    assert employee_files.tool_read("E001", cut_id)["text_is_incomplete"] is True
    unread_id = next(f["id"] for f in employee_files.own_list("E001") if f["title"] == "Unread")
    assert "cannot read the text" in employee_files.tool_read("E001", unread_id)["error"]
    assert employee_files.open_file("E001", unread_id)["media_type"] == "application/pdf"      # but the person can still open the file


@test
def a_person_sees_only_their_own_documents_and_who_opened_them_and_hr_sees_anyones():
    reset_files()
    emma, hugo = filed("E001", "Emma"), filed("E002", "Hugo", "trois mois")
    amendment = filed("E001", "Emma", kind="amendment", title="Amendment: remote work")
    assert [f["title"] for f in employee_files.own_list("E001")] == ["Amendment: remote work", "Employment contract of Emma"]    # newest first
    assert [f["title"] for f in employee_files.own_list("E002")] == ["Employment contract of Hugo"]
    assert employee_files.own_list("M001") == [] and "error" in employee_files.own_list("nobody")
    assert [f["kind_label"] for f in employee_files.own_list("E001")] == ["Amendment", "Contract"] and employee_files.own_list("E001")[0]["filed_by"] == "Narmadha"
    for who in ("E002", "M001", "C001", "nobody"):                                             # not a colleague, not the manager
        assert employee_files.open_file(who, emma) == {"error": "No such document."}, who
    assert employee_files.open_file("H001", 9999) == {"error": "No such document."}           # the same answer as for a missing one
    mine, hr = employee_files.open_file("E001", emma), employee_files.open_file("H001", emma)
    assert mine["path"] == hr["path"] and open(hr["path"], "rb").read().startswith(b"%PDF") and mine["filename"] == "employment-contract-of-emma.pdf"
    employee_files.open_file("H001", hugo)
    views = employee_files.own_list("E001")[1]["views"]
    assert [(v["viewer_name"], v["via"]) for v in views] == [("Narmadha", "app"), ("Emma", "app")]                              # the owner sees who looked
    assert [f["title"] for f in employee_files.hr_list("H001", "E002")] == ["Employment contract of Hugo"]
    assert "Only HR" in employee_files.hr_list("M001", "E002")["error"] and "Only HR" in employee_files.hr_list("E002", "E002")["error"]
    conn = db.get_conn()
    with conn:
        conn.execute("UPDATE profiles SET end_date = ? WHERE emp_id = 'E002'", (in_days(-1),))       # Hugo has left: he can't open anything
    conn.close()
    assert employee_files.open_file("E002", hugo) == {"error": "No such document."} and employee_files.open_file("H001", hugo)["path"]


@test
def deleting_removes_the_file_and_its_text_and_keeps_only_who_and_why():
    reset_files()
    emma = filed("E001", "Emma")
    assert len(stored_files()) == 2
    for who in ("E001", "M001"):
        assert "Only HR" in employee_files.delete(who, emma, "x")["error"], who
    assert "reason" in employee_files.delete("H001", emma, " ")["error"] and "No such document" in employee_files.delete("H001", 9999, "x")["error"]
    assert employee_files.delete("H001", emma, "Filed under the wrong person") == {"success": True}
    assert stored_files() == [] and employee_files.own_list("E001") == []
    assert employee_files.open_file("H001", emma) == {"error": "No such document."} and employee_files.tool_read("E001", emma) == {"error": "No such document."}
    assert "No such document" in employee_files.delete("H001", emma, "again")["error"]
    log = [(c["new_value"], c["reason"]) for c in e.history("H001", "E001")["changes"] if c["field"].startswith("file:")]
    assert ("deleted", "Filed under the wrong person") in log


@test
def the_assistant_lists_and_reads_only_the_signed_in_persons_own_documents_and_each_reading_is_logged():
    reset_files()
    emma, hugo = filed("E001", "Emma", "deux mois"), filed("E002", "Hugo", "trois mois")
    listing = employee_files.tool_list("E001")
    assert [(d["document_id"], d["title"], d["assistant_can_read_it"]) for d in listing["documents"]] == [(emma, "Employment contract of Emma", True)]
    assert "trois" not in str(listing) and "Hugo" not in str(listing)                           # nothing about anyone else
    read = employee_files.tool_read("E001", emma)
    assert read["document"] == "Employment contract of Emma" and "préavis de deux mois" in read["text"] and read["text_is_incomplete"] is False
    assert "name the document by its title" in read["rules"].lower() and "not instructions" in read["rules"] and "From the company policy, not your contract" in read["rules"]
    assert employee_files.tool_read("E001", hugo) == employee_files.tool_read("E001", 9999) == {"error": "No such document."}     # someone else's id = a missing one
    assert employee_files.tool_read("E002", emma) == {"error": "No such document."}
    assert "trois mois" not in str(employee_files.tool_read("H001", hugo))                      # even HR's assistant reads only HR's own
    assert employee_files.tool_list("H001")["documents"] == [] and "No document has been filed" in employee_files.tool_list("H001")["message"]
    views = [(v["viewer_name"], v["via"]) for v in employee_files.own_list("E001")[0]["views"]]
    assert views == [("Emma", "assistant")]                                                       # the reading is a logged view, by the assistant
    assert employee_files.own_list("E002")[0]["views"] == []                                      # Hugo's was never touched
    assert "error" in employee_files.tool_list("nobody") and "error" in employee_files.tool_read("nobody", emma)
    big = demo_data.sample_contract("Emma", "Engineer", "deux mois")[0]
    employee_files.add("H001", "E001", "certificate", "Very long", big, text="x" * (employee_files.TEXT_LIMIT + 10))
    long_id = next(d["document_id"] for d in employee_files.tool_list("E001")["documents"] if d["title"] == "Very long")
    cut = employee_files.tool_read("E001", long_id)
    assert len(cut["text"]) == employee_files.TEXT_LIMIT and cut["text_is_incomplete"] is True


def fake_embedding(text):
    # a stand-in for the real embedding: a bag of words, so questions that share words are close and the rest is not
    vec = [0.0] * 64
    for word in re.findall(r"[a-z]{3,}", re.sub(r"\[[a-z]+\]", " ", text.lower())):      # not the [name] / [email] markers
        if word not in ("can", "get", "for", "and", "the", "you", "are", "does", "how"):
            vec[sum(map(ord, word)) % 64] += 1.0
    return vec


def reset_gaps():
    fresh()
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM policy_gaps")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'policy_gaps'")       # ids start at 1 again, so the tests can name them
    conn.close()
    policy_gaps.EMBED = fake_embedding


@test
def a_question_the_policy_does_not_answer_is_kept_counted_and_grouped_without_naming_anyone():
    reset_gaps()
    first = policy_gaps.record("Can Emma get a company car? emma@cognivexai.eu")
    assert first == {"recorded": True, "gap_id": 1, "new": True}
    assert policy_gaps.record("company car for employees")["gap_id"] == 1                      # the same topic: counted, not listed twice
    assert policy_gaps.record("is a company car possible")["gap_id"] == 1
    assert policy_gaps.record("gym membership refund")["gap_id"] == 2                          # another topic
    gaps = {g["id"]: g for g in policy_gaps.open_gaps()}
    assert gaps[1]["asks"] == 3 and gaps[2]["asks"] == 1
    assert [g["id"] for g in policy_gaps.open_gaps()] == [1, 2]                                # most asked first
    assert gaps[1]["topic"] == "Can [name] get a company car? [email]" and len(gaps[1]["examples"]) == 3
    assert policy_gaps.record("   ")["recorded"] is False and policy_gaps.record("12")["recorded"] is False and policy_gaps.record(None)["recorded"] is False
    # nothing in the database points at a person: no such column, and none of the words they used
    conn = db.get_conn()
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(policy_gaps)")}
    dump = " ".join(str(v) for r in conn.execute("SELECT * FROM policy_gaps").fetchall() for v in tuple(r)).lower()
    conn.close()
    assert not columns & {"emp_id", "employee_id", "asked_by", "user_id", "person"}, columns
    for private in ("emma", "cognivexai.eu", "e001"):
        assert private not in dump, private


@test
def without_an_embedding_questions_are_grouped_by_the_words_they_share():
    reset_gaps()
    policy_gaps.EMBED = None
    assert policy_gaps.record("company car")["new"] and policy_gaps.record("company car policy")["gap_id"] == 1
    assert policy_gaps.record("gym membership refund")["gap_id"] == 2 and policy_gaps.record("gym membership")["gap_id"] == 2
    assert policy_gaps.record("company laptop")["gap_id"] == 3
    def broken(text):
        raise RuntimeError("the embedding service is down")
    policy_gaps.EMBED = broken                                                                  # recording a question never fails because of it
    assert policy_gaps.record("parental leave for fathers")["new"]
    assert len(policy_gaps.open_gaps()) == 4


@test
def hr_dismisses_a_gap_for_good_or_marks_it_fixed_and_it_comes_back_if_people_still_hear_the_policy_is_silent():
    reset_gaps()
    for question in ("company car", "company car policy", "gym membership"):
        policy_gaps.record(question)
    for who in ("E001", "M001", "C001", "nobody"):
        assert "Only HR" in policy_gaps.close(who, 1, "fixed")["error"], who
    assert "No question" in policy_gaps.close("H001", 99, "fixed")["error"] and "dismissed or marked fixed" in policy_gaps.close("H001", 1, "deleted")["error"]
    assert policy_gaps.close("H001", 1, "fixed", "added a Company car section") == {"success": True, "status": "fixed"}
    assert "already closed" in policy_gaps.close("H001", 1, "dismissed")["error"]
    assert [g["id"] for g in policy_gaps.open_gaps()] == [2]
    # still unanswered after the fix: it opens again, counted from the fix, and says so
    assert policy_gaps.record("company car")["reopened"] is True
    again = {g["id"]: g for g in policy_gaps.open_gaps()}[1]
    assert again["asks"] == 1 and again["reopened"] is True
    # dismissed means quiet: further questions like it are not shown
    assert policy_gaps.close("H001", 2, "dismissed") == {"success": True, "status": "dismissed"}
    assert policy_gaps.record("gym membership")["recorded"] is False and policy_gaps.record("gym membership")["dismissed"] is True
    assert [g["id"] for g in policy_gaps.open_gaps()] == [1]


@test
def a_dismissed_gap_only_absorbs_close_paraphrases_so_it_never_swallows_a_neighbouring_topic():
    reset_gaps()
    assert policy_gaps.record("company car lease")["new"] and policy_gaps.record("company car insurance")["gap_id"] == 1      # neighbours share a gap while it is open
    policy_gaps.close("H001", 1, "dismissed")
    assert policy_gaps.record("company car lease")["dismissed"] is True                  # the same question: quiet
    other = policy_gaps.record("company car insurance cover")                            # a neighbour is not the same question
    assert other.get("new") is True and other["gap_id"] == 2, other
    assert [g["topic"] for g in policy_gaps.open_gaps()] == ["company car insurance cover"]


@test
def hr_sees_the_dismissed_questions_and_can_restore_one_and_only_a_dismissed_one():
    reset_gaps()
    for question in ("company car", "company car policy", "gym membership"):
        policy_gaps.record(question)
    assert policy_gaps.dismissed_gaps("H001") == []
    policy_gaps.close("H001", 2, "dismissed", "not something we offer")
    policy_gaps.close("H001", 1, "fixed", "added a section")
    shown = policy_gaps.dismissed_gaps("H001")
    assert [(g["id"], g["topic"], g["asks"], g["note"], g["dismissed_by"]) for g in shown] == [(2, "gym membership", 1, "not something we offer", "Narmadha")]
    assert shown[0]["dismissed_at"] and set(shown[0]) == {"id", "topic", "asks", "examples", "last_asked_at", "dismissed_by", "dismissed_at", "note"}   # nobody who asked
    for who in ("E001", "M001", "C001", "nobody"):
        assert "Only HR" in policy_gaps.dismissed_gaps(who)["error"] and "Only HR" in policy_gaps.restore(who, 2)["error"], who
    assert "No question" in policy_gaps.restore("H001", 99)["error"]
    assert "isn't dismissed" in policy_gaps.restore("H001", 1)["error"]                  # marked fixed, not dismissed
    assert policy_gaps.record("gym membership")["dismissed"] is True                     # quiet while dismissed
    assert policy_gaps.restore("H001", 2) == {"success": True, "status": "open"}
    assert "isn't dismissed" in policy_gaps.restore("H001", 2)["error"]                  # it is open now
    assert policy_gaps.dismissed_gaps("H001") == []
    back = {g["id"]: g for g in policy_gaps.open_gaps()}[2]
    assert back["asks"] == 1 and back["reopened"] is False                               # the count it had, and not the "marked fixed, asked again" line
    assert policy_gaps.record("gym membership")["gap_id"] == 2                           # questions like it are counted again
    assert {g["id"]: g for g in policy_gaps.open_gaps()}[2]["asks"] == 2


@test
def hr_sets_changes_and_clears_a_category_each_edit_is_logged_and_only_the_listed_categories_are_kept():
    fresh()
    assert e.category_of("E001") is None
    assert e.update_employee("H001", "E001", {"category": "Cadre"}, "Classification confirmed in the contract") == {"success": True, "id": "E001", "changed": ["category"]}
    assert e.category_of("E001") == "Cadre"
    assert "Nothing changed" in e.update_employee("H001", "E001", {"category": "Cadre"}, "again")["error"]
    assert e.update_employee("H001", "E001", {"category": "Agent de maîtrise"}, "Reclassified")["success"]
    for wrong in ("Executive", "cadre", "Non cadre"):
        assert "Category must be one of" in e.update_employee("H001", "E001", {"category": wrong}, "x")["error"], wrong      # exactly as listed
    assert "reason" in e.update_employee("H001", "E001", {"category": "Cadre"}, "  ")["error"]
    for who in ("E001", "M001", "C001", "nobody"):
        assert "Only HR" in e.update_employee(who, "E001", {"category": "Cadre"}, "x")["error"], who
    assert e.category_of("E001") == "Agent de maîtrise"                                    # every refused edit changed nothing
    assert e.update_employee("H001", "E001", {"category": ""}, "Taken out until the contract is checked")["success"]
    assert e.category_of("E001") is None                                                    # empty means "not set", it is never a category
    log = [(c["old_value"], c["new_value"], c["reason"], c["changed_by"]) for c in e.history("H001", "E001")["changes"] if c["field"] == "category"]
    assert log == [("Agent de maîtrise", None, "Taken out until the contract is checked", "Narmadha"),
                   ("Cadre", "Agent de maîtrise", "Reclassified", "Narmadha"),
                   (None, "Cadre", "Classification confirmed in the contract", "Narmadha")]          # newest first: who, what, why


@test
def a_new_hire_can_be_given_a_category_which_is_logged_and_a_wrong_one_is_refused_and_none_is_never_guessed():
    fresh()
    assert "Category must be one of" in e.create_employee("H001", {**NEW_HIRE, "category": "Boss"})["error"]
    made = e.create_employee("H001", {**NEW_HIRE, "category": "Cadre"})
    assert made["success"] and e.category_of(made["id"]) == "Cadre"
    assert (None, "Cadre") in [(c["old_value"], c["new_value"]) for c in e.history("H001", made["id"])["changes"] if c["field"] == "category"]
    other = e.create_employee("H001", {**NEW_HIRE, "email": "bo@cognivexai.eu", "name": "Bo", "title": "Chief Engineer"})
    assert other["success"] and e.category_of(other["id"]) is None                          # not given: not set, whatever the job title says


@test
def only_the_person_has_their_category_in_their_record_and_the_assistant_is_told_it_or_told_it_is_not_set():
    import sqlite3
    fresh()
    e.update_employee("H001", "E001", {"category": "Cadre"}, "contract")
    assert e.my_record("E001")["people"][0]["category"] == "Cadre" and e.my_record("E002")["people"][0]["category"] is None
    for entry in (e.directory_entry("E001"), *e.find_colleagues("Emma")["people"]):
        assert "category" not in entry                                                       # the colleague view never has it
    set_text, unset_text = e.category_prompt("E001"), e.category_prompt("E002")
    assert "is Cadre" in set_text and "on file" in set_text and "HR hasn't set" not in set_text
    assert "HR hasn't set your category" in unset_text and "HR's record" not in unset_text
    assert "non-cadre, agent de maîtrise, cadre" in unset_text and "Never decide it from their job title" in unset_text
    real = e.category_of
    def broken(emp_id):
        raise sqlite3.OperationalError("no such table: profiles")
    e.category_of = broken                                                                   # a database made before categories existed
    try:
        assert "HR hasn't set your category" in e.category_prompt("E001")                    # never breaks a conversation
    finally:
        e.category_of = real


@test
def hr_sees_the_gaps_in_needs_you_with_the_count_and_nobody_else_does():
    reset_gaps()
    hr_before = needs_you.waiting_count("H001")
    for question in ("company car", "company car policy", "gym membership"):
        policy_gaps.record(question)
    box = needs_you.inbox("H001")
    assert [(g["topic"], g["asks"]) for g in box["policy_gaps"]] == [("company car", 2), ("gym membership", 1)]
    assert box["waiting_on_you"] == needs_you.waiting_count("H001") == hr_before + 2
    spoken = needs_you.for_assistant("H001")["questions_the_policy_does_not_answer"]
    assert spoken == [{"topic": "company car", "times_asked": 2, "also_asked_as": ["company car policy"]}, {"topic": "gym membership", "times_asked": 1, "also_asked_as": []}]
    assert needs_you.inbox("M001")["policy_gaps"] == [] and needs_you.for_assistant("M001")["questions_the_policy_does_not_answer"] == []
    assert "error" in needs_you.inbox("E001")
    policy_gaps.close("H001", 1, "fixed")
    assert needs_you.waiting_count("H001") == hr_before + 1


@test
def recording_a_gap_after_the_answer_can_never_break_the_answer():
    reset_gaps()
    asked = [{"query": "company car", "found": False}]
    assert policy_gaps.record_from_turn(asked, "The policy doesn't say.")["recorded"] is True
    assert policy_gaps.record_from_turn([{"query": "remote work", "found": True}], "Up to 2 days a week.") is None
    real = policy_gaps.record
    def locked(question):
        raise RuntimeError("database is locked")
    policy_gaps.record = locked
    try:
        assert policy_gaps.record_from_turn(asked, "The policy doesn't say.") is None            # no exception reaches the person
    finally:
        policy_gaps.record = real


AGREEMENT = """Article 1 – Notice
The notice period for resignation is three months for managers and one month for the others.

Article 2 – Sick pay
After one year of service the employer pays 90 percent of the salary for 60 days of sick leave.

Article 3 – Remote work
Remote work is possible up to three days a week with the manager's agreement."""
AGREEMENT_NAME = "Convention collective Test"


def fake_many(texts):
    return [fake_embedding(t) for t in texts]


def reset_agreement():
    fresh(); clear_files()
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM agreement_sections")
        conn.execute("DELETE FROM collective_agreements")
    conn.close()
    collective_agreement.EMBED_MANY = fake_many
    collective_agreement.READER = None


def agreement_files():
    return [f for f in stored_files()]


@test
def only_hr_uploads_the_collective_agreement_and_it_is_read_cut_and_searched_by_meaning():
    reset_agreement()
    data = AGREEMENT.encode("utf-8")
    for who in ("E001", "M001", "C001", "nobody"):
        assert "Only HR" in collective_agreement.add(who, AGREEMENT_NAME, data)["error"], who
    assert "name" in collective_agreement.add("H001", "  ", data)["error"] and "under 120" in collective_agreement.add("H001", "x" * 121, data)["error"]
    assert "empty" in collective_agreement.add("H001", AGREEMENT_NAME, b"")["error"] and agreement_files() == [] and collective_agreement.summary() is None
    done = collective_agreement.add("H001", AGREEMENT_NAME, data, "../../etc/convention.txt")
    assert done["success"] and done["sections"] == 3 and done["assistant"] == "ready", done
    info = collective_agreement.summary()
    assert info["name"] == AGREEMENT_NAME and info["sections"] == 3 and info["uploaded_by"] == "Narmadha" and info["original_name"] == "convention.txt"
    # the file is kept like the other private files: a random name, readable by its owner only
    assert len(agreement_files()) == 1 and oct(os.stat(os.path.join(documents.files_dir(), agreement_files()[0])).st_mode & 0o777) == "0o600"
    first = lambda q: collective_agreement.search(q)[0]["title"]
    assert first("how long is the notice period when I resign") == "Article 1 – Notice"
    assert first("what percent of salary is paid during sick leave") == "Article 2 – Sick pay"
    assert first("how many days of remote work a week") == "Article 3 – Remote work"
    assert collective_agreement.search("zebra giraffe volcano") == []                                      # nothing related: nothing returned


@test
def uploading_another_agreement_replaces_it_and_removing_deletes_the_file_and_the_text():
    reset_agreement()
    collective_agreement.add("H001", "First", AGREEMENT.encode("utf-8"))
    old_files = agreement_files()
    second = collective_agreement.add("H001", "Second", "Article 1 – Holidays\nTwo extra days.".encode("utf-8"))
    assert second["sections"] == 1 and collective_agreement.summary()["name"] == "Second"
    assert len(agreement_files()) == 1 and agreement_files() != old_files                                   # the first file is gone
    conn = db.get_conn()
    history = [tuple(r) for r in conn.execute("SELECT name, status, ended_by, file_name IS NULL FROM collective_agreements ORDER BY id")]
    texts = conn.execute("SELECT COUNT(*) FROM agreement_sections").fetchone()[0]
    conn.close()
    assert history == [("First", "replaced", "H001", 1), ("Second", "current", None, 0)] and texts == 1      # history keeps who and when, not the text
    assert all("Notice" not in s["title"] for s in collective_agreement.search("notice period"))
    for who in ("E001", "M001", "nobody"):
        assert "Only HR" in collective_agreement.remove(who)["error"], who
    assert collective_agreement.remove("H001") == {"success": True}
    assert collective_agreement.summary() is None and agreement_files() == [] and collective_agreement.search("holidays") == []
    assert "no collective agreement to remove" in collective_agreement.remove("H001")["error"]


@test
def a_failed_upload_changes_nothing_and_a_scan_is_read_by_claude_when_there_is_no_text_layer():
    reset_agreement()
    collective_agreement.add("H001", "Current", AGREEMENT.encode("utf-8"))
    def down(texts):
        raise RuntimeError("the embedding service is down")
    collective_agreement.EMBED_MANY = down
    assert "Nothing was changed" in collective_agreement.add("H001", "New", b"Article 1 - A\nText.")["error"]
    collective_agreement.EMBED_MANY = fake_many
    assert collective_agreement.summary()["name"] == "Current" and len(agreement_files()) == 1                # the current one is still there
    collective_agreement.READER = lambda data, kind: ("   \n  ", True)
    assert "any text" in collective_agreement.add("H001", "Blank", b"Article 1 - A\nText.")["error"]
    def broken(data, kind):
        raise RuntimeError("cannot read")
    collective_agreement.READER = broken
    assert "couldn't read" in collective_agreement.add("H001", "Broken", b"Article 1 - A\nText.")["error"] and collective_agreement.summary()["name"] == "Current"
    # a PDF with no text layer goes to Claude (long ones come back partial)
    collective_agreement.READER = None
    seen = []
    real_pdf, real_claude = collective_agreement._pdf_text, employee_files.transcribe_with_claude
    collective_agreement._pdf_text = lambda data: ""
    employee_files.transcribe_with_claude = lambda data, kind: (seen.append(kind) or "Article 1 – Scanned\nTrois jours.", False)
    try:
        scan = collective_agreement.add("H001", "Scanned", b"%PDF-1.4 a scan")
    finally:
        collective_agreement._pdf_text, employee_files.transcribe_with_claude = real_pdf, real_claude
    assert seen == ["pdf"] and scan["assistant"] == "partial" and collective_agreement.summary()["assistant"] == "partial"


@test
def a_pdf_with_a_text_layer_is_read_without_claude_and_cut_like_the_text():
    try:
        import pypdf    # noqa: F401  (CI's unit-test job has no packages: the text path above covers the rest there)
    except ImportError:
        return
    reset_agreement()
    name, pdf, text = demo_data.sample_agreement()
    def no_claude(data, kind):
        raise AssertionError("Claude must not be asked: the PDF has a text layer")
    real = employee_files.transcribe_with_claude
    employee_files.transcribe_with_claude = no_claude
    try:
        done = collective_agreement.add("H001", name, pdf, "convention.pdf")
    finally:
        employee_files.transcribe_with_claude = real
    assert done["success"] and done["assistant"] == "ready" and done["sections"] == len(collective_agreement.split_sections(text))
    assert collective_agreement.summary()["media_type"] == "application/pdf"


@test
def everyone_who_works_here_opens_the_file_and_nobody_else_does():
    reset_agreement()
    assert "no collective agreement" in collective_agreement.open_file("E001")["error"]
    collective_agreement.add("H001", AGREEMENT_NAME, AGREEMENT.encode("utf-8"))
    for who in ("E001", "M001", "C001", "H001"):
        opened = collective_agreement.open_file(who)
        assert opened["filename"] == "convention-collective-test.txt" and opened["media_type"] == "text/plain" and open(opened["path"], "rb").read() == AGREEMENT.encode("utf-8"), who
    assert "no collective agreement" in collective_agreement.open_file("nobody")["error"]
    conn = db.get_conn()
    with conn:
        conn.execute("UPDATE profiles SET end_date = '2020-01-01' WHERE emp_id = 'E002'")             # Hugo left
    conn.close()
    assert "no collective agreement" in collective_agreement.open_file("E002")["error"] and "filename" in collective_agreement.open_file("E001")


@test
def the_assistant_only_gets_the_agreement_tool_and_the_order_of_sources_once_hr_has_uploaded_one():
    reset_agreement()
    tools = [{"name": "search_policy"}, {"name": collective_agreement.TOOL}, {"name": "search_labour_code"}]
    assert [t["name"] for t in collective_agreement.available_tools(tools)] == ["search_policy", "search_labour_code"]
    assert collective_agreement.agreement_prompt() is None
    assert collective_agreement.tool_result("notice") == {"error": "No collective agreement has been uploaded."}
    collective_agreement.add("H001", AGREEMENT_NAME, AGREEMENT.encode("utf-8"))
    assert len(collective_agreement.available_tools(tools)) == 3
    order = collective_agreement.agreement_prompt()
    assert AGREEMENT_NAME in order and order.index("their own contract") < order.index("the collective agreement") < order.index("the company policy") < order.index("the law")
    assert "FIRST source that answers" in order and "does not specify" in order and "Never present" in order
    found = collective_agreement.tool_result("how long is the notice period when I resign")
    assert found["found"] is True and found["agreement"] == AGREEMENT_NAME and found["articles"][0]["article"] == "Article 1 – Notice"
    assert AGREEMENT_NAME in found["rules"] and "Never present them as the person's contract" in found["rules"] and "score" not in str(found)
    silent = collective_agreement.tool_result("zebra giraffe volcano")
    assert silent["found"] is False and AGREEMENT_NAME in silent["message"] and "does not specify it" in silent["rules"]
    collective_agreement.remove("H001")
    assert [t["name"] for t in collective_agreement.available_tools(tools)] == ["search_policy", "search_labour_code"]


REINDEXED = []


def reset_policy():
    fresh()
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM policy_versions")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'policy_versions'")       # version ids start at 1 again
    conn.close()
    REINDEXED.clear()
    policy_store.REINDEX = lambda: REINDEXED.append(policy_store.sections())              # no embedding service here: record that it was asked to


WFH = "Work from home"


@test
def hr_edits_a_policy_section_and_every_save_is_a_logged_version_with_the_earlier_ones_kept():
    reset_policy()
    shipped = policy_store.default_sections()
    assert policy_store.current()["version"] == 0 and policy_store.current()["original"] and policy_store.sections() == shipped
    for who in ("E001", "M001", "C001", "nobody"):
        assert "Only HR" in policy_store.save_section(who, 0, WFH, WFH, "Anything.")["error"], who
    assert policy_store.current()["version"] == 0 and REINDEXED == []                                       # nothing happened
    done = policy_store.save_section("H001", 0, WFH, WFH, "Employees may work from home up to 3 days per week with manager approval.", "after the 2024 agreement")
    assert done == {"success": True, "version": 1, "change": "Edited \"Work from home\"", "indexed": True}
    assert len(REINDEXED) == 1 and "3 days per week" in str(REINDEXED[0])                                   # re-indexed with the new text, at once
    now = policy_store.current()
    assert now["version"] == 1 and now["saved_by"] == "H001" and not now["original"] and now["saved_at"]
    assert next(s for s in now["sections"] if s["title"] == WFH)["text"].startswith("Employees may work from home up to 3 days")
    assert open(policy_store.DEFAULT_PATH, encoding="utf-8").read().count("up to 2 days per week") == 1         # the shipped file is never touched
    assert [s["title"] for s in now["sections"]] == [s["title"] for s in shipped]                              # the order stays
    # add, rename, remove: each one is a version
    assert policy_store.save_section("H001", 1, "", "Company car", "A company car is available to managers after two years.")["version"] == 2
    assert policy_store.save_section("H001", 2, "Company car", "Company vehicles", "A company car is available to managers after two years.")["change"] == \
        "Renamed \"Company car\" to \"Company vehicles\""
    assert policy_store.save_section("H001", 3, "Company vehicles", "Company cars", "Managers get a car after two years.")["change"] == \
        "Renamed \"Company vehicles\" to \"Company cars\" and edited it"
    assert policy_store.delete_section("H001", 4, "Company cars", "not offered after all")["change"] == "Removed \"Company cars\""
    history = policy_store.versions()
    assert [v["id"] for v in history] == [5, 4, 3, 2, 1, 0] and [v["current"] for v in history] == [True] + [False] * 5
    assert history[0]["saved_by"] == "Narmadha" and history[0]["change"] == "Removed \"Company cars\"" and history[0]["note"] == "not offered after all"
    assert history[-1]["change"] == "The policy as shipped" and history[-1]["saved_by"] is None
    assert any(s["title"] == "Company car" for s in policy_store.get_version(2)) and policy_store.get_version(0) == shipped and policy_store.get_version(99) is None   # earlier versions are readable


@test
def a_save_on_top_of_a_newer_version_or_one_that_breaks_a_rule_the_app_enforces_is_refused():
    reset_policy()
    assert policy_store.save_section("H001", 0, WFH, WFH, "Up to 3 days.")["success"]
    stale = policy_store.save_section("H001", 0, WFH, WFH, "Up to 4 days.")                                  # someone else saved first
    assert "changed since you opened it" in stale["error"] and policy_store.current()["version"] == 1
    assert "Nothing changed" in policy_store.save_section("H001", 1, WFH, WFH, "Up to 3 days.")["error"]
    assert "no longer exists" in policy_store.save_section("H001", 1, "Gone", "Gone", "x")["error"] and "no longer exists" in policy_store.delete_section("H001", 1, "Gone")["error"]
    casual = next(s for s in policy_store.sections() if s["title"] == "Casual leave")
    refused = policy_store.save_section("H001", 1, "Casual leave", "Casual leave", casual["text"].replace("12 casual leave days", "15 casual leave days"))
    assert "12 casual leave days per year" in refused["error"] and "ask a developer" in refused["error"]
    assert "2.5 days of paid leave per month" in policy_store.delete_section("H001", 1, "Paid leave (congés payés)")["error"]       # the whole paid-leave section cannot go
    assert "needs some text" in policy_store.save_section("H001", 1, "", "New", "   ")["error"] and policy_store.current()["version"] == 1   # nothing was saved by any refusal
    assert len(REINDEXED) == 1                                                                                # and nothing was re-indexed


@test
def an_earlier_version_can_be_restored_as_a_new_version_and_a_failing_reindex_does_not_lose_the_save():
    reset_policy()
    policy_store.save_section("H001", 0, WFH, WFH, "Up to 3 days.")
    policy_store.save_section("H001", 1, "", "Company car", "Managers get one.")
    back = policy_store.restore("H001", 2, 1)
    assert back["version"] == 3 and back["change"] == "Restored version 1" and not any(s["title"] == "Company car" for s in policy_store.sections())
    assert policy_store.versions()[0]["restored_from"] == 1 and len(policy_store.versions()) == 4                  # the history only grows
    assert policy_store.restore("H001", 3, 0)["change"] == "Restored the policy as shipped" and policy_store.sections() == policy_store.default_sections()
    assert "Nothing changed" in policy_store.restore("H001", 4, 0)["error"] and "No such version" in policy_store.restore("H001", 4, 99)["error"]
    assert "Only HR" in policy_store.restore("E001", 4, 1)["error"]
    def down():
        raise RuntimeError("the embedding service is down")
    policy_store.REINDEX = down
    saved = policy_store.save_section("H001", 4, WFH, WFH, "Up to 5 days.")
    assert saved["success"] and saved["indexed"] is False and policy_store.current()["version"] == 5            # kept; the search embeds it on its next question


@test
def the_paid_leave_window_the_leave_rules_use_follows_the_policy_text_hr_saves():
    reset_policy()
    assert tools.paid_policy()[0] == ((5, 1), (10, 31)) and tools.paid_policy_note(date(2030, 6, 3), date(2030, 6, 5)) is None
    paid = next(s for s in policy_store.sections() if s["title"].startswith("Paid leave"))
    done = policy_store.save_section("H001", 0, paid["title"], paid["title"], paid["text"].replace("1 May and 31 October", "1 June and 30 September"))
    assert done["success"]
    assert tools.paid_policy()[0] == ((6, 1), (9, 30)) and "between 1 June and 30 September" in tools.paid_policy()[1]
    assert tools.paid_policy_note(date(2030, 5, 20), date(2030, 5, 22)) == tools.paid_policy()[1]               # May is now outside the window
    assert tools.paid_policy_note(date(2030, 6, 3), date(2030, 6, 5)) is None
    assert tools.get_leave_balance("E001", "paid")["policy_note"] == tools.paid_policy()[1]


# ---------- recruitment: jobs and the pipeline (jobs.py) ----------
# The app informs and a person decides: only a person moves a candidate, "Not selected" always has a reason, nothing ranks anyone, and
# only HR and the job's hiring manager see candidates.

JOB = {"title": "Data Analyst", "team": "Engineering", "location": "Paris", "contract_type": "CDI", "hiring_manager_id": "M001",
       "description": "Turn product data into decisions for the engineering team.", "must_have": ["2 years of data analysis", "SQL"],
       "nice_to_have": ["dbt"]}


def an_open_job(**changes):
    fresh()
    job = jobs.create_job("H001", {**JOB, **changes})
    assert "error" not in job, job
    done = jobs.set_status("H001", job["id"], "open")
    assert done["status"] == "open", done
    return job["id"]


def a_candidate(job_id, name="Inès Garnier", email="ines@example.com", who="H001"):
    c = jobs.add_candidate(who, job_id, {"name": name, "email": email})
    assert "error" not in c, c
    return c["id"]


@test
def hr_creates_any_job_a_manager_only_their_own_and_an_employee_none():
    fresh()
    job = jobs.create_job("H001", JOB)
    assert job["status"] == "draft" and job["hiring_manager_id"] == "M001" and job["hiring_manager_name"] == "Lucas" and job["counts"]["applied"] == 0
    assert job["must_have"] == ["2 years of data analysis", "SQL"] and job["can_open"] is True
    own = jobs.create_job("M001", {**JOB, "hiring_manager_id": "C001"})                      # a manager cannot hire for someone else: they are the hiring manager
    assert own["hiring_manager_id"] == "M001" and own["can_open"] is False
    assert "Only HR and managers" in jobs.create_job("E001", JOB)["error"]
    assert "hiring manager" in jobs.create_job("H001", {**JOB, "hiring_manager_id": "E001"})["error"]       # an employee is not a hiring manager
    assert "hiring manager" in jobs.create_job("H001", {**JOB, "hiring_manager_id": None})["error"]


@test
def a_job_needs_its_fields_and_at_least_one_requirement():
    fresh()
    for change, word in [({"title": ""}, "title"), ({"team": " "}, "team"), ({"location": ""}, "location"), ({"description": ""}, "description"),
                         ({"contract_type": "Freelance"}, "contract type"), ({"must_have": []}, "must-have"), ({"must_have": " \n "}, "must-have"),
                         ({"must_have": ["x"] * 16}, "At most 15"), ({"title": "AB"}, "too short"), ({"title": "T" * 121}, "too long"),
                         ({"salary_min": 40000}, "both ends"), ({"salary_min": 50000, "salary_max": 40000}, "not valid"), ({"salary_min": "a", "salary_max": "b"}, "numbers")]:
        assert word in jobs.create_job("H001", {**JOB, **change})["error"], change
    job = jobs.create_job("H001", {**JOB, "must_have": "- SQL\n* Python\n\n3 years", "salary_min": "42000", "salary_max": 52000.0})
    assert job["must_have"] == ["SQL", "Python", "3 years"] and (job["salary_min"], job["salary_max"]) == (42000, 52000)       # one per line, a list marker is dropped


@test
def only_hr_opens_a_job_a_manager_can_close_their_own_and_closing_rejects_nobody():
    fresh()
    job = jobs.create_job("M001", JOB)["id"]
    assert "Only HR opens" in jobs.set_status("M001", job, "open")["error"]                         # opening makes it public
    assert jobs.set_status("H001", job, "open")["status"] == "open"
    assert "already open" in jobs.set_status("H001", job, "open")["error"]
    a_candidate(job)
    closed = jobs.set_status("M001", job, "closed")
    assert closed["status"] == "closed" and closed["still_waiting"] == 1                              # one person is still waiting for a human: nothing was rejected
    assert jobs.pipeline("H001", job)["candidates"][0]["stage"] == "applied"
    assert "Only HR opens" in jobs.set_status("M001", job, "open")["error"] and jobs.set_status("H001", job, "open")["status"] == "open"
    assert "can be opened or closed" in jobs.set_status("H001", job, "draft")["error"]
    assert [e["event"] for e in jobs.pipeline("H001", job)["events"]] == ["open", "closed", "open", "created"]               # the log, newest first


@test
def a_job_text_that_refers_to_a_protected_characteristic_cannot_be_opened():
    fresh()
    job = jobs.create_job("H001", {**JOB, "description": "We are looking for young and dynamic people. Native English speaker only."})
    assert {w["about"].split(" (")[0] for w in job["warnings"]} == {"age", "the language a person speaks from birth"}      # the form shows these when it saves
    refused = jobs.set_status("H001", job["id"], "open")["error"]
    assert "young and dynamic people" in refused and "L1132-1" in refused and "L5321-2" in refused and jobs.pipeline("H001", job["id"])["job"]["status"] == "draft"
    fixed = jobs.update_job("H001", job["id"], {"description": "Turn product data into decisions. English at C1 level."})
    assert fixed["warnings"] == [] and jobs.set_status("H001", job["id"], "open")["status"] == "open"
    assert jobs.text_problems({"title": "Dev", "description": "Single sign-on and race conditions. Poste H/F. We welcome all, regardless of age or religion.", "must_have": []}) == []


@test
def a_job_can_be_edited_by_hr_or_its_own_manager_and_the_change_is_logged():
    fresh()
    job = jobs.create_job("M001", JOB)["id"]
    assert jobs.update_job("M001", job, {"location": "Lyon"})["location"] == "Lyon"                 # only what was sent changes
    assert jobs.update_job("M001", job, {"location": "Lyon"})["title"] == "Data Analyst"
    assert jobs.update_job("H001", job, {"hiring_manager_id": "C001"})["hiring_manager_id"] == "C001"
    assert jobs.update_job("M001", job, {"title": "x"})["error"] == jobs.NOT_FOUND                    # no longer their job
    log = jobs.pipeline("H001", job)["events"]
    assert [(e["event"], e["actor_name"]) for e in log][:2] == [("edited", "Narmadha"), ("edited", "Lucas")] and log[0]["detail"] == "hiring manager"
    jobs.set_status("H001", job, "open")
    jobs.set_status("H001", job, "closed")
    assert "closed" in jobs.update_job("C001", job, {"title": "Senior Data Analyst"})["error"]        # a manager cannot change a closed job
    assert jobs.update_job("H001", job, {"title": "Senior Data Analyst"})["title"] == "Senior Data Analyst"


@test
def hr_sees_every_job_a_manager_their_own_and_an_employee_nothing():
    fresh()
    mine = jobs.create_job("M001", JOB)["id"]
    other = jobs.create_job("C001", {**JOB, "title": "Product Designer"})["id"]
    assert {j["id"] for j in jobs.list_jobs("H001")} == {mine, other} and [j["id"] for j in jobs.list_jobs("M001")] == [mine]
    assert "for HR and hiring managers" in jobs.list_jobs("E001")["error"]
    for who in ("M001", "E001", "E002"):
        assert jobs.pipeline(who, other) == {"error": jobs.NOT_FOUND}                                # the same answer as a job that does not exist
    assert jobs.pipeline("H001", 999) == {"error": jobs.NOT_FOUND} and jobs.pipeline("M001", mine)["job"]["id"] == mine
    assert jobs.hiring_managers("M001")["error"] and {m["id"] for m in jobs.hiring_managers("H001")} == {"C001", "M001", "H001"}


@test
def a_candidate_is_added_to_an_open_job_once_by_hr_or_its_hiring_manager():
    fresh()
    job = jobs.create_job("M001", JOB)["id"]
    assert "open" in jobs.add_candidate("H001", job, {"name": "Inès Garnier", "email": "ines@example.com"})["error"]        # a draft has no candidates
    jobs.set_status("H001", job, "open")
    c = jobs.add_candidate("M001", job, {"name": " Inès Garnier ", "email": "Ines@Example.com", "phone": "+33 6 12 34 56 78"})
    assert c["name"] == "Inès Garnier" and c["email"] == "ines@example.com" and c["stage"] == "applied" and c["source"] == "added by HR"
    assert "already in this job" in jobs.add_candidate("H001", job, {"name": "Ines G", "email": "INES@example.com"})["error"]
    assert "valid email" in jobs.add_candidate("H001", job, {"name": "Karim Benali", "email": "karim"})["error"]
    assert "name" in jobs.add_candidate("H001", job, {"name": "K", "email": "k@example.com"})["error"]
    other = jobs.create_job("C001", {**JOB, "title": "Product Designer"})["id"]
    jobs.set_status("H001", other, "open")
    assert jobs.add_candidate("M001", other, {"name": "Théo Marchand", "email": "theo@example.com"}) == {"error": jobs.NOT_FOUND}       # not their job
    assert jobs.add_candidate("E001", job, {"name": "Théo Marchand", "email": "theo@example.com"}) == {"error": jobs.NOT_FOUND}


@test
def only_a_person_moves_a_candidate_every_move_is_logged_and_the_reasons_are_required():
    job = an_open_job()
    c = a_candidate(job)
    moved = jobs.move("M001", c, "screening")
    assert moved["stage"] == "screening" and moved["days_in_stage"] == 0
    assert "already in Screening" in jobs.move("H001", c, "screening")["error"] and "Unknown stage" in jobs.move("H001", c, "winner")["error"]
    assert "reason" in jobs.move("H001", c, "not_selected")["error"] and "reason" in jobs.move("H001", c, "not_selected", "  ")["error"]
    assert "Offer first" in jobs.move("H001", c, "hired")["error"]                                    # hired comes after an offer
    jobs.move("H001", c, "interview")
    jobs.move("H001", c, "offer")
    assert jobs.move("H001", c, "hired")["stage"] == "hired" and "hired can't be moved" in jobs.move("H001", c, "interview")["error"]       # final
    log = jobs.history("H001", c)
    assert [(e["event"], e["from_stage"], e["to_stage"], e["actor_name"]) for e in log] == [
        ("added", None, "applied", "Narmadha"), ("moved", "applied", "screening", "Lucas"), ("moved", "screening", "interview", "Narmadha"),
        ("moved", "interview", "offer", "Narmadha"), ("moved", "offer", "hired", "Narmadha")]
    other = a_candidate(job, "Karim Benali", "karim@example.com")
    assert jobs.move("H001", other, "not_selected", "Does not have the SQL experience the job needs")["stage"] == "not_selected"
    assert jobs.history("H001", other)[-1]["reason"].startswith("Does not have") and jobs.history("H001", other)[-1]["to_stage"] == "not_selected"
    assert "reason" in jobs.move("H001", other, "screening")["error"] and jobs.move("H001", other, "screening", "New information")["stage"] == "screening"     # back needs a reason too


@test
def someone_who_does_not_hire_for_the_job_cannot_move_or_read_a_candidate():
    job = an_open_job()
    c = a_candidate(job)
    for who in ("C001", "E001", "E002"):
        assert jobs.move(who, c, "screening") == {"error": "No such candidate."}, who
        assert jobs.history(who, c) == {"error": "No such candidate."}
    assert jobs.move("H001", 999, "screening") == {"error": "No such candidate."}
    assert jobs.pipeline("H001", job)["candidates"][0]["stage"] == "applied"


@test
def candidates_are_listed_in_the_order_they_applied_and_nothing_ranks_anyone():
    job = an_open_job()
    ids = [a_candidate(job, n, f"{n[0].lower()}@example.com") for n in ("Zoé Petit", "Alex Roy", "Maya Dubois")]
    jobs.move("H001", ids[1], "screening")
    jobs.move("H001", ids[2], "screening")
    jobs.move("H001", ids[2], "interview")
    shown = jobs.pipeline("H001", job)
    assert [c["name"] for c in shown["candidates"]] == ["Zoé Petit", "Alex Roy", "Maya Dubois"]       # the order they applied, not by name and not by stage
    text = str(shown).lower() + str(jobs.for_assistant("H001", "Data Analyst")).lower() + str(jobs.list_jobs("H001")).lower()
    for word in ("score", "rank", "rating", "best", "recommend", "fit"):
        assert word not in text.replace("ranks anyone", "").replace("does not rank or score anyone", ""), word


@test
def new_applications_wait_for_hr_and_the_hiring_manager_only_until_a_person_moves_them():
    job = an_open_job()
    other = jobs.create_job("C001", {**JOB, "title": "Product Designer"})["id"]
    jobs.set_status("H001", other, "open")
    a = a_candidate(job)
    b = a_candidate(other, "Théo Marchand", "theo@example.com")
    assert [x["name"] for x in jobs.new_applications("H001")] == ["Inès Garnier", "Théo Marchand"]
    assert [x["name"] for x in jobs.new_applications("M001")] == ["Inès Garnier"] and [x["name"] for x in jobs.new_applications("C001")] == ["Théo Marchand"]
    assert jobs.new_applications("E001") == []
    assert needs_you.waiting_count("H001") == 2 and needs_you.waiting_count("M001") == 1 and needs_you.waiting_count("C001") == 1
    box = needs_you.inbox("M001")
    assert [x["job_title"] for x in box["new_applications"]] == ["Data Analyst"] and box["waiting_on_you"] == 1
    jobs.move("M001", a, "screening")
    assert jobs.new_applications("M001") == [] and needs_you.waiting_count("M001") == 0 and needs_you.waiting_count("H001") == 1
    assert needs_you.for_assistant("H001")["new_applications"] == [{"candidate": "Théo Marchand", "job": "Product Designer", "applied_on": date.today().isoformat()}]
    assert b


@test
def the_assistant_reads_names_stages_and_dates_only_and_only_for_jobs_the_person_may_see():
    job = an_open_job()
    a_candidate(job)
    a_candidate(job, "Karim Benali", "karim@example.com")
    jobs.move("H001", jobs.pipeline("H001", job)["candidates"][1]["id"], "interview")
    many = jobs.for_assistant("H001")
    assert many["jobs"][0]["candidates_by_stage"] == {"Applied": 1, "Interview": 1} and "does not rank" in many["note"]
    one = jobs.for_assistant("M001", "data analyst")["jobs"][0]
    assert [(c["name"], c["stage"]) for c in one["candidates"]] == [("Inès Garnier", "Applied"), ("Karim Benali", "Interview")]
    assert set(one["candidates"][0]) == {"name", "stage", "applied_on", "days_in_stage"}              # no email, no phone, no notes
    assert jobs.for_assistant("M001", str(job))["jobs"][0]["job_id"] == job
    assert "No job of that name" in jobs.for_assistant("C001", "Data Analyst")["error"]              # not their job
    assert "No job of that name" in jobs.for_assistant("H001", "Astronaut")["error"]
    for who in ("E001", "E002", "E003"):
        assert "for HR and hiring managers" in jobs.for_assistant(who)["error"] and jobs.candidate_names(who) == []
    assert sorted(jobs.candidate_names("H001")) == ["Inès Garnier", "Karim Benali"] and jobs.candidate_names("C001") == []


# ---------- recruitment: the careers page, tracking, erasing and retention (careers.py, retention.py) ----------

def a_cv(text="Data analyst. Three years of SQL and Python."):
    return pdf_text.make_pdf([("h1", "Inès Garnier"), ("p", text)])


APPLICATION = {"name": "Inès Garnier", "email": "Ines.Garnier@example.com", "phone": "+33 6 12 34 56 78", "cover_letter": "I would like to join.", "consent": True}


def cv_files():
    return {n for n in os.listdir(documents.files_dir()) if n.startswith("cv_")}


def apply_to(job_id, talent_pool=False, **changes):
    done = careers.apply(job_id, {**APPLICATION, "talent_pool": talent_pool, **changes}, a_cv())
    assert "token" in done, done
    return done["token"]


def application_id(job_id, index=-1):
    return jobs.pipeline("H001", job_id)["candidates"][index]["id"]


def set_columns(app_id, **columns):
    conn = db.get_conn()
    with conn:
        conn.execute(f"UPDATE applications SET {', '.join(f'{k} = ?' for k in columns)} WHERE id = ?", (*columns.values(), app_id))
    conn.close()


def days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")


@test
def the_careers_page_shows_open_jobs_only_and_never_who_hires_or_who_applied():
    fresh()
    draft = jobs.create_job("H001", JOB)["id"]
    first = jobs.create_job("H001", {**JOB, "title": "Support Engineer"})["id"]
    jobs.set_status("H001", first, "open")
    second = jobs.create_job("H001", {**JOB, "title": "Old job"})["id"]
    jobs.set_status("H001", second, "open")
    jobs.set_status("H001", second, "closed")
    assert [j["title"] for j in careers.open_jobs()] == ["Support Engineer"]
    assert careers.open_job(draft) == {"error": careers.NOT_OPEN} and careers.open_job(second) == {"error": careers.NOT_OPEN} and careers.open_job(999) == {"error": careers.NOT_OPEN}
    shown = careers.open_job(first)
    assert set(shown) == {"id", "title", "team", "location", "contract_type", "description", "must_have", "nice_to_have", "salary_min", "salary_max"}
    apply_to(first)
    assert "Garnier" not in str(careers.open_jobs()) and "M001" not in str(careers.open_jobs()) and "Lucas" not in str(shown)


@test
def an_application_is_saved_with_the_consent_the_cv_and_a_tracking_token_that_is_only_stored_as_a_hash():
    job = an_open_job()
    before = cv_files()
    token = apply_to(job, talent_pool=True)
    app = jobs.pipeline("H001", job)["candidates"][0]
    assert (app["name"], app["email"], app["stage"], app["source"]) == ("Inès Garnier", "ines.garnier@example.com", "applied", "careers page")
    assert app["talent_pool"] is True and app["has_cv"] is True and app["consented_on"] == date.today().isoformat() and app["cover_letter"] == "I would like to join."
    new = cv_files() - before
    assert len(new) == 1 and open(os.path.join(documents.files_dir(), new.pop()), "rb").read() == a_cv()
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM applications").fetchone()
    assert token not in str(dict(row)) and row["track_hash"] == careers._hash(token) and row["notice_version"] == careers.NOTICE_VERSION
    first = conn.execute("SELECT * FROM application_events").fetchone()
    conn.close()
    assert (first["event"], first["actor_id"], first["to_stage"]) == ("added", None, "applied") and "talent pool: yes" in first["reason"]
    assert [x["name"] for x in jobs.new_applications("M001")] == ["Inès Garnier"]                       # it waits in Needs you like any application
    again = careers.apply(job, {**APPLICATION, "talent_pool": False}, a_cv())                              # the same person may apply twice: each gets their own link
    assert again["token"] != token and len(jobs.pipeline("H001", job)["candidates"]) == 2


@test
def an_application_needs_consent_a_real_cv_and_an_open_job_and_a_refusal_leaves_nothing_behind():
    job = an_open_job()
    before = cv_files()
    for change, word in [({"consent": False}, "Tick the box"), ({"consent": "yes"}, "Tick the box"), ({"consent": None}, "Tick the box"), ({"name": "I"}, "Give your name"),
                         ({"email": "nobody"}, "valid email"), ({"email": ""}, "valid email"), ({"phone": "1" * 41}, "phone number is too long"),
                         ({"cover_letter": "x" * 3001}, "cover letter is too long")]:
        assert word in careers.apply(job, {**APPLICATION, **change}, a_cv())["error"], change
    for cv, word in [(b"", "Attach your CV"), (b"MZ not a pdf", "must be a PDF"), (a_cv() + b"/JavaScript", "script"), (a_cv() + b"/Encrypt", "password")]:
        assert word in careers.apply(job, APPLICATION, cv)["error"], word
    assert careers.apply(999, APPLICATION, a_cv()) == {"error": careers.NOT_OPEN}
    jobs.set_status("H001", job, "closed")
    assert careers.apply(job, APPLICATION, a_cv()) == {"error": careers.NOT_OPEN}
    assert jobs.pipeline("H001", job)["candidates"] == [] and cv_files() == before                        # no application, no file left behind


@test
def a_candidate_sees_where_they_stand_in_plain_words_and_never_the_reason():
    job = an_open_job()
    token = apply_to(job)
    view = careers.tracking(token)
    assert (view["job_title"], view["stage"], view["stage_label"], view["name"]) == ("Data Analyst", "applied", "Received", "Inès Garnier")
    assert "A person at CognivexAI will read it" in view["message"] and view["can_withdraw"] is True and view["talent_pool"] is False
    cid = application_id(job)
    jobs.move("M001", cid, "screening")
    assert careers.tracking(token)["stage_label"] == "Being reviewed"
    jobs.move("M001", cid, "not_selected", "Does not have the SQL experience the job needs")
    seen = careers.tracking(token)
    assert seen["stage_label"] == "Not selected" and "SQL" not in str(seen) and "reason" not in str(seen).lower()                          # the reason stays internal
    assert seen["erase_on"] == (date.today() + timedelta(days=30)).isoformat()                           # and they are told when their data goes
    for bad in ("nope", "", None, "A" * 43):
        assert careers.tracking(bad) == {"error": careers.NOT_FOUND}, bad


@test
def withdrawing_erases_every_trace_of_the_person_and_only_a_record_without_personal_data_stays():
    job = an_open_job()
    before = cv_files()
    token = apply_to(job, talent_pool=True)
    other = apply_to(job, name="Karim Benali", email="karim@example.com")
    cid = application_id(job, 0)
    assert len(cv_files() - before) == 2
    assert careers.withdraw(token) == {"erased": True}
    assert len(cv_files() - before) == 1                                                                     # their CV file is gone, the other's is not
    conn = db.get_conn()
    row = dict(conn.execute("SELECT * FROM applications WHERE id = ?", (cid,)).fetchone())
    erasure = dict(conn.execute("SELECT * FROM candidate_erasures WHERE application_id = ?", (cid,)).fetchone())
    log = [dict(r) for r in conn.execute("SELECT * FROM application_events WHERE application_id = ?", (cid,))]
    conn.close()
    assert (row["name"], row["email"], row["phone"], row["cover_letter"], row["cv_file"], row["track_hash"]) == ("(erased)", "", "", "", None, None) and row["erased_at"]
    assert erasure["reason"] == "Withdrawn and erased by the candidate" and erasure["stage"] == "applied"
    for place in (str(erasure), str(log)):
        assert "Garnier" not in place and "ines" not in place.lower() and "+33" not in place                    # the log of the erasure holds nothing personal
    assert careers.tracking(token) == {"error": careers.NOT_FOUND} and careers.withdraw(token) == {"error": careers.NOT_FOUND}
    shown = jobs.pipeline("H001", job)
    assert [c["name"] for c in shown["candidates"]] == ["Karim Benali"] and shown["job"]["counts"]["applied"] == 1
    assert [x["name"] for x in jobs.new_applications("H001")] == ["Karim Benali"] and jobs.candidate_names("H001") == ["Karim Benali"]
    assert "Garnier" not in str(jobs.for_assistant("H001", "Data Analyst")) and careers.tracking(other)["name"] == "Karim Benali"
    hired = jobs.move("H001", application_id(job), "screening") and application_id(job)
    for stage in ("interview", "offer", "hired"):
        jobs.move("H001", hired, stage)
    assert "contact HR" in careers.withdraw(other)["error"] and careers.tracking(other)["can_withdraw"] is False                # a hired person is an employee now


@test
def a_candidate_can_leave_the_talent_pool_and_hr_can_give_a_new_link_that_replaces_the_old_one():
    job = an_open_job()
    token = apply_to(job, talent_pool=True)
    cid = application_id(job)
    assert careers.tracking(token)["talent_pool"] is True
    left = careers.leave_talent_pool(token)
    assert left["talent_pool"] is False and left["erase_on"] == retention.erase_on("applied", False, date.today()).isoformat()          # no longer the pool's 24 months
    assert [e["event"] for e in jobs.history("H001", cid)] == ["added", "talent pool left"]
    assert careers.leave_talent_pool("nope") == {"error": careers.NOT_FOUND}
    assert careers.new_tracking_token("M001", cid) == {"error": "Only HR gives a candidate a new link."}
    assert careers.new_tracking_token("E001", cid) == {"error": "No such candidate."} and careers.new_tracking_token("C001", cid) == {"error": "No such candidate."}
    fresh_link = careers.new_tracking_token("H001", cid)["token"]
    assert careers.tracking(token) == {"error": careers.NOT_FOUND} and careers.tracking(fresh_link)["name"] == "Inès Garnier"       # the old link stops working
    assert [e["event"] for e in jobs.history("H001", cid)] == ["added", "talent pool left", "new link"]


@test
def only_hr_and_the_hiring_manager_open_a_cv_and_each_opening_is_logged():
    job = an_open_job()
    apply_to(job)
    jobs.add_candidate("H001", job, {"name": "Théo Marchand", "email": "theo@example.com"})                 # added by hand: no CV
    first, manual = application_id(job, 0), application_id(job, 1)
    opened = careers.open_cv("M001", first)
    assert opened["filename"] == f"cv-{first}.pdf" and open(opened["path"], "rb").read() == a_cv()
    assert careers.open_cv("H001", first)["path"] == opened["path"]
    for who in ("C001", "E001", "E002"):
        assert careers.open_cv(who, first) == {"error": "No such candidate."}, who
    assert careers.open_cv("H001", manual) == {"error": "This candidate has no CV."} and careers.open_cv("H001", 999) == {"error": "No such candidate."}
    log = [(e["event"], e["actor_name"]) for e in jobs.history("H001", first)]
    assert log == [("added", None), ("cv opened", "Lucas"), ("cv opened", "Narmadha")]


@test
def the_daily_job_erases_each_candidate_on_the_day_the_rules_say_and_logs_it_without_a_name():
    job = an_open_job()
    for i, pool in enumerate([True, True, False, False, False, False]):
        apply_to(job, talent_pool=pool, name=f"Person {i}", email=f"p{i}@example.com")
    ids = {c["name"]: c["id"] for c in jobs.pipeline("H001", job)["candidates"]}
    before = cv_files()
    set_columns(ids["Person 0"], applied_at=days_ago(800), last_contact_at=days_ago(800))                 # in the talent pool, 26 months since the last contact
    set_columns(ids["Person 1"], applied_at=days_ago(800), last_contact_at=days_ago(300))                 # in the talent pool, 10 months
    for name, age in (("Person 2", 31), ("Person 3", 29)):                                                 # not selected 31 and 29 days ago, no talent pool
        jobs.move("H001", ids[name], "not_selected", "Does not have the experience the job needs")
        set_columns(ids[name], stage_changed_at=days_ago(age))
    for stage in ("screening", "interview", "offer", "hired"):
        jobs.move("H001", ids["Person 5"], stage)
    set_columns(ids["Person 5"], applied_at=days_ago(900), last_contact_at=days_ago(900))                 # hired long ago: never erased here
    erase_on = {c["name"]: c["erase_on"] for c in jobs.pipeline("H001", job)["candidates"]}
    assert erase_on["Person 5"] is None and erase_on["Person 0"] < date.today().isoformat() < erase_on["Person 1"]
    done = careers.run_retention(ran_by="H001")
    assert done["erased"] == 2 and done["by_rule"] == {"talent pool limit": 1, "not selected": 1}, done            # Person 4: no decision, open job, nothing is due yet
    assert sorted(c["name"] for c in jobs.pipeline("H001", job)["candidates"]) == ["Person 1", "Person 3", "Person 4", "Person 5"]
    assert len(before - cv_files()) == 2                                                                   # the two erased people's CV files are gone
    assert careers.run_retention()["erased"] == 0                                                           # safe to run every day
    # the job closes 91 days ago: the person who never got a decision goes, the hired one stays
    jobs.set_status("H001", job, "closed")
    set_columns_on_job(job, closed_at=days_ago(91))
    assert careers.run_retention(ran_by="H001")["by_rule"] == {"job closed": 2}                             # Person 4 (undecided) and Person 3 (not selected 29 days ago, but the job closed long ago)
    assert sorted(c["name"] for c in jobs.pipeline("H001", job)["candidates"]) == ["Person 1", "Person 5"]    # the talent pool member (10 months) and the hired one stay
    erasures = careers._rows("SELECT * FROM candidate_erasures ORDER BY id")
    assert len(erasures) == 4 and {e["erased_by"] for e in erasures} == {"H001"} and all(e["reason"].startswith("Retention (") for e in erasures)
    assert "Person" not in str(erasures) and "@example.com" not in str(erasures) and careers.settings("H001")["erased_so_far"] == 4


def set_columns_on_job(job_id, **columns):
    conn = db.get_conn()
    with conn:
        conn.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in columns)} WHERE id = ?", (*columns.values(), job_id))
    conn.close()


@test
def hr_sets_how_long_a_talent_pool_is_kept_up_to_the_cnil_limit_and_nobody_else_can():
    fresh()
    assert careers.settings("H001")["retention_months"] == 24 and careers.settings("H001")["max_months"] == 24 and "Recrutement" in careers.settings("H001")["cite"]
    assert careers.settings("M001") == {"error": "Only HR can see this."} and "Only HR" in careers.set_retention("M001", 6)["error"]
    for bad in (0, 25, "x", "", 2.5, -3):
        assert "1 to 24" in careers.set_retention("H001", bad)["error"], bad
    assert careers.set_retention("H001", "12")["retention_months"] == 12 and jobs.retention_months() == 12
    job = an_open_job()
    careers.set_retention("H001", 6)
    apply_to(job, talent_pool=True)
    cid = application_id(job)
    set_columns(cid, applied_at=days_ago(200), last_contact_at=days_ago(200))                               # 6.5 months: past the setting, well inside 24
    assert careers.run_retention()["erased"] == 1 and jobs.pipeline("H001", job)["candidates"] == []


@test
def moving_a_candidate_to_a_stage_that_means_contact_restarts_the_clock_and_screening_does_not():
    job = an_open_job()
    apply_to(job, talent_pool=True)
    cid = application_id(job)
    set_columns(cid, applied_at=days_ago(100), last_contact_at=days_ago(100))
    jobs.move("H001", cid, "screening")
    assert careers._rows("SELECT last_contact_at FROM applications")[0]["last_contact_at"][:10] == days_ago(100)[:10]               # an internal move is not a contact
    jobs.move("H001", cid, "interview")
    assert careers._rows("SELECT last_contact_at FROM applications")[0]["last_contact_at"][:10] == date.today().isoformat()


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