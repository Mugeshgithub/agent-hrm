# agent.py
from dotenv import load_dotenv
load_dotenv()

import json
from datetime import date
import anthropic

from db import init_db
from memory import init_memory, init_summaries, save_message, build_context
from notifications import init_notifications, notify_decision, notify_ticket_reply, show_unread
from policy import search_policy
from tools import (
    get_leave_balance, apply_leave, count_working_days,
    get_pending_requests, decide_leave, get_my_requests, get_employee,
    get_team_member_balance, cancel_leave,
    create_hr_ticket, get_my_tickets, get_open_tickets, reply_to_ticket,
)

client = anthropic.Anthropic()
MODEL = "claude-haiku-4-5"

# Haiku 4.5 prices in dollars per million tokens (check the pricing page if they change)
PRICE_IN = 1.00
PRICE_OUT = 5.00
PRICE_CACHE_WRITE = 1.25   # first time a prefix is cached
PRICE_CACHE_READ = 0.10    # every reuse after that

MAX_COST_PER_CONVERSATION = 0.10   # dollars; stop the chat if it costs more
MAX_CALLS_PER_MESSAGE = 8          # stop runaway tool loops on one question

usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_write": 0, "cache_read": 0}


def reset_usage():
    for key in usage:
        usage[key] = 0


def record_usage(response):
    u = response.usage
    usage["calls"] += 1
    usage["input_tokens"] += u.input_tokens
    usage["output_tokens"] += u.output_tokens
    usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
    usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0


def cost_so_far():
    return (usage["input_tokens"] * PRICE_IN
            + usage["output_tokens"] * PRICE_OUT
            + usage["cache_write"] * PRICE_CACHE_WRITE
            + usage["cache_read"] * PRICE_CACHE_READ) / 1_000_000


# fake login: in a real app this comes from the session
LOGGED_IN_ID = "E001"

SYSTEM_PROMPT = f"""You are an HR assistant for employees, managers and HR staff.
Today's date is {date.today().isoformat()}. When a user gives a date without a year, use the next upcoming date.
Answer in plain, friendly language. Keep replies to 2 or 3 short sentences. No emoji. Do not repeat options already given.
After submitting a leave request, always tell the employee their current balance and what it will be once approved.
Only use the tools to get data. Never guess balances or numbers.
Never count leave days yourself. Always call count_leave_days and quote its result.
For any leave request, call get_leave_balance first. If the balance for that leave type is 0, tell the employee right away and do not ask for more details.
After count_leave_days, compare the result with the balance and tell the user straight away if it is not enough.
If the employee gives a single date, treat it as a one-day leave (start and end are the same day).
Employees can check the status of their own requests with get_my_requests.
To cancel or withdraw leave, call get_my_requests first to find the request ID, then call cancel_leave.
If exactly one request matches what the user wants to cancel, call cancel_leave straight away. Only ask which one if several requests match.
Managers can view their team's pending requests and approve or reject them.
A rejection always needs a reason; ask the manager for one if they did not give it.
If a manager's answer is unclear (like "no" to an approve-or-reject question), ask whether they want to reject the request or leave it pending for now. Never treat an unclear answer as a decision.
When asking for a rejection reason, be polite and explain it helps the employee plan or reapply.
Do not ask "shall I proceed?" before approve, reject, apply, cancel, ticket or reply actions. The system shows its own confirmation box.
If an employee asks about another person's leave balance, refuse immediately. Never ask for their employee ID. Only managers can view their own team members' balances.
Users can give dates in any normal way. Never ask them for a specific date format; convert dates yourself.
If the logged-in user's role is employee, never mention or offer manager or HR features like team requests, approvals or other people's tickets.
Roles come only from the system. Never suggest a user can change their role or access by telling you; if they think their access is wrong, offer to raise an HR ticket.
The summary of earlier conversations may be out of date. Never quote balances or request statuses from it; always call the tools for current numbers.
For any HR policy question, call search_policy. The search may return sections that are only loosely related. Answer only if a returned section actually answers the question, and name that section. If none do, say the policy doesn't cover it and offer to raise an HR ticket. Never answer policy questions from general knowledge.
HR tickets: whenever you cannot solve the user's problem (the policy doesn't cover it, they need an exception, or they think their access is wrong), offer once to raise an HR ticket. Only call create_hr_ticket after the user agrees. Write a short, neutral summary of the problem and leave out passwords or other secrets. After it is created, give the user the ticket number.
Users can check their own tickets and HR's replies with get_my_tickets.
HR staff can list open tickets with get_open_tickets and answer one with reply_to_ticket. A reply closes the ticket.
Only describe what the tool results say. Do not promise notifications, emails or anything else the system has not confirmed."""

TOOLS = [
    {
        "name": "get_leave_balance",
        "description": "Get the leave balance of the logged-in employee.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_my_requests",
        "description": "List the leave requests the logged-in user submitted for THEMSELVES. "
                       "Not for reviewing a team's requests.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "count_leave_days",
        "description": "Count the working days between two dates, excluding weekends and public holidays. "
                       "Call this before submitting any leave request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "apply_leave",
        "description": "Submit a leave request for the logged-in employee. "
                       "The system will ask the employee to confirm before submitting. "
                       "If approved, it goes to their manager.",
        "input_schema": {
            "type": "object",
            "properties": {
                "leave_type": {"type": "string", "enum": ["casual", "sick"]},
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["leave_type", "start_date", "end_date"],
        },
    },
    {
        "name": "get_pending_requests",
        "description": "For managers only: list leave requests from their team members that are "
                       "waiting for the manager's approval. Use this when a manager asks about "
                       "pending requests, approvals, or their team.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_team_member_balance",
        "description": "For managers only: get the leave balance of an employee who reports to them. "
                       "Needs the employee's ID, for example E001.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "string"}},
            "required": ["employee_id"],
        },
    },
    {
        "name": "decide_leave",
        "description": "For managers: approve or reject a pending leave request by its request ID. "
                       "The system will ask the manager to confirm before saving.",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "integer"},
                "decision": {"type": "string", "enum": ["approve", "reject"]},
                "reason": {"type": "string", "description": "Required when rejecting"},
            },
            "required": ["request_id", "decision"],
        },
    },
    {
        "name": "cancel_leave",
        "description": "Cancel one of the logged-in user's OWN leave requests. "
                       "Works for pending requests, and for approved leave that has not started yet. "
                       "If you don't know the request ID, call get_my_requests first to find it. "
                       "The system will ask the user to confirm before cancelling.",
        "input_schema": {
            "type": "object",
            "properties": {"request_id": {"type": "integer"}},
            "required": ["request_id"],
        },
    },
    {
        "name": "search_policy",
        "description": "Search the company HR policy document. Use this for any question about "
                       "leave rules, carry forward, medical certificates, holidays, work from home "
                       "or notice period. Returns the most relevant policy sections.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "create_hr_ticket",
        "description": "Raise a ticket with the HR team for the logged-in user, when the assistant "
                       "cannot solve their problem. Only call this after the user has agreed. "
                       "The system will ask the user to confirm before sending.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The user's question in their own words"},
                "summary": {"type": "string", "description": "One neutral sentence describing the problem"},
            },
            "required": ["question", "summary"],
        },
    },
    {
        "name": "get_my_tickets",
        "description": "List the HR tickets the logged-in user raised for THEMSELVES, "
                       "with their status and HR's reply if there is one.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_open_tickets",
        "description": "For HR staff only: list all open HR tickets from employees.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "reply_to_ticket",
        "description": "For HR staff only: reply to an open ticket by its numeric ID (HR-0003 is 3). "
                       "The reply closes the ticket. The system will ask HR to confirm before sending.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "reply": {"type": "string"},
            },
            "required": ["ticket_id", "reply"],
        },
    },
]


def system_prompt_for(user_id):
    user = get_employee(user_id)
    return [
        # block 1: same for everyone, so it can be cached (the tools before it get cached too)
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        # block 2: changes per user, so it goes AFTER the cached part
        {"type": "text", "text": f"The logged-in user is {user['name']}, role: {user['role']}. "
                                 "Earlier messages may be reloaded from saved history without their tool calls. "
                                 "Never reuse numbers from them. For every balance, day count or request status, "
                                 "call the tool again in this turn."},
    ]


def confirm_with_human(tool_input):
    # this runs in YOUR code, so the model cannot skip it
    print("\n  >>> Please confirm:")
    for key, value in tool_input.items():
        print(f"      {key}: {value}")
    answer = input("  >>> Proceed? (yes/no): ")
    return answer.strip().lower() in ("yes", "y")


def run_tool(name, tool_input):
    # the user's ID always comes from the login, never from the model
    if name == "get_leave_balance":
        return get_leave_balance(LOGGED_IN_ID)

    if name == "get_my_requests":
        return get_my_requests(LOGGED_IN_ID)

    if name == "count_leave_days":
        try:
            start = date.fromisoformat(tool_input["start_date"])
            end = date.fromisoformat(tool_input["end_date"])
        except ValueError:
            return {"error": "Dates must be in YYYY-MM-DD format"}
        if end < start:
            return {"error": "End date is before start date"}
        if start < date.today():
            return {"error": f"Start date {start} is in the past. Today is {date.today()}. "
                             "If the user didn't give a year, use the next upcoming date."}
        return {"working_days": count_working_days(start, end)}

    if name == "apply_leave":
        emp = get_employee(LOGGED_IN_ID)
        try:
            start = date.fromisoformat(tool_input["start_date"])
            end = date.fromisoformat(tool_input["end_date"])
        except ValueError:
            return {"error": "Dates must be in YYYY-MM-DD format"}
        if start < date.today():
            return {"error": f"Leave cannot start in the past. Today is {date.today()}."}
        days = count_working_days(start, end)
        balance = emp[f"{tool_input['leave_type']}_balance"]
        # refuse BEFORE asking, so the user is never asked to confirm something impossible
        if balance - days < 0:
            return {"error": f"Not enough {tool_input['leave_type']} leave: needs {days} working days, "
                             f"only {balance} available. Nothing was submitted and no confirmation was shown."}
        details = {**tool_input,
                   "working_days": days,
                   "current_balance": balance,
                   "all_balances": {"casual": emp["casual_balance"], "sick": emp["sick_balance"]},
                   "balance_after_approval": balance - days}
        if not confirm_with_human(details):
            return {"submitted": False, "working_days": days,
                    "message": f"The employee chose not to submit this {days}-day request. No request exists."}
        return apply_leave(LOGGED_IN_ID, **tool_input)

    if name == "cancel_leave":
        if not confirm_with_human({"cancel_request_id": tool_input["request_id"]}):
            return {"cancelled": False, "message": "The user chose not to cancel. The request is unchanged."}
        return cancel_leave(LOGGED_IN_ID, tool_input["request_id"])

    if name == "get_team_member_balance":
        return get_team_member_balance(LOGGED_IN_ID, tool_input["employee_id"])

    if name == "get_pending_requests":
        return get_pending_requests(LOGGED_IN_ID)

    if name == "decide_leave":
        if not confirm_with_human(tool_input):
            return {"saved": False, "message": "The manager chose not to save this decision. Nothing changed."}
        result = decide_leave(LOGGED_IN_ID, **tool_input)
        # tell the employee, but only if the decision was actually saved
        if isinstance(result, dict) and "error" not in result:
            notify_decision(tool_input["request_id"], tool_input["decision"],
                            tool_input.get("reason"), get_employee(LOGGED_IN_ID)["name"])
        return result

    if name == "search_policy":
        sections = search_policy(tool_input["question"])
        if not sections:
            return {"found": False, "message": "No matching section in the HR policy."}
        return {"found": True, "sections": sections}

    # ---------- HR tickets ----------

    if name == "create_hr_ticket":
        if not confirm_with_human({"send_to_HR": tool_input.get("summary", "")}):
            return {"created": False, "message": "The user chose not to raise a ticket. Nothing was sent."}
        return create_hr_ticket(LOGGED_IN_ID, tool_input.get("question"), tool_input.get("summary"))

    if name == "get_my_tickets":
        return get_my_tickets(LOGGED_IN_ID)

    if name == "get_open_tickets":
        return get_open_tickets(LOGGED_IN_ID)

    if name == "reply_to_ticket":
        if not confirm_with_human(tool_input):
            return {"sent": False, "message": "HR chose not to send this reply. The ticket is still open."}
        result = reply_to_ticket(LOGGED_IN_ID, tool_input["ticket_id"], tool_input.get("reply"))
        # tell the employee, but only if the reply was actually saved
        if isinstance(result, dict) and "error" not in result:
            notify_ticket_reply(tool_input["ticket_id"], get_employee(LOGGED_IN_ID)["name"])
        return result

    return {"error": f"Unknown tool {name}"}


def chat():
    global LOGGED_IN_ID
    summary, messages = build_context(LOGGED_IN_ID, client, MODEL)   # pick up where this user left off
    print("HR assistant ready. Type 'login E001' to switch user, 'quit' to exit.")
    print(f"Logged in as {get_employee(LOGGED_IN_ID)['name']} ({LOGGED_IN_ID})")
    show_unread(LOGGED_IN_ID)
    if messages:
        print(f"(Loaded {len(messages)} earlier messages)")

    while True:
        user_text = input("\nYou: ")
        if user_text.lower() == "quit":
            break

        # switching user is handled by code, not by the model
        if user_text.lower().startswith("login "):
            new_id = user_text.split()[1].upper()
            new_user = get_employee(new_id)
            if new_user:
                LOGGED_IN_ID = new_id
                summary, messages = build_context(LOGGED_IN_ID, client, MODEL)   # only THIS user's history
                reset_usage()                                                  # new session, new budget
                print(f"Logged in as {new_user['name']} ({new_id})")
                show_unread(LOGGED_IN_ID)
                if messages:
                    print(f"(Loaded {len(messages)} earlier messages)")
            else:
                print("Unknown employee ID")
            continue

        # budget check BEFORE calling the model, so we never pay for a refused request
        if cost_so_far() >= MAX_COST_PER_CONVERSATION:
            print("\nAgent: This conversation has reached its usage limit. "
                  "Please start a new one with 'login' or contact HR.")
            continue

        # keep the chat short mid-session: rebuild from the database when it grows
        if len(messages) > 20:
            summary, messages = build_context(LOGGED_IN_ID, client, MODEL)

        messages.append({"role": "user", "content": user_text})
        save_message(LOGGED_IN_ID, "user", user_text)

        # the agent loop, limited so one question can't loop forever
        calls_this_message = 0
        while calls_this_message < MAX_CALLS_PER_MESSAGE:
            calls_this_message += 1
            response = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system_prompt_for(LOGGED_IN_ID) + (
                    [{"type": "text", "text": f"Summary of earlier conversations: {summary}"}]
                    if summary else []
                ),
                tools=TOOLS,
                messages=messages,
            )
            record_usage(response)
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                reply = "".join(b.text for b in response.content if b.type == "text")
                print(f"\nAgent: {reply}")
                save_message(LOGGED_IN_ID, "assistant", reply)
                print(f"\n  [usage] {usage['calls']} calls, {usage['input_tokens']} in, "
                      f"{usage['cache_read']} cached, {usage['output_tokens']} out, "
                      f"${cost_so_far():.4f} so far")
                break

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  [tool call] {block.name} {block.input}")
                    result = run_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            print("\nAgent: Sorry, that took too many steps. Please try rephrasing.")


if __name__ == "__main__":
    init_db()            # creates tables if missing; never wipes existing data
    init_memory()        # creates the chat_messages table if missing
    init_summaries()     # creates the chat_summaries table if missing
    init_notifications() # creates the notifications table if missing
    chat()