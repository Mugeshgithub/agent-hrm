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
