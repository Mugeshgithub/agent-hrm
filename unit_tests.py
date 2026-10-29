# unit_tests.py: fast checks of the business rules. No AI, no API keys, no cost.
import sys
from datetime import date, timedelta

import db
db.DB_PATH = "unit_test_hr.db"   # its own database, never the real hr.db

import tools
from tools import (count_working_days, apply_leave, decide_leave, cancel_leave,
                   get_leave_balance, get_team_member_balance)


def next_monday(weeks_ahead=1):
    # dates relative to today, so these tests never go out of date
    today = date.today()
    return today + timedelta(days=(7 - today.weekday()) + 7 * (weeks_ahead - 1))


def d(day):
    return day.isoformat()


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# ---------- leave rules ----------

@test
def counts_working_days():
    assert count_working_days(date(2026, 11, 10), date(2026, 11, 15)) == 4   # weekend excluded
    assert count_working_days(date(2026, 12, 21), date(2026, 12, 25)) == 4   # Christmas excluded


@test
def past_dates_are_blocked():
    yesterday = date.today() - timedelta(days=1)
    assert "error" in apply_leave("E001", "casual", d(yesterday), d(yesterday))


@test
def zero_balance_is_blocked():
    mon = next_monday()
    assert "error" in apply_leave("E003", "casual", d(mon), d(mon))   # Divya has 0 casual


@test
def duplicate_request_is_blocked():
    mon = next_monday()
    assert apply_leave("E001", "casual", d(mon), d(mon)).get("success")
    assert "error" in apply_leave("E001", "casual", d(mon), d(mon))


@test
def pending_days_count_against_balance():
    mon = next_monday()
    tue, wed = mon + timedelta(days=1), mon + timedelta(days=2)
    assert apply_leave("E002", "casual", d(mon), d(mon)).get("success")   # Karthik has 2 casual
    assert apply_leave("E002", "casual", d(tue), d(tue)).get("success")
    assert "error" in apply_leave("E002", "casual", d(wed), d(wed))      # both already pending


@test
def approve_deducts_once_and_cancel_returns_days():
    mon = next_monday(2)
    rid = apply_leave("E001", "casual", d(mon), d(mon + timedelta(days=1)))["request"]["id"]  # 2 days
    assert decide_leave("M001", rid, "approve").get("success")
    assert get_leave_balance("E001")["leave_balance"]["casual"] == 6
    assert "error" in decide_leave("M001", rid, "approve")              # no double approval
    assert cancel_leave("E001", rid)["cancelled"] is True
    assert get_leave_balance("E001")["leave_balance"]["casual"] == 8    # days came back


@test
def only_the_right_manager_can_decide():
    mon = next_monday()
    rid = apply_leave("E001", "casual", d(mon), d(mon))["request"]["id"]
    assert "error" in decide_leave("C001", rid, "approve")   # Mugesh is not Priya's manager
    assert "error" in decide_leave("E002", rid, "approve")   # an employee can't approve
    assert "error" in decide_leave("M001", rid, "reject")    # a rejection needs a reason


@test
def cannot_cancel_someone_elses_request():
    mon = next_monday()
    rid = apply_leave("E001", "casual", d(mon), d(mon))["request"]["id"]
    assert cancel_leave("E002", rid)["cancelled"] is False


@test
def team_balance_rules():
    assert get_team_member_balance("M001", "E001")["leave_balance"]["casual"] == 8
    not_team = get_team_member_balance("E002", "E001")
    missing = get_team_member_balance("M001", "E999")
    assert "error" in not_team and not_team == missing   # same message, so no enumeration


# ---------- NEW: HR tickets ----------

@test
def ticket_gets_a_number_and_starts_open():
    result = tools.create_hr_ticket("E001", "What is the company car policy?",
                                    "Asked about company car policy; not in the HR policy.")
    assert result.get("success")
    assert result["ticket"] == "HR-0001"
    mine = tools.get_my_tickets("E001")["tickets"]
    assert len(mine) == 1 and mine[0]["status"] == "open"


@test
def empty_ticket_is_rejected():
    assert "error" in tools.create_hr_ticket("E001", "   ", "   ")


@test
def employees_only_see_their_own_tickets():
    tools.create_hr_ticket("E001", "Priya's question", "Priya's summary")
    tools.create_hr_ticket("E002", "Karthik's question", "Karthik's summary")
    karthik = tools.get_my_tickets("E002")["tickets"]
    assert len(karthik) == 1
    assert "Priya" not in str(karthik)


@test
def only_hr_can_see_open_tickets():
    tools.create_hr_ticket("E001", "A question", "A summary")
    assert len(tools.get_open_tickets("H001")["tickets"]) == 1   # Lakshmi (HR) can
    assert "error" in tools.get_open_tickets("E001")             # an employee can't
    assert "error" in tools.get_open_tickets("M001")             # a manager can't either


@test
def hr_reply_closes_ticket_once():
    tid = tools.create_hr_ticket("E001", "Car policy?", "Car policy question")["id"]
    assert "error" in tools.reply_to_ticket("M001", tid, "Not HR, can't reply")
    assert "error" in tools.reply_to_ticket("H001", tid, "   ")        # an empty reply isn't allowed
    assert tools.reply_to_ticket("H001", tid, "No company cars, sorry.").get("success")
    mine = tools.get_my_tickets("E001")["tickets"][0]
    assert mine["status"] == "closed" and mine["hr_reply"] == "No company cars, sorry."
    assert "error" in tools.reply_to_ticket("H001", tid, "Second reply")   # already closed
    assert get_open_tickets_count() == 0


def get_open_tickets_count():
    return len(tools.get_open_tickets("H001")["tickets"])

# ---------- NEW: tools per role ----------

def fake_tools():
    # the same 16 tool names the agent uses; payslip tools have an employee_id field
    names = ["get_leave_balance", "get_my_requests", "count_leave_days", "apply_leave",
             "cancel_leave", "search_policy", "create_hr_ticket", "get_my_tickets",
             "get_payslip", "compare_payslips", "get_pay_summary",
             "get_pending_requests", "get_team_member_balance", "decide_leave",
             "get_open_tickets", "reply_to_ticket"]
    tools = [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}}
             for n in names]
    for t in tools:
        if t["name"] in ("get_payslip", "compare_payslips", "get_pay_summary"):
            t["input_schema"]["properties"]["employee_id"] = {"type": "string"}
    return tools


MANAGER_ONLY = {"get_pending_requests", "get_team_member_balance", "decide_leave"}
HR_ONLY = {"get_open_tickets", "reply_to_ticket"}


def names_for(role):
    import roles
    return {t["name"] for t in roles.tools_for(role, fake_tools())}


@test
def employee_gets_no_manager_or_hr_tools():
    names = names_for("employee")
    assert not names & (MANAGER_ONLY | HR_ONLY), names
    assert "apply_leave" in names and len(names) == 11, names


@test
def manager_gets_team_tools_but_not_hr_tools():
    names = names_for("manager")
    assert MANAGER_ONLY <= names and not names & HR_ONLY, names
    assert len(names) == 14, names


@test
def hr_gets_ticket_tools_but_not_manager_tools():
    names = names_for("hr")
    assert HR_ONLY <= names and not names & MANAGER_ONLY, names
    assert len(names) == 13, names


@test
def only_hr_sees_the_employee_id_field():
    import roles
    for role, should_have in (("employee", False), ("manager", False), ("hr", True)):
        for t in roles.tools_for(role, fake_tools()):
            if t["name"] == "get_payslip":
                has = "employee_id" in t["input_schema"]["properties"]
                assert has == should_have, (role, has)


@test
def filtering_never_changes_the_original_list():
    import roles
    original = fake_tools()
    roles.tools_for("employee", original)
    payslip = next(t for t in original if t["name"] == "get_payslip")
    assert "employee_id" in payslip["input_schema"]["properties"]   # still there


@test
def unknown_role_gets_only_employee_tools():
    assert names_for("intern") == names_for("employee")


@test
def run_tool_guard_knows_who_may_call_what():
    import roles
    assert roles.allowed("employee", "decide_leave") is False
    assert roles.allowed("manager", "decide_leave") is True
    assert roles.allowed("manager", "reply_to_ticket") is False
    assert roles.allowed("hr", "reply_to_ticket") is True


if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        db.init_db(reset=True)   # fresh data for every test
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e!r}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)   # a non-zero exit is how CI knows something failed