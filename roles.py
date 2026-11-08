# roles.py: which tools each role is allowed to see and call.
# No API key needed, so it can be unit-tested in CI.
import copy

# tools every logged-in person gets (managers and HR also have their own leave and payslips)
EVERYONE = {
    "get_leave_balance", "get_my_requests", "count_leave_days", "apply_leave", "cancel_leave",
    "search_policy", "search_labour_code", "search_collective_agreement", "create_hr_ticket", "get_my_tickets",
    "get_payslip", "compare_payslips", "get_pay_summary", "lookup_colleague", "get_balance_history", "get_holidays",
    "get_my_documents", "read_my_document",       # the signed-in person's OWN documents: no way to name anyone else's
}

# a new hire (signed in with their welcome link, before day one) can only read their own onboarding: no leave,
# pay, colleagues, policy or tickets. The role exists only inside the link session; in the database they are
# an ordinary employee, so it never changes what they can do once they work here.
NEW_HIRE = "new_hire"
NEW_HIRE_TOOLS = {"get_my_onboarding"}

ROLE_TOOLS = {
    NEW_HIRE: NEW_HIRE_TOOLS,
    "employee": EVERYONE,
    "manager":  EVERYONE | {"get_pending_requests", "get_team_member_balance", "decide_leave", "get_needs_attention"},
    "hr":       EVERYONE | {"get_open_tickets", "reply_to_ticket", "get_needs_attention", "get_contract_status"},
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