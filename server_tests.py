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
    # Priya asks for "E002"'s payslip through her own endpoint: she still only gets her own
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
    c = as_person("E003")                       # Divya has 0 casual days
    r = c.post("/api/leave", json={"leave_type": "casual", "start_date": "2027-03-01", "end_date": "2027-03-01"})
    assert r.status_code == 400 and "Not enough" in r.json()["detail"]


@test
def manager_decision_notifies_the_employee():
    c = as_person("M001")
    pending = c.get("/api/approvals").json()
    karthik = next(p for p in pending if p["employee_id"] == "E002")
    assert c.post(f"/api/approvals/{karthik['request_id']}", json={"decision": "approve"}).status_code == 200
    notes = as_person("E002").get("/api/notifications").json()
    assert any("approved by Arjun" in n["message"] for n in notes)


@test
def rejecting_needs_a_reason():
    c = as_person("M001")
    divya = next(p for p in c.get("/api/approvals").json() if p["employee_id"] == "E003")
    r = c.post(f"/api/approvals/{divya['request_id']}", json={"decision": "reject", "reason": ""})
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
    r = hr.post("/api/hr/people", json={"name": "Anu", "email": "anu@company.com", "role": "employee",
                                        "title": "QA Engineer", "department": "Engineering",
                                        "location": "Paris", "start_date": "2026-12-01", "manager_id": "M001"})
    assert r.status_code == 200, r.json()
    new_id = r.json()["id"]
    r = hr.patch(f"/api/hr/people/{new_id}", json={"changes": {"title": "Senior QA"}, "reason": "Offer revised"})
    assert r.status_code == 200, r.json()
    log = hr.get(f"/api/people/{new_id}/history").json()["changes"]
    assert len(log) == 2 and all(c["changed_by"] == "Lakshmi" for c in log)
    # a colleague sees the safe view, but not the history
    priya = as_person("E001")
    card = priya.get(f"/api/people/{new_id}").json()
    assert card["title"] == "Senior QA" and "casual_balance" not in card
    assert priya.get(f"/api/people/{new_id}/history").status_code == 400


@test
def rule_errors_come_back_with_the_reason():
    r = as_person("H001").post("/api/hr/people/M001/offboard", json={"end_date": "2027-01-31", "reason": "test"})
    assert r.status_code == 400 and "still manages" in r.json()["detail"]

@test
def colleagues_see_the_safe_profile_hr_sees_more():
    card = as_person("E002").get("/api/people/E001").json()
    assert card["manager_name"] == "Arjun" and "contract_type" not in card and "balances" not in card
    assert "contract_type" not in as_person("E002").get("/api/people").json()[0]
    full = as_person("H001").get("/api/people/E001").json()
    assert full["contract_type"] == "CDI" and "casual" in full["balances"]
    reports = {r["id"] for r in as_person("E001").get("/api/people/M001").json()["reports"]}
    assert {"E001", "E002", "E003"} <= reports


@test
def people_who_have_left_lose_access_and_leave_their_team():
    hr = as_person("H001")
    r = hr.post("/api/hr/people", json={"name": "Temp", "email": "temp@company.com", "role": "employee",
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
