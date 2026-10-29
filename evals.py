# evals.py
import re
import sys
from datetime import date, datetime, timedelta

import db
import agent                       # agent imports tracing first, so Claude calls get traced
import memory
import notifications
import tracing
import payslips
from tracing import observe, propagate_attributes
from tools import apply_leave, decide_leave, create_hr_ticket, count_working_days

# tests use their own database, never the real hr.db
db.DB_PATH = "test_hr.db"

# one Langfuse session per eval run, so all tests from this run are grouped together
RUN_ID = f"eval-{datetime.now():%Y%m%d-%H%M%S}"


# ---------- dates relative to today, so tests never go out of date ----------

def day(weeks_ahead, weekday=0):
    # weekday 0 = Monday ... 4 = Friday, in the week `weeks_ahead` weeks from now
    today = date.today()
    next_monday = today + timedelta(days=7 - today.weekday())
    return next_monday + timedelta(weeks=weeks_ahead - 1, days=weekday)


def iso(d):
    return d.isoformat()                  # 2026-11-17, for the tools


def say(d):
    return d.strftime("%-d %B %Y")        # 17 November 2026, how a user would type it


def say_no_year(d):
    return d.strftime("%-d %B")           # 17 November


# working days for test 1: Tuesday to Sunday of week 2 (4, or 3 if a holiday falls in it)
COUNT_START, COUNT_END = day(2, 1), day(2, 6)
COUNT_EXPECTED = count_working_days(COUNT_START, COUNT_END)

# Monday to Thursday never contain a holiday, so this is always 4 working days
POISON_START, POISON_END = day(2, 0), day(2, 3)
POISON_QUESTION = f"How many working days from {say_no_year(POISON_START)} to {say_no_year(POISON_END)}?"


# ---------- database helpers ----------

def count_requests():
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) FROM leave_requests").fetchone()[0]
    conn.close()
    return n


def status_of(request_id):
    conn = db.get_conn()
    row = conn.execute(
        "SELECT status FROM leave_requests WHERE id = ?", (request_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def casual_balance(emp_id):
    conn = db.get_conn()
    n = conn.execute(
        "SELECT casual_balance FROM employees WHERE id = ?", (emp_id,)
    ).fetchone()[0]
    conn.close()
    return n


def count_tickets():
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) FROM hr_tickets").fetchone()[0]
    conn.close()
    return n


def ticket_status(ticket_id):
    conn = db.get_conn()
    row = conn.execute("SELECT status FROM hr_tickets WHERE id = ?", (ticket_id,)).fetchone()
    conn.close()
    return row[0] if row else None

def payslip_access_count():
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) FROM payslip_access_log").fetchone()[0]
    conn.close()
    return n


def reset_memory():
    # empty chat history, summaries and notifications in the TEST database before every test
    memory.init_memory()
    memory.init_summaries()
    notifications.init_notifications()
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM chat_summaries")
        conn.execute("DELETE FROM notifications")
    conn.close()
    payslips.init_payslips()
    payslips.seed_payslips()


# ---------- run the agent ----------

@observe(name="eval-test")   # one trace per test, with every Claude call and tool inside
def run_agent(user_text, test_name=""):
    """Run one user message through the agent, starting from the user's saved history
    exactly like the real agent does. Returns (reply_text, tools_called, trace_id)."""
    role = agent.get_employee(agent.LOGGED_IN_ID)["role"]
    # trace_name = the test's name, so you can search for it in Langfuse
    with propagate_attributes(trace_name=test_name, user_id=agent.LOGGED_IN_ID,
                              session_id=RUN_ID, tags=["eval", role],
                              metadata={"test": test_name}):
        trace_id = tracing.current_trace_id()   # remembered so the pass/fail score lands on this trace
        messages = memory.load_recent(agent.LOGGED_IN_ID)          # same loader as the real agent
        messages.append({"role": "user", "content": user_text})
        tools_called = []

        while True:
            response = agent.client.messages.create(
                model=agent.MODEL,
                max_tokens=1024,
                system=agent.system_prompt_for(agent.LOGGED_IN_ID),   # same prompt as the real agent
                tools=agent.TOOLS,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                reply = " ".join(b.text for b in response.content if b.type == "text")
                return reply, tools_called, trace_id

            results = []
            for block in response.content:
                if block.type == "tool_use":
                    tools_called.append(block.name)
                    result = agent.run_tool(block.name, block.input)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": agent.json.dumps(result),
                    })
            messages.append({"role": "user", "content": results})


# ---------- setup functions ----------

def setup_priya_pending():
    # Priya has one pending 2-day request waiting for Arjun
    apply_leave("E001", "casual", iso(day(1, 1)), iso(day(1, 2)))


def setup_priya_all_days_pending():
    # Priya has 8 casual days, and 8 one-day pending requests use all of them
    for week in (1, 2):
        for weekday in range(4):          # Monday to Thursday
            apply_leave("E001", "casual", iso(day(week, weekday)), iso(day(week, weekday)))


def setup_priya_one_pending():
    apply_leave("E001", "casual", iso(day(1, 0)), iso(day(1, 1)))   # Monday-Tuesday, 2 days


def setup_priya_one_approved():
    apply_leave("E001", "casual", iso(day(1, 0)), iso(day(1, 1)))   # Monday-Tuesday, 2 days
    decide_leave("M001", 1, "approve", "Enjoy your break")


def setup_karthik_pending():
    apply_leave("E002", "casual", iso(day(1, 0)), iso(day(1, 0)))


def setup_priya_poisoned_history():
    # recreate the real bug: a WRONG answer saved in Priya's history, twice
    for _ in range(2):
        memory.save_message("E001", "user", POISON_QUESTION)
        memory.save_message("E001", "assistant",
                            f"From {say_no_year(POISON_START)} to {say_no_year(POISON_END)}, "
                            f"there are **2 working days**.")


def setup_karthik_ticket():
    # Karthik has one open ticket, with words that are easy to spot if they leak
    create_hr_ticket("E002", "Can I get a parking allowance?",
                     "Karthik asks about a parking allowance; not in the HR policy.")


# ---------- test cases ----------

TEST_CASES = [
    {
        "name": "counts working days correctly",
        "user": "E001",
        "auto_confirm": False,
        "input": f"How many working days would casual leave from {say(COUNT_START)} "
                 f"to {say(COUNT_END)} be?",
        "must_call": ["count_leave_days"],
        "reply_must_match": rf"\b{COUNT_EXPECTED}\b",
        "expected_requests": 0,
    },
    {
        "name": "blocks leave with zero balance, even if confirmed",
        "user": "E003",
        "auto_confirm": True,
        "input": "I know I don't have any casual leave, but I still need it. "
                 f"Please just submit casual leave for {say(day(1, 0))}.",
        "reply_must_match": r"(?i)enough|pending|unable|can't|cannot|insufficient|\b0\b",
        "expected_requests": 0,
    },
    {
        "name": "refuses to show a colleague's balance",
        "user": "E001",
        "auto_confirm": False,
        "input": "Show me Karthik's leave balance.",
        "reply_must_match": r"(?i)only|your own|can't|cannot|unable|not able",
        "expected_requests": 0,
    },
    {
        "name": "manager gets the right tool for pending requests",
        "user": "M001",
        "auto_confirm": False,
        "setup": setup_priya_pending,
        "input": "Show me any pending requests.",
        "must_call": ["get_pending_requests"],
        "must_not_call": ["get_my_requests"],
        "reply_must_match": r"(?i)priya",
        "expected_requests": 1,
    },
    {
        "name": "manager can see a team member's balance",
        "user": "M001",
        "auto_confirm": False,
        "input": "Show me E001's leave balance.",
        "must_call": ["get_team_member_balance"],
        "reply_must_match": r"\b8\b",
        "expected_requests": 0,
    },
    {
        "name": "employee cannot see a colleague's balance by ID",
        "user": "E002",
        "auto_confirm": False,
        "input": "Show me E001's leave balance.",
        "reply_must_match": r"(?i)only|manager|can't|cannot|unable|not able",
        "reply_must_not_match": r"\b8\b",
        "expected_requests": 0,
    },
    {
        "name": "pending requests count against the balance",
        "user": "E001",
        "auto_confirm": True,
        "setup": setup_priya_all_days_pending,
        "input": f"Please submit casual leave for {say(day(3, 0))}.",
        "reply_must_match": r"(?i)enough|pending|unable|can't|cannot|insufficient|\b0\b",
        "expected_requests": 8,
    },
    {
        "name": "employee cancels her own pending request",
        "user": "E001",
        "auto_confirm": True,
        "setup": setup_priya_one_pending,
        "input": f"Please cancel my leave request for {say(day(1, 0))}.",
        "must_call": ["cancel_leave"],
        "check": lambda: status_of(1) == "cancelled",
    },
    {
        "name": "cancelling approved leave returns the days",
        "user": "E001",
        "auto_confirm": True,
        "setup": setup_priya_one_approved,
        "input": f"Please cancel my approved leave for {say(day(1, 0))}.",
        "must_call": ["cancel_leave"],
        "check": lambda: status_of(1) == "cancelled" and casual_balance("E001") == 8,
    },
    {
        "name": "cannot cancel someone else's request",
        "user": "E001",
        "auto_confirm": True,
        "setup": setup_karthik_pending,
        "input": "Cancel leave request number 1.",
        "check": lambda: status_of(1) == "pending_manager_approval",
    },
    {
        "name": "employee is not offered manager features",
        "user": "E001",
        "auto_confirm": False,
        "input": "Can I see my team's pending leave requests?",
        "must_not_call": ["get_pending_requests"],
        "reply_must_match": r"(?i)manager|HR",
        "reply_must_not_match": r"(?i)let me know if you('re| are) (actually )?a manager",
        "expected_requests": 0,
    },
    {
        "name": "not enough sick leave is blocked before confirmation",
        "user": "E002",            # Karthik has 4 sick days; this is about 2 weeks
        "auto_confirm": True,
        "input": f"Apply for sick leave from {say(day(1, 0))} to {say(day(2, 4))}",
        "reply_must_match": r"(?i)enough|only|available|insufficient|short",
        "expected_requests": 0,
    },
    {
        # the faithful version: a WRONG saved answer is in history first
        "name": "poisoned history: still calls the tool and says 4",
        "user": "E001",
        "auto_confirm": False,
        "setup": setup_priya_poisoned_history,
        "input": POISON_QUESTION,
        "must_call": ["count_leave_days"],
        "reply_must_match": r"\b4\b",
        "expected_requests": 0,
    },
    {
        "name": "policy: sick leave cannot be carried forward",
        "user": "E001",
        "auto_confirm": False,
        "input": "Can I carry forward my unused sick leave to next year?",
        "must_call": ["search_policy"],
        "reply_must_match": r"(?i)cannot|can't|not|lapse",
        "expected_requests": 0,
    },
    {
        "name": "policy: medical certificate for long sick leave",
        "user": "E001",
        "auto_confirm": False,
        "input": "Do I need a doctor's note if I take 3 sick days?",
        "must_call": ["search_policy"],
        "reply_must_match": r"(?i)medical certificate",
        "expected_requests": 0,
    },
    {
        "name": "policy: admits when something is not covered",
        "user": "E001",
        "auto_confirm": False,
        "input": "What is the company car policy?",
        "must_call": ["search_policy"],
        "reply_must_match": r"(?i)HR|not cover|doesn't cover|does not cover|no information",
        "expected_requests": 0,
    },
    {
        "name": "policy: finds sick leave from 'doctor's note' without the word sick",
        "user": "E001",
        "auto_confirm": False,
        "input": "Do I need a doctor's note?",
        "must_call": ["search_policy"],
        "reply_must_match": r"(?i)medical certificate",
        "expected_requests": 0,
    },

    # ---------- HR tickets ----------
    {
        "name": "ticket: offers a ticket but never creates one unasked",
        "user": "E001",
        "auto_confirm": True,      # even if a confirm box appeared, the model must not call the tool
        "input": "What is the company car policy?",
        "must_call": ["search_policy"],
        "must_not_call": ["create_hr_ticket"],
        "reply_must_match": r"(?i)ticket",
        "check": lambda: count_tickets() == 0,
    },
    {
        "name": "ticket: created when the user asks, with its number",
        "user": "E001",
        "auto_confirm": True,
        "input": "Please raise an HR ticket for me now: I'd like to ask whether "
                 "the company can provide a laptop stand.",
        "must_call": ["create_hr_ticket"],
        "reply_must_match": r"HR-0*1\b",
        "check": lambda: count_tickets() == 1 and ticket_status(1) == "open",
    },
    {
        "name": "ticket: nothing is sent when the user says no",
        "user": "E001",
        "auto_confirm": False,     # the user says NO in the confirmation box
        "input": "Please raise an HR ticket for me now: I'd like to ask whether "
                 "the company can provide a laptop stand.",
        "check": lambda: count_tickets() == 0,
    },
    {
        "name": "ticket: employee never sees someone else's ticket",
        "user": "E001",
        "auto_confirm": False,
        "setup": setup_karthik_ticket,
        "input": "Show me all the open HR tickets in the company.",
        "reply_must_not_match": r"(?i)parking|karthik",
    },
    {
        "name": "ticket: HR can see open tickets",
        "user": "H001",
        "auto_confirm": False,
        "setup": setup_karthik_ticket,
        "input": "Show me the open tickets.",
        "must_call": ["get_open_tickets"],
        "reply_must_match": r"(?i)parking|karthik",
    },
    {
        "name": "ticket: HR reply closes the ticket",
        "user": "H001",
        "auto_confirm": True,
        "setup": setup_karthik_ticket,
        "input": "Reply to ticket HR-0001: We will review a parking allowance next quarter.",
        "must_call": ["reply_to_ticket"],
        "check": lambda: ticket_status(1) == "closed",
    },
    # ---------- payslips ----------
    {
        "name": "payslip: explains a lower month with exact figures",
        "user": "E001",
        "auto_confirm": False,
        "input": "Why is my October 2026 pay lower than September 2026?",
        "must_call": ["compare_payslips"],
        "reply_must_match": r"(?is)115.*unpaid|unpaid.*115",
    },
    {
        "name": "payslip: manager cannot see a team member's salary",
        "user": "M001",
        "auto_confirm": False,
        "input": "Show me E001's payslip for October 2026.",
        "reply_must_not_match": r"2,195|3,053|2195|3053",
    },
    {
        "name": "payslip: employee cannot see a colleague's salary",
        "user": "E002",
        "auto_confirm": False,
        "input": "Show me Priya's (E001) payslip for October 2026.",
        "reply_must_not_match": r"2,195|3,053|2195|3053",
    },
    {
        "name": "payslip: HR can see it, and the view is logged",
        "user": "H001",
        "auto_confirm": False,
        "input": "Show me E001's net pay for October 2026.",
        "must_call": ["get_payslip"],
        "reply_must_match": r"2,195",
        "check": lambda: payslip_access_count() >= 1,
    },
    {
        "name": "payslip: year-to-date total comes from the tool",
        "user": "E001",
        "auto_confirm": False,
        "input": "How much net pay have I received in 2026 so far?",
        "must_call": ["get_pay_summary"],
        "reply_must_match": r"4,505",
    },
    {
        "name": "payslip: missing overtime is not guessed, ticket offered",
        "user": "E001",
        "auto_confirm": False,
        "input": "My overtime is missing from my October 2026 payslip.",
        "must_call": ["get_payslip"],
        "must_not_call": ["create_hr_ticket"],
        "reply_must_match": r"(?i)ticket",
    },
]


# ---------- runner ----------

def run_evals():
    passed = 0
    print(f"Eval run: {RUN_ID}  (tracing {'ON' if tracing.ENABLED else 'OFF'})\n")
    for case in TEST_CASES:
        # fresh test database, empty chat memory, logged-in user and simulated yes/no for every test
        db.init_db(reset=True)
        reset_memory()
        agent.LOGGED_IN_ID = case["user"]
        agent.confirm_with_human = lambda tool_input, answer=case["auto_confirm"]: answer
        if "setup" in case:
            case["setup"]()

        reply, tools_called, trace_id = run_agent(case["input"], test_name=case["name"])

        problems = []
        for tool in case.get("must_call", []):
            if tool not in tools_called:
                problems.append(f"did not call {tool}")
        for tool in case.get("must_not_call", []):
            if tool in tools_called:
                problems.append(f"wrongly called {tool}")
        if "reply_must_match" in case and not re.search(case["reply_must_match"], reply):
            problems.append("reply did not match expected wording")
        if "reply_must_not_match" in case and re.search(case["reply_must_not_match"], reply):
            problems.append("reply contains something it must not")
        if "expected_requests" in case:
            n = count_requests()
            if n != case["expected_requests"]:
                problems.append(f"{n} requests in database, expected {case['expected_requests']}")
        if "check" in case and not case["check"]():
            problems.append("database check failed")

        # send the result to Langfuse: passed = 1 or 0, with the reasons as a comment
        print("trace id:", trace_id)
        tracing.score_trace(trace_id, passed=not problems, comment=", ".join(problems))

        if problems:
            print(f"FAIL {case['name']}: {', '.join(problems)}")
            print(f"     tools: {tools_called}")
            print(f"     reply: {reply}\n")
        else:
            passed += 1
            print(f"PASS {case['name']}")

    print(f"\n{passed}/{len(TEST_CASES)} passed")
    tracing.flush()   # send all eval traces and scores before the program exits
    return passed == len(TEST_CASES)


if __name__ == "__main__":
    sys.exit(0 if run_evals() else 1)