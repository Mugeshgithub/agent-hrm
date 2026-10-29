# roles.py: which tools each role is allowed to see and call.
# No API key needed, so it can be unit-tested in CI.
import copy

# tools every logged-in person gets (managers and HR also have their own leave and payslips)
EVERYONE = {
    "get_leave_balance", "get_my_requests", "count_leave_days", "apply_leave", "cancel_leave",
    "search_policy", "create_hr_ticket", "get_my_tickets",
    "get_payslip", "compare_payslips", "get_pay_summary",
}

ROLE_TOOLS = {
    "employee": EVERYONE,
    "manager":  EVERYONE | {"get_pending_requests", "get_team_member_balance", "decide_leave"},
    "hr":       EVERYONE | {"get_open_tickets", "reply_to_ticket"},
}

# tools where only HR may name another employee
HR_ONLY_FIELD = {"get_payslip", "compare_payslips", "get_pay_summary"}


def allowed(role, tool_name):
    # unknown roles get the smallest set: least privilege
    return tool_name in ROLE_TOOLS.get(role, EVERYONE)


def tools_for(role, all_tools):
    # the list of tool definitions to send to the model for this role
    result = []
    for tool in all_tools:
        if not allowed(role, tool["name"]):
            continue
        if role != "hr" and tool["name"] in HR_ONLY_FIELD:
            tool = copy.deepcopy(tool)   # never change the shared list
            tool["input_schema"]["properties"].pop("employee_id", None)
        result.append(tool)
    return result