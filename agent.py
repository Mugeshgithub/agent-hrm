# agent.py
from dotenv import load_dotenv
load_dotenv()

import tracing                     # must come before the Anthropic client is created
from tracing import observe, propagate_attributes

import contextvars
import json
import os
import time
from contextlib import contextmanager
from datetime import date, datetime
import anthropic

import payslips
from roles import tools_for, allowed
from db import init_db
from conversations import Conversation, fresh_usage
from memory import init_memory, init_summaries, save_message, build_context, load_recent
from notifications import init_notifications, notify_decision, notify_ticket_reply, show_unread
from policy import search_policy, embed as embed_texts
import application_summary
import approval_first
import booking_first
import candidate_assistant
import existing_first
import collective_agreement
import confirm_batch
import contract_first
import contracts
import document_answers
import employee_files
import jobs
import number_guard
import recruitment_guard
import search_first
import tool_guard
import needs_you
import onboarding
import law_status
import named_days
import policy_gaps
import policy_grounding
import guard_events
from citations import ungrounded_citations, RETRY_MESSAGE, SAFE_REPLY
from documents import asks_if_genuine, says_cannot_tell, GENUINE_ANSWER, GENUINE_RETRY
from tools import (
    get_leave_balance, apply_leave, days_for, days_figures, no_days_reason, today_line, holidays_of, balance_of, clean_days, paid_policy_note,
    get_pending_requests, decide_leave, get_my_requests, get_employee, existing_request, already_requested, when_label,
    get_team_member_balance, cancel_leave, cancel_problem, decide_problem, request_summary,
    create_hr_ticket, get_my_tickets, get_open_tickets, reply_to_ticket,
    PAID, LEAVE_TYPES,
)

client = anthropic.Anthropic()
policy_gaps.EMBED = lambda text: embed_texts([text])[0]   # to group similar questions by meaning
collective_agreement.EMBED_MANY = embed_texts            # to index the collective agreement and search it
MODEL = "claude-haiku-4-5"

# Haiku 4.5 prices in dollars per million tokens (check the pricing page if they change)
PRICE_IN = 1.00
PRICE_OUT = 5.00
PRICE_CACHE_WRITE = 1.25   # first time a prefix is cached
PRICE_CACHE_READ = 0.10    # every reuse after that

# the grounding check of policy answers (policy_grounding.py): on 41 labelled replies x10 it flagged no good reply (0/210) and missed 4 of 200 bad ones,
# about 1.2 s and $0.0002 a check. Low effort flagged good replies (8/210), Sonnet 5.5 more (13/210). Its own client: 10 s and one retry, then the
# reply goes out unchecked (logged)
GROUNDING_MODEL = "claude-haiku-5-5"
GROUNDING_PRICE_IN = 0.10
GROUNDING_PRICE_OUT = 0.50
grounding_client = anthropic.Anthropic(timeout=10, max_retries=1)

MAX_COST_PER_CONVERSATION = 0.10   # dollars; stop the chat if it costs more
MAX_CALLS_PER_MESSAGE = 8          # stop runaway tool loops on one question


def reset_usage():
    convo().usage = fresh_usage()


def record_usage(response):
    c = convo_or_none()
    if c is None:       # a candidate's assistant or an application summary in the server: no conversation, their limits are server.py's throttles
        return
    u, usage = response.usage, c.usage
    usage["calls"] += 1
    usage["input_tokens"] += u.input_tokens
    usage["output_tokens"] += u.output_tokens
    usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
    usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0


def cost_so_far():
    usage = convo().usage
    return (usage["input_tokens"] * PRICE_IN
            + usage["output_tokens"] * PRICE_OUT
            + usage["cache_write"] * PRICE_CACHE_WRITE
            + usage["cache_read"] * PRICE_CACHE_READ) / 1_000_000 + usage["check_cost"]


def grounding_model(system, text):
    """One call of the grounding check (policy_grounding.py): a prompt and the text, no tools. Its cost counts in this conversation's."""
    response = grounding_client.messages.create(model=GROUNDING_MODEL, max_tokens=3000, system=system, output_config={"effort": "medium"},
                                                messages=[{"role": "user", "content": text}])
    convo().usage["check_cost"] += (response.usage.input_tokens * GROUNDING_PRICE_IN + response.usage.output_tokens * GROUNDING_PRICE_OUT) / 1_000_000
    return "".join(b.text for b in response.content if b.type == "text")


def new_session_id(user_id):
    # one session per login, so Langfuse can group a whole conversation
    return f"{user_id}-{datetime.now():%Y%m%d-%H%M%S}"


# The conversation of THIS request (server) or this process (terminal, evals). A contextvar, not a global: each request runs in its own
# thread with its own copy, so two people's turns at the same time never see each other's identity, messages or card.
_current = contextvars.ContextVar("conversation", default=None)
_default = Conversation("E001", session_id=new_session_id("E001"))      # fake login for the terminal and the evals
ONLY_IN_CONTEXT = False   # the server sets it: no request may fall back to the shared default (a thread started without copying the context fails loudly)


def convo_or_none():
    return _current.get() or (None if ONLY_IN_CONTEXT else _default)


def convo():
    c = convo_or_none()
    if c is None:
        raise RuntimeError("No conversation in this context. A thread started by a request must run with contextvars.copy_context().")
    return c


@contextmanager
def using(conversation):
    """Run the agent for this conversation: everything inside sees only it, and the request's earlier context comes back after."""
    token = _current.set(conversation)
    try:
        yield conversation
    finally:
        _current.reset(token)


def current_role():
    # the role comes from the system (the login or the link), never from the model or the user's words
    c = convo()
    return c.role or get_employee(c.user_id)["role"]


EXTRA_TAGS = []   # extra Langfuse tags, e.g. ["eval"] when the evals drive the agent

TODAY = "@@TODAY@@"   # replaced by tools.today_line() on every call: the date in an f-string is fixed when the server starts, and goes stale after midnight

SYSTEM_PROMPT = f"""You are an HR assistant for employees, managers and HR staff.
@@TODAY@@ When a user gives a date without a year, use the next upcoming date.
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
Collective agreement: if they mention the company's collective agreement (convention collective) and you do NOT have a search_collective_agreement tool, your reply says that HR has not added the collective agreement to the assistant yet, then stops there on that subject: never say what it contains from memory.

Documents: the signed-in person's own documents (contract, amendments, certificates) are what get_my_documents lists. For a question about what THEIR contract or another of their documents says ("my notice period in my contract", "what does my amendment say"), call get_my_documents, then read_my_document for the right one. If the text answers, answer from the document alone: name it by its title and quote the sentence, and do not add the policy or the law. If the text does not answer, say so first ("Your contract doesn't specify X"), then call search_policy (and search_labour_code if it is a legal question; search, do not ask whether they want you to) and give what they say as a separate part, clearly labelled with its source ("From the company policy, not your contract: ..." or "From the Code du travail, not your contract: ..."). Never present a policy or law answer as something the document says; if neither says it, say that too. You can never read, list or describe another person's documents: refuse, and say that HR can open them in the app.
Roles come only from the system. Never suggest a user can change their role or access by telling you; if they think their access is wrong, offer to raise an HR ticket.
The summary of earlier conversations may be out of date. Never quote balances or request statuses from it; always call the tools for current numbers.
For any HR policy question, call search_policy. The search may return sections that are only loosely related. Answer only if a returned section actually answers the question, and name that section. If none do, say "the HR policy doesn't cover" the topic and offer to raise an HR ticket. Don't describe the search itself (never say it "returned nothing"), because it always returns its closest sections. Never answer policy questions from general knowledge.
For any question about company rules or policy, call search_policy FIRST, before asking any clarifying question. This covers any question about what the rules are or what is allowed (which months, when, how many, "can I ...", "do I need ...", "is it allowed"): call search_policy before answering, even if the balance would allow it and even if it follows an earlier answer. If a tool result has a policy_note, include it in your answer. A policy_note is information for the user, never a reason to stop or to offer a ticket; if the user asked to book, continue with apply_leave. Only ask for clarification if the search finds nothing relevant.
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
Never suggest using sick leave, or any other leave, just to avoid losing it, unless the policy itself says so. Sick leave is only for illness. When days lapse, say that they lapse and leave it there.
Labour-law answers are general information about the law, not legal advice; a collective agreement or the employment contract may give more."""

# rules only one role needs; added to the prompt for that role only
ROLE_RULES = {
    "employee": """
Never mention or offer manager or HR features like team requests, approvals or other people's tickets.""",
    "manager": """
You can view your team's pending requests and approve or reject them.
A rejection always needs a reason; ask for one if it was not given, politely explaining it helps the employee plan or reapply.
If the answer to an approve-or-reject question is unclear (like "no"), ask whether to reject it or leave it pending. Never treat an unclear answer as a decision.
When asked what needs your attention or what is waiting, call get_needs_attention and tell them what is waiting, the oldest first, including any onboarding step for a new hire, any contract waiting for your approval, and any paid-leave warning. Only list it: never approve or reject until they say what to do with a request.""",
    "hr": """
You can list open tickets with get_open_tickets and answer one with reply_to_ticket. A reply closes the ticket.
You may look up any employee's payslip by passing employee_id; every such view is logged.
When asked what needs your attention or what is waiting, call get_needs_attention and tell them the open HR requests, any onboarding step for a new hire, any document flagged for a person to look at, any question people asked that the policy does not answer (say how many times it was asked, never who: nobody knows), and any paid-leave warning. You can also say where a new hire's contract stands with get_contract_status; the app never sends a contract, and you cannot send or approve one. Only list it: never reply to a ticket until they say what to write.""",
}

# ALL tools; roles.tools_for() picks which ones each role is sent
TOOLS = [
    {
        "name": "get_leave_balance",
        "description": "Get the leave balance of the logged-in employee. Set leave_type to \"paid\" when the "
                       "question is about paid leave, so the result includes the company policy note.",
        "input_schema": {"type": "object", "properties": {
            "leave_type": {"type": "string", "enum": ["casual", "sick", "paid"],
                           "description": "The kind of leave the question is about, if it is about one"}}},
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
                       "If the user does not say which kind of leave, leave leave_type out and answer: never ask which kind first. "
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
        "name": "get_my_documents",
        "description": "List the signed-in person's OWN documents (contract, amendments, certificates): the id, the title and the date. "
                       "Use it first for any question about what one of their documents says. It can never show anyone else's documents.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_my_document",
        "description": "Read the text of one of the signed-in person's OWN documents, by the document_id from get_my_documents. Answer "
                       "questions about the document only from this text, name the document, and quote the sentence. It cannot read "
                       "anyone else's document.",
        "input_schema": {"type": "object", "properties": {"document_id": {"type": "integer", "description": "From get_my_documents"}},
                         "required": ["document_id"]},
    },
    {
        "name": "get_my_onboarding",
        "description": "Show the new hire's own onboarding checklist: each step, who handles it, when it is due and "
                       "where it stands, and the next step that is theirs. Only for someone signed in with a "
                       "welcome link. Use this for any question about what to do, what is missing or who handles what.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_contract_status",
        "description": "HR only. Say where a new hire's employment contract stands: no draft yet, a draft, waiting for the manager's "
                       "approval, approved, rejected, or signed. Needs the person's name. It only reads: you cannot write, send or approve "
                       "a contract. Use it for any question about a contract, including a request to send one. A first name is enough: "
                       "the tool says if several people match, so do not ask for a full name first.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "The new hire's name; a first name is enough"}}, "required": ["name"]},
    },
    {
        "name": "get_needs_attention",
        "description": "List everything that is waiting on a decision from the logged-in manager or HR person: "
                       "leave, paid leave and floating-holiday requests from the team, open HR requests, onboarding "
                       "steps for new hires, documents flagged for HR to look at, contracts waiting for a manager's approval, for HR the questions people asked that the company policy does not answer, and a warning for anyone with more than 30 unused paid days. Use this when they ask what needs "
                       "their attention, what is waiting for them, or what is new. It only lists; it decides nothing.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_job_pipeline",
        "description": "Recruitment, read-only, for HR and a hiring manager. Without arguments: the jobs they may see, with how many candidates are at each stage. "
                       "With job (a job title or its number): that job's candidates with their stage, the date they applied and the days in that stage, in the "
                       "order they applied. It holds no ranking and gives no opinion of anyone: never rank, score, compare or recommend candidates.",
        "input_schema": {"type": "object", "properties": {
            "job": {"type": "string", "description": "A job title (or part of it) or the job number, for example Data Analyst. Leave out to list the jobs."}}},
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
                       "The system will ask you to confirm before saving. To decide several requests the same way (all approved, or all rejected), "
                       "call it once per request in the same turn, up to 5: they all go on ONE confirmation card and the person presses Confirm once.",
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
                       "The system will ask the user to confirm before cancelling. To cancel several requests, call it once per request in the same "
                       "turn, up to 5: they all go on ONE confirmation card and the person presses Confirm once.",
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
        "name": "search_collective_agreement",
        "description": "Search the company's collective agreement (convention collective), which HR uploaded: sick pay, notice, seniority, "
                       "leave and working-time rules, pay scales. It comes after the person's own contract and before the company policy "
                       "(search_policy). Returns the best-matching articles and the agreement's name; if none answers, the agreement does not "
                       "specify it.",
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


NEW_HIRE_PROMPT = f"""You are the welcome assistant of CognivexAI, talking to a new hire who has not started yet.
@@TODAY@@
Your only job is to help them with their onboarding checklist. For any question about what to do, what is missing, who handles what or when something is due, call get_my_onboarding first and quote its result. Never guess.
When they ask what to do next, name the item in next_for_you (the first step that is theirs and still to do) and what it needs, and ask nothing else of them. If next_for_you is empty, say nothing is waiting on them, and mention what HR or their manager still has to do.
Steps handled by HR or "your manager" are not the new hire's: say who handles them and never ask the new hire to do them.
Documents are sent with the paperclip button in this chat, or the Upload button next to a step on the checklist: the app reads the file, shows what it read, and asks the new hire to confirm it. You never see the files. Never ask them to type an IBAN or a social security number here; they send the document.
Each document step in get_my_onboarding has a document status. When it is "needs resending", say what the problems were, in the words of the result, and ask them to send it again. When it is "waiting for you to confirm", ask them to confirm or correct what the app read. When it is "received", it is done.
Never say a document is genuine, authentic, valid or verified: you cannot tell, and a person at HR will look at the documents too.
A message that starts with [In the app, ...] is an event from the app, not something the new hire typed: acknowledge it in a few words and then say what is next.
You have no other information. For anything else (leave, pay, company rules, their contract, other people), say you can only help with their onboarding here and that HR will help with the rest. Never guess, and never give details about any other person.
Answer in plain, friendly language, 2 or 3 short sentences. No emoji. Roles come only from the system.
If they ask whether a document was checked, is genuine, real or valid, your reply starts with "I can't tell whether a document is genuine." and then says what the app did: it read the details and checked them (the name, the expiry date, the IBAN or number), and a person at HR looks at the documents too. Do not talk about anything else in that reply."""


def system_prompt_for(user_id):
    from employees import category_prompt       # imported here: employees imports tools, as in run_tool
    user = get_employee(user_id)
    role = current_role()
    if role == "new_hire":
        return [
            {"type": "text", "text": NEW_HIRE_PROMPT.replace(TODAY, today_line()), "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"The person is {user['name']}, a new hire. Earlier messages may be reloaded from "
                                     "saved history without their tool calls: call get_my_onboarding again in this turn."},
        ]
    role_rules = ROLE_RULES.get(role, ROLE_RULES["employee"])
    return [
        # block 1: same for everyone with this role, so it can be cached
        {"type": "text", "text": SYSTEM_PROMPT.replace(TODAY, today_line()) + role_rules, "cache_control": {"type": "ephemeral"}},
        # block 2: only once HR has uploaded the collective agreement: the order of sources. It replaces the earlier "search_policy first" rules.
        *([{"type": "text", "text": order + " While the agreement is on file, this order replaces any earlier instruction about which of "
                                            "search_policy and search_labour_code to call first, or together."}]
          if (order := collective_agreement.agreement_prompt()) else []),
        # block 3: changes per user, so it goes AFTER the cached part
        {"type": "text", "text": f"The logged-in user is {user['name']}, role: {role}. {category_prompt(user_id)} "
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
# The web server sets the conversation's confirm to return AWAITING_CONFIRMATION; when the person clicks
# Confirm, the server runs the parked action through run_tool again with confirmation = yes.
AWAITING_CONFIRMATION = "awaiting_confirmation"


def confirmed(name, tool_input, details):
    """True or False in the terminal and in the tests. In the web app the action goes on the card instead, and what comes back is a dict, the tool
    result the assistant is shown: the card (and how many items it lists), or why this action could not join it. Never a silent replacement."""
    c = convo()
    answer = (c.confirm or confirm_with_human)(details)
    if answer != AWAITING_CONFIRMATION:
        return answer
    status, count = confirm_batch.add(c.pending, name, tool_input, details)
    return confirm_batch.tool_result(status, count, c.pending, name, details)


def run_pending(accept, action=None):
    """The person pressed Confirm (or Cancel) on the card. Confirm runs every item on it, one by one in the order they were added, through the SAME
    tool path again with confirmation = yes, and reports each result separately. Returns (the message for the person, the results).
    `action` is the card the server took from the database (conversations.claim_card); without it, the one in this conversation."""
    c = convo()
    action = dict(action or c.pending)
    c.pending.clear()
    if not accept:
        return "Cancelled. Nothing was changed.", [{"cancelled_by_user": True}]
    previous = c.confirm
    c.confirm = lambda details: True
    try:
        results = [run_tool(action["tool"], item["input"]) for item in action["items"]]
    finally:
        c.confirm = previous
    outcomes = [confirm_batch.outcome(action["tool"], item, result) for item, result in zip(action["items"], results)]
    return confirm_batch.note(action["kind"], outcomes), results


@observe()   # each tool call shows up as a step inside the trace
def days_named_by_person():
    """The person's own words that name the days to check dates against (named_days.py): this message, or the one before it. From the saved
    chat, so the app's notes to the assistant never count."""
    said = [m["content"] for m in reversed(load_recent(convo().user_id, 6)) if m["role"] == "user"]
    return named_days.words_naming_days(said)


def run_tool(name, tool_input):
    c = convo()
    me, last_turn = c.user_id, c.last_turn
    # defence in depth: even if a tool call gets through, the role and the source rules must allow it
    if (refused := tool_guard.refusal(current_role(), name, last_turn["tools_called"], collective_agreement.on_file())):
        return refused

    # the user's ID always comes from the login, never from the model
    if name == "get_leave_balance":
        return get_leave_balance(me, tool_input.get("leave_type"))

    if name == "get_my_requests":
        return get_my_requests(me)

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
        # the dates fall on the weekdays the person named ("next Thursday and Friday"): the model gets the weekday wrong (named_days.py)
        if (wrong := named_days.mismatch(days_named_by_person(), start, end, date.today())):
            return {"error": wrong}
        kind = tool_input.get("leave_type") or "casual"
        if kind not in LEAVE_TYPES:
            return {"error": f"Unknown leave type '{kind}'"}
        if (found := existing_request(me, start.isoformat(), end.isoformat())):       # these dates are taken: say so first, whatever the earlier chat says
            return {"already_requested": already_requested(found),
                    "note": "These dates already have a request, so there is nothing to count or book. Say so, with its status, and quote no balance."}
        result = {"working_days": days_for(kind, start, end, holidays_of(me))}
        if not result["working_days"]:
            result["cannot_be_booked"] = no_days_reason(kind, start, end, holidays_of(me))      # a Saturday, a Sunday, a public holiday: nothing to book
        result.update(days_figures(me, kind, result["working_days"]))       # what is left after these days, or how short: the reply never works it out
        if kind == PAID:
            result["counted"] = "Monday to Saturday (jours ouvrables); Sundays and public holidays are not counted"
            if note := paid_policy_note(start, end):
                result["policy_note"] = note
        return result

    if name == "apply_leave":
        emp = get_employee(me)
        try:
            start = date.fromisoformat(tool_input["start_date"])
            end = date.fromisoformat(tool_input["end_date"])
        except ValueError:
            return {"error": "Dates must be in YYYY-MM-DD format"}
        if start < date.today():
            return {"error": f"Leave cannot start in the past. Today is {date.today()}."}
        if end < start:
            return {"error": "End date is before start date"}
        # refuse BEFORE asking: dates on other weekdays than the ones the person named never get a card (named_days.py)
        if (wrong := named_days.mismatch(days_named_by_person(), start, end, date.today())):
            return {"error": f"{wrong} Nothing was submitted and no confirmation was shown."}
        # refuse BEFORE asking: dates that already have a pending or approved request never get a card (the refusal used to come only after Confirm)
        if (found := existing_request(me, start.isoformat(), end.isoformat())):
            return {"error": f"You already have request {found['id']} ({found['status']}), {found['leave_type']} leave, "
                             f"{when_label(found['start_date'], found['end_date'])}. Nothing was submitted and no confirmation was shown.",
                    "already_requested": already_requested(found)}
        # refuse BEFORE asking: a request for no days (a Saturday, a Sunday, a public holiday) never gets a confirmation card it could not pass
        if (why := no_days_reason(tool_input["leave_type"], start, end, holidays_of(me))):
            return {"error": f"{why} Nothing was submitted and no confirmation was shown."}
        days = days_for(tool_input["leave_type"], start, end, holidays_of(me))
        # available = saved balance minus days already waiting for approval (same rule as apply_leave),
        # so the confirmation never shows a "balance after" that ignores pending requests
        from hr_queries import balances
        balance = balances(me)[tool_input["leave_type"]]["available"]
        # refuse BEFORE asking, so the user is never asked to confirm something impossible
        if balance - days < 0:
            return {"error": f"Not enough {tool_input['leave_type']} leave: needs {days} days, "
                             f"only {balance} available (pending requests included). "
                             f"Nothing was submitted and no confirmation was shown.",
                    "short_by": clean_days(days - balance)}
        details = {**tool_input,
                   "working_days": days,
                   "current_balance": balance,
                   "all_balances": balance_of(emp),
                   "balance_after_approval": clean_days(balance - days)}
        answer = confirmed(name, tool_input, details)
        if isinstance(answer, dict):
            return answer       # the card, with its figures (the reply may quote them, nothing else), or why this booking is not on it
        if not answer:
            return {"submitted": False, "working_days": days,
                    "message": f"The employee chose not to submit this {days}-day request. No request exists."}
        return apply_leave(me, **tool_input)

    if name == "cancel_leave":
        # refuse BEFORE the card, as for a booking: a request that would fail is left off it, and the reply says why
        if (problem := cancel_problem(me, tool_input["request_id"])):
            return {"cancelled": False, "message": problem,
                    "left_off_the_card": "This request is NOT on the card and nothing was cancelled for it: tell the person why, in these words."}
        answer = confirmed(name, tool_input, request_summary(tool_input["request_id"]))
        if isinstance(answer, dict):
            return answer
        if not answer:
            return {"cancelled": False, "message": "The user chose not to cancel. The request is unchanged."}
        return cancel_leave(me, tool_input["request_id"])

    if name == "get_team_member_balance":
        return get_team_member_balance(me, tool_input["employee_id"])

    if name == "get_pending_requests":
        return get_pending_requests(me)

    if name == "get_my_documents":
        return employee_files.tool_list(me)                       # always their own: the id is the login's
    if name == "read_my_document":
        return employee_files.tool_read(me, tool_input.get("document_id"))

    if name == "get_contract_status":
        return contracts.status_for_assistant(me, tool_input.get("name"))     # HR only, in roles.py and in contracts.py

    if name == "get_my_onboarding":
        return onboarding.my_checklist(me)     # always their own: the id is the link's, never the model's

    if name == "get_job_pipeline":
        return jobs.for_assistant(me, tool_input.get("job"))        # HR, or the hiring manager of the job: checked in jobs.py and roles.py

    if name == "get_needs_attention":
        return needs_you.for_assistant(me)     # the role check is in roles.py and in needs_you.py

    if name == "decide_leave":
        # refuse BEFORE the card: a decision that would fail is left off it, and the reply says why
        if (problem := decide_problem(me, tool_input["request_id"], tool_input["decision"], tool_input.get("reason", ""))):
            return {"error": problem, "left_off_the_card": "This decision is NOT on the card and nothing was saved for it: tell the person why, in these words."}
        answer = confirmed(name, tool_input, {**request_summary(tool_input["request_id"]), "decision": tool_input["decision"],
                                              "reason": tool_input.get("reason", "")})
        if isinstance(answer, dict):
            return answer
        if not answer:
            return {"saved": False, "message": "The manager chose not to save this decision. Nothing changed."}
        result = decide_leave(me, **tool_input)
        # tell the employee, but only if the decision was actually saved
        if isinstance(result, dict) and "error" not in result:
            notify_decision(tool_input["request_id"], tool_input["decision"],
                            tool_input.get("reason"), get_employee(me)["name"])
        return result

    if name == "get_holidays":
        from leave_admin import my_holidays
        return my_holidays(me, tool_input.get("year"))   # always the user's own plan

    if name == "get_balance_history":
        from leave_admin import balance_history
        return balance_history(me)     # always the user's own history

    if name == "lookup_colleague":
        # directory details only (employees.directory_entry): never balances, pay or history
        from employees import find_colleagues, my_record
        wanted = tool_input.get("name", "").strip().lower()
        if wanted in ("me", "myself", "i", "my", ""):
            return my_record(me)       # "me" is always the login, never a name the model chose
        return find_colleagues(wanted)

    if name == "search_policy":
        sections = search_policy(tool_input["question"])
        last_turn["policy_searches"].append({"query": tool_input["question"], "found": bool(sections)})
        if not sections:
            return {"found": False, "message": "No matching section in the HR policy."}
        return {"found": True, "sections": sections}
    
    if name == "search_collective_agreement":
        return collective_agreement.tool_result(tool_input["question"])

    if name == "search_labour_code":
        # no database configured (for example in CI): say so instead of crashing
        if not os.environ.get("LABOUR_DB_URL"):
            return {"error": "The labour-law database isn't available right now."}
        try:
            # imported here, not at the top, so CI (which has no labour-code database) still runs
            from labour_code.search import EmbeddingFailed, as_of as search_as_of, connect, keyword_search_for_agent, search_for_agent
            conn = connect()
        except Exception:
            # e.g. Docker is not running: the user gets a clear message, the agent keeps working
            return {"error": "The labour-law database is not reachable right now. Please try again later."}
        try:
            # multi-query finds candidates (top 20), then Haiku reranks them by "does it answer the question?"
            # recall@8 on 50 EN/FR questions: vector 45, multi-query 48, multi+rerank 50
            # search with the employee's own words AND the agent's query (the agent's rewording once hid L3141-3)
            # a failed rewrite or rerank does not land here: the search falls back to the user's words or the search order;
            # a failed embedding neither: the French keyword search alone, and the agent is told the search is limited
            with conn:
                try:
                    results, keyword_only = search_for_agent(conn, last_turn.get("question"), tool_input["question"], k=20), False
                except EmbeddingFailed as e:
                    results, keyword_only = keyword_search_for_agent(conn, last_turn.get("question"), tool_input["question"], e, k=20), True
                as_of = search_as_of(conn)           # the day the nightly sync last checked this data against Légifrance (labour_code/sync.py)
        except Exception:
            # the database answered but a step failed, e.g. the embedding service
            return {"error": "The labour-law search failed this time. Please try again later."}
        articles = {}
        for r in results:                      # keep the 5 best articles, one entry each
            # top 5: full recall on all 70 test questions (top 8 gave no extra), ~37% fewer tokens
            if r["num"] not in articles and len(articles) < 5:
                articles[r["num"]] = r["text"]
        result = {"articles": [{"article": n, "text_fr": t} for n, t in articles.items()],
                  "note": "Official French text of the Code du travail. Cite the article number."}
        if as_of:                              # none before the first sync: the old copy has no date to give
            result["as_of"] = as_of.isoformat()
            result["note"] = f"Official French text of the Code du travail (Légifrance), as of {as_of.isoformat()}. Cite the article number."
        if keyword_only:                       # keyword search alone missed paid leave in every wording measured: no confident answer from a near miss
            result["keyword_only"] = True
            result["note"] += (" The search is limited right now: these articles were found by French keywords only, so they may not be "
                               "the ones that answer. Use an article only if its text clearly answers the question. If none does, say "
                               "you can't confirm the answer right now and suggest asking HR; never answer from an article that only "
                               "shares words with the question. The app adds a line under your answer saying the search is limited: "
                               "don't write one yourself.")
        return result

    # ---------- HR tickets ----------

    if name == "create_hr_ticket":
        answer = confirmed(name, tool_input, {"send_to_HR": tool_input.get("summary", "")})
        if isinstance(answer, dict):
            return answer
        if not answer:
            return {"created": False, "message": "The user chose not to raise a ticket. Nothing was sent."}
        return create_hr_ticket(me, tool_input.get("question"), tool_input.get("summary"))

    if name == "get_my_tickets":
        return get_my_tickets(me)

    if name == "get_open_tickets":
        return get_open_tickets(me)

    if name == "reply_to_ticket":
        answer = confirmed(name, tool_input, tool_input)
        if isinstance(answer, dict):
            return answer
        if not answer:
            return {"sent": False, "message": "HR chose not to send this reply. The ticket is still open."}
        result = reply_to_ticket(me, tool_input["ticket_id"], tool_input.get("reply"))
        # tell the employee, but only if the reply was actually saved
        if isinstance(result, dict) and "error" not in result:
            notify_ticket_reply(tool_input["ticket_id"], get_employee(me)["name"])
        return result

    # ---------- payslips (read-only; the viewer is always the logged-in user) ----------

    if name == "get_payslip":
        target = tool_input.get("employee_id") or me
        return payslips.get_payslip(me, target, tool_input["month"])

    if name == "compare_payslips":
        target = tool_input.get("employee_id") or me
        return payslips.compare_payslips(me, target,
                                         tool_input["month_a"], tool_input["month_b"])

    if name == "get_pay_summary":
        target = tool_input.get("employee_id") or me
        return payslips.get_pay_summary(me, target, tool_input["year"])

    return {"error": f"Unknown tool {name}"}


def fixed_reply(reply, messages, user_text):
    """Answer without calling the model: a guard in code decided the answer. It is part of the conversation and saved like any other."""
    c = convo()
    c.last_turn.update(tools_called=[], refused=[], policy_searches=[], sources=[], question=user_text)
    messages.append({"role": "assistant", "content": reply})
    save_message(c.user_id, "assistant", reply)
    print(f"\nAgent: {reply}")
    return reply


def candidate_model(system, messages, tools):
    """One model call for the candidate assistant (candidate_assistant.py): its own prompt, its own three tools, nothing of the employee agent."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=700,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        tools=tools,
        messages=with_cache_marker(messages),
    )
    record_usage(response)
    return response


@observe(name="candidate-assistant-turn")   # one trace per message from a candidate; the user id is the application, never the name
def candidate_answer(token, text):
    who = f"application-{candidate_assistant.application_id(token)}"
    with propagate_attributes(user_id=who, session_id=who, tags=["candidate", *EXTRA_TAGS], metadata={"model": MODEL}):
        return candidate_assistant.answer(token, text, candidate_model)


def summary_model(system, user):
    """One model call for an application summary (application_summary.py): a prompt and the text, NO tools, so nothing in a CV can make it do anything.
    The CV is not sent to Langfuse: this call is left out of the trace."""
    with tracing.private_call():
        response = client.messages.create(model=MODEL, max_tokens=3000, temperature=0, system=system, messages=[{"role": "user", "content": user}])
    record_usage(response)
    return "".join(b.text for b in response.content if b.type == "text")


@observe(name="application-summary", capture_input=False, capture_output=False)   # the trace holds no CV, no quote and no name: the id of the application only
def summary_answer(actor_id, application_id):
    who = f"application-{application_id}"
    with propagate_attributes(user_id=who, session_id=who, tags=["summary", *EXTRA_TAGS], metadata={"model": MODEL}):
        return application_summary.prepare(actor_id, application_id, summary_model)


@observe(name="hr-agent-turn")   # one trace per user question, with every Claude call and tool inside
def answer_turn(user_text):
    c = convo()
    me, last_turn, pending_action, usage = c.user_id, c.last_turn, c.pending, c.usage
    role = current_role()
    with propagate_attributes(user_id=me, session_id=c.session_id,
                              tags=[role, *EXTRA_TAGS], metadata={"model": MODEL}):
        messages = c.messages
        summary = c.summary

        # guardrail in code: nobody gets a ranking, a score or a "hire this one" from the assistant, whoever asks (recruitment_guard.py).
        # The model is not called at all: the answer is fixed.
        if (fixed := recruitment_guard.refusal(role, user_text, jobs.candidate_names(me))):
            print("  [guard] asked to rank or choose between candidates: the fixed refusal")
            return fixed_reply(fixed, messages, user_text)

        # guardrail in code: leave asked for on a Saturday, a Sunday or a weekend, with no date, has nothing to count: the answer is fixed (booking_first.py).
        if (fixed := booking_first.weekend_answer(role, user_text)):
            print("  [guard] leave asked for on a weekend, no date: the fixed answer")
            return fixed_reply(fixed, messages, user_text)

        # the agent loop, limited so one question can't loop forever
        calls_this_message = 0
        turn_sources = []          # every tool result in this turn: the only allowed sources of citations
        citation_retry_used = False
        genuine_retry_used = False
        document_retry_used = False
        agreement_retry_used = False
        search_retry_used = False
        contract_retry_used = False
        booking_retry_used = False
        decision_retry_used = False
        existing_retry_used = False
        card_retry_used = False
        number_retry_used = False
        grounding_retry_used = False
        guards = guard_events.Turn()      # what each guard did in this answer, written once it is final (guard_events.py)
        last_turn["tools_called"] = []
        last_turn["refused"] = []
        last_turn["policy_searches"] = []     # what search_policy was asked and whether it found a section (policy_gaps.py)
        last_turn["sources"] = turn_sources
        last_turn["question"] = user_text   # the employee's own words, used by the labour-law search
        while calls_this_message < MAX_CALLS_PER_MESSAGE:
            calls_this_message += 1
            response = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system_prompt_for(me) + (
                    [{"type": "text", "text": f"Summary of earlier conversations: {summary}"}]
                    if summary else []
                ),
                tools=collective_agreement.available_tools(tools_for(role, TOOLS)),   # only this role's tools (and the agreement's only once HR uploaded it)
                messages=with_cache_marker(messages),   # cache the conversation so far
            )
            record_usage(response)
            # plain dicts, as they are saved and loaded again (conversations.py): the next request sends the same bytes, so the cached prefix still matches
            messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in response.content]})

            if response.stop_reason != "tool_use":
                reply = "".join(b.text for b in response.content if b.type == "text")

                # guardrail in code: asked whether a document is genuine, a new hire always hears "I can't tell"
                if role == "new_hire" and asks_if_genuine(user_text) and not says_cannot_tell(reply):
                    if not genuine_retry_used:
                        genuine_retry_used = True
                        guards.retried("genuine")
                        print("  [guard] asked about a document being genuine, no \"can't tell\": asking again")
                        messages.append({"role": "user", "content": GENUINE_RETRY})
                        continue
                    print("  [guard] still no \"can't tell\": replying with the fixed answer")
                    reply = GENUINE_ANSWER
                    guards.ended("genuine", "replaced")

                # guardrail in code: a question about the company's rules is searched, not asked back or handed to HR (search_first.py). One retry.
                if not search_retry_used and (fix := search_first.nudge(role, user_text, last_turn["tools_called"])):
                    search_retry_used = True
                    guards.retried("search", recheck=lambda: search_first.nudge(role, user_text, last_turn["tools_called"]))
                    print("  [guard] a rules question was answered without searching: asking again")
                    messages.append({"role": "user", "content": fix})
                    continue

                # guardrail in code: a question about where a new hire's contract stands, or a request to send one, is answered from get_contract_status (contract_first.py). One retry.
                if not contract_retry_used and (fix := contract_first.nudge(role, user_text, last_turn["tools_called"])):
                    contract_retry_used = True
                    guards.retried("contract", recheck=lambda: contract_first.nudge(role, user_text, last_turn["tools_called"]))
                    print("  [guard] a contract question was answered without checking the contract: asking again")
                    messages.append({"role": "user", "content": fix})
                    continue

                # guardrail in code: asked to book leave, the assistant books it (the confirmation card is the confirmation), and it never offers to
                # book dates that use no days (booking_first.py). One retry.
                if not booking_retry_used and (fix := booking_first.nudge(role, user_text, reply, last_turn["tools_called"], turn_sources)):
                    booking_retry_used = True
                    guards.retried("booking", recheck=lambda: booking_first.nudge(role, user_text, reply, last_turn["tools_called"], turn_sources))
                    print("  [guard] asked before booking, or offered to book dates that use no days: asking again")
                    messages.append({"role": "user", "content": fix})
                    continue

                # guardrail in code: what a person is told about dates they already asked for comes from the tools, not the old chat: the dates are looked up
                # before asking which kind of leave, "you already have a request" needs a tool result (and its real status), and a card is claimed only when
                # one was made (existing_first.py). One retry.
                if not existing_retry_used and (fix := existing_first.nudge(role, user_text, reply, last_turn["tools_called"], turn_sources, bool(pending_action))):
                    existing_retry_used = True
                    guards.retried("existing", recheck=lambda: existing_first.nudge(role, user_text, reply, last_turn["tools_called"], turn_sources, bool(pending_action)))
                    print("  [guard] the reply about existing requests or the card does not match the tools: asking again")
                    messages.append({"role": "user", "content": fix})
                    continue

                # guardrail in code: a manager is told which requests are pending NOW, from this turn's get_pending_requests, never from the chat: asked to
                # approve or reject one, it goes on the card (never "already approved"); asked what is pending, every request the tool returned is listed
                # (approval_first.py). One retry.
                if not decision_retry_used and (fix := approval_first.nudge(role, user_text, reply, last_turn["tools_called"], turn_sources)):
                    decision_retry_used = True
                    guards.retried("approval", recheck=lambda: approval_first.nudge(role, user_text, reply, last_turn["tools_called"], turn_sources))
                    print("  [guard] the reply does not match the requests pending now (asked to decide one, or left one out of the list): asking again")
                    messages.append({"role": "user", "content": fix})
                    continue

                # guardrail in code: the app shows ONE card, however many items it lists: the reply never talks about several cards (confirm_batch.py). One retry.
                if not card_retry_used and (fix := confirm_batch.nudge(pending_action, reply)):
                    card_retry_used = True
                    guards.retried("card", recheck=lambda: confirm_batch.nudge(pending_action, reply))
                    print("  [guard] the reply talks about several cards and the app shows one: asking again")
                    messages.append({"role": "user", "content": fix})
                    continue

                # guardrail in code: an answer about the person's own document says when the document is silent, and only then
                # gives the policy or the law, labelled (see document_answers.py). One retry.
                if not document_retry_used and (fix := document_answers.nudge(last_turn["tools_called"], reply)):
                    document_retry_used = True
                    guards.retried("document", recheck=lambda: document_answers.nudge(last_turn["tools_called"], reply))
                    print("  [guard] the answer about the document needs another part: asking again")
                    messages.append({"role": "user", "content": fix})
                    continue

                # guardrail in code: the collective agreement was searched and did not answer: the reply says so (collective_agreement.py). One retry.
                if not agreement_retry_used and (fix := collective_agreement.nudge(last_turn["tools_called"], reply)):
                    agreement_retry_used = True
                    guards.retried("agreement", recheck=lambda: collective_agreement.nudge(last_turn["tools_called"], reply))
                    print("  [guard] the answer does not say what the collective agreement says: asking again")
                    messages.append({"role": "user", "content": fix})
                    continue

                # guardrail in code: never show an article the agent didn't retrieve in this turn
                bad = ungrounded_citations(reply, turn_sources)
                if bad and not citation_retry_used:
                    citation_retry_used = True
                    guards.retried("citation", bad)
                    print(f"  [guard] ungrounded citation {bad}: asking the agent to search first")
                    messages.append({"role": "user", "content": RETRY_MESSAGE.format(articles=", ".join(bad))})
                    continue
                if bad:
                    print(f"  [guard] still ungrounded {bad}: replying safely instead")
                    reply = SAFE_REPLY
                    guards.ended("citation", "safe_reply", bad)

                # guardrail in code: never show a number of days or an amount that no tool returned in this turn (number_guard.py).
                # One retry with the tools' own figures, then the sentence is replaced by them.
                action, shown = number_guard.review(reply, turn_sources, user_text, number_retry_used)
                if action != "ok":
                    figures = number_guard.ungrounded(reply, turn_sources, user_text)
                    print(f"  [guard] {figures} was not returned by any tool in this turn")
                if action == "retry":
                    number_retry_used = True
                    guards.retried("number", {"figures": len(figures)})
                    print("  [guard] asking the agent to use the tools' figures")
                    messages.append({"role": "user", "content": shown})
                    continue
                if action == "replaced":
                    print("  [guard] still not from a tool: replacing it with the tools' figures")
                    reply = shown
                    guards.ended("number", "replaced", {"figures": len(figures)})

                # guardrail in code: a policy answer gives no reason, condition, deadline or consequence that this turn's tool results do not contain;
                # a second model reads it (policy_grounding.py). One retry naming those parts, then their sentences are taken out or the policy is quoted.
                if policy_grounding.applies(last_turn["tools_called"]):
                    started, spent = time.time(), usage["check_cost"]
                    action, shown, detail = policy_grounding.review(user_text, reply, turn_sources, grounding_model, grounding_retry_used)
                    found = f": {[f.get('claim') for f in detail]}" if action in ("retry", "replaced") else f" ({detail})" if action == "unchecked" else ""
                    print(f"  [guard] grounding check {action} in {time.time() - started:.1f}s, ${usage['check_cost'] - spent:.5f}{found}")
                    if action == "retry":
                        grounding_retry_used = True
                        guards.retried("grounding", {"claims": len(detail)})
                        messages.append({"role": "user", "content": shown})
                        continue
                    if action == "replaced":
                        reply = shown
                        guards.ended("grounding", "replaced", {"claims": len(detail)})
                if document_retry_used or agreement_retry_used or search_retry_used or contract_retry_used or booking_retry_used or decision_retry_used or existing_retry_used or card_retry_used or number_retry_used or grounding_retry_used:       # a retry note was sent: the reply must not talk about it
                    reply = document_answers.strip_correction(reply)

                # every guard that acted on this answer: one row each, and a tag on the Langfuse trace (guard_events.py)
                if (found := guards.write(me, role, tracing.current_trace_id())):
                    tracing.tag_trace(guard_events.tags(found))
                    print(f"  [guard events] {found}")

                # the labour search was keyword-only in this turn: a line says the answer may be incomplete, added by code like the date below
                if (limited_line := law_status.limited_line(turn_sources, user_text)):
                    reply = f"{reply}\n\n{limited_line}"
                # a law answer says how recent the law it quotes is: added by code after every guard, the model can forget a line (law_status.py)
                if (as_of_line := law_status.line_for(reply, turn_sources, user_text)):
                    reply = f"{reply}\n\n{as_of_line}"

                # the policy does not answer it: keep the question, without a name, for HR (policy_gaps.py)
                if policy_gaps.record_from_turn(last_turn["policy_searches"], reply):
                    print("  [policy gap] the question is kept for HR")
                print(f"\nAgent: {reply}")
                save_message(me, "assistant", reply)
                print(f"\n  [usage] {usage['calls']} calls, {usage['input_tokens']} in, "
                      f"{usage['cache_write']} cache-written, {usage['cache_read']} cached, {usage['output_tokens']} out, "
                      f"${cost_so_far():.4f} so far")
                return reply

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  [tool call] {block.name} {block.input}")
                    if tool_guard.refusal(role, block.name, last_turn["tools_called"], collective_agreement.on_file()):
                        last_turn["refused"].append(block.name)         # refused by the code: it did nothing, so it is not "called"
                    else:
                        last_turn["tools_called"].append(block.name)
                    result = run_tool(block.name, block.input)          # run_tool applies the same refusal
                    turn_sources.append(json.dumps(result, ensure_ascii=False))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })
            messages.append({"role": "user", "content": tool_results})

        print("\nAgent: Sorry, that took too many steps. Please try rephrasing.")
        return "(stopped: too many steps)"


def start_session(user_id, role=None):
    """Log a user in: load their history (+ summary) and start a fresh budget. Used by chat() and the evals.
    role is "new_hire" for a welcome-link session; otherwise the role in the database applies."""
    c = convo()
    c.user_id, c.role, c.session_id = user_id, role, new_session_id(user_id)
    c.summary, c.messages = build_context(user_id, client, MODEL)
    c.stored, c.restarted = 0, True
    reset_usage()


def handle_message(user_text):
    """One user message, exactly as production runs it. chat(), the web server and the evals all call this,
    so the tests always check the real agent, never a copy."""
    c = convo()
    # budget check BEFORE calling the model, so we never pay for a refused request
    if cost_so_far() >= MAX_COST_PER_CONVERSATION:
        reply = ("This conversation has reached its usage limit. "
                 "Please start a new one with 'login' or contact HR.")
        print(f"\nAgent: {reply}")
        return reply

    # keep the chat short mid-session: rebuild from the database when it grows
    if len(c.messages) > 20:
        c.summary, c.messages = build_context(c.user_id, client, MODEL)
        c.stored, c.restarted = 0, True

    c.messages.append({"role": "user", "content": user_text})
    save_message(c.user_id, "user", user_text)
    return answer_turn(user_text)


def chat():
    c = convo()
    start_session(c.user_id)   # pick up where this user left off
    print("HR assistant ready. Type 'login E001' to switch user, 'quit' to exit.")
    print(f"Logged in as {get_employee(c.user_id)['name']} ({c.user_id})")
    print(f"(Tracing {'ON' if tracing.ENABLED else 'OFF'})")
    show_unread(c.user_id)
    if c.messages:
        print(f"(Loaded {len(c.messages)} earlier messages)")

    while True:
        user_text = input("\nYou: ")
        if user_text.lower() == "quit":
            break

        # switching user is handled by code, not by the model
        if user_text.lower().startswith("login "):
            new_id = user_text.split()[1].upper()
            new_user = get_employee(new_id)
            if new_user:
                start_session(new_id)                # new conversation in Langfuse, new budget
                print(f"Logged in as {new_user['name']} ({new_id})")
                show_unread(new_id)
                if c.messages:
                    print(f"(Loaded {len(c.messages)} earlier messages)")
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
    guard_events.init_events()       # one row each time a guard acts on a reply
    chat()