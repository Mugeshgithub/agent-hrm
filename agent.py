# agent.py
from dotenv import load_dotenv
load_dotenv()

import tracing                     # must come before the Anthropic client is created
from tracing import observe, propagate_attributes

import json
import os
from datetime import date, datetime
import anthropic

import payslips
from roles import tools_for, allowed
from db import init_db
from memory import init_memory, init_summaries, save_message, build_context
from notifications import init_notifications, notify_decision, notify_ticket_reply, show_unread
from policy import search_policy
from citations import ungrounded_citations, RETRY_MESSAGE, SAFE_REPLY
from tools import (
    get_leave_balance, apply_leave, days_for, holidays_of, balance_of, clean_days,
    get_pending_requests, decide_leave, get_my_requests, get_employee,
    get_team_member_balance, cancel_leave,
    create_hr_ticket, get_my_tickets, get_open_tickets, reply_to_ticket,
    PAID, LEAVE_TYPES,
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


def new_session_id(user_id):
    # one session per login, so Langfuse can group a whole conversation
    return f"{user_id}-{datetime.now():%Y%m%d-%H%M%S}"


SESSION_ID = new_session_id(LOGGED_IN_ID)

# the current conversation, shared by chat() and answer_turn()
state = {"messages": [], "summary": None}

# what happened in the last answered message: read by the evals (tools used, tool results for citations)
last_turn = {"tools_called": [], "sources": []}
EXTRA_TAGS = []   # extra Langfuse tags, e.g. ["eval"] when the evals drive the agent

SYSTEM_PROMPT = f"""You are an HR assistant for employees, managers and HR staff.
Today's date is {date.today().isoformat()}. When a user gives a date without a year, use the next upcoming date.
Answer in plain, friendly language. Keep replies to 2 or 3 short sentences. No emoji. Do not repeat options already given.
After submitting a leave request, always tell the employee their current balance and what it will be once approved.
Only use the tools to get data. Never guess balances or numbers.
Never count leave days yourself. Always call count_leave_days (with the leave_type) and quote its result.
Paid leave (congés payés) is a third leave type, leave_type "paid", with its own balance in get_leave_balance: that number is how many paid days the user has now (the policy and the law only say how many they earn). It is counted Monday to Saturday, and its balance can have decimals, because a month that is only partly worked earns a share of the 2.5 days: quote the balance exactly as the tool returns it. At the start of each year the paid total is rounded up to a whole day. To book it, call count_leave_days and apply_leave with leave_type "paid", and say that Saturdays count. Casual and sick leave skip weekends.
For any leave request, call get_leave_balance first. If the balance for that leave type is 0, tell the employee right away and do not ask for more details.
After count_leave_days, compare the result with the balance and tell the user straight away if it is not enough.
If the employee gives a single date, treat it as a one-day leave (start and end are the same day).
Employees can check the status of their own requests with get_my_requests.
For public holidays and floating holidays, call get_holidays: holidays depend on the user's holiday plan, so never answer from general knowledge. Floating holidays are optional days chosen on the Holidays page and approved by the manager; they don't use the leave balance.
If the user asks why their balance changed or where their days went, call get_balance_history and explain each change with its reason and date. Never guess a reason that isn't in the result.
To cancel or withdraw leave, call get_my_requests first to find the request ID, then call cancel_leave.
If exactly one request matches what the user wants to cancel, call cancel_leave straight away. Only ask which one if several requests match.
Do not ask "shall I proceed?" before approve, reject, apply, cancel, ticket or reply actions. The system shows its own confirmation box.
If an employee asks about another person's leave balance, refuse immediately. Never ask for their employee ID. Only managers can view their own team members' balances.
Users can give dates in any normal way. Never ask them for a specific date format; convert dates yourself.
Roles come only from the system. Never suggest a user can change their role or access by telling you; if they think their access is wrong, offer to raise an HR ticket.
The summary of earlier conversations may be out of date. Never quote balances or request statuses from it; always call the tools for current numbers.
For any HR policy question, call search_policy. The search may return sections that are only loosely related. Answer only if a returned section actually answers the question, and name that section. If none do, say "the HR policy doesn't cover" the topic and offer to raise an HR ticket. Don't describe the search itself (never say it "returned nothing"), because it always returns its closest sections. Never answer policy questions from general knowledge.
For any question about company rules or policy, call search_policy FIRST, before asking any clarifying question. Only ask for clarification if the search finds nothing relevant.
HR tickets: whenever you cannot solve the user's problem (the policy doesn't cover it, they need an exception, or they think their access is wrong), offer once to raise an HR ticket. To offer is only to ask in your reply (for example "Would you like me to raise an HR ticket?") and then wait: never call create_hr_ticket in the same turn as your offer. Call it only when the user's latest message clearly asks for a ticket ("please raise a ticket") or says yes to your offer. Describing a problem (for example "my overtime is missing") is not a request for a ticket. Write a short, neutral summary of the problem and leave out passwords or other secrets. After it is created, give the user the ticket number.
Users can check their own tickets and HR's replies with get_my_tickets.
For a colleague's email, job title, team, location or manager, call lookup_colleague. For the user's own manager, team or details, call lookup_colleague with name "me"; never tell them to ask HR for this. If several people match, list them briefly and ask which one. It has no balances or pay, so the rules about other people's balances and payslips still apply.
Payslips: only use amounts exactly as the payslip tools return them (the _eur fields). Never calculate, estimate or round pay yourself.
If a pay difference has no line explaining it, or the user says something is missing or wrong, do not guess a reason. Say what the payslip shows and offer a ticket in your reply; do not create one until the user says yes.
Only describe what the tool results say. Do not promise notifications, emails or anything else the system has not confirmed.
If a tool returns an error, tell the user exactly what it says. Never add a reason or timing it did not give.
For questions about French labour law, call search_labour_code. Answer only from the articles it returns, in the user's language, citing the article number (for example "Article L3141-3") and quoting the key French sentence.
Use the article that directly answers the question: for 'how many' or 'how long', the article that gives the number. Prefer it over articles about exceptions or details, unless the user asks about exceptions. Never say the law does not specify something if one of the returned articles does. If none of the articles answers the question, say so.
For questions about the user's own entitlements (paid leave or holiday days, working hours, rest, notice periods), call BOTH search_policy and search_labour_code. Paid leave (congés payés) is a separate entitlement from casual leave and sick leave: never compare casual or sick days with the paid-leave law. If the user gives a number (for example "my manager says 20 days"), compare that number with the law and say whether it is below it. Article L3141-3 gives 2.5 working days (jours ouvrables) per month worked, so 30 for a full year: describe it that way and always cite it. Give the company policy, then the legal minimum with its article. In policy and law answers state only what the returned text says: do not add your own examples or calculations (such as how many days a week off uses). If the policy gives less than the law, say so clearly: the law is a minimum, so the legal amount applies, and offer to raise an HR ticket.
Labour-law answers are general information about the law, not legal advice; a collective agreement or the employment contract may give more."""

# rules only one role needs; added to the prompt for that role only
ROLE_RULES = {
    "employee": """
Never mention or offer manager or HR features like team requests, approvals or other people's tickets.""",
    "manager": """
You can view your team's pending requests and approve or reject them.
A rejection always needs a reason; ask for one if it was not given, politely explaining it helps the employee plan or reapply.
If the answer to an approve-or-reject question is unclear (like "no"), ask whether to reject it or leave it pending. Never treat an unclear answer as a decision.""",
    "hr": """
You can list open tickets with get_open_tickets and answer one with reply_to_ticket. A reply closes the ticket.
You may look up any employee's payslip by passing employee_id; every such view is logged.""",
}

# ALL tools; roles.tools_for() picks which ones each role is sent
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
        "description": "Count the days a leave request would use between two dates, excluding public "
                       "holidays. Casual and sick leave skip weekends; paid leave (leave_type 'paid') is "
                       "counted Monday to Saturday and skips only Sundays. "
                       "Call this before submitting any leave request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "leave_type": {"type": "string", "enum": ["casual", "sick", "paid"],
                               "description": "Which kind of leave; defaults to casual"},
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
                "leave_type": {"type": "string", "enum": ["casual", "sick", "paid"]},
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["leave_type", "start_date", "end_date"],
        },
    },
    {
        "name": "get_pending_requests",
        "description": "List leave requests from your team members that are waiting for your approval. "
                       "Use this when a manager asks about pending requests, approvals, or their team.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_team_member_balance",
        "description": "Get the leave balance of an employee who reports to you. "
                       "Needs the employee's ID, for example E001.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "string"}},
            "required": ["employee_id"],
        },
    },
    {
        "name": "decide_leave",
        "description": "Approve or reject a pending leave request from your team by its request ID. "
                       "The system will ask you to confirm before saving.",
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
                       "leave rules, carry forward, medical certificates or doctor's notes, holidays, "
                       "work from home or notice period. Call it FIRST, even for short or vague "
                       "questions, before asking the user to clarify. Returns the most relevant "
                       "policy sections.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "create_hr_ticket",
        "description": "Raise a ticket with the HR team for the logged-in user, when the assistant "
                       "cannot solve their problem. Only call this when the user has explicitly asked "
                       "for a ticket or said yes to your offer in this conversation. If they have not, "
                       "do NOT call it: offer the ticket in your reply and wait for their answer.",
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
        "description": "List all open HR tickets from employees.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "reply_to_ticket",
        "description": "Reply to an open HR ticket by its numeric ID (HR-0003 is 3). "
                       "The reply closes the ticket. The system will ask you to confirm before sending.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "reply": {"type": "string"},
            },
            "required": ["ticket_id", "reply"],
        },
    },
    {
        "name": "get_payslip",
        "description": "Get one month's payslip: line items, gross, contributions, tax and net paid.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "YYYY-MM, e.g. 2026-10"},
                "employee_id": {"type": "string", "description": "Another employee's ID. Omit for your own."},
            },
            "required": ["month"],
        },
    },
    {
        "name": "compare_payslips",
        "description": "Compare two months' payslips line by line, with the exact change in net pay. "
                       "Use for 'why is my pay different?'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month_a": {"type": "string", "description": "Earlier month, YYYY-MM"},
                "month_b": {"type": "string", "description": "Later month, YYYY-MM"},
                "employee_id": {"type": "string", "description": "Another employee's ID. Omit for your own."},
            },
            "required": ["month_a", "month_b"],
        },
    },
    {
        "name": "get_pay_summary",
        "description": "Year-to-date totals (gross, contributions, tax, net paid) for one year.",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "string", "description": "YYYY"},
                "employee_id": {"type": "string", "description": "Another employee's ID. Omit for your own."},
            },
            "required": ["year"],
        },
    },
        {
        "name": "get_holidays",
        "description": "List the user's own public holidays and floating holidays for a year, from their "
                       "holiday plan (which depends on their office), and how many floating holidays they have left. "
                       "Use it for 'when is the next public holiday?', 'is Good Friday a holiday for me?' or "
                       "'how many floating holidays do I have?'.",
        "input_schema": {
            "type": "object",
            "properties": {"year": {"type": "integer", "description": "The year, e.g. 2026. Defaults to this year."}},
        },
    },
        {
        "name": "get_balance_history",
        "description": "Explain why the user's own leave balance is what it is: HR adjustments with their "
                       "reasons, the yearly reset (new allowance, casual carry-over, sick days lapsing) and the "
                       "leave they took this year. Use it for 'why did my balance change?' or 'where did my days go?'.",
        "input_schema": {"type": "object", "properties": {}},
    },
        {
        "name": "lookup_colleague",
        "description": "Find a current colleague by name and get their work email, job title, department, "
                       "location and manager. Use it for questions like 'who is Hugo's manager?' or "
                       "'what's Clara's email?'. For the user themselves ('who is my manager?', 'who is "
                       "in my team?'), pass name 'me': it returns their own record and who reports to them. "
                       "It never returns leave balances, pay or personal details.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string",
                                    "description": "All or part of the person's name, or 'me' for the user"}},
            "required": ["name"],
        },
    },
        {
        "name": "search_labour_code",
        "description": "Search the French Code du travail (labour law) for articles relevant to an "
                       "employment-law question: paid leave entitlement, working hours, rest periods, "
                       "maternity leave, trial periods, harassment, dismissal, and so on. Returns article "
                       "numbers and their French text. Use this for what the LAW says; use search_policy "
                       "for the company's own rules.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "The user's question, in any language"}},
            "required": ["question"],
        },
    },
]


def system_prompt_for(user_id):
    user = get_employee(user_id)
    role_rules = ROLE_RULES.get(user["role"], ROLE_RULES["employee"])
    return [
        # block 1: same for everyone with this role, so it can be cached
        {"type": "text", "text": SYSTEM_PROMPT + role_rules, "cache_control": {"type": "ephemeral"}},
        # block 2: changes per user, so it goes AFTER the cached part
        {"type": "text", "text": f"The logged-in user is {user['name']}, role: {user['role']}. "
                                 "Earlier messages may be reloaded from saved history without their tool calls. "
                                 "Never reuse numbers from them. For every balance, day count or request status, "
                                 "call the tool again in this turn."},
    ]


def with_cache_marker(messages):
    # mark the LAST message so the whole conversation so far (incl. tool results like the
    # ~10k tokens of labour articles) is cached; the next call re-reads it at ~10% of the price.
    # works on a copy, so markers never pile up in the saved history (max 4 per request).
    if not messages:
        return messages
    marked = list(messages)
    last = dict(marked[-1])
    content = last["content"]
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    else:
        content = [b if isinstance(b, dict) else b.model_dump(exclude_none=True) for b in content]
    content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
    last["content"] = content
    marked[-1] = last
    return marked


def confirm_with_human(tool_input):
    # this runs in YOUR code, so the model cannot skip it
    print("\n  >>> Please confirm:")
    for key, value in tool_input.items():
        print(f"      {key}: {value}")
    answer = input("  >>> Proceed? (yes/no): ")
    return answer.strip().lower() in ("yes", "y")


# web app: instead of asking in the terminal, the action is parked and shown as a confirmation card.
# The web server sets confirm_with_human to return AWAITING_CONFIRMATION; when the person clicks
# Confirm, the server runs the parked action through run_tool again with confirmation = yes.
AWAITING_CONFIRMATION = "awaiting_confirmation"
pending_action = {}
CARD_SHOWN = {"awaiting_confirmation": True,
              "message": "Nothing has been done yet. The app is showing the user a confirmation card for "
                         "this action; tell them to review it and press Confirm."}


def confirmed(name, tool_input, details):
    answer = confirm_with_human(details)
    if answer == AWAITING_CONFIRMATION:
        pending_action.clear()
        pending_action.update(tool=name, input=dict(tool_input), details=details)
    return answer


@observe()   # each tool call shows up as a step inside the trace
def run_tool(name, tool_input):
    # defence in depth: even if a tool call gets through, the role must allow it
    if not allowed(get_employee(LOGGED_IN_ID)["role"], name):
        return {"error": "That action isn't available for your role."}

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
        kind = tool_input.get("leave_type") or "casual"
        if kind not in LEAVE_TYPES:
            return {"error": f"Unknown leave type '{kind}'"}
        result = {"working_days": days_for(kind, start, end, holidays_of(LOGGED_IN_ID))}
        if kind == PAID:
            result["counted"] = "Monday to Saturday (jours ouvrables); Sundays and public holidays are not counted"
        return result

    if name == "apply_leave":
        emp = get_employee(LOGGED_IN_ID)
        try:
            start = date.fromisoformat(tool_input["start_date"])
            end = date.fromisoformat(tool_input["end_date"])
        except ValueError:
            return {"error": "Dates must be in YYYY-MM-DD format"}
        if start < date.today():
            return {"error": f"Leave cannot start in the past. Today is {date.today()}."}
        days = days_for(tool_input["leave_type"], start, end, holidays_of(LOGGED_IN_ID))
        # available = saved balance minus days already waiting for approval (same rule as apply_leave),
        # so the confirmation never shows a "balance after" that ignores pending requests
        from hr_queries import balances
        balance = balances(LOGGED_IN_ID)[tool_input["leave_type"]]["available"]
        # refuse BEFORE asking, so the user is never asked to confirm something impossible
        if balance - days < 0:
            return {"error": f"Not enough {tool_input['leave_type']} leave: needs {days} days, "
                             f"only {balance} available (pending requests included). "
                             f"Nothing was submitted and no confirmation was shown."}
        details = {**tool_input,
                   "working_days": days,
                   "current_balance": balance,
                   "all_balances": balance_of(emp),
                   "balance_after_approval": clean_days(balance - days)}
        answer = confirmed(name, tool_input, details)
        if answer == AWAITING_CONFIRMATION:
            return CARD_SHOWN
        if not answer:
            return {"submitted": False, "working_days": days,
                    "message": f"The employee chose not to submit this {days}-day request. No request exists."}
        return apply_leave(LOGGED_IN_ID, **tool_input)

    if name == "cancel_leave":
        answer = confirmed(name, tool_input, {"cancel_request_id": tool_input["request_id"]})
        if answer == AWAITING_CONFIRMATION:
            return CARD_SHOWN
        if not answer:
            return {"cancelled": False, "message": "The user chose not to cancel. The request is unchanged."}
        return cancel_leave(LOGGED_IN_ID, tool_input["request_id"])

    if name == "get_team_member_balance":
        return get_team_member_balance(LOGGED_IN_ID, tool_input["employee_id"])

    if name == "get_pending_requests":
        return get_pending_requests(LOGGED_IN_ID)

    if name == "decide_leave":
        answer = confirmed(name, tool_input, tool_input)
        if answer == AWAITING_CONFIRMATION:
            return CARD_SHOWN
        if not answer:
            return {"saved": False, "message": "The manager chose not to save this decision. Nothing changed."}
        result = decide_leave(LOGGED_IN_ID, **tool_input)
        # tell the employee, but only if the decision was actually saved
        if isinstance(result, dict) and "error" not in result:
            notify_decision(tool_input["request_id"], tool_input["decision"],
                            tool_input.get("reason"), get_employee(LOGGED_IN_ID)["name"])
        return result

    if name == "get_holidays":
        from leave_admin import my_holidays
        return my_holidays(LOGGED_IN_ID, tool_input.get("year"))   # always the user's own plan

    if name == "get_balance_history":
        from leave_admin import balance_history
        return balance_history(LOGGED_IN_ID)     # always the user's own history

    if name == "lookup_colleague":
        # directory details only (employees.directory_entry): never balances, pay or history
        from employees import find_colleagues, my_record
        wanted = tool_input.get("name", "").strip().lower()
        if wanted in ("me", "myself", "i", "my", ""):
            return my_record(LOGGED_IN_ID)       # "me" is always the login, never a name the model chose
        return find_colleagues(wanted)

    if name == "search_policy":
        sections = search_policy(tool_input["question"])
        if not sections:
            return {"found": False, "message": "No matching section in the HR policy."}
        return {"found": True, "sections": sections}
    
    if name == "search_labour_code":
        # no database configured (for example in CI): say so instead of crashing
        if not os.environ.get("LABOUR_DB_URL"):
            return {"error": "The labour-law database isn't available right now."}
        try:
            # imported here, not at the top, so CI (which has no labour-code database) still runs
            # multi-query finds candidates (top 20), then Haiku reranks them by "does it answer the question?"
            # recall@8 on 50 EN/FR questions: vector 45, multi-query 48, multi+rerank 50
            # search with the employee's own words AND the agent's query (the agent's rewording once hid L3141-3)
            from labour_code.search import connect, search_for_agent
            with connect() as conn:
                results = search_for_agent(conn, last_turn.get("question"), tool_input["question"], k=20)
        except Exception:
            # e.g. Docker is not running: the user gets a clear message, the agent keeps working
            return {"error": "The labour-law search is not reachable right now. Please try again later."}
        articles = {}
        for r in results:                      # keep the 5 best articles, one entry each
            # top 5: full recall on all 70 test questions (top 8 gave no extra), ~37% fewer tokens
            if r["num"] not in articles and len(articles) < 5:
                articles[r["num"]] = r["text"]
        return {"articles": [{"article": n, "text_fr": t} for n, t in articles.items()],
                "note": "Official French text of the Code du travail. Cite the article number."}   

    # ---------- HR tickets ----------

    if name == "create_hr_ticket":
        answer = confirmed(name, tool_input, {"send_to_HR": tool_input.get("summary", "")})
        if answer == AWAITING_CONFIRMATION:
            return CARD_SHOWN
        if not answer:
            return {"created": False, "message": "The user chose not to raise a ticket. Nothing was sent."}
        return create_hr_ticket(LOGGED_IN_ID, tool_input.get("question"), tool_input.get("summary"))

    if name == "get_my_tickets":
        return get_my_tickets(LOGGED_IN_ID)

    if name == "get_open_tickets":
        return get_open_tickets(LOGGED_IN_ID)

    if name == "reply_to_ticket":
        answer = confirmed(name, tool_input, tool_input)
        if answer == AWAITING_CONFIRMATION:
            return CARD_SHOWN
        if not answer:
            return {"sent": False, "message": "HR chose not to send this reply. The ticket is still open."}
        result = reply_to_ticket(LOGGED_IN_ID, tool_input["ticket_id"], tool_input.get("reply"))
        # tell the employee, but only if the reply was actually saved
        if isinstance(result, dict) and "error" not in result:
            notify_ticket_reply(tool_input["ticket_id"], get_employee(LOGGED_IN_ID)["name"])
        return result

    # ---------- payslips (read-only; the viewer is always the logged-in user) ----------

    if name == "get_payslip":
        target = tool_input.get("employee_id") or LOGGED_IN_ID
        return payslips.get_payslip(LOGGED_IN_ID, target, tool_input["month"])

    if name == "compare_payslips":
        target = tool_input.get("employee_id") or LOGGED_IN_ID
        return payslips.compare_payslips(LOGGED_IN_ID, target,
                                         tool_input["month_a"], tool_input["month_b"])

    if name == "get_pay_summary":
        target = tool_input.get("employee_id") or LOGGED_IN_ID
        return payslips.get_pay_summary(LOGGED_IN_ID, target, tool_input["year"])

    return {"error": f"Unknown tool {name}"}


@observe(name="hr-agent-turn")   # one trace per user question, with every Claude call and tool inside
def answer_turn(user_text):
    user = get_employee(LOGGED_IN_ID)
    with propagate_attributes(user_id=LOGGED_IN_ID, session_id=SESSION_ID,
                              tags=[user["role"], *EXTRA_TAGS], metadata={"model": MODEL}):
        messages = state["messages"]
        summary = state["summary"]

        # the agent loop, limited so one question can't loop forever
        calls_this_message = 0
        turn_sources = []          # every tool result in this turn: the only allowed sources of citations
        citation_retry_used = False
        last_turn["tools_called"] = []
        last_turn["sources"] = turn_sources
        last_turn["question"] = user_text   # the employee's own words, used by the labour-law search
        while calls_this_message < MAX_CALLS_PER_MESSAGE:
            calls_this_message += 1
            response = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system_prompt_for(LOGGED_IN_ID) + (
                    [{"type": "text", "text": f"Summary of earlier conversations: {summary}"}]
                    if summary else []
                ),
                tools=tools_for(user["role"], TOOLS),   # only this role's tools
                messages=with_cache_marker(messages),   # cache the conversation so far
            )
            record_usage(response)
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                reply = "".join(b.text for b in response.content if b.type == "text")

                # guardrail in code: never show an article the agent didn't retrieve in this turn
                bad = ungrounded_citations(reply, turn_sources)
                if bad and not citation_retry_used:
                    citation_retry_used = True
                    print(f"  [guard] ungrounded citation {bad}: asking the agent to search first")
                    messages.append({"role": "user", "content": RETRY_MESSAGE.format(articles=", ".join(bad))})
                    continue
                if bad:
                    print(f"  [guard] still ungrounded {bad}: replying safely instead")
                    reply = SAFE_REPLY
                print(f"\nAgent: {reply}")
                save_message(LOGGED_IN_ID, "assistant", reply)
                print(f"\n  [usage] {usage['calls']} calls, {usage['input_tokens']} in, "
                      f"{usage['cache_write']} cache-written, {usage['cache_read']} cached, {usage['output_tokens']} out, "
                      f"${cost_so_far():.4f} so far")
                return reply

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  [tool call] {block.name} {block.input}")
                    last_turn["tools_called"].append(block.name)
                    result = run_tool(block.name, block.input)
                    turn_sources.append(json.dumps(result, ensure_ascii=False))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })
            messages.append({"role": "user", "content": tool_results})

        print("\nAgent: Sorry, that took too many steps. Please try rephrasing.")
        return "(stopped: too many steps)"


def start_session(user_id):
    """Log a user in: load their history (+ summary) and start a fresh budget. Used by chat() and the evals."""
    global LOGGED_IN_ID, SESSION_ID
    LOGGED_IN_ID = user_id
    SESSION_ID = new_session_id(user_id)
    state["summary"], state["messages"] = build_context(user_id, client, MODEL)
    reset_usage()


def handle_message(user_text):
    """One user message, exactly as production runs it. chat() and the evals both call this,
    so the tests always check the real agent, never a copy."""
    # budget check BEFORE calling the model, so we never pay for a refused request
    if cost_so_far() >= MAX_COST_PER_CONVERSATION:
        reply = ("This conversation has reached its usage limit. "
                 "Please start a new one with 'login' or contact HR.")
        print(f"\nAgent: {reply}")
        return reply

    # keep the chat short mid-session: rebuild from the database when it grows
    if len(state["messages"]) > 20:
        state["summary"], state["messages"] = build_context(LOGGED_IN_ID, client, MODEL)

    state["messages"].append({"role": "user", "content": user_text})
    save_message(LOGGED_IN_ID, "user", user_text)
    return answer_turn(user_text)


def chat():
    global LOGGED_IN_ID, SESSION_ID
    state["summary"], state["messages"] = build_context(LOGGED_IN_ID, client, MODEL)   # pick up where this user left off
    print("HR assistant ready. Type 'login E001' to switch user, 'quit' to exit.")
    print(f"Logged in as {get_employee(LOGGED_IN_ID)['name']} ({LOGGED_IN_ID})")
    print(f"(Tracing {'ON' if tracing.ENABLED else 'OFF'})")
    show_unread(LOGGED_IN_ID)
    if state["messages"]:
        print(f"(Loaded {len(state['messages'])} earlier messages)")

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
                SESSION_ID = new_session_id(new_id)                                   # new conversation in Langfuse
                state["summary"], state["messages"] = build_context(LOGGED_IN_ID, client, MODEL)
                reset_usage()                                                         # new session, new budget
                print(f"Logged in as {new_user['name']} ({new_id})")
                show_unread(LOGGED_IN_ID)
                if state["messages"]:
                    print(f"(Loaded {len(state['messages'])} earlier messages)")
            else:
                print("Unknown employee ID")
            continue

        handle_message(user_text)

    tracing.flush()   # send any traces still waiting before the program exits


if __name__ == "__main__":
    init_db()                 # creates tables if missing; never wipes existing data
    init_memory()             # creates the chat_messages table if missing
    init_summaries()          # creates the chat_summaries table if missing
    init_notifications()      # creates the notifications table if missing
    payslips.init_payslips()  # creates the payslip tables if missing
    payslips.seed_payslips()  # sample data for now; later replaced by PDF import or a payroll feed
    import hr_queries, employees, leave_admin
    hr_queries.init_profiles()   # job details, used by lookup_colleague
    employees.init_records()
    leave_admin.init_leave_admin()   # public holidays and the balance history
    chat()