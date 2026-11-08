# server_tests.py: checks the web API's rules. No AI, no API keys, runs in CI.
#   python server_tests.py
import os
import re
import sys
import tempfile
from datetime import date, timedelta

os.environ["HRM_DB"] = os.path.join(tempfile.mkdtemp(), "server_test_hr.db")
os.environ["HRM_FILES_DIR"] = tempfile.mkdtemp()                       # documents go to a throwaway folder
import db
db.DB_PATH = os.environ["HRM_DB"]

from fastapi.testclient import TestClient
import demo_data
import documents
import employee_files
import pdf_text
demo_data.init_all(reset=True)
demo_data.seed_activity()
import server

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def in_days(n):
    return (date.today() + timedelta(days=n)).isoformat()


def as_person(emp_id):
    c = TestClient(server.app)
    assert c.post("/api/session", json={"employee_id": emp_id}).status_code == 200
    return c


@test
def nothing_works_without_a_session():
    c = TestClient(server.app)
    for path in ("/api/me", "/api/home", "/api/leave", "/api/payslips", "/api/tickets", "/api/people"):
        assert c.get(path).status_code == 401, path


@test
def identity_comes_from_the_session_not_the_request():
    # Emma asks for "E002"'s payslip through her own endpoint: she still only gets her own
    c = as_person("E001")
    assert c.get("/api/me").json()["id"] == "E001"
    assert c.get("/api/payslips/2026-10").json()["net_paid_eur"] == "€2,195.00"


@test
def employees_cannot_use_manager_or_hr_pages():
    c = as_person("E001")
    for path in ("/api/approvals", "/api/team", "/api/hr/tickets", "/api/hr/access-log", "/api/hr/payslips/E002/2026-10"):
        assert c.get(path).status_code == 403, path
    assert c.post("/api/approvals/1", json={"decision": "approve"}).status_code == 403


@test
def managers_cannot_open_payslips_of_their_team():
    c = as_person("M001")
    assert c.get("/api/hr/payslips/E001/2026-10").status_code == 403


@test
def balances_count_pending_days():
    b = as_person("E001").get("/api/leave").json()["balances"]["casual"]
    assert b["available"] == b["balance"] - b["pending"]


@test
def leave_request_follows_the_same_rules_as_the_agent():
    c = as_person("E003")                       # Clara has 0 casual days
    r = c.post("/api/leave", json={"leave_type": "casual", "start_date": "2027-03-01", "end_date": "2027-03-01"})
    assert r.status_code == 400 and "Not enough" in r.json()["detail"]


@test
def manager_decision_notifies_the_employee():
    c = as_person("M001")
    pending = c.get("/api/approvals").json()
    hugo = next(p for p in pending if p["employee_id"] == "E002")
    assert c.post(f"/api/approvals/{hugo['request_id']}", json={"decision": "approve"}).status_code == 200
    notes = as_person("E002").get("/api/notifications").json()
    assert any("approved by Lucas" in n["message"] for n in notes)


@test
def rejecting_needs_a_reason():
    c = as_person("M001")
    clara = next(p for p in c.get("/api/approvals").json() if p["employee_id"] == "E003")
    r = c.post(f"/api/approvals/{clara['request_id']}", json={"decision": "reject", "reason": ""})
    assert r.status_code == 400


@test
def hr_payslip_views_are_logged():
    c = as_person("H001")
    before = len(c.get("/api/hr/access-log").json())
    assert c.get("/api/hr/payslips/E001/2026-10").status_code == 200
    log = c.get("/api/hr/access-log").json()
    assert len(log) == before + 1 and log[0]["viewer_id"] == "H001" and log[0]["emp_id"] == "E001"


@test
def only_hr_sees_balances_in_the_directory():
    assert "casual_balance" not in as_person("E001").get("/api/people").json()[0]
    assert "casual_balance" in as_person("H001").get("/api/people").json()[0]


@test
def hr_reply_closes_the_ticket_and_notifies():
    c = as_person("H001")
    t = c.get("/api/hr/tickets?status=open").json()[0]
    assert c.post(f"/api/hr/tickets/{t['id']}/reply", json={"reply": "Yes, see the handbook."}).status_code == 200
    assert all(x["id"] != t["id"] for x in c.get("/api/hr/tickets?status=open").json())


@test
def only_hr_can_change_records():
    assert as_person("E001").post("/api/hr/people", json={}).status_code in (403, 422)
    assert as_person("M001").patch("/api/hr/people/E001",
                                   json={"changes": {"title": "Boss"}, "reason": "x"}).status_code == 403
    assert as_person("M001").post("/api/hr/people/E001/offboard",
                                  json={"end_date": "2027-01-31", "reason": "x"}).status_code == 403


@test
def hr_hires_and_edits_and_history_shows_who():
    hr = as_person("H001")
    r = hr.post("/api/hr/people", json={"name": "Anu", "email": "anu@cognivexai.eu", "role": "employee",
                                        "title": "QA Engineer", "department": "Engineering",
                                        "location": "Paris", "start_date": in_days(30), "manager_id": "M001"})
    assert r.status_code == 200, r.json()
    new_id = r.json()["id"]
    r = hr.patch(f"/api/hr/people/{new_id}", json={"changes": {"title": "Senior QA"}, "reason": "Offer revised"})
    assert r.status_code == 200, r.json()
    log = hr.get(f"/api/people/{new_id}/history").json()["changes"]
    assert len(log) == 3 and all(c["changed_by"] == "Narmadha" for c in log)      # created, checklist started, title
    # a colleague sees the safe view, but not the history
    emma = as_person("E001")
    card = emma.get(f"/api/people/{new_id}").json()
    assert card["title"] == "Senior QA" and "casual_balance" not in card
    assert emma.get(f"/api/people/{new_id}/history").status_code == 400


@test
def rule_errors_come_back_with_the_reason():
    r = as_person("H001").post("/api/hr/people/M001/offboard", json={"end_date": "2027-01-31", "reason": "test"})
    assert r.status_code == 400 and "still manages" in r.json()["detail"]

@test
def colleagues_see_the_safe_profile_hr_sees_more():
    card = as_person("E002").get("/api/people/E001").json()
    assert card["manager_name"] == "Lucas" and "contract_type" not in card and "balances" not in card
    assert "contract_type" not in as_person("E002").get("/api/people").json()[0]
    full = as_person("H001").get("/api/people/E001").json()
    assert full["contract_type"] == "CDI" and "casual" in full["balances"]
    reports = {r["id"] for r in as_person("E001").get("/api/people/M001").json()["reports"]}
    assert {"E001", "E002", "E003"} <= reports


@test
def people_who_have_left_lose_access_and_leave_their_team():
    hr = as_person("H001")
    r = hr.post("/api/hr/people", json={"name": "Temp", "email": "temp@cognivexai.eu", "role": "employee",
                                        "title": "Intern", "department": "Engineering", "location": "Paris",
                                        "start_date": "2025-01-06", "manager_id": "M001", "contract_type": "Internship"})
    temp = r.json()["id"]
    temp_client = as_person(temp)                                   # signed in while still employed
    assert hr.post(f"/api/hr/people/{temp}/offboard", json={"end_date": "2025-06-30", "reason": "End of internship"}).status_code == 200
    assert temp_client.get("/api/me").status_code == 401           # the open session stops working
    assert TestClient(server.app).post("/api/session", json={"employee_id": temp}).status_code == 403
    assert temp not in [m["id"] for m in as_person("M001").get("/api/team").json()]
    assert temp not in [p["id"] for p in as_person("E001").get("/api/people").json()]
    assert as_person("E001").get(f"/api/people/{temp}").status_code == 404
    assert any(p["id"] == temp and not p["active"] for p in hr.get("/api/people").json())   # HR still sees the record


@test
def hr_adjusts_a_balance_and_the_person_sees_why():
    r = as_person("H001").post("/api/hr/people/E002/balance", json={"leave_type": "sick", "days": 1, "reason": "Correction"})
    assert r.status_code == 200 and r.json()["change"] == 1, r.json()
    line = as_person("E002").get("/api/leave/history").json()["adjustments"][0]
    assert line["reason"] == "Correction" and line["changed_by"] == "Narmadha"
    assert as_person("M001").post("/api/hr/people/E002/balance", json={"leave_type": "sick", "days": 1, "reason": "x"}).status_code == 403
    assert as_person("E001").get("/api/hr/people/E002/leave-history").status_code == 403


@test
def holiday_plans_floaters_and_who_can_edit():
    mine = as_person("E001").get("/api/holidays?year=2026").json()
    assert mine["plan"]["name"] == "France" and mine["floaters"]["quota"] == 2
    assert any(h["day"] == "2026-12-24" and h["kind"] == "floater" for h in mine["holidays"])
    assert as_person("E001").get("/api/hr/holiday-plans").status_code == 403
    hr = as_person("H001")
    france = next(p for p in hr.get("/api/hr/holiday-plans").json() if p["name"] == "France")
    r = hr.post(f"/api/hr/holiday-plans/{france['id']}/holidays", json={"day": "2026-12-28", "name": "Day off", "kind": "floater"})
    assert r.status_code == 200, r.json()
    r = as_person("E003").post("/api/holidays/floater", json={"day": "2026-12-28"})
    assert r.status_code == 200, r.json()
    r = hr.delete(f"/api/hr/holiday-plans/{france['id']}/holidays/2026-12-28")
    assert r.status_code == 400 and "chosen" in r.json()["detail"]
    preview = hr.post(f"/api/hr/holiday-plans/{france['id']}/import", json={"csv": "date,name\n2027-07-16,Summer day"}).json()
    assert preview["will_add"] == 1 and preview["dry_run"]


@test
def year_start_cannot_run_early_from_the_web():
    hr = as_person("H001")
    assert hr.get("/api/hr/leave/year-start?year=2027").json()["people"] >= 6
    r = hr.post("/api/hr/leave/year-start", json={"year": 2030})
    assert r.status_code == 400 and "1 January 2030" in r.json()["detail"]


@test
def leave_preview_counts_paid_leave_monday_to_saturday_with_its_own_balance():
    c = as_person("E001")
    # Mon 4 to Sat 9 March 2030: paid leave uses 6 days, casual leave only 5 (no Saturday)
    week = {"start_date": "2030-03-04", "end_date": "2030-03-09"}
    paid = c.post("/api/leave/preview", json={"leave_type": "paid", **week}).json()
    casual = c.post("/api/leave/preview", json={"leave_type": "casual", **week}).json()
    assert paid["working_days"] == 6 and casual["working_days"] == 5, (paid, casual)
    assert paid["available"] > 0 and paid["after"] == paid["available"] - 6 and paid["enough"] == (paid["available"] >= 6)
    assert c.post("/api/leave/preview", json={"leave_type": "unpaid", **week}).status_code == 400
    home = c.get("/api/home").json()["balances"]
    assert set(home) == {"casual", "sick", "paid"} and home["paid"]["balance"] == 12.5



@test
def hr_can_credit_paid_leave_only_for_months_that_have_ended_and_only_once():
    for who in ("E001", "M001"):                    # employees and managers have no access
        c = as_person(who)
        assert c.get("/api/hr/leave/month-accrual").status_code == 403
        assert c.post("/api/hr/leave/month-accrual").status_code == 403
    hr = as_person("H001")
    status = hr.get("/api/hr/leave/month-accrual").json()
    assert status["last_credited"] is None and status["next"]["can_run_now"]      # last month has ended
    assert status["next"]["people_earning"] == 6 and status["next"]["days_in_total"] > 0
    before = hr.get("/api/hr/people/E001/leave-history").json()["balance"]["paid"]
    done = hr.post("/api/hr/leave/month-accrual")
    assert done.status_code == 200 and done.json()["success"], done.text
    assert hr.get("/api/hr/people/E001/leave-history").json()["balance"]["paid"] == round(before + 2.5, 2)
    line = next(a for a in hr.get("/api/hr/people/E001/leave-history").json()["adjustments"] if a["kind"] == "accrual")
    assert line["changed_by"] == "Narmadha" and line["days"] == 2.5                # logged with who ran it
    # a month can be credited once: credit what has ended (a month more if today is its last day),
    # then the next one is refused until it ends
    while hr.get("/api/hr/leave/month-accrual").json()["next"]["can_run_now"]:
        assert hr.post("/api/hr/leave/month-accrual").status_code == 200
    status = hr.get("/api/hr/leave/month-accrual").json()
    assert status["last_credited"]["ran_by"] == "Narmadha"
    refused = hr.post("/api/hr/leave/month-accrual")
    assert refused.status_code == 400 and f"from {status['next']['can_run_from']}" in refused.json()["detail"]



@test
def needs_you_is_for_managers_and_hr_and_approving_uses_the_same_rules_as_before():
    assert as_person("E001").get("/api/needs-you").status_code == 403               # employees have no inbox
    lucas = as_person("M001")
    box = lucas.get("/api/needs-you").json()
    assert box["role"] == "manager" and box["hr_requests"] == []
    assert box["waiting_on_you"] == len(box["leave_requests"]) + len(box["onboarding_steps"])
    # Emma asks for paid leave (Monday to Saturday = 6 days): it shows up for Lucas, her manager
    sent = as_person("E001").post("/api/leave", json={"leave_type": "paid", "start_date": "2030-05-06", "end_date": "2030-05-11"})
    assert sent.status_code == 200, sent.text
    rid = sent.json()["request"]["id"]
    after = lucas.get("/api/needs-you").json()
    assert after["waiting_on_you"] == box["waiting_on_you"] + 1
    item = next(x for x in after["leave_requests"] if x["request_id"] == rid)
    assert (item["employee_name"], item["leave_type"], item["days"]) == ("Emma", "paid", 6)
    assert item["balances"]["paid"]["pending"] >= 6 and "overlaps" in item
    # one-click approve and reject are the Approvals endpoint: same rules
    assert lucas.post(f"/api/approvals/{rid}", json={"decision": "reject", "reason": ""}).status_code == 400   # a reason is needed
    assert as_person("C001").post(f"/api/approvals/{rid}", json={"decision": "approve"}).status_code == 400    # not her manager
    assert lucas.post(f"/api/approvals/{rid}", json={"decision": "approve"}).status_code == 200
    assert lucas.get("/api/needs-you").json()["waiting_on_you"] == box["waiting_on_you"]
    assert lucas.get("/api/home").json()["waiting_on_you"] == box["waiting_on_you"]            # the menu badge
    assert all(x["request_id"] != rid for x in lucas.get("/api/approvals").json())            # the Approvals page agrees


@test
def hr_inbox_has_open_hr_requests_and_the_over_30_paid_days_warning():
    hr = as_person("H001")
    box = hr.get("/api/needs-you").json()
    assert box["role"] == "hr" and box["leave_requests"] == []
    sent = as_person("E002").post("/api/tickets", json={"subject": "Parking", "details": "Can I get a parking allowance?"})
    assert sent.status_code == 200, sent.text
    after = hr.get("/api/needs-you").json()
    assert len(after["hr_requests"]) == len(box["hr_requests"]) + 1 and after["waiting_on_you"] == box["waiting_on_you"] + 1
    assert after["hr_requests"][-1]["summary"] == "Parking"                                   # newest last: oldest first
    # Hugo goes above 30 unused paid days: HR and his manager see the warning, nobody else does
    current = hr.get("/api/hr/people/E002/leave-history").json()["balance"]["paid"]
    assert hr.post("/api/hr/people/E002/balance", json={"leave_type": "paid", "days": 31 - current, "reason": "test"}).status_code == 200
    message = "Hugo has 31 unused paid days, more than 30. Worth planning some leave."
    assert message in [w["message"] for w in hr.get("/api/needs-you").json()["paid_leave_warnings"]]
    assert message in [w["message"] for w in as_person("M001").get("/api/needs-you").json()["paid_leave_warnings"]]
    assert all(w["name"] != "Hugo" for w in as_person("C001").get("/api/needs-you").json()["paid_leave_warnings"])


@test
def onboarding_checklist_is_for_hr_and_the_steps_follow_the_rules_in_code():
    hr = as_person("H001")
    made = hr.post("/api/hr/people", json={"name": "Lea", "email": "lea@cognivexai.eu", "role": "employee", "title": "Designer",
                                           "department": "Engineering", "location": "Paris", "start_date": in_days(20),
                                           "manager_id": "M001"})
    assert made.status_code == 200, made.text
    lea = made.json()["id"]
    box = hr.get(f"/api/hr/people/{lea}/onboarding").json()
    assert box["progress"] == {"done": 0, "skipped": 0, "total": 9, "complete": False} and box["manager_name"] == "Lucas"
    assert hr.get("/api/hr/people/E001/onboarding").json() is None                 # started long ago: no checklist
    for who in ("E001", "M001"):
        assert as_person(who).get(f"/api/hr/people/{lea}/onboarding").status_code == 403, who
    contract = next(s["id"] for s in box["steps"] if s["step_key"] == "contract")
    equipment = next(s["id"] for s in box["steps"] if s["step_key"] == "equipment")
    # the rules are in onboarding.py: the identity is the session's, never something the request says
    lucas, emma = as_person("M001"), as_person("E001")
    assert emma.post(f"/api/onboarding/steps/{equipment}/complete", json={}).status_code == 400
    assert lucas.post(f"/api/onboarding/steps/{contract}/complete", json={}).status_code == 400          # an HR step
    assert lucas.post(f"/api/onboarding/steps/{equipment}/skip", json={"reason": "no"}).status_code == 400
    assert hr.post(f"/api/onboarding/steps/{contract}/skip", json={"reason": ""}).status_code == 400    # a reason is needed
    inbox = lucas.get("/api/needs-you").json()
    assert {s["step_title"] for s in inbox["onboarding_steps"] if s["employee_id"] == lea} == {
        "Laptop and equipment ready", "Welcome message sent"}
    before = lucas.get("/api/home").json()["waiting_on_you"]
    assert lucas.post(f"/api/onboarding/steps/{equipment}/complete", json={"note": "Laptop ordered"}).status_code == 200
    assert lucas.get("/api/home").json()["waiting_on_you"] == before - 1                                  # the menu badge
    assert hr.post(f"/api/onboarding/steps/{contract}/complete", json={}).status_code == 200
    assert hr.post(f"/api/onboarding/steps/{contract}/reopen", json={"reason": "Wrong file"}).status_code == 200
    done = next(s for s in hr.get(f"/api/hr/people/{lea}/onboarding").json()["steps"] if s["id"] == equipment)
    assert done["status"] == "done" and done["done_by_name"] == "Lucas" and done["note"] == "Laptop ordered"
    assert TestClient(server.app).post(f"/api/onboarding/steps/{equipment}/complete", json={}).status_code == 401


def make_hire(hr, email, name="Mia Roy", days=25):
    r = hr.post("/api/hr/people", json={"name": name, "email": email, "role": "employee", "title": "Designer",
                                        "department": "Engineering", "location": "Paris", "start_date": in_days(days),
                                        "manager_id": "M001"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def open_welcome(hr, emp_id):
    # HR issues the link, the "email" waits in the outbox, the new hire opens it: returns the signed-in client
    issued = hr.post(f"/api/hr/people/{emp_id}/welcome-link")
    assert issued.status_code == 200, issued.text
    body = next(m["body"] for m in hr.get("/api/hr/outbox").json() if m["id"] == issued.json()["outbox_id"])
    token = re.search(r"/welcome/([A-Za-z0-9_-]+)", body).group(1)
    return token, issued.json()["code"]


@test
def hr_issues_a_welcome_link_the_email_waits_in_the_outbox_and_only_hr_sees_it():
    hr = as_person("H001")
    emp_id = make_hire(hr, "mia@cognivexai.eu")
    for who in ("E001", "M001"):
        c = as_person(who)
        assert c.post(f"/api/hr/people/{emp_id}/welcome-link").status_code == 403, who
        assert c.get("/api/hr/outbox").status_code == 403, who
        assert c.get(f"/api/hr/people/{emp_id}/welcome-link").status_code == 403, who
    assert hr.get(f"/api/hr/people/{emp_id}/welcome-link").json() is None                 # nothing yet
    token, code = open_welcome(hr, emp_id)
    status = hr.get(f"/api/hr/people/{emp_id}/welcome-link").json()
    assert status["state"] == "ready" and status["wrong_codes"] == 0 and "token" not in str(status) and code not in str(status)
    mail = hr.get("/api/hr/outbox").json()[0]
    assert mail["to_email"] == "mia@cognivexai.eu" and f"/welcome/{token}" in mail["body"] and code not in mail["body"]
    assert hr.post(f"/api/hr/outbox/{mail['id']}/sent").status_code == 200
    assert token not in hr.get("/api/hr/outbox").json()[0]["body"]                         # scrubbed once sent
    assert as_person("E001").post(f"/api/hr/outbox/{mail['id']}/sent").status_code == 403
    nobody = hr.post("/api/hr/people/E001/welcome-link")                                    # E001 has no checklist
    assert nobody.status_code == 400 and "no onboarding checklist" in nobody.json()["detail"]


@test
def a_welcome_link_needs_the_right_code_and_the_session_can_only_read_its_own_checklist():
    hr = as_person("H001")
    emp_id = make_hire(hr, "noa@cognivexai.eu", "Noa Berg")
    token, code = open_welcome(hr, emp_id)
    anon = TestClient(server.app)
    assert anon.get(f"/api/welcome/{token}").json() == {"state": "ready"}
    assert anon.get("/api/welcome/nonsense").json() == {"state": "invalid"}
    assert anon.get("/api/welcome-me").status_code == 401                                   # no session, no checklist
    wrong = "000000" if code != "000000" else "111111"
    bad = anon.post("/api/welcome/login", json={"token": token, "code": wrong})
    assert bad.status_code == 400 and "4 tries left" in bad.json()["detail"]
    assert anon.get("/api/welcome-me").status_code == 401                                   # a wrong code opens nothing
    ok = anon.post("/api/welcome/login", json={"token": token, "code": code})
    assert ok.status_code == 200 and ok.json()["name"] == "Noa Berg" and ok.json()["next_for_you"] == "Identity document"
    mine = anon.get("/api/welcome-me").json()
    assert mine["total"] == 9 and "emp_id" not in mine and "E001" not in str(mine)         # nobody else's data
    # an ordinary signed-in person is not a new hire, and a new hire is not an ordinary signed-in person
    assert as_person("E001").get("/api/welcome-me").status_code == 401
    assert as_person("H001").post("/api/welcome-assistant", json={"message": "hi"}).status_code == 401
    assert anon.delete("/api/welcome-me").status_code == 200
    assert anon.get("/api/welcome-me").status_code == 401                                   # signed out


@test
def a_new_hire_session_is_refused_by_every_other_page():
    hr = as_person("H001")
    emp_id = make_hire(hr, "ivo@cognivexai.eu", "Ivo Lund")
    token, code = open_welcome(hr, emp_id)
    c = TestClient(server.app)
    assert c.post("/api/welcome/login", json={"token": token, "code": code}).status_code == 200
    gets = ["/api/me", "/api/home", "/api/leave", "/api/payslips", "/api/payslips/2026-10", "/api/tickets", "/api/people",
            "/api/people/E001", "/api/people/E001/history", "/api/handbook", "/api/holidays", "/api/notifications",
            "/api/calendar?start=2030-01-01&end=2030-01-31", "/api/needs-you", "/api/approvals", "/api/team",
            "/api/assistant/history", "/api/leave/history", "/api/hr/outbox", "/api/hr/tickets", "/api/hr/access-log",
            f"/api/hr/people/{emp_id}/onboarding", f"/api/hr/people/{emp_id}/welcome-link", "/api/hr/payslips/E001/2026-10"]
    for path in gets:
        assert c.get(path).status_code == 403, path
    posts = {"/api/assistant": {"message": "What is Emma's salary?"}, "/api/leave": {"leave_type": "casual", "start_date": "2030-03-04", "end_date": "2030-03-04"},
             "/api/tickets": {"subject": "x", "details": "y"}, "/api/notifications/read": {},
             f"/api/hr/people/{emp_id}/welcome-link": {}, "/api/hr/people": {"name": "X", "email": "x@cognivexai.eu", "role": "employee", "title": "t", "department": "d",
                                "location": "Paris", "start_date": in_days(30)}, "/api/onboarding/steps/1/complete": {}}
    for path, body in posts.items():
        assert c.post(path, json=body).status_code == 403, path
    assert c.get("/api/welcome-me").status_code == 200                                      # and its own page still works


@test
def five_wrong_codes_lock_the_link_until_hr_issues_a_new_one():
    hr = as_person("H001")
    emp_id = make_hire(hr, "zed@cognivexai.eu", "Zed Kim")
    token, code = open_welcome(hr, emp_id)
    wrong = "000000" if code != "000000" else "111111"
    c = TestClient(server.app)
    for _ in range(5):
        assert c.post("/api/welcome/login", json={"token": token, "code": wrong}).status_code == 400
    locked = c.post("/api/welcome/login", json={"token": token, "code": code})
    assert locked.status_code == 400 and "locked" in locked.json()["detail"]
    assert c.get(f"/api/welcome/{token}").json() == {"state": "locked"}
    assert hr.get(f"/api/hr/people/{emp_id}/welcome-link").json()["state"] == "locked"
    new_token, new_code = open_welcome(hr, emp_id)                                           # a new link replaces it
    assert c.post("/api/welcome/login", json={"token": new_token, "code": new_code}).status_code == 200
    assert c.get(f"/api/welcome/{token}").json() == {"state": "revoked"}
    assert hr.post(f"/api/hr/people/{emp_id}/welcome-link/cancel", json={"reason": ""}).status_code == 400
    assert hr.post(f"/api/hr/people/{emp_id}/welcome-link/cancel", json={"reason": "Wrong person"}).status_code == 200
    assert TestClient(server.app).post("/api/welcome/login", json={"token": new_token, "code": new_code}).status_code == 400


FILE = pdf_text.make_pdf([("p", "a document")])


def hire_signed_in(hr, email, name):
    emp_id = make_hire(hr, email, name)
    token, code = open_welcome(hr, emp_id)
    c = TestClient(server.app)
    assert c.post("/api/welcome/login", json={"token": token, "code": code}).status_code == 200
    return emp_id, c


def upload(client, step, data=FILE, name="scan.pdf", reads=None):
    documents.READER = (lambda *a: dict(reads)) if reads else None
    try:
        return client.post(f"/api/welcome-documents?step={step}&name={name}", content=data,
                           headers={"content-type": "application/octet-stream"})
    finally:
        documents.READER = None


@test
def a_new_hire_sends_a_document_confirms_it_and_only_they_and_hr_can_open_the_file():
    hr = as_person("H001")
    emp_id, c = hire_signed_in(hr, "kai@cognivexai.eu", "Kai Voss")
    good = {"readable": True, "looks_off": False, "document_type": "id_card", "holder_name": "VOSS Kai", "expiry_date": "2099-01-01"}
    r = upload(c, "id", reads=good)
    assert r.status_code == 200 and r.json()["accepted"] is True, r.text
    doc = r.json()["document_id"]
    assert {x["label"] for x in r.json()["read"]} >= {"Name on the document", "Valid until"}
    assert c.get("/api/welcome-me").json()["next_for_you"] == "Identity document"               # not done until confirmed
    mine = c.get(f"/api/welcome-documents/{doc}/file")
    assert mine.status_code == 200 and mine.content == FILE and mine.headers["content-type"] == "application/pdf"
    assert c.post(f"/api/welcome-documents/{doc}/confirm").json() == {"success": True}
    assert c.get("/api/welcome-me").json()["next_for_you"] == "Bank details (IBAN)"
    assert hr.get(f"/api/hr/people/{emp_id}/documents").json()[0]["status"] == "confirmed"
    assert hr.get(f"/api/hr/documents/{doc}/file").content == FILE
    views = [v["viewer_name"] for v in hr.get(f"/api/hr/people/{emp_id}/documents").json()[0]["views"]]
    assert views == ["Narmadha", "Kai Voss"]                                                    # both openings are logged
    for who in ("M001", "E001", "C001"):                                                        # the manager too
        assert as_person(who).get(f"/api/hr/documents/{doc}/file").status_code == 403, who
        assert as_person(who).get(f"/api/hr/people/{emp_id}/documents").status_code == 403, who
    _, other = hire_signed_in(hr, "lou@cognivexai.eu", "Lou Mars")
    assert other.get(f"/api/welcome-documents/{doc}/file").status_code == 404                   # another new hire: "no such document"
    assert other.post(f"/api/welcome-documents/{doc}/confirm").status_code == 400
    assert TestClient(server.app).get(f"/api/welcome-documents/{doc}/file").status_code == 401
    assert as_person("E001").get(f"/api/welcome-documents/{doc}/file").status_code == 401       # an ordinary session is not a new hire


@test
def a_waiting_document_comes_back_after_a_reload_for_its_own_person_only():
    hr = as_person("H001")
    emp_id, c = hire_signed_in(hr, "tia@cognivexai.eu", "Tia Lane")
    _, other = hire_signed_in(hr, "uma@cognivexai.eu", "Uma Fox")
    assert c.get("/api/welcome-documents/waiting").json() == []
    doc = upload(c, "id", reads={"readable": True, "looks_off": False, "document_type": "passport", "holder_name": "Tia Lane", "expiry_date": "2099-01-01"}).json()["document_id"]
    waiting = c.get("/api/welcome-documents/waiting").json()
    assert [(w["document_id"], w["step_key"]) for w in waiting] == [(doc, "id")] and {"label": "Name on the document", "value": "Tia Lane"} in waiting[0]["read"]
    assert other.get("/api/welcome-documents/waiting").json() == []                          # someone else's: nothing
    assert TestClient(server.app).get("/api/welcome-documents/waiting").status_code == 401
    assert as_person("E001").get("/api/welcome-documents/waiting").status_code == 401        # an ordinary session is not a new hire
    assert c.post(f"/api/welcome-documents/{doc}/confirm").status_code == 200
    assert c.get("/api/welcome-documents/waiting").json() == []


@test
def uploads_that_are_too_big_the_wrong_type_or_for_the_wrong_step_never_reach_claude():
    hr = as_person("H001")
    emp_id, c = hire_signed_in(hr, "rae@cognivexai.eu", "Rae Dunn")
    calls, files_before = [], len(os.listdir(documents.files_dir()))
    documents.READER = lambda *a: calls.append(1) or {}
    try:
        big = c.post("/api/welcome-documents?step=id", content=b"%PDF-" + b"0" * documents.MAX_BYTES,
                     headers={"content-type": "application/octet-stream"})
        assert big.status_code == 413 and "10 MB" in big.json()["detail"]
        exe = c.post("/api/welcome-documents?step=id&name=passport.pdf", content=b"MZ a program",
                     headers={"content-type": "application/pdf"})                               # the label lies; the bytes don't
        assert exe.status_code == 400 and "PDF, JPG or PNG" in exe.json()["detail"]
        for step in ("contract", "welcome", "nonsense"):
            assert c.post(f"/api/welcome-documents?step={step}", content=FILE).status_code == 400, step
        assert c.post("/api/welcome-documents?step=id", content=b"").status_code == 400
    finally:
        documents.READER = None
    assert calls == [] and len(os.listdir(documents.files_dir())) == files_before          # nothing read, nothing stored
    assert TestClient(server.app).post("/api/welcome-documents?step=id", content=FILE).status_code == 401
    assert as_person("H001").post("/api/welcome-documents?step=id", content=FILE).status_code == 401
    bad_bank = {"readable": True, "looks_off": False, "holder_name": "Rae Dunn", "iban": "FR76 3000 6000 0112 3456 7890 188"}
    r = upload(c, "iban", reads=bad_bank)
    assert r.status_code == 200 and r.json()["accepted"] is False and "check digits" in r.json()["problems"][0]


@test
def hr_cancels_a_hire_through_the_api_and_their_files_and_access_go():
    hr = as_person("H001")
    emp_id, c = hire_signed_in(hr, "max@cognivexai.eu", "Max Orr")
    upload(c, "id", reads={"readable": True, "looks_off": False, "document_type": "passport", "holder_name": "Max Orr", "expiry_date": "2099-01-01"})
    before = len(os.listdir(documents.files_dir()))
    for who in ("E001", "M001"):
        assert as_person(who).post(f"/api/hr/people/{emp_id}/cancel-hire", json={"reason": "x"}).status_code == 403
    assert hr.post(f"/api/hr/people/{emp_id}/cancel-hire", json={"reason": ""}).status_code == 400
    done = hr.post(f"/api/hr/people/{emp_id}/cancel-hire", json={"reason": "Accepted another offer"})
    assert done.status_code == 200 and done.json()["files_deleted"] == 1
    assert len(os.listdir(documents.files_dir())) == before - 1
    assert c.get("/api/welcome-me").status_code == 401                                          # their session stops working too
    assert hr.post(f"/api/hr/people/{emp_id}/cancel-hire", json={"reason": "again"}).status_code == 400


@test
def a_flagged_document_shows_in_hr_needs_you_and_one_person_looks_and_closes_it():
    hr = as_person("H001")
    emp_id, c = hire_signed_in(hr, "nia@cognivexai.eu", "Nia Fox")
    flagged = {"readable": True, "looks_off": True, "looks_off_reason": "The expiry date seems retouched.", "document_type": "passport",
               "holder_name": "Nia Fox", "expiry_date": "2099-01-01"}
    doc = upload(c, "id", reads=flagged).json()["document_id"]
    box = hr.get("/api/needs-you").json()
    mine = [f for f in box["document_flags"] if f["document_id"] == doc]
    assert mine and mine[0]["reason"] == "The expiry date seems retouched." and mine[0]["employee_name"] == "Nia Fox"
    assert as_person("M001").get("/api/needs-you").json()["document_flags"] == []
    assert c.get("/api/welcome-me").status_code == 200 and "retouched" not in c.get("/api/welcome-me").text      # the person is not told
    assert hr.post(f"/api/hr/documents/{doc}/reviewed", json={"note": ""}).status_code == 400
    assert as_person("M001").post(f"/api/hr/documents/{doc}/reviewed", json={"note": "ok"}).status_code == 403
    assert hr.post(f"/api/hr/documents/{doc}/reviewed", json={"note": "Compared with the original: fine"}).status_code == 200
    assert all(f["document_id"] != doc for f in hr.get("/api/needs-you").json()["document_flags"])


TERMS = {"salary_eur": "3200", "category": "executive", "trial": 4}


@test
def hr_drafts_a_contract_the_hires_manager_approves_and_only_then_can_it_be_downloaded():
    hr, lucas = as_person("H001"), as_person("M001")
    emp_id = make_hire(hr, "ada@cognivexai.eu", "Ada Wren")
    for who in ("E001", "M001"):
        assert as_person(who).post(f"/api/hr/people/{emp_id}/contract", json=TERMS).status_code == 403, who
    assert hr.get(f"/api/hr/people/{emp_id}/contract").json() is None
    limits = hr.get(f"/api/hr/people/{emp_id}/contract-limits?category=executive").json()
    assert limits["max"] == 4 and limits["unit"] == "months" and "L1221-19" in limits["cite"]
    assert as_person("E001").get(f"/api/hr/people/{emp_id}/contract-limits?category=executive").status_code == 403
    too_long = hr.post(f"/api/hr/people/{emp_id}/contract", json={**TERMS, "trial": 5})
    assert too_long.status_code == 400 and "L1221-19" in too_long.json()["detail"] and "4 months" in too_long.json()["detail"]
    made = hr.post(f"/api/hr/people/{emp_id}/contract", json=TERMS)
    assert made.status_code == 200, made.text
    draft = made.json()["draft_id"]
    assert hr.get(f"/api/hr/contracts/{draft}/pdf").status_code == 400                           # a draft has no PDF
    assert hr.post(f"/api/hr/contracts/{draft}/submit").status_code == 200
    assert hr.get(f"/api/hr/contracts/{draft}/pdf").status_code == 400                           # nor does one waiting for approval
    box = lucas.get("/api/needs-you").json()
    assert [(c["employee_name"], c["salary_eur"]) for c in box["contract_approvals"] if c["draft_id"] == draft] == [("Ada Wren", 3200.0)]
    assert lucas.get("/api/home").json()["waiting_on_you"] == box["waiting_on_you"]            # the menu badge counts it
    assert lucas.get(f"/api/contracts/{draft}/preview").json()["title"].startswith("CONTRAT DE TRAVAIL")
    for who in ("C001", "E001"):
        assert as_person(who).get(f"/api/contracts/{draft}/preview").status_code == 404, who
        assert as_person(who).post(f"/api/contracts/{draft}/decision", json={"decision": "approve"}).status_code == 400, who
    assert hr.post(f"/api/contracts/{draft}/decision", json={"decision": "approve"}).status_code == 400    # HR can't approve either
    assert lucas.post(f"/api/contracts/{draft}/decision", json={"decision": "reject", "reason": ""}).status_code == 400
    assert lucas.post(f"/api/contracts/{draft}/decision", json={"decision": "approve"}).status_code == 200
    pdf = hr.get(f"/api/hr/contracts/{draft}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF-1.4")
    assert "attachment" in pdf.headers["content-disposition"] and "contrat-ada-wren" in pdf.headers["content-disposition"]
    for who in ("M001", "E001"):
        assert as_person(who).get(f"/api/hr/contracts/{draft}/pdf").status_code == 403, who     # the manager can't download it
    assert len(hr.get(f"/api/hr/people/{emp_id}/contract").json()["downloads"]) == 1
    assert hr.post(f"/api/hr/contracts/{draft}/signed", json={"signed_on": in_days(3)}).status_code == 400
    assert hr.post(f"/api/hr/contracts/{draft}/signed", json={"signed_on": in_days(0), "note": "Both copies"}).status_code == 200
    steps = hr.get(f"/api/hr/people/{emp_id}/onboarding").json()["steps"]
    assert next(s for s in steps if s["step_key"] == "contract")["status"] == "done"
    assert hr.get(f"/api/hr/people/{emp_id}/contract").json()["status"] == "signed"


@test
def a_rejected_contract_goes_back_to_hr_and_a_cancelled_one_has_no_pdf():
    hr, lucas = as_person("H001"), as_person("M001")
    emp_id = make_hire(hr, "eli@cognivexai.eu", "Eli Park")
    draft = hr.post(f"/api/hr/people/{emp_id}/contract", json=TERMS).json()["draft_id"]
    hr.post(f"/api/hr/contracts/{draft}/submit")
    assert lucas.post(f"/api/contracts/{draft}/decision", json={"decision": "reject", "reason": "Salary too low"}).status_code == 200
    view = hr.get(f"/api/hr/people/{emp_id}/contract").json()
    assert view["status"] == "rejected" and view["decision_note"] == "Salary too low"
    assert hr.patch(f"/api/hr/contracts/{draft}", json={**TERMS, "salary_eur": "3500"}).status_code == 200
    assert hr.get(f"/api/hr/people/{emp_id}/contract").json()["fields"]["salary_eur"] == 3500.0
    assert as_person("M001").patch(f"/api/hr/contracts/{draft}", json=TERMS).status_code == 403
    assert hr.post(f"/api/hr/contracts/{draft}/cancel", json={"reason": ""}).status_code == 400
    assert hr.post(f"/api/hr/contracts/{draft}/cancel", json={"reason": "Offer changed"}).status_code == 200
    assert hr.get(f"/api/hr/contracts/{draft}/pdf").status_code == 400
    assert lucas.post(f"/api/contracts/{draft}/decision", json={"decision": "approve"}).status_code == 400
    assert TestClient(server.app).get(f"/api/contracts/{draft}/preview").status_code == 401        # nothing without a session
    link, code = open_welcome(hr, emp_id)                                                           # and a new hire's link session sees none of it
    c = TestClient(server.app)
    assert c.post("/api/welcome/login", json={"token": link, "code": code}).status_code == 200
    for path in (f"/api/contracts/{draft}/preview", f"/api/hr/people/{emp_id}/contract", f"/api/hr/contracts/{draft}/pdf"):
        assert c.get(path).status_code == 403, path


@test
def hr_runs_the_daily_check_by_hand_and_the_new_hire_reads_and_dismisses_the_reminder():
    hr = as_person("H001")
    emp_id = make_hire(hr, "oli@cognivexai.eu", "Oli Hart", days=2)                      # starts in 2 days: the first-day information is due
    for who in ("E001", "M001"):
        assert as_person(who).post("/api/hr/onboarding/daily-run").status_code == 403, who
    assert TestClient(server.app).post("/api/hr/onboarding/daily-run").status_code == 401
    done = hr.post("/api/hr/onboarding/daily-run")
    assert done.status_code == 200 and "Oli Hart" in done.json()["before_day_one"] and set(done.json()) == {"chase", "before_day_one", "first_week", "trial_end"}
    assert "Oli Hart" in done.json()["chase"]            # entered 2 days before the start, so the documents are already due too
    again = hr.post("/api/hr/onboarding/daily-run").json()
    assert "Oli Hart" not in again["before_day_one"]                                       # never twice
    mail = next(m for m in hr.get("/api/hr/outbox").json() if m["kind"] == "before_day_one" and m["emp_id"] == emp_id)
    assert mail["to_email"] == "oli@cognivexai.eu" and mail["to_name"] == "Oli Hart" and "Your first day" in mail["subject"] and mail["sent_at"] is None
    history = hr.get(f"/api/people/{emp_id}/history").json()["changes"]
    assert any(c["field"] == "reminder" and c["new_value"] == "before_day_one" and c["changed_by"] == "Narmadha" for c in history)   # run by hand: HR's name
    token, code = open_welcome(hr, emp_id)
    c = TestClient(server.app)
    assert c.get("/api/welcome-reminders").status_code == 401
    assert c.post("/api/welcome/login", json={"token": token, "code": code}).status_code == 200
    mine = [r["message"] for r in c.get("/api/welcome-reminders").json()]
    assert len(mine) == 2 and any(m.startswith("Your first day is") for m in mine) and any(m.startswith("Reminder: we still need") for m in mine)
    assert as_person("E001").get("/api/welcome-reminders").status_code == 401             # an ordinary session is not a new hire
    assert c.post("/api/welcome-reminders/read").json() == {"ok": True} and c.get("/api/welcome-reminders").json() == []
    assert c.post("/api/hr/onboarding/daily-run").status_code == 403                        # and a link session can't run it


def file_for(hr, emp_id, title="Employment contract", kind="contract", text="Article 4 – Préavis: deux mois.", data=None):
    employee_files.TRANSCRIBER = lambda d, ft: (text, True)
    try:
        return hr.post(f"/api/hr/people/{emp_id}/files?kind={kind}&title={title}&name=c.pdf", content=data or FILE,
                       headers={"content-type": "application/octet-stream"})
    finally:
        employee_files.TRANSCRIBER = None


@test
def hr_files_a_document_for_anyone_and_a_person_sees_and_opens_only_their_own():
    hr = as_person("H001")
    made = file_for(hr, "E001", "Emma contract")
    assert made.status_code == 200 and made.json()["assistant"] == "ready", made.text
    emma_file = made.json()["file_id"]
    hugo_file = file_for(hr, "E002", "Hugo contract").json()["file_id"]
    emma, hugo = as_person("E001"), as_person("E002")
    assert [f["title"] for f in emma.get("/api/files").json()] == ["Emma contract"]
    assert [f["title"] for f in hugo.get("/api/files").json()] == ["Hugo contract"]
    mine = emma.get(f"/api/files/{emma_file}/file")
    assert mine.status_code == 200 and mine.content == FILE and mine.headers["content-type"] == "application/pdf"
    assert "inline" in mine.headers["content-disposition"]
    for who in ("E002", "M001", "C001"):                                                     # a colleague, the manager, the CEO
        assert as_person(who).get(f"/api/files/{emma_file}/file").status_code == 404, who
    assert emma.get(f"/api/files/{hugo_file}/file").status_code == 404                       # and Emma can't open Hugo's by guessing its id
    assert hr.get(f"/api/files/{emma_file}/file").status_code == 200                         # HR can
    views = [(v["viewer_name"], v["via"]) for v in emma.get("/api/files").json()[0]["views"]]
    assert views == [("Narmadha", "app"), ("Emma", "app")]                                   # the owner sees who opened it
    assert [f["title"] for f in hr.get("/api/hr/people/E001/files").json()] == ["Emma contract"]
    for who in ("E001", "M001"):
        assert as_person(who).get("/api/hr/people/E002/files").status_code == 403, who       # not even your own page's HR route
    assert TestClient(server.app).get("/api/files").status_code == 401


@test
def only_hr_can_file_or_delete_and_a_bad_upload_is_refused_before_anything_is_stored():
    hr = as_person("H001")
    before = len(os.listdir(documents.files_dir()))
    for who in ("E001", "M001"):
        assert file_for(as_person(who), "E001").status_code == 403, who
    assert TestClient(server.app).post("/api/hr/people/E001/files?kind=contract&title=x", content=FILE).status_code == 401
    assert file_for(hr, "E001", kind="memo").status_code == 400
    assert file_for(hr, "E001", title=" ").status_code == 400
    assert file_for(hr, "E999").status_code == 400
    assert file_for(hr, "E001", data=b"MZ a program").json()["detail"].startswith("I can only read PDF, JPG or PNG")
    big = file_for(hr, "E001", data=b"%PDF-" + b"0" * documents.MAX_BYTES)
    assert big.status_code == 413 and "10 MB" in big.json()["detail"]
    assert len(os.listdir(documents.files_dir())) == before                                  # nothing stored for any of them
    doc = file_for(hr, "E003", "Clara certificate", "certificate").json()["file_id"]
    for who in ("E003", "M001"):
        assert as_person(who).post(f"/api/hr/files/{doc}/delete", json={"reason": "x"}).status_code == 403, who
    assert hr.post(f"/api/hr/files/{doc}/delete", json={"reason": ""}).status_code == 400
    assert hr.post(f"/api/hr/files/{doc}/delete", json={"reason": "Wrong person"}).status_code == 200
    assert len(os.listdir(documents.files_dir())) == before
    assert as_person("E003").get(f"/api/files/{doc}/file").status_code == 404 and as_person("E003").get("/api/files").json() == []


@test
def a_new_hire_link_session_cannot_reach_the_documents_area():
    hr = as_person("H001")
    emp_id, c = hire_signed_in(hr, "val@cognivexai.eu", "Val Moss")
    doc = file_for(hr, emp_id, "Val contract").json()["file_id"]
    for path in ("/api/files", f"/api/files/{doc}/file", f"/api/hr/people/{emp_id}/files"):
        assert c.get(path).status_code == 403, path
    assert c.post(f"/api/hr/people/{emp_id}/files?kind=contract&title=x", content=FILE).status_code == 403
    assert c.post(f"/api/hr/files/{doc}/delete", json={"reason": "x"}).status_code == 403


@test
def hr_closes_a_question_the_policy_does_not_answer_and_nobody_else_can():
    import policy_gaps
    policy_gaps.EMBED = None
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM policy_gaps")
    conn.close()
    policy_gaps.record("company car")
    policy_gaps.record("company car policy")
    policy_gaps.record("gym membership")
    hr = as_person("H001")
    gaps = hr.get("/api/needs-you").json()["policy_gaps"]
    assert [(g["topic"], g["asks"]) for g in gaps] == [("company car", 2), ("gym membership", 1)]
    assert "employee_id" not in gaps[0] and "emp_id" not in gaps[0]
    for who in ("E001", "M001", "C001"):
        assert as_person(who).post(f"/api/hr/policy-gaps/{gaps[0]['id']}/dismiss", json={}).status_code == 403, who
        assert as_person(who).post(f"/api/hr/policy-gaps/{gaps[0]['id']}/fixed", json={}).status_code == 403, who
    assert as_person("M001").get("/api/needs-you").json()["policy_gaps"] == []
    assert hr.post("/api/hr/policy-gaps/999/fixed", json={}).status_code == 400
    assert hr.post(f"/api/hr/policy-gaps/{gaps[0]['id']}/fixed", json={"note": "added a section"}).status_code == 200
    assert hr.post(f"/api/hr/policy-gaps/{gaps[0]['id']}/fixed", json={}).status_code == 400            # already closed
    assert hr.post(f"/api/hr/policy-gaps/{gaps[1]['id']}/dismiss", json={}).status_code == 200
    assert hr.get("/api/needs-you").json()["policy_gaps"] == []


if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e!r}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)
