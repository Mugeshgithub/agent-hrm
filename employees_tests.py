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
import contracts
import documents
import needs_you
import onboarding
import pdf_text
import outbox
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