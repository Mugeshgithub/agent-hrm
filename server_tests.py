# server_tests.py: checks the web API's rules. No AI, no API keys, runs in CI.
#   python server_tests.py
import os
import sys
import tempfile

os.environ["HRM_DB"] = os.path.join(tempfile.mkdtemp(), "server_test_hr.db")
import db
db.DB_PATH = os.environ["HRM_DB"]

from fastapi.testclient import TestClient
import demo_data
demo_data.init_all(reset=True)
demo_data.seed_activity()
import server

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


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
                                        "location": "Paris", "start_date": "2026-12-01", "manager_id": "M001"})
    assert r.status_code == 200, r.json()
    new_id = r.json()["id"]
    r = hr.patch(f"/api/hr/people/{new_id}", json={"changes": {"title": "Senior QA"}, "reason": "Offer revised"})
    assert r.status_code == 200, r.json()
    log = hr.get(f"/api/people/{new_id}/history").json()["changes"]
    assert len(log) == 2 and all(c["changed_by"] == "Narmadha" for c in log)
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
    assert box["role"] == "manager" and box["hr_requests"] == [] and box["waiting_on_you"] == len(box["leave_requests"])
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
