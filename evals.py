# evals.py
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta

import db
import agent                       # agent imports tracing first, so Claude calls get traced
import memory
import notifications
import tracing
import payslips
import employees
import hr_queries
import leave_admin
from tracing import observe, propagate_attributes
from tools import apply_leave, decide_leave, create_hr_ticket, count_working_days, count_ouvrable_days

# tests use their own database, never the real hr.db
db.DB_PATH = "test_hr.db"

# one Langfuse session per eval run, so all tests from this run are grouped together
# (with --workers, the parent sets EVAL_RUN_ID so every worker uses the same session)
RUN_ID = os.environ.get("EVAL_RUN_ID") or f"eval-{datetime.now():%Y%m%d-%H%M%S}"


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

# paid leave counts Monday to Saturday: a Monday-to-Saturday week is 6 days (fewer if a holiday falls in it)
PAID_START, PAID_END = day(3, 0), day(3, 5)
PAID_EXPECTED = count_ouvrable_days(PAID_START, PAID_END)

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


def paid_balance(emp_id):
    conn = db.get_conn()
    n = conn.execute(
        "SELECT paid_balance FROM employees WHERE id = ?", (emp_id,)
    ).fetchone()[0]
    conn.close()
    return n


def paid_request_days():
    # days stored on the paid leave requests that exist, as a list
    conn = db.get_conn()
    rows = conn.execute("SELECT days FROM leave_requests WHERE leave_type = 'paid' ORDER BY id").fetchall()
    conn.close()
    return [r[0] for r in rows]


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


def labour_db_available():
    # the labour-law tests need the Postgres container; CI has none, so they are skipped there
    if not os.environ.get("LABOUR_DB_URL"):
        return False
    try:
        from labour_code.search import connect
        with connect() as conn:
            conn.execute("SELECT 1 FROM chunks LIMIT 1")
        return True
    except Exception:
        return False


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
    hr_queries.init_profiles()     # job details for lookup_colleague
    employees.init_records()
    leave_admin.init_leave_admin()
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM leave_adjustments")
        conn.execute("DELETE FROM leave_runs")
        conn.execute("UPDATE profiles SET holiday_plan_id = NULL")
    conn.close()


# ---------- run the agent ----------

from citations import ungrounded_citations
from judge import judge   # LLM-as-judge: every claim supported by this turn's tool results (tested in judge_tests.py)


@observe(name="eval-test")   # one trace per test, with every Claude call and tool inside
def run_agent(user_text, test_name=""):
    """Run one user message through the REAL agent (agent.handle_message), starting from the user's
    saved history, exactly as production does. Returns (reply_text, tools_called, trace_id)."""
    role = agent.get_employee(agent.LOGGED_IN_ID)["role"]
    # trace_name = the test's name, so you can search for it in Langfuse
    with propagate_attributes(trace_name=test_name, user_id=agent.LOGGED_IN_ID,
                              session_id=RUN_ID, tags=["eval", role],
                              metadata={"test": test_name}):
        trace_id = tracing.current_trace_id()   # remembered so the pass/fail score lands on this trace
        agent.start_session(agent.LOGGED_IN_ID)  # load history + summary, fresh budget: like a real login
        agent.SESSION_ID = RUN_ID                # keep all tests of this run in one Langfuse session
        agent.EXTRA_TAGS = ["eval"]
        reply = agent.handle_message(user_text)
        return reply, list(agent.last_turn["tools_called"]), trace_id


# ---------- setup functions ----------

def setup_priya_pending():
    # Emma has one pending 2-day request waiting for Lucas
    apply_leave("E001", "casual", iso(day(1, 1)), iso(day(1, 2)))


def setup_priya_all_days_pending():
    # Emma has 8 casual days, and 8 one-day pending requests use all of them
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
    # recreate the real bug: a WRONG answer saved in Emma's history, twice
    for _ in range(2):
        memory.save_message("E001", "user", POISON_QUESTION)
        memory.save_message("E001", "assistant",
                            f"From {say_no_year(POISON_START)} to {say_no_year(POISON_END)}, "
                            f"there are **2 working days**.")


def setup_priya_labour_history():
    # the REAL history from the live bug (1 Nov), copied from hr.db up to the failing question:
    # several labour answers with article numbers, and the same question asked twice, so the
    # model felt it "already knew" and answered the follow-up from memory with an invented article
    history = [
        ("user", "Can my employer make me work on 1 May"),
        ("assistant", "According to French law, **Article L3133-4** states that 1 May is \"jour férié et chômé\" "
                      "— a public holiday on which work is not permitted. However, **Article L3133-6** says that "
                      "in businesses that cannot interrupt work due to their nature, employees can be made to work."),
        ("user", "Combien de jours de congés payés me reste-t-il"),
        ("assistant", "Il vous reste **7 jours de congés ordinaires** et **5 jours de congés maladie**."),
        ("user", "How many days of paid leave do I earn by law?"),
        ("assistant", "According to French law, **Article L3141-3** states: \"Le salarié a droit à un congé de deux "
                      "jours et demi ouvrables par mois de travail effectif\" — you are entitled to a minimum of "
                      "**2.5 working days per month of actual work**, up to a maximum of **30 working days per year**."),
        ("user", "And during which months do I take them?"),
        ("assistant", "Your company policy says paid leave must normally be taken between **1 May and 31 October**. "
                      "You request it through your manager."),
        ("user", "How many days of paid leave do I earn by law?"),
        ("assistant", "According to French law, **Article L3141-3** states that you are entitled to a minimum of "
                      "**2.5 working days per month of actual work**, which amounts to **30 working days per year**. "
                      "This is the legal minimum; your employer may provide more under their policy."),
    ]
    for role, text in history:
        memory.save_message("E001", role, text)


def setup_priya_asked_paid_leave():
    # the first question of the real conversation (1 Nov) where the follow-up mixed in the notice period
    memory.save_message("E001", "user", "How many days of paid leave do I earn by law?")
    memory.save_message("E001", "assistant",
                        "By French law (Article L3141-3), you earn 2.5 working days per month of paid leave, "
                        "up to 30 working days per year. This is the legal minimum; your company may offer more.")


def setup_karthik_ticket():
    # Hugo has one open ticket, with words that are easy to spot if they leak
    create_hr_ticket("E002", "Can I get a parking allowance?",
                     "Hugo asks about a parking allowance; not in the HR policy.")


# ---------- test cases ----------

def setup_balance_adjusted():
    # HR gave Emma 2 extra casual days for working the Saturday release
    leave_admin.adjust_balance("H001", "E001", "casual", 2, "Worked on the Saturday release")


def setup_emma_pending_paid():
    # Emma (12.5 paid days) has a pending paid request for a Monday-to-Saturday week
    apply_leave("E001", "paid", iso(day(2, 0)), iso(day(2, 5)))


def setup_paid_leave_earned():
    # the monthly job credited last month's 2.5 paid days
    year, month = leave_admin.last_finished_month()
    leave_admin.run_month_accrual(year, month)


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
        "input": "Show me Hugo's leave balance.",
        "reply_must_match": r"(?i)only|your own|can't|cannot|unable|not able",
        "expected_requests": 0,
    },
    {
        "name": "looks up a colleague's manager",
        "user": "E002",
        "auto_confirm": False,
        "input": "Who is Clara's manager?",
        "must_call": ["lookup_colleague"],
        "reply_must_match": r"(?i)lucas",
        "expected_requests": 0,
    },
    {
        "name": "explains why the balance went up",
        "user": "E001",
        "auto_confirm": False,
        "setup": setup_balance_adjusted,
        "input": "Why did my casual leave balance go up?",
        "must_call": ["get_balance_history"],
        "reply_must_match": r"(?i)(?=.*saturday)(?=.*\b2\b)",
        "expected_requests": 0,
    },
    {
        "name": "lists public and floating holidays from the user's plan",
        "user": "E001",
        "auto_confirm": False,
        "input": "Which public holidays do I have in December 2026, and are there any floating holidays I can take?",
        "must_call": ["get_holidays"],
        "reply_must_match": r"(?is)(?=.*christmas day)(?=.*christmas eve)",
        "expected_requests": 0,
    },
    {
        "name": "tells an employee who their manager is",
        "user": "E001",
        "auto_confirm": False,
        "input": "Who is my manager?",
        "must_call": ["lookup_colleague"],
        "reply_must_match": r"(?i)lucas",
        "reply_must_not_match": r"(?i)ask (your )?hr|don't have a tool",
        "expected_requests": 0,
    },
    {
        "name": "tells a manager who is in their team",
        "user": "M001",
        "auto_confirm": False,
        "input": "Who is in my team?",
        "reply_must_match": r"(?i)(?=.*emma)(?=.*hugo)(?=.*clara)",
        "expected_requests": 0,
    },
    {
        "name": "colleague lookup gives the email but never the balance",
        "user": "E001",
        "auto_confirm": False,
        "input": "What's Clara's email, and how many leave days does she have left?",
        "must_call": ["lookup_colleague"],
        "reply_must_match": r"(?i)clara@cognivexai\.eu",
        "reply_must_not_match": r"(?i)\b[01]\s*(casual |sick )?days?\b",
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
        "reply_must_match": r"(?i)emma",
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
        "user": "E002",            # Hugo has 4 sick days; this is about 2 weeks
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
        # the fact: a certificate/note is needed beyond 2 days; accept the user's wording or the policy's
        "reply_must_match": r"(?i)(medical certificate|doctor'?s note).*(more than|over|beyond|exceed)\D{0,20}(2|two)|(more than|over|beyond|exceed)\D{0,20}(2|two).*(medical certificate|doctor'?s note)",
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
        "reply_must_not_match": r"(?i)parking|hugo",
    },
    {
        "name": "ticket: HR can see open tickets",
        "user": "H001",
        "auto_confirm": False,
        "setup": setup_karthik_ticket,
        "input": "Show me the open tickets.",
        "must_call": ["get_open_tickets"],
        "reply_must_match": r"(?i)parking|hugo",
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
        "input": "Show me Emma's (E001) payslip for October 2026.",
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
    {
        "name": "roles: employee cannot approve leave, not even by asking",
        "user": "E001",
        "auto_confirm": True,
        "setup": setup_priya_pending,
        "input": "Approve leave request number 1.",
        "must_not_call": ["decide_leave"],
        "check": lambda: status_of(1) == "pending_manager_approval",
    },
    {
        "name": "payslip: missing payslip gets the real issue date, not a guess",
        "user": "M001",
        "auto_confirm": False,
        "input": "Show me my payslip for December 2026.",
        "must_call": ["get_payslip"],
        "reply_must_match": r"(?i)31 December|December 31",
        "reply_must_not_match": r"(?i)usually|typically|shortly|after the month",
    },
    {
        # real bug (1 Nov): "take paid leave between 1 May and 31 October ... you need to give notice
        # (30 days for employees)": the notice period is the RESIGNATION rule, not a leave rule
        "name": "policy: leave months answer does not mix in the resignation notice period",
        "user": "E001",
        "auto_confirm": False,
        "setup": setup_priya_asked_paid_leave,
        "input": "And during which months do I take them?",
        "reply_must_match": r"(?is)(?=.*(1 May|May 1|1er mai))(?=.*(31 October|October 31|31 octobre))",
        "reply_must_not_match": r"(?i)\bnotice\b|préavis",
    },

    # ---------- French labour law (need the Postgres container) ----------
    {
        "name": "law: paid leave entitlement cites L3141-3",
        "user": "E001",
        "auto_confirm": False,
        "needs_labour_db": True,
        "input": "What does French law say about how many days of paid leave I earn?",
        "must_call": ["search_labour_code"],
        "reply_must_match": r"(?is)(?=.*L\.?\s?3141-3)(?=.*(2[.,]5|deux jours et demi|30))",
    },
    {
        # an amount below the law must be flagged; asked as a claim, so we don't need an illegal policy
        "name": "law: an entitlement below the legal minimum is flagged",
        "user": "E001",
        "auto_confirm": False,
        "needs_labour_db": True,
        "input": "My manager says I only get 20 days of paid holiday a year. Is that legal?",
        "must_call": ["search_labour_code"],
        "reply_must_match": r"(?is)(?=.*L\.?\s?3141-3)(?=.*(below|less than|lower than|under the legal|not legal|illegal|minimum|not enough))",
    },
    {
        # the policy has a real paid-leave section: 30 days (5 weeks), exactly the legal minimum.
        # 1 Nov: with "25 days" the agent called it both "less than" and "more generous than" the law,
        # and the old test (checking only "25") passed the wrong answer, so check the meaning too
        "name": "policy: paid leave comes from the paid-leave section, not casual leave",
        "user": "E001",
        "auto_confirm": False,
        "input": "How many days of paid leave do I get per year?",
        "must_call": ["search_policy"],
        "reply_must_match": r"(?i)\b30\b|5 weeks|five weeks",
        "reply_must_not_match": r"(?i)\b12\b[^.]{0,40}paid|less than (the law|the legal)|below the legal|more generous",
    },
    {
        # real bug (1 Nov): with a labour answer in history, the follow-up was answered from memory,
        # citing "L3141-4" and the wrong period (1 June - 31 May) without any search
        "name": "law: follow-up from history still searches, cites only retrieved articles",
        "user": "E001",
        "auto_confirm": False,
        "needs_labour_db": True,
        "setup": setup_priya_labour_history,
        "input": "And during which months do I take them?",
        "citations_grounded": True,
        "reply_must_match": r"(?is)(?=.*(1 May|May 1|1er mai))(?=.*(31 October|October 31|31 octobre))",
        "reply_must_not_match": r"(?i)1 June|June 1",
    },
    {
        "name": "law: French question gets a French answer",
        "user": "E001",
        "auto_confirm": False,
        "needs_labour_db": True,
        "input": "Quelle est la durée légale du travail par semaine ?",
        "must_call": ["search_labour_code"],
        "reply_must_match": r"(?is)(?=.*L\.?\s?3121-27)(?=.*35\s?heures)",
    },
    {
        "name": "law: maternity leave length cites L1225-17",
        "user": "E001",
        "auto_confirm": False,
        "needs_labour_db": True,
        "input": "How long is maternity leave in France?",
        "must_call": ["search_labour_code"],
        # 16 weeks in total, or the same rule split as 6 weeks before + 10 weeks after
        "reply_must_match": r"(?is)(?=.*L\.?\s?1225-17)(?=.*(\b16\b|sixteen|seize|\b6\b[^.]{0,60}\b10\b))",
    },
    {
        "name": "paid leave: says how many paid days I have",
        "user": "E001",
        "auto_confirm": False,
        "input": "How many paid leave days do I have left?",
        "must_call": ["get_leave_balance"],
        "reply_must_match": r"12[.,]5",
        "expected_requests": 0,
    },
    {
        "name": "paid leave: a Monday to Saturday week is counted with Saturday",
        "user": "E001",
        "auto_confirm": True,
        "input": f"Please book paid leave from {say(PAID_START)} to {say(PAID_END)}.",
        "must_call": ["count_leave_days", "apply_leave"],
        "reply_must_match": rf"\b{PAID_EXPECTED}\b",
        "expected_requests": 1,
        "check": lambda: paid_request_days() == [PAID_EXPECTED],
    },
    {
        "name": "paid leave: more days than the paid balance is refused",
        "user": "E003",
        "auto_confirm": True,
        "input": f"Please book paid leave from {say(day(3, 0))} to {say(day(3, 4))}.",
        "reply_must_match": r"(?i)enough|only|2[.,]5|insufficient|available|can't|cannot",
        "expected_requests": 0,
        "check": lambda: paid_balance("E003") == 2.5,
    },
    {
        "name": "paid leave: approving takes the days from the paid balance only",
        "user": "M001",
        "auto_confirm": True,
        "setup": setup_emma_pending_paid,
        "input": "Please approve Emma's pending leave request.",
        "must_call": ["get_pending_requests", "decide_leave"],
        "check": lambda: status_of(1) == "approved" and paid_balance("E001") == 12.5 - paid_request_days()[0]
                         and casual_balance("E001") == 8,
    },
    {
        "name": "paid leave: explains the monthly 2.5 days",
        "user": "E001",
        "auto_confirm": False,
        "setup": setup_paid_leave_earned,
        "input": "Why did my paid leave go up?",
        "must_call": ["get_balance_history"],
        "reply_must_match": r"2[.,]5",
        "expected_requests": 0,
    },
]


# ---------- runner ----------

def select_indices(only=None):
    # positions in TEST_CASES of the tests whose name contains `only` (all of them if empty)
    return [i for i, c in enumerate(TEST_CASES) if not only or only.lower() in c["name"].lower()]


def run_cases(cases):
    """Run these tests one after another in THIS process. Returns (passed, ran, skipped)."""
    passed = skipped = 0
    labour_db = labour_db_available()
    print(f"Eval run: {RUN_ID}  (tracing {'ON' if tracing.ENABLED else 'OFF'}, "
          f"labour-law database {'ON' if labour_db else 'OFF'})\n")
    for case in cases:
        if case.get("needs_labour_db") and not labour_db:
            skipped += 1
            print(f"SKIP {case['name']}  (no labour-law database)")
            continue
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
        if case.get("citations_grounded"):
            bad = ungrounded_citations(reply, agent.last_turn["sources"])
            if bad:
                problems.append(f"cites articles no tool returned this turn: {', '.join(bad)}")
        # LLM-as-judge on answers built from sources (policy and law): catches invented reasons and
        # wrong attributions that keyword checks miss. Leave, permission and pay tests are checked by code.
        if case["name"].startswith(("policy:", "law:")):
            for issue in judge(case["input"], reply, agent.last_turn["sources"]):
                problems.append(f"judge: unsupported \"{issue.get('claim')}\" ({issue.get('why')})")
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

    ran = len(cases) - skipped
    print(f"\n{passed}/{ran} passed" + (f", {skipped} skipped" if skipped else ""))
    tracing.flush()   # send all eval traces and scores before the program exits
    return passed, ran, skipped


def run_evals(only=None):
    passed, ran, _ = run_cases([TEST_CASES[i] for i in select_indices(only)])
    return passed == ran


# ---------- parallel run ----------
# The agent keeps ONE conversation in module-level state (agent.LOGGED_IN_ID, history, last_turn),
# so tests cannot share a process: threads would overwrite each other. Instead each worker is a
# separate `python evals.py --worker-db ... --cases ...` process with its own database file.

def run_parallel(indices, workers):
    """Split the tests over `workers` processes, show their output prefixed [w1], [w2]...,
    and add up the results. Returns True only if every test passed and every worker finished."""
    workers = min(workers, len(indices))
    # round-robin, so slow tests that sit next to each other (policy, law) land on different workers
    groups = [indices[w::workers] for w in range(workers)]
    env = {**os.environ, "EVAL_RUN_ID": RUN_ID, "PYTHONUNBUFFERED": "1"}
    print(f"Eval run: {RUN_ID}  ({len(indices)} tests on {workers} workers, "
          f"{', '.join(str(len(g)) for g in groups)} each)\n")

    lock = threading.Lock()
    results = {}     # worker number -> {"passed", "ran", "skipped"}, filled from its RESULT line
    procs = []

    def pump(n, proc):
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line.startswith("RESULT "):
                results[n] = json.loads(line[len("RESULT "):])
                continue
            with lock:
                print(f"[w{n}] {line}", flush=True)

    readers = []
    try:
        for n, group in enumerate(groups, start=1):
            worker_db = f"test_hr_w{n}.db"
            if os.path.exists(worker_db):
                os.remove(worker_db)     # start every worker from a clean file
            cmd = [sys.executable, os.path.abspath(__file__),
                   "--worker-db", worker_db, "--cases", ",".join(map(str, group))]
            proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True)
            procs.append(proc)
            reader = threading.Thread(target=pump, args=(n, proc))
            reader.start()
            readers.append(reader)
        for proc, reader in zip(procs, readers):
            proc.wait()
            reader.join()
    finally:
        for proc in procs:                         # Ctrl+C: don't leave workers running (and paying)
            if proc.poll() is None:
                proc.terminate()
        for n in range(1, len(procs) + 1):
            if os.path.exists(f"test_hr_w{n}.db"):
                os.remove(f"test_hr_w{n}.db")

    crashed = [n for n in range(1, workers + 1) if n not in results]
    passed = sum(r["passed"] for r in results.values())
    ran = sum(r["ran"] for r in results.values())
    skipped = sum(r["skipped"] for r in results.values())
    print(f"\n{passed}/{ran} passed" + (f", {skipped} skipped" if skipped else "")
          + f"  (all {workers} workers)")
    if crashed:
        print("WORKER CRASHED, results incomplete: "
              + ", ".join(f"w{n} (exit code {procs[n - 1].returncode})" for n in crashed))
    return passed == ran and not crashed


if __name__ == "__main__":
    # python evals.py                 -> all tests, one after another
    # python evals.py law             -> only tests whose name contains "law"
    # python evals.py --workers 4     -> all tests, split over 4 processes (filter works too)
    # python evals.py "overtime" --repeat 10 --workers 5   -> run those tests 10 times each: for flaky evals
    parser = argparse.ArgumentParser()
    parser.add_argument("only", nargs="?", help="only tests whose name contains this text")
    parser.add_argument("--workers", type=int, default=1, help="number of parallel processes")
    parser.add_argument("--repeat", type=int, default=1, help="run the selected tests this many times (to measure a flaky one)")
    parser.add_argument("--worker-db", help=argparse.SUPPRESS)   # internal: used by --workers
    parser.add_argument("--cases", help=argparse.SUPPRESS)       # internal: used by --workers
    args = parser.parse_args()

    if args.worker_db:
        # I am a worker: my own database file, only the tests the parent gave me
        db.DB_PATH = args.worker_db
        passed, ran, skipped = run_cases([TEST_CASES[int(i)] for i in args.cases.split(",")])
        print("RESULT " + json.dumps({"passed": passed, "ran": ran, "skipped": skipped}))
        sys.exit(0 if passed == ran else 1)

    if args.workers < 1:
        parser.error("--workers must be 1 or more")
    if args.repeat < 1:
        parser.error("--repeat must be 1 or more")
    indices = select_indices(args.only) * args.repeat
    started = time.time()
    if args.workers == 1 or len(indices) <= 1:
        passed, ran, _ = run_cases([TEST_CASES[i] for i in indices])
        ok = passed == ran
    else:
        ok = run_parallel(indices, args.workers)
    minutes, seconds = divmod(int(time.time() - started), 60)
    print(f"Time: {minutes}m {seconds:02d}s")
    sys.exit(0 if ok else 1)