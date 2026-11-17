# unit_tests.py: fast checks of the business rules. No AI, no API keys, no cost.
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import time
import types
from datetime import date, datetime, timedelta, timezone

import db
db.DB_PATH = "unit_test_hr.db"   # its own database, never the real hr.db

import approval_first
import booking_first
import collective_agreement
import confirm_batch
import contract_first
import contracts
import document_answers
import documents
import careers
import existing_first
import employee_files
import eval_patterns
import jobs
import number_guard
import pdf_text
import policy_gaps
import policy_grounding
import retention
import policy_store
import recruitment_guard
import search_first
import tool_guard
import tools
from eval_retry import MAX_RETRIED, retry_report, run_with_retry
from judge_parse import verdict
import law_status
from labour_code import chunk as law_chunk
from labour_code import legifrance
from labour_code import sync as law_sync
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
    assert count_working_days(date(2026, 11, 10), date(2026, 11, 15)) == 3   # weekend and Armistice Day (11 Nov) excluded
    assert count_working_days(date(2026, 12, 21), date(2026, 12, 25)) == 4   # Christmas excluded


# ---------- paid leave (congés payés): counted Monday to Saturday ----------
# Fixed dates (Mon 4 March 2030 to Sun 10 March 2030) so no holiday or "today" can change the answer.
MON, SAT, SUN = date(2030, 3, 4), date(2030, 3, 9), date(2030, 3, 10)


@test
def paid_leave_is_counted_monday_to_saturday():
    assert tools.count_ouvrable_days(MON, SAT) == 6                 # a week off is 6 days, not 5
    assert tools.count_ouvrable_days(MON, SUN) == 6                 # Sunday is never counted
    assert tools.count_ouvrable_days(SUN, SUN) == 0
    assert count_working_days(MON, SUN) == 5                        # casual and sick still skip weekends
    # a public holiday is not counted, Saturdays included
    assert tools.count_ouvrable_days(MON, SAT, holidays={date(2030, 3, 9)}) == 5
    assert tools.days_for("paid", MON, SAT) == 6 and tools.days_for("casual", MON, SAT) == 5


@test
def paid_leave_balance_shows_halves_but_not_needless_decimals():
    bal = get_leave_balance("E001")["leave_balance"]                # Emma: 8 casual, 5 sick, 12.5 paid
    assert bal == {"casual": 8, "sick": 5, "paid": 12.5}
    assert isinstance(bal["casual"], int)                           # 8, never 8.0
    assert get_leave_balance("C001")["leave_balance"]["paid"] == 25 and isinstance(
        get_leave_balance("C001")["leave_balance"]["paid"], int)


@test
def paid_leave_request_approve_and_cancel_use_the_paid_balance_and_saturdays():
    r = apply_leave("E001", "paid", d(MON), d(SAT))
    assert r["success"] and r["request"]["days"] == 6, r
    assert r["balance_after_approval"] == {"paid": 6.5}             # 12.5 - 6
    assert decide_leave("M001", r["request"]["id"], "approve")["employee_new_balance"] == 6.5
    bal = get_leave_balance("E001")["leave_balance"]
    assert bal == {"casual": 8, "sick": 5, "paid": 6.5}             # only the paid balance moved
    assert cancel_leave("E001", r["request"]["id"])["days_returned"] == 6
    assert get_leave_balance("E001")["leave_balance"]["paid"] == 12.5


@test
def not_enough_paid_leave_is_blocked_and_pending_paid_days_count():
    # Clara has 2.5 paid days: a 3-day request is too much, a 2-day one is fine
    assert "Not enough paid leave" in apply_leave("E003", "paid", d(MON), d(MON + timedelta(days=2)))["error"]
    assert apply_leave("E003", "paid", d(MON), d(MON + timedelta(days=1))).get("success")
    assert "error" in apply_leave("E003", "paid", d(SAT), d(SAT))   # 0.5 left, one more day is too much
    # a Sunday-only request has no days to take
    sunday_only = apply_leave("E001", "paid", d(SUN), d(SUN))["error"]
    assert "Sunday" in sunday_only and "uses no days" in sunday_only and "Nothing was submitted" in sunday_only


# ---------- paid leave: the usual 1 May to 31 October window ----------

@test
def the_paid_leave_note_appears_only_when_the_dates_leave_the_usual_window():
    note = tools.paid_policy()[1]
    n = tools.paid_policy_note
    assert n(date(2030, 5, 1), date(2030, 5, 4)) is None          # the first day of the window
    assert n(date(2030, 7, 1), date(2030, 7, 6)) is None
    assert n(date(2030, 10, 28), date(2030, 10, 31)) is None      # the last day of the window
    assert n(date(2030, 12, 2), date(2030, 12, 7)) == note
    assert n(date(2030, 4, 29), date(2030, 5, 3)) == note         # starts before the window
    assert n(date(2030, 10, 30), date(2030, 11, 2)) == note       # runs past it
    assert n(date(2030, 12, 30), date(2031, 1, 3)) == note        # across New Year


@test
def the_note_and_the_window_are_read_from_the_policy_text_and_the_note_carries_the_whole_policy():
    # the policy text is the only place they are written (hr_policy.md until HR edits it in the Handbook): the note is that text, the window comes from its dates
    text = open("hr_policy.md", encoding="utf-8").read()
    window, note = tools.paid_policy()
    assert note in text
    assert window == ((5, 1), (10, 31))
    # the note must not read as a blocker, or as a promise: outside the window it can still be requested, and
    # the manager decides
    assert "normally taken between 1 May and 31 October" in note
    assert "can still be requested" in note
    assert "your manager decides whether to approve it" in note and "approves it" not in note


@test
def paid_leave_results_carry_the_note_outside_the_window_and_other_leave_never_does():
    december = apply_leave("E001", "paid", "2030-12-02", "2030-12-04")
    assert december["success"] and december["policy_note"] == tools.paid_policy()[1]
    summer = apply_leave("E001", "paid", "2030-07-01", "2030-07-03")
    assert summer["success"] and "policy_note" not in summer
    casual = apply_leave("E001", "casual", "2030-12-09", "2030-12-09")       # a December casual day is normal
    assert casual["success"] and "policy_note" not in casual


@test
def a_balance_answer_about_paid_leave_has_the_note_and_other_balance_answers_do_not():
    assert get_leave_balance("E001", "paid")["policy_note"] == tools.paid_policy()[1]
    assert "policy_note" not in get_leave_balance("E001")
    assert "policy_note" not in get_leave_balance("E001", "casual")
    assert get_leave_balance("E001", "paid")["leave_balance"]["paid"] == 12.5     # the numbers are unchanged


@test
def past_dates_are_blocked():
    yesterday = date.today() - timedelta(days=1)
    assert "error" in apply_leave("E001", "casual", d(yesterday), d(yesterday))


@test
def zero_balance_is_blocked():
    mon = next_monday()
    assert "error" in apply_leave("E003", "casual", d(mon), d(mon))   # Clara has 0 casual


@test
def duplicate_request_is_blocked():
    mon = next_monday()
    assert apply_leave("E001", "casual", d(mon), d(mon)).get("success")
    assert "error" in apply_leave("E001", "casual", d(mon), d(mon))


@test
def pending_days_count_against_balance():
    mon = next_monday()
    tue, wed = mon + timedelta(days=1), mon + timedelta(days=2)
    assert apply_leave("E002", "casual", d(mon), d(mon)).get("success")   # Hugo has 2 casual
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
    assert "error" in decide_leave("C001", rid, "approve")   # Mugesh is not Emma's manager
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
    tools.create_hr_ticket("E001", "Emma's question", "Emma's summary")
    tools.create_hr_ticket("E002", "Hugo's question", "Hugo's summary")
    hugo = tools.get_my_tickets("E002")["tickets"]
    assert len(hugo) == 1
    assert "Emma" not in str(hugo)


@test
def only_hr_can_see_open_tickets():
    tools.create_hr_ticket("E001", "A question", "A summary")
    assert len(tools.get_open_tickets("H001")["tickets"]) == 1   # Narmadha (HR) can
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
    # the same tool names the agent uses; payslip tools have an employee_id field
    names = ["get_leave_balance", "get_my_requests", "count_leave_days", "apply_leave",
             "cancel_leave", "search_policy", "create_hr_ticket", "get_my_tickets",
             "get_payslip", "compare_payslips", "get_pay_summary",
             "get_pending_requests", "get_team_member_balance", "decide_leave",
             "get_open_tickets", "reply_to_ticket", "search_labour_code", "lookup_colleague", "get_balance_history", "get_holidays",
             "get_needs_attention", "get_my_onboarding", "get_contract_status", "get_my_documents", "read_my_document", "search_collective_agreement",
             "get_job_pipeline"]
    tools = [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}}
             for n in names]
    for t in tools:
        if t["name"] in ("get_payslip", "compare_payslips", "get_pay_summary"):
            t["input_schema"]["properties"]["employee_id"] = {"type": "string"}
    return tools


@test
def fake_tool_list_matches_the_agent():
    import re
    real = set(re.findall(r'"name": "(\w+)",\n\s+"description"', open("agent.py", encoding="utf-8").read()))
    assert real == {t["name"] for t in fake_tools()}, real ^ {t["name"] for t in fake_tools()}


MANAGER_ONLY = {"get_pending_requests", "get_team_member_balance", "decide_leave"}
HR_ONLY = {"get_open_tickets", "reply_to_ticket"}
STAFF_ONLY = {"get_needs_attention", "get_job_pipeline"}      # the Needs you inbox and the recruitment pipeline: managers and HR, never employees


def names_for(role):
    import roles
    return {t["name"] for t in roles.tools_for(role, fake_tools())}


@test
def employee_gets_no_manager_or_hr_tools():
    names = names_for("employee")
    assert not names & (MANAGER_ONLY | HR_ONLY | STAFF_ONLY), names
    assert "apply_leave" in names and len(names) == 18, names


@test
def manager_gets_team_tools_but_not_hr_tools():
    names = names_for("manager")
    assert MANAGER_ONLY <= names and not names & HR_ONLY, names
    assert len(names) == 23 and "get_job_pipeline" in names, names


@test
def hr_gets_ticket_tools_but_not_manager_tools():
    names = names_for("hr")
    assert HR_ONLY <= names and not names & MANAGER_ONLY, names
    assert len(names) == 23 and "get_contract_status" in names and "get_job_pipeline" in names, names


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


@test
def needs_attention_is_for_managers_and_hr_only():
    import roles
    for role, allowed in (("employee", False), ("manager", True), ("hr", True), ("intern", False)):
        assert roles.allowed(role, "get_needs_attention") is allowed, role
        assert ("get_needs_attention" in names_for(role)) is allowed, role


@test
def a_new_hire_gets_exactly_one_tool_and_nobody_else_gets_it():
    import roles
    assert names_for("new_hire") == {"get_my_onboarding"}
    for role in ("employee", "manager", "hr", "intern"):
        assert "get_my_onboarding" not in names_for(role), role
        assert roles.allowed(role, "get_my_onboarding") is False, role
    for tool in names_for("employee") | MANAGER_ONLY | HR_ONLY | STAFF_ONLY:      # the guard in run_tool agrees
        assert roles.allowed("new_hire", tool) is False, tool
    assert roles.allowed("new_hire", "get_my_onboarding") is True



# ---------- citations: only cite what a tool returned this turn ----------

@test
def citation_finder_reads_common_formats():
    from citations import cited_articles
    assert cited_articles("Article L3141-3 and L. 3141-13, see D3131-3") == {"L3141-3", "L3141-13", "D3131-3"}
    assert cited_articles("Due on 2026-11-01, ticket HR-0001") == set()   # dates and tickets aren't articles


@test
def invented_article_is_caught():
    # the real bug: L3141-4 cited from memory, no labour search in this turn
    from citations import ungrounded_citations
    reply = "According to French law, Article L3141-4 says leave is taken from 1 June to 31 May."
    assert ungrounded_citations(reply, []) == ["L3141-4"]
    sources = ['{"articles": [{"article": "L3141-13", "text_fr": "..."}]}']
    assert ungrounded_citations(reply, sources) == ["L3141-4"]


@test
def retrieved_article_is_allowed():
    from citations import ungrounded_citations
    reply = "Article L3141-13: leave is taken in a period that includes 1 May to 31 October."
    sources = ['{"articles": [{"article": "L3141-13", "text_fr": "Les congés sont pris..."}]}']
    assert ungrounded_citations(reply, sources) == []
    # an article named inside a policy section also counts as a source
    assert ungrounded_citations("L3141-3 sets the minimum.", ['{"sections": [{"text": "article L3141-3"}]}']) == []

# ---------- labour-law search: a failed or odd Haiku step falls back to the search, never an error (labour_code/search.py) ----------

class FakeClaude:
    """Stands in for search.claude: gives these answers in turn (an exception is raised) and records each call."""
    def __init__(self, answers, options):
        self.answers, self.calls, self.options = list(answers), [], options   # options: what the real client was built with
        self.messages = self

    def create(self, **request):
        self.calls.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=answer)])


def labour_search(*answers):
    """labour_code/search.py with a fake Claude. Its libraries are stand-ins (CI's unit-test job installs no packages), put back after loading."""
    stand_ins = {name: types.ModuleType(name) for name in ("psycopg", "psycopg.rows", "dotenv", "openai", "anthropic")}
    stand_ins["psycopg.rows"].dict_row = None
    stand_ins["dotenv"].load_dotenv = lambda: None
    stand_ins["openai"].OpenAI = lambda: None
    stand_ins["anthropic"].Anthropic = lambda **options: options
    saved, saved_url = {name: sys.modules.get(name) for name in stand_ins}, os.environ.get("LABOUR_DB_URL")
    sys.modules.update(stand_ins)
    os.environ["LABOUR_DB_URL"] = "postgresql://never-used"
    try:
        spec = importlib.util.spec_from_file_location("labour_search", os.path.join(os.path.dirname(os.path.abspath(__file__)), "labour_code", "search.py"))
        search = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(search)
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if saved_url is None:
            os.environ.pop("LABOUR_DB_URL", None)
        else:
            os.environ["LABOUR_DB_URL"] = saved_url
    search.claude = FakeClaude(answers, search.claude)
    return search


def candidates():
    # three articles in search order; L3141-3 has two chunks, the reranker sees it once
    return [{"chunk_id": 1, "num": "L3141-1", "text": "Tout salarié a droit chaque année à un congé payé."},
            {"chunk_id": 2, "num": "L3141-3", "text": "Le salarié a droit à un congé de deux jours et demi ouvrables par mois."},
            {"chunk_id": 3, "num": "L3141-3", "text": "La durée totale du congé exigible ne peut excéder trente jours ouvrables."},
            {"chunk_id": 4, "num": "L3141-12", "text": "Les congés peuvent être pris dès l'embauche."}]


SEARCH_ORDER = ["L3141-1", "L3141-3", "L3141-12"]


def nums(rows):
    return [r["num"] for r in rows]


def searches_seen(search):
    # the database searches stand in too: record which words each one was given
    seen = []
    search.vector_search = lambda conn, question, k=20: seen.append(("vector", question)) or candidates()[:2]
    search.keyword_search = lambda conn, question, k=20: seen.append(("keyword", question)) or candidates()[3:]
    return seen


@test
def labour_rerank_failing_keeps_the_search_order_and_says_so_in_the_log():
    search = labour_search(RuntimeError("Overloaded"))
    with contextlib.redirect_stdout(io.StringIO()) as log:
        assert nums(search.rerank("How many days of paid leave?", candidates())) == SEARCH_ORDER
    assert "rerank failed (RuntimeError): search order kept" in log.getvalue()
    assert len(search.claude.calls) == 1


@test
def labour_rewrite_failing_searches_with_the_users_own_words_and_tries_again_next_time():
    search = labour_search(RuntimeError("Overloaded"), "congés payés durée")
    seen = searches_seen(search)
    with contextlib.redirect_stdout(io.StringIO()) as log:
        assert nums(search.multi_query_search(None, "How many days of paid leave?")) == ["L3141-1", "L3141-3"]
    assert seen == [("vector", "How many days of paid leave?")]                     # no keyword search on English words
    assert "query rewrite failed (RuntimeError): searching with the user's words only" in log.getvalue()
    seen.clear()
    search.multi_query_search(None, "How many days of paid leave?")                    # a failure is not cached
    assert seen == [("vector", "How many days of paid leave?"), ("vector", "congés payés durée"), ("keyword", "congés payés durée")]


@test
def labour_rewrite_and_rerank_have_a_10_second_timeout_one_retry_and_temperature_0():
    search = labour_search(TimeoutError("Request timed out."), TimeoutError("Request timed out."))
    assert search.claude.options == {"timeout": 10, "max_retries": 1}
    searches_seen(search)
    with contextlib.redirect_stdout(io.StringIO()) as log:
        assert nums(search.reranked_search(None, "How many days of paid leave?")) == ["L3141-1", "L3141-3"]   # timed out twice: still articles
    assert "query rewrite failed (TimeoutError)" in log.getvalue() and "rerank failed (TimeoutError)" in log.getvalue()
    assert [call["temperature"] for call in search.claude.calls] == [0, 0]
    search = labour_search("congés payés durée", "2,1")
    searches_seen(search)
    with contextlib.redirect_stdout(io.StringIO()):
        search.search_for_agent(None, "How many days of paid leave?", None)
    assert [(call["max_tokens"], call["temperature"]) for call in search.claude.calls] == [(60, 0), (150, 0)]


@test
def labour_agent_search_still_returns_articles_when_every_haiku_step_fails():
    search = labour_search(RuntimeError("Overloaded"), RuntimeError("Overloaded"), RuntimeError("Overloaded"))
    seen = searches_seen(search)
    with contextlib.redirect_stdout(io.StringIO()):
        results = search.search_for_agent(None, "How many days of paid leave?", "paid leave entitlement per year")
    assert nums(results) == ["L3141-1", "L3141-3"]
    assert [kind for kind, _ in seen] == ["vector", "vector"] and len(search.claude.calls) == 3    # two rewrites and the rerank, all failed


@test
def labour_rerank_reads_only_a_plain_list_of_positions():
    def reranked(answer):
        search = labour_search(answer)
        with contextlib.redirect_stdout(io.StringIO()) as log:
            order = nums(search.rerank("How many days of paid leave?", candidates()))
        return order, log.getvalue()
    # the old reader took the 3 of "L3141-3" as position 3 and put L3141-12 first
    order, log = reranked("L3141-3 answers this")
    assert order == SEARCH_ORDER and "not a list of positions: search order kept" in log
    for answer in ("", "[2, 1, 3]", "2,1,3.", "Ranking: 2,1,3", "2\n1\n3", "2;1;3"):
        assert reranked(answer) == (SEARCH_ORDER, "  [labour search] rerank answer is not a list of positions: search order kept\n"), answer
    assert reranked("2,3,1") == (["L3141-3", "L3141-12", "L3141-1"], "")
    assert reranked(" 3, 1 \n") == (["L3141-12", "L3141-1", "L3141-3"], "")               # spaces and a final line break are fine
    assert reranked("3,9,3,0")[0] == ["L3141-12", "L3141-1", "L3141-3"]                       # out of range and repeats ignored
    assert reranked("2")[0] == ["L3141-3", "L3141-1", "L3141-12"]                             # what it left out keeps search order


@test
def labour_rerank_with_no_candidates_makes_no_call():
    search = labour_search()
    assert search.rerank("How many days of paid leave?", []) == []
    assert search.claude.calls == []
    search.vector_search = search.keyword_search = lambda conn, question, k=20: []           # the search found nothing
    search.claude.answers = ["congés payés durée"]
    assert search.reranked_search(None, "How many days of paid leave?") == []
    assert len(search.claude.calls) == 1                                                       # the rewrite only, no rerank


@test
def labour_search_table_can_be_switched_for_a_measurement_but_only_to_a_plain_name():
    saved = os.environ.get("LABOUR_TABLE")
    try:
        os.environ["LABOUR_TABLE"] = "chunks_api"                                              # load_api.py's side table
        search = labour_search()
        seen = []
        conn = types.SimpleNamespace(execute=lambda sql, params: seen.append(sql) or types.SimpleNamespace(fetchall=lambda: []))
        search.embed = lambda question: "[0]"
        search.vector_search(conn, "congés payés")
        search.keyword_search(conn, "congés payés")
        assert len(seen) == 2 and all("FROM chunks_api" in sql for sql in seen), seen
        os.environ["LABOUR_TABLE"] = "chunks; DROP TABLE chunks"                               # the name goes into the SQL text
        try:
            labour_search()
            accepted = True
        except AssertionError:
            accepted = False
        assert not accepted
    finally:
        if saved is None:
            os.environ.pop("LABOUR_TABLE", None)
        else:
            os.environ["LABOUR_TABLE"] = saved

# ---------- the labour code from Légifrance: one call, cleaned before anything is stored (labour_code/legifrance.py) ----------

def at_midnight_ms(day):
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def legifrance_answer():
    """A small consult/legiPart answer with each problem the real one has (Nov 2026: 164 entries listed twice, one article in
    two versions, 62 old-code articles, 10 annexes, a number with spaces, a link label left in a text, the same decree sentence 14 times)."""
    today = date.today()
    long_ago, last_month = at_midnight_ms(today - timedelta(days=4000)), at_midnight_ms(today - timedelta(days=30))

    def art(id, num, content, etat="VIGUEUR", start=long_ago, end=at_midnight_ms(date(2999, 1, 1))):
        return {"id": id, "cid": "C" + id, "num": num, "etat": etat, "dateDebut": str(start), "dateFin": end, "content": content}
    decree = "<p>Un décret en Conseil d'Etat détermine les modalités d'application du présent chapitre.</p>"
    chapter = {"title": "Chapitre Ier : Congés payés ", "sections": [], "articles": [
        art("A1", "L3141-3", '<p>Le salarié a droit à un congé de <a href="x" title="y">deux jours et demi</a> ouvrables.</p><p>Surface : 2 m<sup>2</sup>.</p>'),
        art("A2", "L3141-4", "<p>Ancienne version.</p>"),
        art("A3", "L3141-4", "<p>Nouvelle version.</p>", start=last_month),
        art("A4", "", "<p>Sans numéro.</p>"),
        art("A5", "Annexe I", "<table><tr><td>a</td><td>b</td></tr></table>"),
        art("A6", "L3141-5", "<p>Abrogé.</p>", etat="ABROGE"),
        art("A7", "L3141-6", "<p>Fini hier.</p>", end=at_midnight_ms(today - timedelta(days=1))),
        art("A8", "L3141-7", "<p>Abrogé l'an prochain.</p>", etat="ABROGE_DIFF", end=at_midnight_ms(today + timedelta(days=365))),
        art("A9", " L3141-8 ", "<p>mentionnés à l'article AncreL. 1221-13Ancre, sont</p>"),
        art("A10", "L3141-9", "<p> </p>"),
    ] + [art(f"B{i}", f"R3141-{i}", decree) for i in range(1, 6)]}
    return {"sections": [
        {"title": "Partie législative  ", "sections": [chapter], "articles": []},
        {"title": "Partie réglementaire", "sections": [{"title": "Ailleurs", "sections": [], "articles": [chapter["articles"][0]]}], "articles": []},
        {"title": "Partie législative ancienne", "sections": [], "articles": [art("O1", "L143-11-4", "<p>Ancien code.</p>")]},
    ]}


@test
def legifrance_articles_are_cleaned_merged_and_every_left_out_one_is_counted():
    articles, report, boilerplate, annexes = legifrance.articles_from(legifrance_answer(), date.today())
    by = {a["num"]: a for a in articles}
    assert sorted(by) == ["L3141-3", "L3141-4", "L3141-7", "L3141-8"] + [f"R3141-{i}" for i in range(1, 6)], sorted(by)
    assert by["L3141-3"]["texte"] == "Le salarié a droit à un congé de deux jours et demi ouvrables. Surface : 2 m2."   # a link and an exponent unwrapped in place
    assert by["L3141-4"]["id"] == "A3" and by["L3141-4"]["texte"] == "Nouvelle version."                             # the version that started last
    assert by["L3141-8"]["texte"] == "mentionnés à l'article L. 1221-13, sont"                                        # number trimmed, link label gone
    assert by["L3141-3"]["dateFin"] == "2999-01-01" and by["L3141-3"]["cid"] == "CA1"
    assert report == {"entries": 17, "same entry twice": 1, "not in force today": 2, "old pre-2008 code": 1, "no article number": 1,
                      "annexes left out": 1, "empty text": 1, "two versions, latest kept": 1, "kept": 9, "articles with a repeated text": 5}, dict(report)
    assert annexes == ["Annexe I"]
    assert boilerplate == [("Un décret en Conseil d'Etat détermine les modalités d'application du présent chapitre.", 5)]   # reported, still kept
    chunks = law_chunk.chunks_for(by["L3141-3"])                                                                     # cut like the current copy
    assert [c["chunk_id"] for c in chunks] == ["L3141-3#1"] and chunks[0]["path"] == "Partie législative > Chapitre Ier : Congés payés"
    assert chunks[0]["search_text"].startswith("Article L3141-3. Partie législative > Chapitre Ier : Congés payés. Le salarié a droit")


@test
def legifrance_dates_arrive_as_milliseconds_in_numbers_or_text():
    assert legifrance.to_day(at_midnight_ms(date(2026, 11, 17))) == date(2026, 11, 17)
    assert legifrance.to_day(str(at_midnight_ms(date(2999, 1, 1)))) == date(2999, 1, 1)
    assert legifrance.to_day("2026-11-17") == date(2026, 11, 17)
    assert legifrance.to_day(None) is None and legifrance.to_day("") is None


def stand_in_httpx(answers):
    """httpx for legifrance.fetch_code (CI's unit job has no packages): gives these (status, body) answers in turn, records each request."""
    class HTTPStatusError(Exception):
        pass

    class TransportError(Exception):
        pass
    calls = []

    def post(url, **request):
        calls.append((url, request))
        status, body = answers.pop(0)

        def raise_for_status():
            if status >= 400:
                raise HTTPStatusError(status)
        return types.SimpleNamespace(json=lambda: body, raise_for_status=raise_for_status)
    return types.SimpleNamespace(post=post, HTTPStatusError=HTTPStatusError, TransportError=TransportError, calls=calls)


@test
def legifrance_fetch_is_one_call_retried_on_429_and_the_secret_goes_only_to_the_token_url():
    names = ("PISTE_CLIENT_ID", "PISTE_CLIENT_SECRET", "PISTE_ENV")
    saved = sys.modules.get("httpx"), {k: os.environ.get(k) for k in names}, legifrance.RETRY_WAIT
    token, (sandbox_token, sandbox_api), (prod_token, prod_api) = (200, {"access_token": "T1"}), legifrance.HOSTS["sandbox"], legifrance.HOSTS["production"]
    try:
        os.environ.update(PISTE_CLIENT_ID="id-1", PISTE_CLIENT_SECRET="secret-1")
        os.environ.pop("PISTE_ENV", None)
        legifrance.RETRY_WAIT = 0
        sys.modules["httpx"] = fake = stand_in_httpx([token, (429, {}), token, (200, {"sections": []})])
        assert legifrance.fetch_code(date(2026, 11, 17)) == {"sections": []}
        assert [url for url, _ in fake.calls] == [sandbox_token, sandbox_api + "consult/legiPart"] * 2          # sandbox unless PISTE_ENV says otherwise
        asked = fake.calls[-1][1]
        noon = int(datetime(2026, 11, 17, 12, tzinfo=timezone.utc).timestamp() * 1000)
        assert asked["json"] == {"textId": legifrance.CODE_DU_TRAVAIL, "date": noon}
        assert asked["headers"]["Authorization"] == "Bearer T1" and "secret-1" not in repr(asked)
        assert fake.calls[0][1]["data"]["client_secret"] == "secret-1"
        sys.modules["httpx"] = fake = stand_in_httpx([token, (200, {})])
        legifrance.fetch_code(date(2026, 11, 17), env="production")
        assert [url for url, _ in fake.calls] == [prod_token, prod_api + "consult/legiPart"]
        sys.modules["httpx"] = fake = stand_in_httpx([token, (429, {})] * 3)
        try:
            legifrance.fetch_code(date(2026, 11, 17))
            gave_up = False
        except fake.HTTPStatusError:
            gave_up = True                                                                                     # after 3 tries: the caller keeps its data
        assert gave_up and len(fake.calls) == 6
    finally:
        if saved[0] is None:
            sys.modules.pop("httpx", None)
        else:
            sys.modules["httpx"] = saved[0]
        for k, v in saved[1].items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        legifrance.RETRY_WAIT = saved[2]

# ---------- the grounding check of policy answers (policy_grounding.py), with a scripted checker ----------

WINDOW = json.dumps({"found": True, "sections": [{"title": "Paid leave (congés payés)", "text": "Paid leave is normally taken between 1 May and 31 October. "
                     "Outside this period, paid leave can still be requested and is handled like any other leave request: your manager decides whether to approve it."}]})
NEEDS = "Outside that period you can still request it. Your manager will decide whether to approve it based on business needs."
FLAGGED = json.dumps({"unsupported": [{"claim": "based on business needs", "why": "the policy only says the manager decides"}]})
CARRY = json.dumps({"found": True, "sections": [{"title": "Carry forward", "text": "Unused paid leave is not lost at the end of the year: it is carried forward in full."}]})


def checker(*answers):
    """A grounding checker that gives these answers in turn (an exception is raised) and remembers what it was shown."""
    left, seen = list(answers), []

    def check_model(system, text):
        seen.append(text)
        answer = left.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    check_model.seen = seen
    return check_model


@test
def a_policy_answer_with_a_reason_the_policy_does_not_give_is_sent_back_once_to_search_again_and_answer_from_the_text():
    assert policy_grounding.applies(["get_leave_balance", "search_policy"]) and not policy_grounding.applies(["get_leave_balance"])
    assert not policy_grounding.applies(["read_my_document", "search_policy", "search_labour_code"])           # a law answer: the check is for the policy only
    assert not policy_grounding.applies(["search_collective_agreement", "search_policy"])
    reply = f"Yes, you can take paid leave in December. {NEEDS}"
    fine = checker('{"unsupported": []}')
    assert policy_grounding.review("Can I take paid leave in December?", reply, [WINDOW], fine, False) == ("ok", reply, [])
    shown = fine.seen[0]
    assert "Can I take paid leave in December?" in shown and "your manager decides whether to approve it" in shown and NEEDS in shown   # question, tool results, reply
    action, note, found = policy_grounding.review("Can I take paid leave in December?", reply, [WINDOW], checker(FLAGGED), False)
    assert action == "retry" and note.startswith(document_answers.HIDDEN) and '"based on business needs"' in note and found[0]["claim"] == "based on business needs"
    assert "call search_policy once more" in note                       # the search may have missed the section: the real cause of "the policy doesn't restrict it"
    assert "do not say what happens in a case the text does not name" in note and "the policy does not cover it and suggest asking HR" in note
    # the checker thinks aloud and corrects itself: its last verdict counts
    assert policy_grounding.review("Can I take paid leave in December?", reply, [WINDOW], checker(f"{FLAGGED} Wait, that is supported. " + '{"unsupported": []}'), False)[0] == "ok"
    # an offer or a question to the person is never a rule, whatever the checker says (a real false alarm, 17 Nov)
    offer = json.dumps({"unsupported": [{"claim": "Would you like me to raise an HR ticket to ask about this?", "why": "no tool result"}]})
    assert policy_grounding.review("Is a gym membership covered?", "The policy doesn't cover it. Would you like me to raise an HR ticket to ask about this?",
                                   [WINDOW], checker(offer), False)[0] == "ok"


@test
def still_unsupported_after_the_retry_the_policy_found_is_quoted_and_a_failed_check_lets_the_reply_through():
    action, safe, _ = policy_grounding.review("Can I take paid leave in December?", NEEDS, [json.dumps({"days": 3}), CARRY, WINDOW, WINDOW], checker(FLAGGED), True)
    assert action == "replaced" and safe.startswith("Here is what the company policy says, word for word (Carry forward)")
    assert "31 October" in safe and safe.count("Paid leave (congés payés)") == 1 and "business needs" not in safe          # every section found, each once
    french = policy_grounding.review("Puis-je prendre des congés payés en décembre ?", NEEDS, [WINDOW], checker(FLAGGED), True)[1]
    assert french.startswith("Voici ce que dit la politique de l'entreprise") and "« Paid leave is normally" in french
    nothing = json.dumps({"found": False, "message": "No matching section in the HR policy."})
    assert policy_grounding.review("Can I bring my dog?", "Yes, dogs are welcome on Fridays.", [nothing], checker(FLAGGED), True)[1] == policy_grounding.NOT_CONFIRMED
    # the check itself fails: the reply goes out unchecked, and the agent logs why
    assert policy_grounding.review("December?", NEEDS, [WINDOW], checker(TimeoutError("slow")), False) == ("unchecked", NEEDS, "TimeoutError")
    assert policy_grounding.review("December?", NEEDS, [WINDOW], checker("I think it is fine."), False) == ("unchecked", NEEDS, "no usable answer")


# ---------- the nightly labour-code sync: what holds a night, who is told, and the date in law answers (labour_code/sync.py, law_status.py) ----------

@test
def law_sync_holds_a_night_when_a_check_fails_and_switches_only_when_recall_holds():
    known = [{"num": num, "texte": f"... {words} ..."} for num, words in legifrance.SPOT_CHECKS.items()]
    assert law_sync.spot_verdict(known) == ""
    assert "L3141-3" in law_sync.spot_verdict([{"num": "L3141-3", "texte": "Le salarié a droit à un congé payé."}] + known[1:])
    assert "L1225-17" in law_sync.spot_verdict(known[:1] + known[2:])                                          # gone altogether
    assert law_sync.size_verdict(None, 11524) == ""                                                             # the first sync replaces the old copy
    assert law_sync.size_verdict(11524, 11600) == ""                                                            # 0.7%: a normal night
    assert "more than 2%" in law_sync.size_verdict(11524, 9000)
    live = {"tuning": {"hits": 50, "of": 50}, "held-out": {"hits": 20, "of": 20}}
    assert law_sync.recall_verdict(live, {"tuning": {"hits": 50, "of": 50}, "held-out": {"hits": 20, "of": 20}}) == ""
    assert law_sync.recall_verdict(live, {"tuning": {"hits": 50, "of": 50}, "held-out": {"hits": 19, "of": 20}}) == "recall dropped: held-out 20/20 -> 19/20"
    assert law_sync.changes({"A": "x y", "B": "y", "C": "z"}, {"A": "x  y", "B": "y2", "D": "w"}) == {"added": 1, "changed": 1, "removed": 1}   # spacing is not a change
    assert law_sync.cost({"haiku_in": 1_000_000, "haiku_out": 100_000, "embed": 1_000_000}) == 1.52


@test
def law_sync_sets_aside_a_test_question_whose_article_was_repealed_instead_of_blocking_every_night():
    expected = {e for cases in law_sync.SETS.values() for _, _, exp in cases for e in exp.split("|")}
    assert law_sync.set_aside(expected) == []
    aside = law_sync.set_aside(expected - {"L3141-3"})
    assert aside and all(set(exp.split("|")) <= {"L3141-3"} for cases in law_sync.SETS.values() for q, _, exp in cases if q in aside)


def stand_in_github(open_issues):
    """httpx for sync.github_issue: answers the open-issue list, records every request."""
    calls = []

    def answer(method):
        def call(url, **request):
            calls.append((method, url, request))
            body = open_issues if method == "GET" else {"number": 31}
            return types.SimpleNamespace(json=lambda: body, raise_for_status=lambda: None)
        return call
    return types.SimpleNamespace(get=answer("GET"), post=answer("POST"), patch=answer("PATCH"), calls=calls)


@test
def law_sync_first_night_holds_when_data_looks_missing_rather_than_repealed():
    lost = law_sync.set_aside(set())                                                                            # every expected article gone
    assert law_sync.aside_verdict(lost[:2]) == ""                                                               # one or two: a real repeal, named in the log
    assert law_sync.aside_verdict(lost[:3]).startswith("3 recall questions lost their article")
    assert law_sync.first_sync_verdict(9754, 11524) == ""                                                       # Légifrance has more than the old copy
    assert law_sync.first_sync_verdict(9754, 9600) == ""                                                        # 1.6% fewer
    assert law_sync.first_sync_verdict(9754, 9559) == ""                                                        # 1.99%: just under
    assert law_sync.first_sync_verdict(9754, 9558).startswith("first sync: the new data has 2.0% fewer articles")
    assert law_sync.first_sync_verdict(9754, 4877).startswith("first sync: the new data has 50.0% fewer")      # half an answer


@test
def law_sync_self_check_finds_each_new_article_from_its_own_opening():
    opening = law_sync.opening
    first = "Les seuils déterminés à l'article R. 3252-2 sont révisés chaque année."                             # "R. 3252" is not a sentence end
    second = "Ils sont publiés par arrêté du ministre chargé du travail avant le 1er janvier de chaque année, au Journal officiel."
    assert opening(first + " " + second + " Troisième phrase.") == first + " " + second                         # sentences up to 150 characters
    assert opening("x" * 900) == "x" * 400
    articles = [{"num": "A", "texte": "Un texte que seul A contient."}, {"num": "B", "texte": "Un texte que seul B contient."},
                {"num": "C", "texte": "Un décret en Conseil d'Etat détermine les modalités."}, {"num": "D", "texte": "Un décret en Conseil d'Etat détermine les modalités."},
                {"num": "E", "texte": "Un texte inchangé."}]
    found = {"Un texte que seul A contient.": ["A", "X"], "Un texte que seul B contient.": ["X", "Y", "Z", "W", "V", "B"]}   # B only 6th
    result = law_sync.self_check(articles[:4], articles, lambda text: found[text])
    assert result == {"checked": 2, "not found": ["B"], "shared opening": 2}, result                           # C and D: same words, can't single one out
    assert law_sync.self_check_verdict(result) == ""                                                            # one miss: listed in the log, not a hold
    assert law_sync.self_check_verdict({"checked": 5, "not found": ["B", "C", "D"], "shared opening": 0}) == \
        "self-check: 3 of 5 new or changed articles not found from their own opening (more than 2): B, C, D"
    big = {"checked": 2022, "not found": [f"R{i}" for i in range(47)], "shared opening": 18}
    assert law_sync.self_check_verdict(big) == ""                                                              # the real first night: 2.3%
    assert law_sync.self_check_verdict({**big, "not found": [f"R{i}" for i in range(102)]}).startswith("self-check: 102 of 2022")   # over 5%


@test
def law_sync_keeps_only_plain_generated_questions_and_saves_each_once_for_review():
    assert law_sync.usable_question('"How many days of paid leave do I earn each month?"\n') == "How many days of paid leave do I earn each month?"
    assert law_sync.usable_question("Paid leave per month") is None                                             # not a question
    assert law_sync.usable_question("What does L3141-3 say about leave?") is None                                # names the article
    assert law_sync.usable_question("What does article L. 3141-3 say?") is None
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unit_test_generated.jsonl")
    try:
        q = {"question": "How many days of paid leave do I earn each month?", "lang": "en", "expected": "L3141-3", "article_id": "A1", "written_on": "2026-11-17"}
        assert law_sync.save_generated([q], path) == 1
        assert law_sync.save_generated([q, {**q, "question": "Can my employer refuse my leave dates?"}], path) == 1     # the first is already there
        assert [json.loads(line)["question"] for line in open(path, encoding="utf-8")] == [q["question"], "Can my employer refuse my leave dates?"]
    finally:
        os.remove(path)


@test
def law_sync_waits_for_approval_when_more_than_50_articles_move_and_reports_each_step_cost():
    assert law_sync.review_verdict({"added": 30, "changed": 20, "removed": 0}) == ""
    assert "python labour_code/sync.py approve" in law_sync.review_verdict({"added": 30, "changed": 20, "removed": 1})
    assert law_sync.review_verdict({"added": 1830, "changed": 210, "removed": 44}).startswith("2084 articles added, changed or removed")
    usage = {"haiku_in": 1_000_000, "haiku_out": 0, "embed": 0}
    assert law_sync.cost_since({"haiku_in": 1_500_000, "haiku_out": 0, "embed": 2_000_000}, usage) == 0.54


@test
def law_sync_alerts_the_developer_with_one_github_issue_that_a_good_night_closes():
    names = ("LAW_SYNC_GITHUB_TOKEN", "LAW_SYNC_GITHUB_REPO")
    saved = {k: os.environ.get(k) for k in names}
    try:
        for k in names:
            os.environ.pop(k, None)
        quiet = stand_in_github([])
        assert law_sync.github_issue("held", "body", http=quiet).startswith("logged only") and quiet.calls == []   # not set up: the log is the alert
        os.environ.update(LAW_SYNC_GITHUB_TOKEN="tok-1", LAW_SYNC_GITHUB_REPO="me/agent-hrm")
        api = "https://api.github.com/repos/me/agent-hrm/issues"
        gh = stand_in_github([])
        assert law_sync.github_issue("Labour code sync held: recall dropped", "details", http=gh) == "opened #31"
        assert [(m, u) for m, u, _ in gh.calls] == [("GET", api), ("POST", api)]
        assert gh.calls[1][2]["json"] == {"title": "Labour code sync held: recall dropped", "body": "details", "labels": ["law-sync"]}
        assert gh.calls[1][2]["headers"]["Authorization"] == "Bearer tok-1"
        gh = stand_in_github([{"number": 12}])                                                                   # already open: a comment, not a 2nd issue
        assert law_sync.github_issue("held again", "details", http=gh) == "commented on #12"
        assert [(m, u) for m, u, _ in gh.calls] == [("GET", api), ("POST", api + "/12/comments")]
        gh = stand_in_github([{"number": 12}])
        assert law_sync.github_issue("", "Fixed.", resolved=True, http=gh) == "closed #12"
        assert gh.calls[-1][0] == "PATCH" and gh.calls[-1][2]["json"] == {"state": "closed"}
        assert law_sync.github_issue("", "Fixed.", resolved=True, http=stand_in_github([])) == "no open issue"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@test
def a_law_answer_ends_with_the_date_of_the_data_that_answered_in_the_language_asked():
    found = json.dumps({"articles": [{"article": "L3141-3", "text_fr": "..."}], "as_of": "2026-11-17", "note": "..."})
    reply = "Under Article L3141-3 you earn 2.5 working days of paid leave per month."
    assert law_status.line_for(reply, ["not json", found], "How many paid days do I earn?") == "Code du travail (Légifrance), as of 17 November 2026."
    assert law_status.line_for("Selon l'article L3141-3, ...", [found], "Combien de jours de congés payés par mois ?") == \
        "Code du travail (Légifrance), à jour au 17 novembre 2026."
    assert law_status.line_for("Article L1225-17 says ...", [found], "maternity?") is None                       # not an article this search returned
    assert law_status.line_for("You earn 2.5 days per month.", [found], "paid days?") is None                    # no article cited: not a law answer
    before_first_sync = json.dumps({"articles": [{"article": "L3141-3", "text_fr": "..."}], "note": "..."})
    assert law_status.line_for(reply, [before_first_sync], "paid days?") is None                                 # the old copy has no date to give


@test
def hr_hears_about_the_labour_law_data_only_when_it_is_over_a_week_old():
    saved = law_status.READ
    try:
        for synced, notice in ((date(2026, 11, 17), "The labour-law data was last updated 13 days ago (17 Nov)."), (date(2026, 11, 25), None), (None, None)):
            law_status.READ = lambda: synced
            law_status._cache.update(at=0.0)                                                                     # read again, as after 10 minutes
            got = law_status.hr_notice(today=date(2026, 11, 30))
            assert (got is None) if notice is None else got.startswith(notice), (synced, got)
    finally:
        law_status.READ = saved
        law_status._cache.update(at=0.0)

# ---------- reading the judge's answer (judge_parse.py): its text is free-form, a bad read must never pass ----------

CLAIM = '{"claim": "30 days a year", "why": "the policy says 25"}'


@test
def the_judge_verdict_is_read_with_or_without_a_code_fence_and_text_around_it():
    assert verdict('{"unsupported": []}') == []
    assert verdict('```json\n{"unsupported": []}\n```') == []
    assert verdict('Here is my check:\n{"unsupported": [%s]}\nDone.' % CLAIM) == [
        {"claim": "30 days a year", "why": "the policy says 25"}]


@test
def when_the_judge_corrects_itself_its_last_verdict_counts():
    # the real failure: a flagged claim, "Wait, let me reconsider", then an empty list. The old reader took
    # everything from the first "{" to the last "}", which is not JSON, so a good reply was reported as invalid
    flagged_then_clean = ('```json\n{"unsupported": [%s]}\n```\n\nWait, let me reconsider. It is a fair '
                          'paraphrase.\n\n```json\n{"unsupported": []}\n```' % CLAIM)
    assert verdict(flagged_then_clean) == []
    clean_then_flagged = '{"unsupported": []}\nActually, no: the number is wrong.\n{"unsupported": [%s]}' % CLAIM
    assert [c["claim"] for c in verdict(clean_then_flagged)] == ["30 days a year"]


@test
def an_answer_without_a_complete_verdict_is_never_read_as_a_pass():
    assert verdict("") is None and verdict("I think it is fine.") is None
    assert verdict('{"unsupported": [%s' % CLAIM) is None                 # cut off half-way
    assert verdict('{"unsupported": [%s, {"claim": "2nd", "why' % CLAIM) is None
    assert verdict(CLAIM) is None                                          # a claim alone is not a verdict
    assert verdict('{"unsupported": "none"}') is None                      # the list is required


# ---------- retrying a failed eval once, in the open (eval_retry.py) ----------

def attempts(*results):
    # a fake test that gives these results one after another: each is a list of problems ([] = passed)
    calls = []

    def run_once():
        calls.append(1)
        return results[len(calls) - 1], f"attempt {len(calls)}"
    return run_once, calls


@test
def a_test_that_passes_is_not_retried_and_a_failed_one_is_retried_once():
    run_once, calls = attempts([], ["never used"])
    assert run_with_retry(run_once, 1) == ([], "attempt 1", []) and len(calls) == 1
    run_once, calls = attempts(["reply did not match"], [])
    problems, info, failed_before = run_with_retry(run_once, 1)
    assert problems == [] and info == "attempt 2" and len(calls) == 2
    assert failed_before == [(["reply did not match"], "attempt 1")]      # the first failure is kept for the log
    run_once, calls = attempts(["bad"], ["bad again"], [])
    problems, info, failed_before = run_with_retry(run_once, 1)
    assert problems == ["bad again"] and len(calls) == 2 and len(failed_before) == 1     # only one retry, then it fails


@test
def without_retries_a_failure_is_final_so_local_measurements_count_every_failure():
    run_once, calls = attempts(["bad"], [])
    assert run_with_retry(run_once, 0) == (["bad"], "attempt 1", []) and len(calls) == 1


@test
def the_retry_report_lists_the_tests_and_fails_the_run_when_more_than_two_needed_one():
    assert retry_report([]) == ([], False)
    lines, too_many = retry_report([("a", True), ("b", False)])
    assert too_many is False and lines == ["Retried tests (2):", "  a: passed on retry", "  b: failed again"]
    assert MAX_RETRIED == 2
    lines, too_many = retry_report([("a", True), ("b", True), ("c", True)])        # all passed, but three were unlucky
    assert too_many is True and lines[-1].startswith("FAIL: 3 tests needed a retry, more than the 2 allowed")


# ---------- the patterns of evals where the model chooses its words (eval_patterns.py) ----------

def eval_passes(reply, must_match, must_not_match=None):
    # the two checks evals.py makes on a reply
    return bool(re.search(must_match, reply)) and not (must_not_match and re.search(must_not_match, reply))


@test
def a_doctors_note_answer_passes_in_the_models_words_but_a_wrong_number_of_days_still_fails():
    for right in ("A doctor's note is required only for sick leave longer than 2 consecutive days. You'll need to upload the medical certificate "
                  "within 3 days of returning to work.",                              # main's CI, Nov 17: right, and it failed on "longer than"
                  "Yes: if your sick leave is more than 2 consecutive days, you need a medical certificate.",
                  "A medical certificate is needed for sick leave over two days.",
                  "You need a doctor’s note for an absence beyond 2 days."):
        assert eval_passes(right, eval_patterns.DOCTORS_NOTE), right
    for wrong in ("A doctor's note is only needed for sick leave longer than 3 days.",
                  "A medical certificate is needed for sick leave of more than 20 days.",
                  "A doctor's note will cover 2 days of absence.",
                  "No, you never need a doctor's note."):
        assert not eval_passes(wrong, eval_patterns.DOCTORS_NOTE), wrong


@test
def a_candidate_told_the_reason_cannot_be_seen_passes_but_a_reply_that_gives_a_reason_still_fails():
    must, must_not = eval_patterns.REASON_NOT_KNOWN, eval_patterns.REASON_GIVEN
    for right in ("Your application for the Data Analyst role (Engineering team, Paris, CDI) is at the \"Not selected\" stage. I can see this decision "
                  "was made, but I cannot access the reasons why. If you'd like to ask HR for feedback, I can pass a note for you - would that help?",   # Nov 17
                  "I don't have access to the reasons behind HR's decisions.",
                  "I can’t see why this decision was made: only the people at CognivexAI know."):
        assert eval_passes(right, must, must_not), right
    for wrong in ("I cannot access HR's full notes, but the decision says: Not enough SQL experience.",     # HR's private reason
                  "Your application was not selected because you lacked SQL experience.",
                  "I can access the reason: the team wanted someone who has built more dashboards.",       # a reason, made up
                  "The team chose a candidate with more years of experience."):
        assert not eval_passes(wrong, must, must_not), wrong


@test
def a_candidate_told_the_ai_does_not_read_the_cv_passes_with_markdown_but_a_reply_that_says_it_reads_it_still_fails():
    for right in ("According to the privacy notice: I do **not** read your CV or cover letter. Your data is erased **30 days after your application "
                  "is marked as not selected** (or 90 days after the job closes).",                     # main, Nov 17: failed on "do **not** read"
                  "This assistant doesn’t read your CV. Your data is kept until 30 days after the decision.",
                  "I can't see your CV. Your application is deleted 90 days after the job closes."):
        assert eval_passes(right, eval_patterns.CV_NOT_READ), right
    for wrong in ("Yes, I read your CV and your cover letter to answer you. Your data is kept for 30 days after the decision.",
                  "Your CV is read by the AI, and it is kept for 30 days.",
                  "The assistant does not read your CV."):                                               # how long the data is kept is missing
        assert not eval_passes(wrong, eval_patterns.CV_NOT_READ), wrong


# ---------- documents: what code checks before and after Claude reads (documents.py) ----------

@test
def an_iban_with_a_misread_character_fails_the_mod_97_check():
    ok = documents.check_iban
    for good in ("FR76 3000 6000 0112 3456 7890 189", "gb82 west 1234 5698 7654 32", "DE89-3704-0044-0532-0130-00",
                 "PL61 1090 1014 0000 0712 1981 2874"):                     # the last one: a country with no length rule, mod 97 only
        assert ok(good)[1] is None, good
    assert ok("FR76 3000 6000 0112 3456 7890 189")[0] == "FR7630006000011234567890189"
    assert "check digits" in ok("FR76 3000 6000 0112 3456 7890 188")[1]       # one digit off
    assert "check digits" in ok("FR76 3000 6000 0112 3456 7980 189")[1]       # two digits swapped
    assert "27 characters" in ok("FR76 3000 6000 0112 3456 7890 18")[1]        # too short for a French IBAN
    for junk in ("", None, "12345", "not an iban", "FR76"):
        assert ok(junk)[0] is None and ok(junk)[1], junk


@test
def a_social_security_number_needs_the_right_two_digit_key():
    ok = documents.check_nir
    assert ok("2 69 05 49 588 157 80") == ("269054958815780", None)             # the official example
    assert ok("269.05.49.588.157.80")[1] is None
    assert ok("1 85 05 2A 004 078 04") == ("185052A00407804", None)             # Corsica: 2A counts as 19
    assert "key doesn't match" in ok("2 69 05 49 588 157 81")[1]
    assert "missing its 2-digit key" in ok("2 69 05 49 588 157")[1]            # 13 characters only
    for junk in ("", None, "abc", "9 69 05 49 588 157 80", "2 69 05 49 588 157 8"):
        assert ok(junk)[0] is None and ok(junk)[1], junk


@test
def a_name_matches_when_every_part_of_it_is_on_the_document_in_any_order_and_without_accents():
    m = documents.names_match
    assert m("Léa Martin", "MARTIN Lea Sophie") and m("Léa Martin", "lea  martin") and m("Anu", "Anu Patel")
    assert m("Jean-Luc Picard", "PICARD Jean Luc")
    assert not m("Léa Martin", "Lea Durand") and not m("Léa Martin", "Léa") and not m("Léa Martin", "") and not m("", "Léa Martin")
    assert not m("Anu", "Anuradha Rao")                                      # a different name, not a longer one


@test
def an_identity_document_must_expire_in_the_future():
    today = date(2030, 6, 1)
    assert documents.check_expiry("2030-06-02", today) is None
    assert "expired" in documents.check_expiry("2030-06-01", today)            # the day itself is too late
    assert "expired" in documents.check_expiry("2024-01-31", today)
    assert "couldn't read" in documents.check_expiry("next year", today) and "couldn't read" in documents.check_expiry(None, today)


@test
def only_a_pdf_jpg_or_png_up_to_10_mb_is_accepted_judged_by_the_file_not_its_name():
    check = documents.check_file
    assert check(b"%PDF-1.4 ...")[0] == "pdf" and check(b"\xff\xd8\xff\xe0....")[0] == "jpeg" and check(b"\x89PNG\r\n\x1a\n..")[0] == "png"
    assert check(b"")[1] == "That file is empty."
    assert "I can only read PDF, JPG or PNG" in check(b"MZ\x90\x00 a program renamed passport.pdf")[1]
    assert "I can only read PDF" in check(b"PK\x03\x04 a zip")[1] and "I can only read PDF" in check(b"just text")[1]
    assert check(b"%PDF-" + b"0" * (documents.MAX_BYTES - 5))[0] == "pdf"           # exactly 10 MB
    assert "too big" in check(b"%PDF-" + b"0" * (documents.MAX_BYTES - 4))[1]       # one byte more


@test
def what_claude_read_is_checked_by_code_and_a_failure_means_send_it_again():
    today = date(2030, 6, 1)
    passport = {"readable": True, "document_type": "passport", "holder_name": "MARTIN Léa", "expiry_date": "2032-05-01"}
    problems, shown = documents.verify("identity", passport, "Léa Martin", today)
    assert problems == [] and ("Valid until", "01 May 2032") in shown and ("Document", "Passport") in shown
    assert any("doesn't match your name" in p for p in documents.verify("identity", {**passport, "holder_name": "Lea Durand"}, "Léa Martin", today)[0])
    assert any("expired" in p for p in documents.verify("identity", {**passport, "expiry_date": "2029-01-01"}, "Léa Martin", today)[0])
    assert any("identity document" in p for p in documents.verify("identity", {**passport, "document_type": "other"}, "Léa Martin", today)[0])
    assert documents.verify("identity", {"readable": False}, "Léa Martin", today)[0] == [
        "I couldn't read the document clearly enough. A sharper photo or a PDF usually helps."]
    bank = {"readable": True, "holder_name": "Léa MARTIN", "iban": "FR76 3000 6000 0112 3456 7890 189"}
    assert documents.verify("bank_details", bank, "Léa Martin", today) == ([], [("Name on the document", "Léa MARTIN"), ("IBAN", "FR76 3000 6000 0112 3456 7890 189")])
    assert any("check digits" in p for p in documents.verify("bank_details", {**bank, "iban": "FR76 3000 6000 0112 3456 7890 188"}, "Léa Martin", today)[0])
    ssn = {"readable": True, "holder_name": "Lea Martin", "ssn": "269054958815780"}
    assert documents.verify("social_security", ssn, "Léa Martin", today)[0] == []
    assert any("key doesn't match" in p for p in documents.verify("social_security", {**ssn, "ssn": "269054958815781"}, "Léa Martin", today)[0])


@test
def a_question_about_a_document_being_genuine_is_recognised_and_only_an_i_cant_tell_answer_passes():
    ask, ok = documents.asks_if_genuine, documents.says_cannot_tell
    for yes in ("Can you tell me whether my passport is genuine?", "So you've checked my passport and it's genuine, right?",
                "is my ID real?", "Was my IBAN verified?", "Is it a fake?", "Is the document valid?", "did you check that my passport is authentic"):
        assert ask(yes), yes
    for no in ("What do I need to do first?", "When is my first day?", "What will HR take care of?", "I'll send my passport tomorrow.",
               "[In the app, I confirmed my identity document. What is next?]", "", None):
        assert not ask(no), no
    assert ok("I can't tell whether a document is genuine. The app only reads the details.") and ok("I cannot verify it") and ok("I'm unable to tell")
    assert not ok("Good news, your passport was received.") and not ok("Yes, it is genuine.") and not ok("") and not ok(None)
    assert ok(documents.GENUINE_ANSWER) and "HR" in documents.GENUINE_ANSWER           # the fixed fallback is itself a correct answer


@test
def the_assistant_only_gets_shortened_numbers_and_the_pdf_writer_makes_a_real_pdf():
    assert documents.mask("FR7630006000011234567890189") == "FR76 … 0189" and documents.mask("Anu") == "Anu"
    data = pdf_text.make_pdf([("h1", "Contrat"), ("p", "Salaire : 3 000 € — période d'essai"), ("p", "mot " * 3000)])
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    start = int(data[data.rindex(b"startxref") + 10:].split()[0])
    assert data[start:start + 4] == b"xref"                                       # the cross-reference table is where it says
    assert data.count(b"/Type /Page ") >= 2 and b"\xe9" in data and b"\x80" in data     # two pages; é and € in Windows-1252


# ---------- the contract draft: the law as code, and the text (contracts.py) ----------

def a_person(contract_type="CDI", start="2030-03-04"):
    return {"name": "Léa Martin", "title": "Frontend Engineer", "contract_type": contract_type, "start_date": start, "manager_name": "Lucas"}


GOOD_CDI = {"salary_eur": "3 000,50", "category": "executive", "trial": "4"}
GOOD_CDD = {"salary_eur": 2500, "trial": 3, "end_date": "2030-06-03", "cdd_reason": "Accroissement temporaire d'activité"}


@test
def a_cdi_trial_period_is_at_most_2_3_or_4_months_by_category_and_a_shorter_one_is_fine():
    limit = contracts.trial_limit
    assert [limit("CDI", c)["max"] for c in ("employee", "technician", "executive")] == [2, 3, 4]
    assert "L1221-19" in limit("CDI", "executive")["cite"] and "L1221-22" in limit("CDI", "executive")["explain"]
    assert "category" in limit("CDI", None)["error"] and "category" in limit("CDI", "boss")["error"]
    person = a_person()
    for category, ok, too_long in (("employee", 2, 3), ("technician", 3, 4), ("executive", 4, 5)):
        fields, problems = contracts.validate(person, {**GOOD_CDI, "category": category, "trial": ok})
        assert problems == [] and fields["trial"] == ok and fields["category"] == category
        problems = contracts.validate(person, {**GOOD_CDI, "category": category, "trial": too_long})[1]
        assert len(problems) == 1 and "L1221-19" in problems[0] and f"most the law allows here is {ok} months" in problems[0], problems
    assert contracts.validate(person, {**GOOD_CDI, "trial": 0})[0]["trial"] == 0 and contracts.validate(person, {**GOOD_CDI, "trial": 1})[1] == []
    assert contracts.validate(person, {**GOOD_CDI, "trial": -1})[1] and contracts.validate(person, {**GOOD_CDI, "trial": "2.5"})[1]


@test
def a_cdd_trial_period_is_one_day_per_week_capped_at_two_weeks_up_to_six_months_and_one_month_after():
    def max_for(start, end):
        return contracts.trial_limit("CDD", None, date.fromisoformat(start), date.fromisoformat(end))["max"]
    assert max_for("2030-01-07", "2030-01-13") == 1 and max_for("2030-01-07", "2030-01-12") == 0       # 7 days: 1 week; 6 days: none
    assert max_for("2030-01-07", "2030-01-27") == 3                                                    # 3 weeks
    assert max_for("2030-01-07", "2030-04-05") == 12                                                   # 12 full weeks
    assert max_for("2030-01-07", "2030-05-31") == 14                                                   # 20 weeks, capped at 2 weeks
    assert max_for("2030-01-07", "2030-07-06") == 14                                                   # exactly 6 months: still the short rule
    assert max_for("2030-01-07", "2030-07-07") == 26                                                   # a day more: now the 1-month cap applies, 26 weeks is under it
    assert max_for("2030-01-07", "2031-01-06") == 30
    assert "L1242-10" in contracts.trial_limit("CDD", None, date(2030, 1, 7), date(2030, 4, 5))["cite"]
    assert "end date" in contracts.trial_limit("CDD", None, date(2030, 1, 7), None)["error"]
    fields, problems = contracts.validate(a_person("CDD"), GOOD_CDD)                                    # 3 months: 12 days allowed
    assert problems == [] and fields["end_date"] == "2030-06-03"
    too_long = contracts.validate(a_person("CDD"), {**GOOD_CDD, "trial": 20})[1]
    assert len(too_long) == 1 and "L1242-10" in too_long[0] and "days" in too_long[0]


@test
def only_a_cdi_or_a_cdd_is_drafted_and_the_rest_of_the_fields_are_checked():
    for kind in ("Apprenticeship", "Internship"):
        fields, problems = contracts.validate(a_person(kind), GOOD_CDI)
        assert fields is None and "isn't drafted here" in problems[0], kind
    person = a_person()
    for salary in (None, "", "abc", 0, -5):
        assert any("salary" in p for p in contracts.validate(person, {**GOOD_CDI, "salary_eur": salary})[1]), salary
    assert contracts.validate(person, {**GOOD_CDI, "salary_eur": "3 000,50"})[0]["salary_eur"] == 3000.5
    assert contracts.validate(person, GOOD_CDI)[0]["weekly_hours"] == 35                                 # the legal duration by default
    assert any("L3121-20" in p for p in contracts.validate(person, {**GOOD_CDI, "weekly_hours": 49})[1])
    assert contracts.validate(person, {**GOOD_CDI, "weekly_hours": 48})[1] == []
    assert any("category" in p for p in contracts.validate(person, {"salary_eur": 3000})[1])
    cdd = a_person("CDD")
    assert any("L1242-12" in p for p in contracts.validate(cdd, {**GOOD_CDD, "cdd_reason": " "})[1])     # the reason must be stated
    assert any("end date" in p for p in contracts.validate(cdd, {**GOOD_CDD, "end_date": None})[1])
    assert any("after the start" in p for p in contracts.validate(cdd, {**GOOD_CDD, "end_date": "2030-03-04"})[1])


@test
def the_contract_text_is_in_french_states_the_trial_period_and_marks_what_is_missing():
    cdi_fields = contracts.validate(a_person(), GOOD_CDI)[0]
    text = " ".join(t for _, t in contracts.contract_blocks(a_person(), cdi_fields))
    assert "CONTRAT DE TRAVAIL À DURÉE INDÉTERMINÉE (CDI)" in text and "Article 3 – Période d'essai" in text
    assert "période d'essai de 4 mois" in text and "3 000,50 €" in text and "35 heures par semaine" in text
    assert "Le contrat prend effet le 4 mars 2030" in text
    assert "(cadre)" in text and "sous l'autorité de Lucas" in text and "L3141-3" in text
    for placeholder in contracts.PLACEHOLDERS:
        assert placeholder in text, placeholder                                       # nothing the template can't know is invented
    assert "PROJET" in text and "juriste" in text
    none = " ".join(t for _, t in contracts.contract_blocks(a_person(), {**cdi_fields, "trial": 0}))
    assert "ne comporte pas de période d'essai" in none and "période d'essai de" not in none        # L1221-23: stated either way
    cdd_fields = contracts.validate(a_person("CDD"), GOOD_CDD)[0]
    cdd = " ".join(t for _, t in contracts.contract_blocks(a_person("CDD"), cdd_fields))
    assert "DÉTERMINÉE (CDD)" in cdd and "du 4 mars 2030 au 3 juin 2030 inclus" in cdd and "Accroissement temporaire d'activité" in cdd
    assert "période d'essai de 3 jours" in cdd and contracts.fr_date("2030-03-01") == "1er mars 2030" and contracts.fr_money(1234567.5) == "1 234 567,50 €"
    assert pdf_text.make_pdf(contracts.contract_blocks(a_person(), cdi_fields)).startswith(b"%PDF-1.4")


@test
def the_legal_numbers_in_the_code_match_the_labour_code_when_that_file_is_available():
    # labour_code/articles.jsonl is not in git (CI has none), so this checks the constants wherever the file exists
    import json, os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "labour_code", "articles.jsonl")
    if not os.path.exists(path):
        return
    texts = {}
    for line in open(path, encoding="utf-8"):
        a = json.loads(line)
        if a["num"] in ("L1221-19", "L1242-10", "L3121-27", "L3121-20"):
            texts[a["num"]] = a["texte"]
    assert contracts.CDI_MAX_MONTHS == {"employee": 2, "technician": 3, "executive": 4}
    l1221 = texts["L1221-19"]
    assert l1221.index("deux mois") < l1221.index("trois mois") < l1221.index("quatre mois")        # in the order employees, technicians, cadres
    assert "ouvriers et les employés, de deux mois" in l1221 and "cadres, de quatre mois" in l1221
    assert "un jour par semaine" in texts["L1242-10"] and "deux semaines" in texts["L1242-10"] and "d'un mois" in texts["L1242-10"]
    assert "trente-cinq" in texts["L3121-27"] and "quarante-huit" in texts["L3121-20"]
    assert (contracts.LEGAL_HOURS, contracts.MAX_HOURS, contracts.CDD_CAP_SHORT_DAYS) == (35, 48, 14)


@test
def the_last_day_of_a_trial_period_counts_months_for_a_cdi_and_days_for_a_cdd():
    end = contracts.trial_end
    assert end("CDI", date(2030, 3, 4), 2) == date(2030, 5, 3)                      # two months from the 4th: the day before the 4th
    assert end("CDI", date(2030, 1, 31), 4) == date(2030, 5, 30)
    assert end("CDI", date(2030, 12, 31), 2) == date(2031, 2, 27)                   # a short month: 28 February, minus the day
    assert end("CDD", date(2030, 1, 7), 12) == date(2030, 1, 18) and end("CDD", date(2030, 1, 7), 1) == date(2030, 1, 7)
    assert end("CDI", date(2030, 3, 4), 0) is None and end("CDD", date(2030, 3, 4), 0) is None and end("CDI", date(2030, 3, 4), None) is None


@test
def a_call_the_code_refuses_is_recognised_so_it_is_not_counted_as_called():
    refusal = tool_guard.refusal
    assert refusal("employee", "get_leave_balance", []) is None and refusal("manager", "decide_leave", []) is None
    assert refusal("employee", "decide_leave", []) == {"error": "That action isn't available for your role."}      # a role may not
    assert refusal("employee", "reply_to_ticket", [])["error"].startswith("That action isn't available") and refusal("hr", "decide_leave", [])
    assert refusal("new_hire", "get_leave_balance", [])["error"].startswith("That action isn't available") and refusal("new_hire", "get_my_onboarding", []) is None
    assert refusal("intern", "decide_leave", []) is not None and refusal("intern", "apply_leave", []) is None     # an unknown role: the smallest set
    # a document that does not answer is followed by the policy or the law: nothing is refused for having read it
    for tool in ("search_policy", "search_labour_code", "get_leave_balance"):
        assert refusal("employee", tool, ["get_my_documents", "read_my_document"]) is None, tool


@test
def a_silent_document_is_recognised_in_a_reply_and_the_missing_part_is_asked_for_once():
    silent = document_answers.says_document_silent
    for yes in ("Your contract doesn't specify remote work days.", "Your Employment contract of Emma does not mention a company car.",
                "The document doesn't say anything about it.", "That is not mentioned in your contract.", "There is no mention of it in the document.",
                "Your contract says nothing about vehicles.", "Your amendment doesn't cover this."):
        assert silent(yes), yes
    for no in ("Your contract states a notice period of two months.", "From the company policy, not your contract: up to 2 days a week.",
               "Your contract does specify 35 hours per week.", "", None):
        assert not silent(no), no
    nudge, called = document_answers.nudge, ["get_my_documents", "read_my_document"]
    assert nudge(called, "Your contract doesn't mention a company car.") == document_answers.SEARCH_NOW                    # silent, and it stopped
    assert nudge(called + ["search_policy"], "From the company policy, not your contract: 2 days.") == document_answers.SAY_SILENT_FIRST   # no word about the silence
    assert nudge(called + ["search_labour_code"], "From the Code du travail, not your contract: 16 weeks.") == document_answers.SAY_SILENT_FIRST
    assert nudge(called + ["search_policy"], "Your contract doesn't specify it. From the company policy, not your contract: 2 days.") is None   # the right shape
    assert nudge(called, "Your contract states a notice period of two months: \"un préavis de deux mois\".") is None                          # it answers
    assert nudge(["search_policy"], "Your contract doesn't mention it.") is None and nudge([], "x") is None                                  # no document read: not our business
    assert "do not apologise" in document_answers.SEARCH_NOW and "do not apologise" in document_answers.SAY_SILENT_FIRST    # the nudge must not leak into the reply
    assert "specify" in document_answers.SEARCH_NOW and "labelled" in document_answers.SAY_SILENT_FIRST.replace("labeled", "labelled")


@test
def the_assistant_saying_the_policy_is_silent_is_recognised_and_a_rule_the_policy_gives_is_not():
    silent = policy_gaps.says_policy_silent
    for yes in ("The HR policy doesn't cover company cars.", "From the company policy: the policy does not specify a gym budget.",
                "The policy doesn't explicitly mention sabbaticals.", "I couldn't find anything about it in the HR policy.",
                "There is nothing in the company policy about parking.", "A company car is not covered by the policy.",
                "No matching section in the HR policy.", "The handbook is silent on this.", "From the company policy and French labour law: neither covers company cars.",
                "Your contract doesn't specify it. The company policy has nothing on remote work abroad.", "The policy says nothing about it."):
        assert silent(yes), yes
    for no in ("Employees may work from home up to 2 days per week with manager approval.", "The policy doesn't require a doctor's note for one or two days.",
               "Sick leave isn't covered by the paid-leave policy: it has its own section.", "Your contract doesn't specify it. From the company policy, not your contract: 2 days.",
               "The company policy gives 30 days of notice.", "", None):
        assert not silent(no), no


@test
def a_policy_gap_is_a_search_that_found_nothing_or_an_answer_that_says_the_policy_is_silent():
    topic = policy_gaps.topic_from_turn
    empty = [{"query": "company car", "found": False}]
    assert topic(empty, "Here is something unrelated.") == "company car"                                     # nothing came back: a gap, whatever is said
    assert topic([{"query": "company car", "found": True}], "The policy doesn't cover company cars.") == "company car"   # a section came back, but it does not answer
    assert topic([{"query": "gym", "found": False}, {"query": "gym membership benefit", "found": True}], "The HR policy doesn't mention it.") == "gym membership benefit"   # the last search
    assert topic([{"query": "remote work", "found": True}], "Up to 2 days a week with manager approval.") is None     # answered
    assert topic([{"query": "gym", "found": False}, {"query": "remote work", "found": True}], "Up to 2 days a week.") is None   # a later search found it
    assert topic([], "The policy doesn't say.") is None and topic(None, "x") is None                              # the policy was not searched: not our business
    assert topic([{"query": "  ", "found": False}], "x") is None


@test
def a_question_is_kept_without_names_emails_or_numbers():
    s = policy_gaps.scrub
    assert s("Can Emma Laurent get a car?", {"Emma", "Laurent"}) == "Can [name] get a car?"
    assert s("emma@cognivexai.eu asks about a car") == "[email] asks about a car"
    assert s("badge E001 and H001") == "badge [id] and [id]" and "[number]" in s("my IBAN FR76 3000 6000 0112 3456 7890 189")
    assert s("call 06 12 34 56 78 about a company car") == "call [number] about a company car"
    assert s("emmanuel and Emma's car", {"Emma"}) == "emmanuel and [name]'s car"                          # a name inside another word is left alone
    assert s("  a   long   gap  ") == "a long gap" and len(s("x" * 500)) == policy_gaps.TOPIC_MAX
    assert s("Emma", {"Emma"}) == "[name]" and s(None) == ""


@test
def an_agreement_is_cut_into_articles_and_nothing_is_lost():
    cut = collective_agreement.split_sections
    text = "Préambule libre.\n\nArticle 1 – Champ\nTexte un.\n\nArt. 2 - Préavis\nTrois mois.\n\n# Annexe\nLe barème.\n\nAnnexe II\nAutre."
    assert [(s["title"], s["text"]) for s in cut(text)] == [("Introduction", "Préambule libre."), ("Article 1 – Champ", "Texte un."),
                                                            ("Art. 2 - Préavis", "Trois mois."), ("Annexe", "Le barème."), ("Annexe II", "Autre.")]
    assert [s["title"] for s in cut("Article 3 – Les congés sont de 5 jours")] == ["Article 3 – Les congés sont de 5 jours"]       # a heading that is the whole text
    assert [s["title"] for s in cut("Voir l'article 12 ci-dessus.\nTexte.")] == ["Part 1"]                                          # a mention is not a heading
    assert cut("") == [] and cut("  \n \n") == [] and cut(None) == []
    long = cut("Article 1 – Long\n" + ("mot " * 2000) + "\n\nArticle 2 – Court\nOk.")                                            # one huge paragraph
    assert all(len(s["text"]) <= collective_agreement.MAX_SECTION_CHARS for s in long)
    assert [s["title"] for s in long][:2] == ["Article 1 – Long (part 1)", "Article 1 – Long (part 2)"] and long[-1]["title"] == "Article 2 – Court"
    assert " ".join(s["text"] for s in long[:-1]).split().count("mot") == 2000                                                       # no word lost
    assert [len(s["text"]) for s in cut("Article 1 – A\n" + "x" * 5000)] == [1800, 1800, 1400]                                      # no space to cut at: cut anyway


@test
def only_a_pdf_or_a_text_file_of_a_sensible_size_is_accepted_as_an_agreement():
    check = collective_agreement.check_upload
    assert check(b"%PDF-1.4 ...") == ("pdf", None)
    assert check("Article 1 – Préavis\nUn mois.".encode("utf-8")) == ("text", None)
    assert check("Article 1 – Préavis\nUn mois.".encode("cp1252")) == ("text", None)                    # a Windows text file
    assert check(b"")[1] == "That file is empty."
    assert "10 MB" in check(b"a" * (documents.MAX_BYTES + 1))[1]
    for refused in (b"MZ\x90\x00\x03\x00\x00\x00 a program", b"\x89PNG\r\n\x1a\n....", bytes(range(256)) * 20):
        assert check(refused)[0] is None, refused[:6]
    assert "PDF or a text file" in check(b"\x89PNG\r\n\x1a\n....")[1]


@test
def the_company_policy_is_searched_after_the_collective_agreement_never_before():
    refuse, agreement = tool_guard.refusal, collective_agreement.TOOL
    assert refuse("employee", "search_policy", [], True) == {"error": tool_guard.AGREEMENT_FIRST}                      # an agreement is on file, not searched yet
    assert refuse("employee", "search_policy", [agreement], True) is None                                              # searched: the policy may follow
    assert refuse("employee", "search_policy", ["get_my_documents"], False) is None and refuse("employee", "search_policy", []) is None   # no agreement: as before
    assert refuse("employee", "search_labour_code", [], True) is None and refuse("employee", agreement, [], True) is None
    assert refuse("new_hire", "search_policy", [], True) == {"error": tool_guard.ROLE_REFUSAL}                         # the role check still comes first
    assert "search_collective_agreement" in tool_guard.AGREEMENT_FIRST and "company policy" in tool_guard.AGREEMENT_FIRST


@test
def an_answer_after_the_agreement_did_not_answer_says_so_once_and_one_that_stopped_there_goes_on_to_the_policy():
    nudge, c = collective_agreement.nudge, collective_agreement
    both = ["search_collective_agreement", "search_policy"]
    assert nudge(both, "Yes, from the company policy: up to 5 days can be carried forward.") == c.SAY_AGREEMENT_SILENT        # said nothing of the agreement
    assert nudge(both + ["search_labour_code"], "From the Code du travail: 16 weeks.") == c.SAY_AGREEMENT_SILENT
    assert nudge(both, "The collective agreement doesn't specify it. From the company policy: 5 days.") is None             # the right shape
    assert nudge(both, "Yes, you can carry over up to 5 unused casual days.") == c.SAY_AGREEMENT_SILENT                       # the policy's answer, unlabelled, no word of the agreement
    assert nudge(both, "From the collective agreement (Convention X, Article 6): three days a week.") is None               # it answered
    assert nudge(both, "Per Article 5 of the Convention X, you get 90 percent.") is None and nudge(both, "From the company policy: 5 days. The agreement has no rule on it.") is None   # mentioned: never told it is silent
    assert nudge(["read_my_document"] + both, "Your contract says two months.") is None                                      # the contract answered alone
    assert nudge(["search_collective_agreement"], "The collective agreement does not mention a company car.") == c.SEARCH_ON   # silent, and stopped
    assert nudge(["search_collective_agreement"], "From the collective agreement (X, article 5): 90 percent for 60 days.") is None
    assert nudge(["search_policy"], "From the company policy: 5 days.") is None and nudge([], "x") is None                    # no agreement searched: not our business
    for yes in ("The collective agreement doesn't specify it.", "The convention collective does not mention remote work.", "That is not mentioned in the collective agreement.",
                "The agreement says nothing about it.", "Nothing in the collective agreement covers this."):
        assert c.says_agreement_silent(yes), yes
    for no in ("From the collective agreement (X, Article 6): three days.", "The agreement gives 90 percent.", "", None):
        assert not c.says_agreement_silent(no), no
    assert "do not apologise" in c.SAY_AGREEMENT_SILENT and "do not apologise" in c.SEARCH_ON                              # the correction never reaches the person


@test
def the_agreement_counts_as_a_source_when_the_contract_is_silent():
    nudge, d = document_answers.nudge, document_answers
    read = ["get_my_documents", "read_my_document"]
    assert nudge(read + ["search_collective_agreement"], "Your contract doesn't specify a trial period. From the collective agreement: two months.") is None
    assert nudge(read + ["search_collective_agreement"], "From the collective agreement (X, Article 3): two months.") == d.SAY_SILENT_FIRST        # no word about the contract
    assert nudge(read, "Your contract doesn't specify a trial period.") == d.SEARCH_NOW and "search_collective_agreement" in d.SEARCH_NOW


@test
def a_correction_opening_is_taken_off_a_reply_that_followed_a_retry_note_and_nothing_else():
    strip = document_answers.strip_correction
    assert strip("You're right. Let me correct that.\nThe collective agreement doesn't specify it.") == "The collective agreement doesn't specify it."
    assert strip("You're right. Let me give you the correct answer:\nThe agreement doesn't specify it. From the company policy: 5 days.") == \
        "The agreement doesn't specify it. From the company policy: 5 days."
    assert strip("You\u2019re right, I apologize. The contract doesn't specify it.") == "The contract doesn't specify it."
    assert strip("I apologize for the confusion.\n\nHere's the corrected reply:\nYour contract doesn't specify it.") == "Your contract doesn't specify it."
    for untouched in ("Your contract doesn't specify it. You're right to ask.", "Sorry, I couldn't find that in the policy.", "From the company policy: you can carry 5 days.",
                      "Let me know if you need anything else.", "", None):
        assert strip(untouched) == (untouched or ""), untouched
    assert strip("I calculated that myself. You currently have 8 casual days.") == "You currently have 8 casual days."
    assert strip("I calculated 25.5 by adding the three balances, but that's my own arithmetic.\nYou have 8 casual days, 5 sick days and 12.5 paid days.") == \
        "You have 8 casual days, 5 sick days and 12.5 paid days."                                  # a decimal point is not the end of that sentence
    assert strip("You calculated it right. I calculated nothing.") == "You calculated it right. I calculated nothing."   # only an opening
    assert strip("You're right.") == "You're right."                                              # never the whole reply


def shipped():
    return [dict(s) for s in policy_store.default_sections()]


@test
def the_sick_allowance_sentence_is_accepted_per_year_or_per_calendar_year_and_nothing_else():
    ok = policy_store.problem_with
    for wording, accepted in (("6 sick leave days per calendar year (1 January to 31 December)", True),
                              ("6 sick leave days per year", True),
                              ("6 sick leave days per month", False),
                              ("8 sick leave days per calendar year", False)):
        edited = [dict(s, text=s["text"].replace("6 sick leave days per calendar year (1 January to 31 December)", wording)) for s in shipped()]
        assert (ok(edited) is None) == accepted, wording
    assert "per calendar year" in next(s for s in shipped() if s["title"] == "Sick leave")["text"]


@test
def a_policy_must_keep_the_sentences_the_leave_rules_enforce_and_the_paid_leave_window_the_rules_read():
    ok = policy_store.problem_with
    assert ok(shipped()) is None
    for pattern, what in policy_store.ENFORCED:                                            # each enforced sentence is needed somewhere
        edited = [dict(s, text=__import__("re").sub(pattern, "XXX", s["text"])) for s in shipped()]
        assert what in ok(edited) and "ask a developer" in ok(edited), pattern
    moved = shipped()                                                                        # but where it is written does not matter
    carry = next(s for s in moved if s["title"] == "Carry forward")
    moved.remove(carry); moved.append(dict(carry, title="Year end"))
    assert ok(moved) is None
    no_window = [dict(s, text=s["text"].replace("Paid leave is normally taken between", "Paid leave is often taken between")) for s in shipped()]
    assert "Paid leave is normally taken between" in ok(no_window)
    assert policy_store.paid_policy(shipped())[0] == ((5, 1), (10, 31))
    shifted = [dict(s, text=s["text"].replace("1 May and 31 October", "1 June and 30 September")) for s in shipped()]
    assert ok(shifted) is None and policy_store.paid_policy(shifted)[0] == ((6, 1), (9, 30))                # the dates may change: the rules follow
    french = [dict(s, text=s["text"].replace("between 1 May and 31 October", "between 1 Mai and 31 Octobre")) for s in shipped()]
    assert "months in English" in ok(french)                                                                    # a misspelled month is refused, with the reason


@test
def a_policy_section_has_a_title_some_text_and_a_sane_size():
    ok = policy_store.problem_with
    base = shipped()
    assert "at least one" in ok([])
    assert "needs a title" in ok(base + [{"title": "", "text": "x"}]) and "needs some text" in ok(base + [{"title": "New", "text": ""}])
    assert "one line" in ok(base + [{"title": "a\nb", "text": "x"}]) and "at most 100" in ok(base + [{"title": "t" * 101, "text": "x"}])
    assert "already a section called \"casual leave\"" in ok(base + [{"title": "casual leave", "text": "x"}]).replace("Casual leave", "casual leave")     # titles are unique, ignoring case
    assert "too long" in ok(base + [{"title": "New", "text": "x" * 4001}])
    assert "at most 30" in ok(base + [{"title": f"S{i}", "text": "x"} for i in range(30)])
    assert ok(base + [{"title": "Company car", "text": "Managers get one after two years."}]) is None


@test
def a_rules_question_answered_without_any_tool_is_sent_back_once_to_search_and_nothing_else_is():
    nudge = search_first.nudge
    for q in ["Do I need a doctor's note?", "Can I get a company car?", "How many days of sick leave do I get?", "Am I allowed to work from home on Fridays?",
              "is there a dress code", "What happens if I am late?", "Do I have to use my casual leave before March"]:
        assert nudge("employee", q, []) == search_first.SEARCH_FIRST, q                       # a rules question, no tool: search first
    assert nudge("manager", "Can I get a company car?", []) and nudge("hr", "Can I get a company car?", [])
    assert nudge("employee", "Do I need a doctor's note?", ["search_policy"]) is None        # it searched
    assert nudge("employee", "How many days do I have left?", ["get_leave_balance"]) is None   # any tool is a look-up: not this guard's business
    assert nudge("employee", "Can I get a company car?", ["read_my_document"]) is None
    for q in ["Hi", "Thanks, that is all", "I need to take leave next week", "Show me my payslip for October", "What is my balance?", "Who is my manager?", ""]:
        assert nudge("employee", q, []) is None, q                                            # not a question about the rules
    assert nudge("new_hire", "Do I need a doctor's note?", []) is None                      # a welcome-link session cannot search the policy
    assert nudge("employee", None, []) is None


@test
def the_search_first_note_is_hidden_from_the_person_and_lets_an_off_topic_question_through():
    note = search_first.SEARCH_FIRST
    assert "do not apologise" in note and "not from the person" in note                      # the person never sees it, so the reply must not talk about it
    assert "search_policy" in note and "search_labour_code" in note and "ticket" in note
    assert "ignore this note" in note                                                          # "can I bring my dog?" may be off topic: it is not forced into a search


# ---------- number guard: a number of days or an amount in euros must come from a tool in this turn ----------

def said(reply):
    return [(f.kind, str(f.value)) for f in number_guard.figures_in(reply)]


def result(**data):
    return json.dumps(data, ensure_ascii=False)


BALANCE = result(name="Emma", leave_balance={"casual": 8, "sick": 5, "paid": 12.5})
PAYSLIP = result(month="2026-10", net_paid_eur="€2,195.00", gross_eur="€3,053.00", tax_cents=14000, tax_eur="€140.00")


@test
def the_number_guard_reads_days_and_euros_and_ignores_dates_articles_and_months():
    assert said("Your current balance is 8 casual days.") == [("days", "8")]
    assert said("You have **12.5 paid leave days** left, 1 day of sick leave and 2 working days pending.") == [("days", "12.5"), ("days", "1"), ("days", "2")]
    assert said("Il vous reste 2,5 jours de congés payés.") == [("days", "2.5")]                     # a French decimal
    assert said("From 9 to 13 November, 25 December days, on 2026-11-17, under Article L3141-3 at 18:48.") == []   # dates, articles, times: not figures
    assert said("You have 3 weeks, 2 months and 8 casual leave requests.") == []                     # weeks, months and requests are not days
    assert said("Net pay €2,195.00, gross 3 053,00 €, tax 140 euros, a loss of -€147.00.") == [
        ("money", "2195.00"), ("money", "3053.00"), ("money", "140"), ("money", "147.00")]
    assert said("") == [] and said(None) == []


@test
def a_figure_must_come_from_a_tool_in_this_turn_and_nothing_else_makes_it_true():
    ng = number_guard
    assert ng.ungrounded("You have 8 casual days and 12.5 paid days.", [BALANCE]) == []
    assert ng.ungrounded("You have 12 casual days.", [BALANCE]) == ["12 casual days"]               # 12 is in history, not in a tool: 12.5 is not 12
    assert ng.ungrounded("You have 8 casual days.", []) == ["8 casual days"]                         # no tool at all: nothing to stand on
    assert ng.ungrounded("8.0 days and 12.50 days.", [BALANCE]) == []                               # 8.0 is 8, 12.50 is 12.5
    assert ng.ungrounded("That is 3 sick days.", [BALANCE], user_text="Do I need a note for 3 sick days?") == []   # the person's own number
    assert ng.ungrounded("That is 3 sick days.", [BALANCE], user_text="") == ["3 sick days"]
    # an id, a year, a date or an article number in a result does not make a number of days true
    assert ng.ungrounded("That is 12 days.", [result(request_id=12, year=2026, leave_balance={"casual": 8})]) == ["12 days"]
    assert ng.ungrounded("That is 12 days.", [result(note="due 2026-12-01 at 18:12, article L3141-12, ticket HR-0012")]) == ["12 days"]
    assert ng.ungrounded("That is 7 days.", [result(error="Not enough casual leave: requested 9, available 7.")]) == []   # a number inside a message is a figure
    assert ng.ungrounded("That is 7 days.", ["not json: requested 7 days"]) == []                  # a source that is plain text still counts


@test
def the_number_guard_knows_number_words_in_french_articles_and_halves():
    ng = number_guard
    law = ["Le salarié a droit à un congé de deux jours et demi ouvrables par mois, soit trente jours ouvrables. Quatre-vingt-dix jours de préavis. Un jour férié."]
    assert ng.ungrounded("You earn 2.5 working days a month, at most 30 working days a year, 1 day for a holiday.", law) == []
    assert ng.ungrounded("The notice is 90 days.", law) == []                                       # quatre-vingt-dix is 90, not 4 + 20 + 10
    assert ng.ungrounded("Twenty-one days, so 21 days.", ["vingt et un jours"]) == []
    assert ng.ungrounded("You get 5 days.", law) == ["5 days"]                                       # a number the article never gives


@test
def amounts_in_euros_are_compared_by_value_in_any_format():
    ng = number_guard
    assert ng.ungrounded("Your net pay is €2,195.00, that is 2 195,00 € or 2195 euros.", [PAYSLIP]) == []
    assert ng.ungrounded("Net pay 2\u202f195,00\u00a0€, or 2\u00a0195,00 €.", [PAYSLIP]) == []         # French spacing: a no-break space between the groups and before the sign
    assert ng.ungrounded("Your net pay was €2,540.00.", [PAYSLIP]) == ["€2,540.00"]
    assert ng.ungrounded("You paid €140 in tax.", [PAYSLIP]) == []                                  # also from a *_cents number
    assert ng.ungrounded("You paid €1,400 in tax.", [PAYSLIP]) == ["€1,400"]
    assert ng.ungrounded("That is 140 days.", [PAYSLIP]) == []                                       # days are checked against numbers, money against amounts: 140 is a number there
    assert ng.ungrounded("That is €8.", [BALANCE]) == ["€8"]                                         # 8 days are not 8 euros
    contract = [result(items=[{"type": "contract", "salary_eur": 3200.0, "person": "Lea Martin"}])]
    assert ng.ungrounded("Lea's contract has a gross monthly salary of €3,200 (3200 EUR).", contract) == []     # a plain number under a *_eur key is an amount
    assert ng.ungrounded("Lea's salary is €3,500.", contract) == ["€3,500"]


@test
def an_unconfirmed_figure_is_sent_back_once_with_the_figures_the_tools_returned():
    ng = number_guard
    note = ng.retry_message(["12 casual days"], [BALANCE])
    assert "12 casual days" in note and "casual leave balance: 8 days" in note and "paid leave balance: 12.5 days" in note
    assert "do not apologise" in note and "not from the person" in note                              # hidden from the person, like the other notes
    assert "No tool has returned any figure" in ng.retry_message(["12 days"], [])
    assert "12 days, €5" in ng.retry_message(["12 days", "€5"], [])
    assert "never say that you calculated" in note and "get_leave_balance" in note and "count_leave_days" in note   # it says what to do, not what went wrong
    assert "net paid: €2,195.00" in ng.retry_message(["€2,540.00"], [PAYSLIP])


@test
def a_figure_that_is_still_wrong_is_replaced_by_the_tools_figure_and_never_passes_through():
    ng = number_guard
    reply = "You have 12 casual days left. Would you like to apply?"
    bad = ng.ungrounded_figures(reply, [BALANCE])
    fixed = ng.correct(reply, bad, [BALANCE])
    assert fixed == "The system shows: casual leave balance: 8 days. Would you like to apply?", fixed   # the casual figure, not all three
    assert not ng.ungrounded_figures(fixed, [BALANCE])
    nothing = ng.correct(reply, bad, [])
    assert nothing == f"{ng.NOT_CONFIRMED} Would you like to apply?" and not ng.ungrounded_figures(nothing, [])
    french = ng.correct("Il vous reste 12 jours.\nVoulez-vous poser un congé ?", ng.ungrounded_figures("Il vous reste 12 jours.", []), [])
    assert french == f"{ng.NOT_CONFIRMED_FR}\nVoulez-vous poser un congé ?"
    two = "Your balance is 12 days. You will have 9 days after approval. Anything else?"
    both = ng.correct(two, ng.ungrounded_figures(two, [BALANCE]), [BALANCE])
    assert both.count("The system shows") == 1 and "Anything else?" in both and not ng.ungrounded_figures(both, [BALANCE])   # the figures once, the other wrong sentence dropped
    pay = ng.correct("Your net pay was €2,540.00.", ng.ungrounded_figures("Your net pay was €2,540.00.", [PAYSLIP]), [PAYSLIP])
    assert pay == "The system shows: net paid: €2,195.00.", pay                                    # the net pay, not every line of the payslip
    assert ng.correct("", [], []) == ng.NOT_CONFIRMED
    twice = "You currently have 8 casual days. If you take 3 you will have 6 casual days left."
    assert ng.correct(twice, ng.ungrounded_figures(twice, [BALANCE]), [BALANCE]) == "You currently have 8 casual days."   # it already says what the tool returned


@test
def the_answer_loop_asks_once_and_then_replaces_the_figure():
    ng = number_guard
    right, wrong = "You have 8 casual days left.", "You have 12 casual days left."
    assert ng.review(right, [BALANCE], "", False) == ("ok", right)                                   # nothing to fix: the reply goes out as it is
    action, note = ng.review(wrong, [BALANCE], "", False)
    assert action == "retry" and "12 casual days" in note and "casual leave balance: 8 days" in note   # first time: send it back with the tool's figures
    action, fixed = ng.review(wrong, [BALANCE], "", True)
    assert action == "replaced" and fixed == "The system shows: casual leave balance: 8 days."          # still wrong after the retry: replaced, never passed through
    assert ng.review(right, [BALANCE], "", True) == ("ok", right)                                     # a corrected reply after the retry goes out
    assert ng.review("You have 12 casual days left.", [], "", True) == ("replaced", ng.NOT_CONFIRMED)   # no tool figure to show: it says it cannot confirm
    assert ng.review("Hello! How can I help?", [], "Hi", False) == ("ok", "Hello! How can I help?")     # no figure in the reply: no check at all


PENDING = result(pending_requests=[
    {"request_id": 2, "employee_name": "Hugo Martin", "leave_type": "casual", "start": "2026-12-02", "days": 1, "balance_after_approval": 1},
    {"request_id": 3, "employee_name": "Clara Dubois", "leave_type": "sick", "start": "2026-12-03", "days": 1, "balance_after_approval": 0},
    {"request_id": 7, "employee_name": "Emma Laurent", "leave_type": "casual", "start": "2026-11-30", "days": 1, "balance_after_approval": 6}])
LISTED = ("\n\n- **Hugo** (2 December): 1 casual day, balance 2 → 1 after\n- **Clara** (3 December): 1 sick day, balance 1 → 0 after"
          "\n- **Emma** (30 November): 1 casual day, balance 7 → 6 after")


@test
def a_count_of_things_a_tool_listed_must_be_how_many_it_listed_or_how_many_have_what_the_sentence_names():
    ng = number_guard
    assert ng.ungrounded("Two pending requests:" + LISTED, [PENDING]) == ["Two pending requests"]       # main, 17 Nov: three listed, "Two" said
    assert ng.ungrounded("Three pending requests:" + LISTED, [PENDING]) == []
    assert ng.ungrounded("You have **3** pending requests.", [PENDING]) == []
    assert ng.ungrounded("You have 2 pending requests.", [PENDING]) == ["2 pending requests"]
    assert ng.ungrounded("Il y a deux demandes en attente.", [PENDING]) == ["deux demandes"]
    # a count of what the sentence names: a person, a type, a status
    assert ng.ungrounded("Emma has one request waiting.", [PENDING]) == []
    assert ng.ungrounded("Two casual requests are waiting.", [PENDING]) == []
    assert ng.ungrounded("Hugo and Clara: two requests.", [PENDING]) == []
    assert ng.ungrounded("Emma has two requests.", [PENDING]) == ["two requests"]
    mine = result(my_requests=[{"request_id": i, "status": s} for i, s in enumerate(["pending", "approved", "approved", "pending", "rejected"], 1)])
    assert ng.ungrounded("You have two pending requests and five requests in all.", [mine]) == []
    assert ng.ungrounded("You have one pending request.", [mine]) == ["one pending request"]
    assert ng.ungrounded("I found two people named Emma.", [result(found=2, people=[{"name": "Emma Laurent"}, {"name": "Emma Roy"}])]) == []
    me = result(found=1, people=[{"name": "Sophie Bernard", "reports": ["Emma Laurent", "Hugo Martin", "Clara Dubois"]}], this_is_the_user=True)
    assert ng.ungrounded("Your team has three people: Emma, Hugo and Clara.", [me]) == []          # full suite, 17 Nov: "reports" is the team
    assert ng.ungrounded("Your team has two people.", [me]) == ["two people"]


@test
def what_is_not_a_count_of_something_a_tool_listed_is_not_checked_and_an_id_is_not_a_count():
    ng = number_guard
    assert ng.ungrounded("One of the requests is Emma's, and two of your requests are casual.", [PENDING]) == []   # "one of", "two of": not a count
    assert ng.ungrounded("You have two pending requests.", [BALANCE]) == []                          # no tool listed requests in this turn
    assert ng.ungrounded("I have two questions for you.", [PENDING]) == []                            # questions are not something a tool listed
    assert ng.ungrounded("You have two requests.", [result(my_requests=[{"request_id": 2, "status": "pending"}])]) == ["two requests"]   # request 2 is an id
    assert ng.ungrounded("Your two requests are waiting.", [result(my_requests=[])], user_text="I sent two requests") == []   # the person's own number
    assert said("You have 3 weeks, 2 months and 8 casual leave requests.") == []                      # a count is not a number of days


@test
def a_wrong_count_is_sent_back_once_with_how_many_the_tool_listed_then_its_sentence_is_replaced():
    ng = number_guard
    wrong = "Two pending requests:" + LISTED
    action, note = ng.review(wrong, [PENDING], "any pending request", False)
    assert action == "retry" and "Two pending requests" in note and "pending requests: 3" in note
    action, fixed = ng.review(wrong, [PENDING], "any pending request", True)
    assert action == "replaced" and fixed.startswith("The system shows: pending requests: 3.\n\n- **Hugo**"), fixed
    twice = "Two pending requests:\n\n- **Hugo** (2 December)\n- **Clara** (3 December)\n\nThree pending requests:" + LISTED   # main, 17 Nov
    action, fixed = ng.review(twice, [PENDING], "any pending request", True)
    assert action == "replaced" and "Two" not in fixed and "Three pending requests:" in fixed        # it already says how many: the wrong count goes
    assert ng.review("Three pending requests:" + LISTED, [PENDING], "any pending request", True)[0] == "ok"


@test
def the_leave_tools_return_the_derived_figures_so_the_reply_never_needs_arithmetic():
    assert "pending_days" not in get_leave_balance("E001")                                           # nothing pending: the result is unchanged
    r = apply_leave("E001", "casual", d(MON), d(MON + timedelta(days=1)))                            # 2 working days, pending
    assert r["success"]
    b = get_leave_balance("E001")
    assert b["leave_balance"]["casual"] == 8 and b["pending_days"] == {"casual": 2} and b["available_after_pending"] == {"casual": 6}
    assert not number_guard.ungrounded("8 days now, 6 once it is approved, 2 pending.", [json.dumps(b)])
    # too much: the tool says by how many days
    short = apply_leave("E001", "casual", d(MON + timedelta(days=7)), d(MON + timedelta(days=18)))
    assert "Not enough casual" in short["error"] and short["short_by"] == 4                          # 10 needed, 6 available
    # a manager sees what the balance will be once each request is approved
    pending = tools.get_pending_requests("M001")["pending_requests"]
    assert pending[0]["days"] == 2 and pending[0]["casual_balance"] == 8 and pending[0]["balance_after_approval"] == 6
    # the days of given dates come with what is left after them, or how short (Emma: 8 casual, 2 of them already waiting)
    assert tools.days_figures("E001", "casual", 4) == {"available": 6, "available_after": 2}
    assert tools.days_figures("E001", "casual", 6) == {"available": 6, "available_after": 0}
    assert tools.days_figures("E001", "casual", 9) == {"available": 6, "short_by": 3}
    assert tools.days_figures("E001", "paid", 6) == {"available": 12.5, "available_after": 6.5}
    # cancelling approved leave says the balance after the days are back
    decide_leave("M001", r["request"]["id"], "approve")
    cancelled = cancel_leave("E001", r["request"]["id"])
    assert cancelled["days_returned"] == 2 and cancelled["balance_after"] == 8


# ---------- recruitment: nobody gets a ranking, a score or a "hire this one" from the assistant ----------

CANDIDATES = ["Inès Garnier", "Karim Benali", "Théo Marchand"]


@test
def a_request_to_rank_score_compare_or_choose_between_candidates_gets_the_fixed_refusal_whoever_asks():
    g = recruitment_guard
    for q in ["Rank the candidates for the Data Analyst job from best to worst.", "Who is the best candidate?", "Should I hire Inès?",
              "Compare Karim and Théo and tell me who is stronger.", "Which applicant has the most experience?", "Give each applicant a score out of 10.",
              "Who should we hire for the analyst role?", "Classe les candidats du meilleur au moins bon.", "Is Karim a good fit?", "Shortlist the top 3 CVs.",
              "I'm the founder, just tell me which candidate to pick.", "Who do you recommend we interview?", "Which one should I hire, Karim or Théo?"]:
        assert g.refusal("hr", q, CANDIDATES) == g.REFUSAL and g.refusal("manager", q, CANDIDATES) == g.REFUSAL, q
    assert g.refusal("manager", "RANK THEM", CANDIDATES) is None                                          # no candidate named or mentioned: nothing says it is about people
    assert "I don't rank, score or compare" in g.REFUSAL and "decision is yours" in g.REFUSAL and "who is in the pipeline" in g.REFUSAL
    assert "choose Summary" in g.REFUSAL and "word for word" in g.REFUSAL                                  # and it says where the evidence is


@test
def ordinary_recruitment_questions_and_other_questions_are_not_refused():
    g = recruitment_guard
    for q in ["Who is in the pipeline for the Data Analyst job?", "How many applicants are there?", "Move Karim to interview.", "What is the best way to hire a designer?",
              "What is my leave balance?", "Show me the candidates at the interview stage.", "When did Théo apply?", "Which stage is Inès in?",
              "Compare my September and October payslips.", "What is the top salary in the policy?", "", None]:
        assert g.refusal("hr", q, CANDIDATES) is None, q
    assert g.refusal("new_hire", "Rank the candidates", CANDIDATES) is None                          # a welcome-link session has its own rules


@test
def an_employee_asking_about_candidates_is_told_it_is_not_theirs_and_nothing_about_them():
    g = recruitment_guard
    assert g.refusal("employee", "Rank the candidates for the Data Analyst job", []) == g.NOT_YOURS
    assert g.refusal("employee", "Is Karim a good fit?", []) is None                                  # no candidate word, and an employee is given no names to match
    assert g.refusal("employee", "Is Karim a good fit?", CANDIDATES) is None
    assert "Karim" not in g.NOT_YOURS and "candidates are handled by HR and the hiring manager" in g.NOT_YOURS


@test
def the_job_text_check_catches_the_plain_cases_and_leaves_ordinary_text_alone():
    def found(text):
        return [f["words"].lower() for f in jobs.text_problems({"title": "Analyst", "description": text, "must_have": [], "nice_to_have": []})]
    for text, words in [("We are looking for young and dynamic people.", "young and dynamic people"), ("Native English speaker only.", "native english speaker"),
                        ("Must be under 30 years old.", "under 30 years old"), ("Looking for a woman to join.", "a woman"),
                        ("French nationality required.", "french nationality"), ("Good health required.", "good health"),
                        ("Candidates should have no children.", "no children"), ("Must live in Paris.", "must live in"),
                        ("Nous cherchons des jeunes dynamiques.", "jeunes dynamiques")]:
        assert words in found(text), text
    for text in ["Experience with single sign-on and race conditions.", "We are a young company with a healthy work-life balance.", "Poste ouvert (H/F).",
                 "We welcome applications regardless of age, sex, religion or origin.", "Égalité entre les femmes et les hommes.", "Fluent English (C1).", "5+ years of Python."]:
        assert found(text) == [], text
    assert [f["words"] for f in jobs.text_problems({"title": "Young and dynamic people wanted", "description": "x", "must_have": ["Native English speaker"], "nice_to_have": []})] == [
        "Young and dynamic people", "Native English speaker"]                                           # the title and the requirements are read too


# ---------- recruitment: the careers page, and how long a candidate's data is kept (retention.py, careers.py) ----------

D = date


@test
def a_candidates_data_is_erased_on_the_day_the_cnil_based_rules_say():
    plan = retention.erase_plan
    contact = D(2026, 1, 15)
    assert plan("hired", True, contact) == (None, None) and plan("hired", False, contact, D(2026, 2, 1)) == (None, None)       # a hired candidate is an employee now
    # in the talent pool (they agreed): at most 24 months after the last contact, whatever HR types
    assert plan("applied", True, contact) == (D(2028, 1, 15), "talent pool limit")
    assert plan("interview", True, contact, months=6) == (D(2026, 7, 15), "talent pool limit")
    assert plan("interview", True, contact, months=60) == (D(2028, 1, 15), "talent pool limit")
    # not in the talent pool: 30 days after "Not selected", 90 days after the job closed if they never got a decision, never later than the limit
    assert plan("not_selected", False, contact, decided_on=D(2026, 3, 1)) == (D(2026, 3, 31), "not selected")
    assert plan("screening", False, contact, job_closed_on=D(2026, 5, 1)) == (D(2026, 7, 30), "job closed")
    assert plan("not_selected", False, contact, decided_on=D(2026, 3, 1), job_closed_on=D(2026, 2, 1)) == (D(2026, 3, 31), "not selected")    # the earlier rule wins
    assert plan("not_selected", False, contact, decided_on=D(2026, 6, 1), job_closed_on=D(2026, 2, 1)) == (D(2026, 5, 2), "job closed")
    assert plan("applied", False, contact) == (D(2028, 1, 15), "retention limit")                   # an open job, no decision: only the outer limit
    assert plan("applied", False, contact, job_closed_on=D(2030, 1, 1), months=3) == (D(2026, 4, 15), "retention limit")
    assert retention.add_months(D(2026, 1, 31), 1) == D(2026, 2, 28) and retention.add_months(D(2026, 11, 8), 24) == D(2028, 11, 8)
    assert [retention.valid_months(v) for v in (24, "12", " 1 ", 0, 25, "x", None, 2.5)] == [24, 12, 1, None, None, None, None, None]
    assert "cnil.fr" in retention.CNIL_URL and "Recrutement" in retention.CNIL_CITE


@test
def a_cv_is_checked_before_anything_is_saved():
    good = pdf_text.make_pdf([("h1", "Inès Garnier"), ("p", "Data analyst, 3 years of SQL.")])
    assert careers.check_cv(good) is None
    assert "Attach your CV" in careers.check_cv(b"") and "Attach your CV" in careers.check_cv(None)
    assert "too big" in careers.check_cv(good + b"0" * careers.MAX_CV_BYTES)
    assert "must be a PDF" in careers.check_cv(b"MZ\x90\x00 a renamed program") and "must be a PDF" in careers.check_cv(b"\x89PNG\r\n\x1a\n" + b"0" * 50)
    assert "password" in careers.check_cv(good + b"\n/Encrypt << /Filter /Standard >>")
    for marker in (b"/JavaScript", b"/JS (app.alert(1))", b"/Launch", b"/OpenAction", b"/EmbeddedFile"):
        assert "script or an attachment" in careers.check_cv(good + b"\n" + marker), marker
    for marker in (b"<< /S /JavaScript /JS (app.alert(1)) >>", b"<< /AA << /O 5 0 R >> >>", b"<</Type/Filespec/EF<</F 9 0 R>>/Type/EmbeddedFile>>",
                   b"<< /Type /Catalog /OpenAction 7 0 R>>", b"/RichMedia<<", b"/Launch\x00/F"):
        assert "script or an attachment" in careers.check_cv(good + b"\n" + marker), marker
    # a PDF that Chrome, Google Docs or macOS wrote: the tag on every embedded font starts with /AA, and that is not an action
    fonts = b"\n<< /Type /FontDescriptor /FontName /AAAAAA+Arial-BoldMT /Flags 4 >>\n<< /Type /Font /BaseFont /AAAAAB+Monaco /JSON 1 >>"
    assert careers.check_cv(good + fonts) is None
    # compressed data (a photo, a font file) can hold any bytes: a marker inside a stream is not a marker...
    stream = b"\n12 0 obj\n<< /Length 30 /Filter /FlateDecode >>\nstream\n\x00\x01/AA \x02/JS(\xff/Launch<\x03/OpenAction\nendstream\nendobj\n"
    assert careers.check_cv(good + stream) is None
    assert careers.check_cv(good + stream + stream) is None
    # ...but one in the dictionary of that same object, or after the stream, still is
    assert "script or an attachment" in careers.check_cv(good + b"\n12 0 obj\n<< /Length 30 /AA << /O 5 0 R >> >>\nstream\nxx\nendstream\nendobj\n")
    assert "script or an attachment" in careers.check_cv(good + stream + b"/OpenAction 7 0 R")
    assert "script or an attachment" in careers.check_cv(good + b"\n12 0 obj\n<< /Length 3 >>\nstream\nxx\nendstream\n<< /JS (x) >>")
    # a stream that never closes is not skipped, and a crafted file with thousands of them is not slow
    assert "script or an attachment" in careers.check_cv(good + b"\n<< /Length 3 >>\nstream\nxx /OpenAction 7 0 R")
    started = time.time()
    assert careers.check_cv(good + b">>\nstream\n" * 400_000) is None
    assert time.time() - started < 5
    assert careers.decode_cv("JVBERi0=") == b"%PDF-" and careers.decode_cv("not base64!") is None and careers.decode_cv(None) == b""


@test
def applications_are_rate_limited_per_key_and_the_window_slides():
    t = careers.Throttle(2, 60)
    assert [t.allow("a", 0), t.allow("a", 10), t.allow("a", 20)] == [True, True, False]
    assert t.allow("b", 20) is True                                                                      # another address is not affected
    assert t.allow("a", 61) is True and t.allow("a", 62) is False                                         # the first try left the window


@test
def the_privacy_notice_says_only_what_the_app_does_and_what_the_cnil_recommends():
    n = careers.notice(18)
    text = " ".join(s["text"] for s in n["sections"])
    assert [s["title"] for s in n["sections"]] == ["Who reads your application", "Artificial intelligence", "What we ask for, and what not to send",
                                                   "How long we keep it", "Your rights"]
    assert "18 months" in text and "30 days after we mark it as not selected" in text and "90 days after the job closes" in text and "two years" in text
    assert "talent-pool box" in text and "CNIL" in text and "L1132-1" in text and "withdraw and erase" in text
    # the assistant on the tracking page is there, so the notice says what it does (and does not): it never promises more than the app does
    assert "answers your questions about the job and about your application" in text and "does not read your CV or your cover letter" in text
    assert "kept with your application and erased with it" in text and "No AI ever ranks, scores or rejects anyone" in text and "No AI is used" not in text
    # the summary for the hiring team is there too, so the notice says what it reads, what it shows and what it leaves out
    assert "an AI reads your CV and your cover letter" in text and "word for word" in text and "does not score, rank or recommend anyone" in text
    assert "age, gender, nationality, family situation, health and address" in text and careers.NOTICE_VERSION >= careers.SUMMARY_NOTICE_VERSION
    saved = careers.AI_ASSISTS
    try:
        careers.AI_ASSISTS = ()
        none = " ".join(s["text"] for s in careers.notice(18)["sections"])
        careers.AI_ASSISTS = ("questions",)
        only_questions = " ".join(s["text"] for s in careers.notice(24)["sections"])
    finally:
        careers.AI_ASSISTS = saved
    assert "No AI is used on your application today" in none and "ever ranks" not in none
    assert "answers your questions about the job" in only_questions and "an AI reads your CV" not in only_questions          # it promises no more than the app does
    assert "No AI is used" not in only_questions


@test
def a_contract_question_to_hr_answered_without_checking_the_contract_is_sent_back_once_and_nothing_else_is():
    nudge = contract_first.nudge
    for q in ["Send Lea's contract to her now.", "Where does Lea's contract stand?", "Has Hugo's contract been approved?", "Is the contract for Clara signed?",
              "Can you email Lea her contract?", "Is Lea's contract ready to download?", "Is Lea's contract waiting for approval?", "Le contrat de Lea est-il signé ?",
              "Please issue the contract for Lea", "What is the status of the new hire's contract?"]:
        assert nudge("hr", q, []) == contract_first.CONTRACT_FIRST, q                                         # a contract question, no look-up: check first
        assert nudge("hr", q, ["search_policy"]) == contract_first.CONTRACT_FIRST, q                           # another tool is not the contract tool
        assert nudge("hr", q, ["get_contract_status"]) is None, q                                              # it looked: nothing to say
    for q in ["What does my contract say about notice?", "Show me my contract.", "How long can a trial period be in a CDD?", "What is my leave balance?",
              "Hello", "Show me E001's payslip for October 2026.", "", None]:
        assert nudge("hr", q, []) is None, q                                                                    # not about a new hire's contract
    assert nudge("employee", "Send Lea's contract to her now.", []) is None and nudge("manager", "Where does Lea's contract stand?", []) is None   # only HR has the tool
    assert nudge("new_hire", "Where does my contract stand?", []) is None
    note = contract_first.CONTRACT_FIRST
    assert "do not apologise" in note and "not from the person" in note and "get_contract_status" in note and "never sends a contract" in note and "ignore this note" in note


@test
def dates_that_use_no_days_say_why_and_a_day_that_counts_says_nothing():
    sat, sun, mon, holiday = date(2026, 11, 21), date(2026, 11, 22), date(2026, 11, 23), date(2026, 11, 11)      # 11 November is a public holiday (a Wednesday)
    why = tools.no_days_reason("casual", sat, sat)
    assert "Saturday 21 November" in why and "Monday to Friday" in why and "cannot be booked" in why and "casual leave" in why
    assert tools.no_days_reason("sick", sun, sun) and tools.no_days_reason("casual", sat, sun).startswith("every day from Saturday 21 November to Sunday 22 November")
    assert tools.no_days_reason("paid", sat, sat) is None                                                          # paid leave counts Saturdays
    assert "Sunday 22 November" in tools.no_days_reason("paid", sun, sun) and "Monday to Saturday" in tools.no_days_reason("paid", sun, sun)
    assert "public holiday" in tools.no_days_reason("casual", holiday, holiday)
    assert tools.no_days_reason("casual", mon, mon) is None and tools.no_days_reason("casual", sat, mon) is None   # a range with a working day in it is fine
    # the request itself is refused with the same reason, and nothing is created (a Saturday that is still to come, whatever day this runs)
    next_saturday = date.today() + timedelta(days=(5 - date.today().weekday()) % 7 or 7)
    refused = apply_leave("E001", "casual", next_saturday.isoformat(), next_saturday.isoformat())
    assert "Saturday" in refused["error"] and "cannot be booked" in refused["error"] and "Nothing was submitted" in refused["error"], refused
    assert tools.get_my_requests("E001")["my_requests"] == []


@test
def a_request_to_book_is_booked_not_asked_about_and_dates_with_no_days_are_never_offered():
    nudge = booking_first.nudge
    count_ok = [json.dumps({"working_days": 6, "available": 12.5, "available_after": 6.5})]
    count_none = [json.dumps({"working_days": 0, "available": 7, "available_after": 7,
                              "cannot_be_booked": "Saturday 21 November is not a working day (casual leave counts Monday to Friday), so it uses no days and cannot be booked."})]
    asked = ["Please book paid leave from 30 November 2026 to 5 December 2026.", "Apply leave on nov 23rd", "I want leave on 21st nov", "I want casual leave on 23 November.",
             "Can you book the 23rd for me?", "Je voudrais poser un congé le 23 novembre"]
    for q in asked:
        assert booking_first.asks_to_book(q), q
    for q in ["How many days if I book 5 to 8 December?", "Can I take leave on 5 December?", "check for 21st nov", "yes", "sure go for it", "What if I apply for 3 days?", "", None]:
        assert not booking_first.asks_to_book(q), q
    for r in ["You have 12.5 paid days. Note: paid leave is normally taken between 1 May and 31 October.\n\nShall I submit this request?", "Shall I proceed with the booking?",
              "That uses 6 days. Shall I go ahead and submit it?", "Would you like me to book it anyway, or did you mean a weekday?",
              "You have 8 casual days available. A one-day leave on 23 November would use 1 day, leaving you with 7 days. Ready to submit?",
              "That's 1 casual leave day. You'd have 7 days left after it's approved. Ready for me to apply it?"]:
        assert booking_first.asks_before_booking(r), r
    for r in ["A confirmation screen has appeared. Please review it and press Confirm.", "Which kind of leave do you want: casual or paid?", "You have 7 casual days.", ""]:
        assert not booking_first.asks_before_booking(r), r
    for q in asked[:4]:                                                                                           # asked to book, asked back instead: booked now, once
        assert nudge("employee", q, "That uses 6 days. Shall I submit this request?", ["get_leave_balance", "count_leave_days"], count_ok) == booking_first.BOOK_NOW, q
        assert nudge("employee", q, "That uses 6 days. Shall I submit this request?", ["count_leave_days", "apply_leave"], count_ok) is None        # it did call the tool
    assert nudge("employee", asked[0], "A confirmation screen has appeared. Please review it and press Confirm.", ["apply_leave"], count_ok) is None
    assert nudge("employee", asked[0], "Which kind of leave do you want: casual or paid?", [], []) is None        # a real question is not nudged
    assert nudge("employee", "How many days if I book 5 to 8 December?", "That is 4 days. Shall I submit it?", ["count_leave_days"], count_ok) is None   # only asked how many
    assert nudge("employee", "yes", "Shall I submit it?", [], []) is None
    assert nudge("new_hire", asked[0], "Shall I submit this request?", [], []) is None                           # a new hire has no leave tool
    # dates that use no days: the offer is sent back, whatever the person asked (even only "check for 21st nov")
    why = "Saturday 21 November is not a working day"
    for q in ["check for 21st nov", "I want leave on 21st nov", "book it on 21st"]:
        for r in ["November 21st is a Saturday, so it doesn't use any days. Would you like me to book it anyway, or did you mean a weekday?",
                  "I can book it anyway if you like.", "That date uses no days. Shall I submit it?"]:
            fix = nudge("employee", q, r, ["count_leave_days"], count_none)
            assert fix and why in fix and "do not offer to book them" in fix, (q, r)
    assert nudge("employee", "check for 21st nov", "21 November is a Saturday, so nothing can be booked. Which working day would you like instead?", ["count_leave_days"], count_none) is None
    assert nudge("employee", "check for 21st nov", "Would you like me to book it anyway?", ["count_leave_days", "apply_leave"], count_none) is None
    assert booking_first.unbookable(count_ok + ["not json", "[1, 2]"]) is None and "Saturday 21 November" in booking_first.unbookable(["not json"] + count_none)
    assert "not from the person" in booking_first.BOOK_NOW and "apply_leave" in booking_first.BOOK_NOW and "confirmation card" in booking_first.BOOK_NOW


@test
def leave_asked_for_on_a_weekend_with_no_date_gets_a_fixed_answer_and_everything_else_goes_to_the_model():
    answer = booking_first.weekend_answer
    for q in ["i want to take leave on saturday ?", "I want leave on Saturday", "Can I take Saturday off?", "book leave for this weekend", "I need a day off on sat",
              "I want to take leave on saturday and sunday", "apply for leave on the weekend please", "i want to take sick leave on saturday"]:
        assert answer("employee", q) == booking_first.WEEKEND_EN, q                                          # Saturdays count for paid leave only: said in the answer
    for q in ["I want leave on sunday", "can i take sunday off", "I want to book a holiday on a sunday"]:
        assert answer("manager", q) == booking_first.SUNDAY_EN, q                                            # nothing counts a Sunday
    assert answer("employee", "je veux poser un congé samedi") == booking_first.WEEKEND_FR and answer("hr", "Je voudrais prendre un congé dimanche") == booking_first.SUNDAY_FR
    assert answer("employee", "Je veux poser un congé ce week-end") == booking_first.WEEKEND_FR
    for q in ["I want leave on saturday 28 november",                    # a date: the model counts it, and the tools say it cannot be booked
              "I want to book paid leave on saturday", "Je veux poser des congés payés samedi",           # paid leave counts Saturdays: the model books it
              "I want leave from friday to sunday", "I want leave on monday and saturday", "I want to take leave tomorrow, it is a saturday",
              "What does the policy say about leave on saturdays?", "Why can't I take leave on a weekend?", "Do we work on saturdays?",
              "I worked on saturday, how many days off do I get?", "I want leave", "yes saturday", "", None]:
        assert answer("employee", q) is None, q
    assert answer("new_hire", "i want to take leave on saturday") is None                                    # a new hire has no leave tool
    for text in (booking_first.WEEKEND_EN, booking_first.SUNDAY_EN, booking_first.WEEKEND_FR, booking_first.SUNDAY_FR):
        assert not any(c.isdigit() for c in text)                                                           # a fixed answer names no date, so none can be wrong
    assert "Paid leave counts Saturdays" in booking_first.WEEKEND_EN and "never Sundays" in booking_first.WEEKEND_EN


@test
def the_prompt_date_line_names_the_weekday_and_is_built_from_the_date_it_is_given():
    assert tools.today_line(date(2026, 11, 10)) == "Today is Tuesday 2026-11-10."
    assert tools.today_line(date(2026, 11, 28)) == "Today is Saturday 2026-11-28."                         # 15 November 2026 is a Sunday, 14 is the Saturday
    assert date(2026, 11, 14).strftime("%A") == "Saturday" and date(2026, 11, 15).strftime("%A") == "Sunday"
    assert tools.today_line().startswith("Today is ") and date.today().isoformat() in tools.today_line()


# ---------- one confirmation card for several actions (confirm_batch.py) ----------

def _insert_request(employee_id, manager_id, start, status, leave_type="casual", days=1):
    conn = db.get_conn()
    with conn:
        cur = conn.execute("INSERT INTO leave_requests (employee_id, manager_id, leave_type, start_date, end_date, days, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (employee_id, manager_id, leave_type, d(start), d(start), days, status))
    conn.close()
    return cur.lastrowid


@test
def dates_on_a_card_come_with_their_weekday():
    assert tools.when_label(date(2026, 11, 20)) == "Fri 20 Nov 2026"
    assert tools.when_label(date(2026, 11, 23), date(2026, 11, 24)) == "Mon 23 Nov to Tue 24 Nov 2026"
    assert tools.when_label("2026-12-30", "2027-01-04") == "Wed 30 Dec 2026 to Mon 4 Jan 2027"
    assert tools.when_label("2026-11-21") == "Sat 21 Nov 2026"
    rid = _insert_request("E001", "M001", MON, "approved", days=1)
    s = tools.request_summary(rid)
    assert s["when"] == "Mon 4 Mar 2030" and s["leave_type"] == "casual" and s["days"] == 1 and s["employee_name"] and s["status"] == "approved"
    assert tools.request_summary(9999) is None


@test
def a_cancellation_is_checked_before_the_card_with_the_same_rules_as_the_cancellation():
    pending = _insert_request("E001", "M001", MON, "pending_manager_approval")
    approved = _insert_request("E001", "M001", MON + timedelta(days=1), "approved")
    started = _insert_request("E001", "M001", date.today(), "approved")
    cancelled = _insert_request("E001", "M001", MON + timedelta(days=2), "cancelled")
    rejected = _insert_request("E001", "M001", MON + timedelta(days=3), "rejected")
    hers = _insert_request("E003", "M001", MON, "approved")
    cases = [(pending, True), (approved, True), (started, False), (cancelled, False), (rejected, False), (hers, False), (99999, False)]
    for rid, can in cases:
        problem = tools.cancel_problem("E001", rid)
        assert (problem is None) == can, (rid, problem)                                                        # the check says what the cancellation will do
        assert bool(tools.cancel_leave("E001", rid)["cancelled"]) == can, rid
    assert tools.cancel_problem("E001", started) == "This leave has already started. Please contact your manager or HR."
    assert tools.cancel_problem("E001", cancelled) == "This request is already cancelled." and tools.cancel_problem("E001", hers) == "No such request found for you."


@test
def a_decision_is_checked_before_the_card_with_the_same_rules_as_the_decision():
    def fresh(owner="E001", manager="M001", status="pending_manager_approval", days=1):
        return _insert_request(owner, manager, MON, status, days=days)
    conn = db.get_conn()
    with conn:
        conn.execute("UPDATE employees SET casual_balance = 0 WHERE id = 'E003'")
    conn.close()
    cases = [(fresh(), "M001", "approve", "", True), (fresh(), "M001", "reject", "clashes with a deadline", True), (fresh(), "M001", "reject", "", False),
             (fresh(), "C001", "approve", "", False), (fresh(status="approved"), "M001", "approve", "", False), (fresh(), "M001", "maybe", "", False),
             (99999, "M001", "approve", "", False), (fresh(owner="E003"), "M001", "approve", "", False)]       # E003 has no casual days left to give
    for rid, manager, decision, reason, can in cases:
        problem = tools.decide_problem(manager, rid, decision, reason)
        assert (problem is None) == can, (rid, decision, problem)
        assert ("error" not in tools.decide_leave(manager, rid, decision, reason)) == can, (rid, decision)
    assert tools.decide_problem("M001", 99999, "approve") == "Request 99999 not found"


@test
def one_card_holds_up_to_five_actions_of_one_kind_and_nothing_replaces_another_silently():
    cb = confirm_batch
    card = {}
    cancel = lambda n: {"request_id": n}
    det = lambda n: {"request_id": n, "when": f"Fri {n} Nov 2026", "leave_type": "casual", "days": 1}
    assert cb.add(card, "cancel_leave", cancel(1), det(1)) == ("started", 1)
    assert cb.add(card, "cancel_leave", cancel(2), det(2)) == ("added", 2) and cb.add(card, "cancel_leave", cancel(2), det(2)) == ("duplicate", 2)
    for n in (3, 4, 5):
        assert cb.add(card, "cancel_leave", cancel(n), det(n)) == ("added", n)
    assert cb.add(card, "cancel_leave", cancel(6), det(6)) == ("full", 5) and [i["input"]["request_id"] for i in card["items"]] == [1, 2, 3, 4, 5]
    assert card["tool"] == "cancel_leave" and card["input"] == cancel(1) and card["details"] == det(1) and card["kind"] == "cancel_leave"   # the first item, as before
    assert cb.add(card, "decide_leave", {"request_id": 7, "decision": "approve"}, {}) == ("other_kind", 5) and len(card["items"]) == 5
    approvals = {}
    assert cb.add(approvals, "decide_leave", {"request_id": 1, "decision": "approve"}, {})[0] == "started"
    assert cb.add(approvals, "decide_leave", {"request_id": 2, "decision": "approve"}, {})[0] == "added"
    assert cb.add(approvals, "decide_leave", {"request_id": 3, "decision": "reject", "reason": "x"}, {}) == ("other_kind", 2)      # approvals and rejections do not mix
    assert cb.kind_of("decide_leave", {"decision": "reject"}) == "decide_leave:reject" and cb.kind_of("cancel_leave", {"request_id": 1}) == "cancel_leave"
    booking = {}
    assert cb.add(booking, "apply_leave", {"leave_type": "casual", "start_date": "a", "end_date": "a"}, {})[0] == "started"
    assert cb.add(booking, "apply_leave", {"leave_type": "casual", "start_date": "b", "end_date": "b"}, {}) == ("other_kind", 1)   # bookings are not batched
    assert len(booking["items"]) == 1 and booking["input"]["start_date"] == "a"


@test
def what_the_assistant_is_told_about_the_card_is_true_and_says_there_is_one():
    cb = confirm_batch
    card = {}
    det = lambda n: {"request_id": n, "when": f"Fri {n} Nov 2026", "leave_type": "casual", "days": 1}
    first = cb.tool_result(*cb.add(card, "cancel_leave", {"request_id": 1}, det(1)), card, "cancel_leave", det(1))
    assert first["awaiting_confirmation"] and first["card_items"] == 1 and "ONE confirmation card" in first["message"] and first["card"] == det(1)
    assert first["on_the_card"] == ["Fri 1 Nov 2026 (casual, 1 day)"]
    second = cb.tool_result(*cb.add(card, "cancel_leave", {"request_id": 2}, det(2)), card, "cancel_leave", det(2))
    assert second["card_items"] == 2 and "still only ONE card" in second["message"] and "Never say there are several cards" in second["message"]
    assert len(second["on_the_card"]) == 2
    for n in (3, 4, 5):
        cb.add(card, "cancel_leave", {"request_id": n}, det(n))
    full = cb.tool_result(*cb.add(card, "cancel_leave", {"request_id": 6}, det(6)), card, "cancel_leave", det(6))
    assert "error" in full and "at most 5" in full["error"] and "NOT on the card" in full["error"] and full["card_items"] == 5 and "awaiting_confirmation" not in full
    other = cb.tool_result(*cb.add(card, "decide_leave", {"request_id": 9, "decision": "approve"}, {}), card, "decide_leave", {})
    assert "error" in other and "one kind of action only" in other["error"] and "NOT on the card" in other["error"]
    single = {}
    cb.add(single, "apply_leave", {"x": 1}, {})
    again = cb.tool_result(*cb.add(single, "apply_leave", {"x": 2}, {}), single, "apply_leave", {})
    assert "Only one of these" in again["error"] and "NOT on the card" in again["error"]


@test
def confirm_reports_each_item_separately_and_failures_are_not_hidden():
    cb = confirm_batch
    items = [{"input": {"request_id": n}, "details": {"when": f"Fri {n} Nov 2026", "leave_type": "casual", "days": 1}} for n in (20, 23, 24)]
    ok = lambda r: cb.outcome("cancel_leave", r[0], r[1])
    good = {"cancelled": True, "days_returned": 1, "balance_after": 8}
    bad = {"cancelled": False, "message": "This leave has already started. Please contact your manager or HR."}
    all_ok = [ok((i, good)) for i in items]
    mixed = [ok((items[0], good)), ok((items[1], bad)), ok((items[2], good))]
    none_ok = [ok((i, bad)) for i in items]
    assert cb.note("cancel_leave", all_ok).startswith("Done: all 3 cancellations.\n\n- Cancelled Fri 20 Nov 2026 (casual, 1 day), 1 day returned to your balance.")
    text = cb.note("cancel_leave", mixed)
    assert text.startswith("2 of 3 cancellations done, 1 not.") and "- Not cancelled, Fri 23 Nov 2026 (casual, 1 day): This leave has already started." in text
    assert text.count("- ") == 3 and cb.note("cancel_leave", none_ok).startswith("None of the 3 cancellations could be done.")
    assert cb.note("cancel_leave", all_ok[:1]) == "Done."                                                          # one item: as before
    assert cb.note("cancel_leave", mixed[1:2]) == "Couldn't complete it: This leave has already started. Please contact your manager or HR."
    approve = [{"input": {"request_id": 1, "decision": "approve"}, "details": {"employee_name": "Emma", "when": "Mon 23 Nov 2026", "leave_type": "casual", "days": 1}},
               {"input": {"request_id": 2, "decision": "approve"}, "details": {"employee_name": "Hugo", "when": "Tue 24 Nov to Wed 25 Nov 2026", "leave_type": "paid", "days": 2}}]
    outs = [cb.outcome("decide_leave", approve[0], {"success": True}), cb.outcome("decide_leave", approve[1], {"error": "Employee no longer has enough paid leave"})]
    text = cb.note("decide_leave:approve", outs)
    assert text.startswith("1 of 2 approvals done, 1 not.") and "- Approved Emma: Mon 23 Nov 2026 (casual, 1 day)." in text
    assert "- Not approved, Hugo: Tue 24 Nov to Wed 25 Nov 2026 (paid, 2 days): Employee no longer has enough paid leave" in text
    assert cb.outcome("apply_leave", {}, {"success": True})[0] and not cb.outcome("apply_leave", {}, {"error": "x"})[0] and not cb.outcome("cancel_leave", items[0], {})[0]


@test
def the_reply_never_talks_about_several_cards_while_the_app_shows_one():
    cb = confirm_batch
    card = {"items": [{}, {}, {}]}
    for reply in ["The app is showing you confirmation cards for all three cancellations.", "Please review each card and press Confirm.", "Review the three cards.",
                  "Two confirmation screens have appeared.", "Les cartes de confirmation sont affichées."]:
        fix = cb.nudge(card, reply)
        assert fix and "exactly ONE confirmation card" in fix and "3 items" in fix, reply
    assert "1 item." in cb.nudge({"items": [{}]}, "I opened the cards.")
    for reply in ["A confirmation card lists all three. Please review it and press Confirm once.", "I'll cancel those three.", "", None]:
        assert cb.nudge(card, reply) is None, reply
    assert cb.nudge({}, "Here are your cards") is None                                                             # no card, nothing to correct


@test
def a_manager_asked_to_approve_or_reject_is_shown_the_request_pending_now_never_the_chat():
    def nudge(role, q, called, sources, reply="Emma's leave request is already approved."):
        return approval_first.nudge(role, q, reply, called, sources)
    hugo = {"request_id": 3, "employee_name": "Hugo", "leave_type": "casual", "start": "2026-11-16", "end": "2026-11-16", "days": 1}
    emma = {"request_id": 409, "employee_name": "Emma", "leave_type": "casual", "start": "2026-11-23", "end": "2026-11-23", "days": 1}
    pending = [json.dumps({"pending_requests": [hugo, emma]})]
    for q, verb in [("approve emma's leave request", "approve"), ("Approve only Emma's leave request", "approve"), ("please approve emma", "approve"),
                    ("Can you approve Hugo's request?", "approve"), ("yes, approve it", "approve"), ("reject emma's request", "reject"),
                    ("I want to decline Hugo's leave", "reject"), ("approuve la demande d'Emma", "approve"), ("refuse la demande de Hugo", "reject")]:
        assert approval_first.asked_to_decide(q) == verb, q
    for q in ["Which requests can I approve?", "what happens if I approve it", "How do I reject a request", "any pending request", "emma", "approved?", "", None]:
        assert approval_first.asked_to_decide(q) is None, q
    fix = nudge("manager", "approve emma's leave request", ["get_pending_requests"], pending)                       # the real bug: "already approved" from the chat
    assert fix and "request 409" in fix and "Emma" in fix and "Mon 23 Nov 2026" in fix and "never say it is already approved" in fix and '"approve"' in fix, fix
    assert "request 3" in nudge("manager", "approve hugo's request", ["get_pending_requests"], pending)
    assert "request 409" in nudge("manager", "approve emma's leave request", ["get_pending_requests"], [json.dumps({"pending_requests": [{**emma, "request_id": 407}]}), *pending])   # the latest list wins
    fix = nudge("manager", "reject emma's request", ["get_pending_requests"], pending)
    assert fix and "request 409" in fix and "reason" in fix and '"reject"' in fix
    assert nudge("manager", "approve emma's leave request", ["get_pending_requests", "decide_leave"], pending) is None   # it did decide: the card is up
    assert nudge("employee", "approve emma's leave request", ["get_pending_requests"], pending) is None                # only a manager can decide
    assert nudge("hr", "approve emma's leave request", ["get_pending_requests"], pending) is None
    assert nudge("manager", "approve emma's leave request", [], []) == approval_first.LOOK_FIRST                       # answered without looking at what is waiting
    assert nudge("manager", "approve emma's leave request", ["get_pending_requests"], ["not json", "[1, 2]"]) == approval_first.LOOK_FIRST
    assert "get_pending_requests" in approval_first.LOOK_FIRST and "already approved" in approval_first.LOOK_FIRST
    assert nudge("manager", "approve the request", ["get_pending_requests"], pending) is None                           # nobody named: asking which one is fine
    assert nudge("manager", "approve clara's request", ["get_pending_requests"], pending) is None                       # nothing pending for her: the reply says so
    assert nudge("manager", "approve emma's request", ["get_pending_requests"], [json.dumps({"pending_requests": [emma, {**emma, "request_id": 410}]})]) is None   # two waiting: which one?
    assert nudge("manager", "which requests can I approve?", [], []) is None and nudge("manager", "hello", [], []) is None
    assert approval_first.names("approve helene's request", "Hélène Dupont") and not approval_first.names("approve emmanuel", "Emma")


@test
def a_manager_asked_what_is_pending_is_told_about_every_request_the_tool_returned():
    def nudge(reply, q="any pending request", called=("get_pending_requests",), sources=None):
        return approval_first.nudge("manager", q, reply, list(called), sources)
    hugo = {"request_id": 3, "employee_name": "Hugo", "leave_type": "casual", "start": "2026-11-16", "end": "2026-11-16", "days": 1}
    clara = {"request_id": 4, "employee_name": "Clara", "leave_type": "sick", "start": "2026-11-12", "end": "2026-11-12", "days": 1}
    emma = {"request_id": 409, "employee_name": "Emma", "leave_type": "casual", "start": "2026-11-23", "end": "2026-11-23", "days": 1}
    three = [json.dumps({"pending_requests": [hugo, clara, emma]})]
    for reply in ["Two pending requests:\n\n- **Hugo** (16 Nov): 1 casual day\n- **Clara** (12 Nov): 1 sick day",                  # the real bug, 8 of 8: the new one left out
                  "The three pending requests now are Hugo's and Clara's.", "Emma's request is already approved. The pending requests now are Hugo (16 Nov) and Clara (12 Nov).",
                  "There are no other pending requests.", "No pending requests right now.", "Hugo and Clara each have one request waiting: 2 requests.",
                  "Il y a deux demandes en attente : Hugo et Clara."]:
        fix = nudge(reply, sources=three)
        assert fix and "3 requests" in fix and "Emma, request 409, casual leave, Mon 23 Nov 2026" in fix and "does not present" in fix, reply
        assert "Hugo, request 3" in fix and "Clara, request 4, sick leave, Thu 12 Nov 2026" in fix
    assert "present Emma as waiting" in nudge("Two pending requests: Hugo and Clara.", sources=three)
    assert "present Hugo, Clara as waiting" in nudge("One pending request: Emma, 23 Nov.", sources=three)
    for reply in ["Three pending requests:\n- Hugo (16 Nov)\n- Clara (12 Nov)\n- Emma (23 Nov)", "You have 3 pending requests from Hugo, Clara and Emma.",
                  "Hugo's, Clara's and Emma's requests are waiting for you."]:
        assert nudge(reply, sources=three) is None, reply                                                              # complete: left alone
    assert nudge("Two pending requests: Hugo and Clara.", "does hugo have a pending request?", sources=three) is None       # a question about one person
    assert nudge("Hugo has one pending request (16 Nov).", "what about Hugo?", sources=three) is None
    assert nudge("Emma's approval is ready to confirm in the app.", "what is Emma waiting for", sources=three) is None
    assert nudge("Two pending requests: Hugo and Clara.", called=(), sources=three) is not None                          # the list is the tool's, however it got into the turn
    assert nudge("Two pending requests: Hugo and Clara.", sources=[]) is None and nudge("Two pending requests.", sources=["not json"]) is None   # no list this turn: nothing to compare
    assert nudge("Two pending requests: Hugo and Clara.", sources=[json.dumps({"pending_requests": []})]) is None      # nothing waiting
    assert nudge("Your balance is 7 days.", "how many casual days do i have", sources=three) is None                     # not a list, not about the requests
    assert nudge("Two pending requests: Hugo and Clara.", called=("get_pending_requests", "decide_leave"), sources=three) is None
    assert approval_first.nudge("employee", "any pending request", "Two pending requests: Hugo.", ["get_pending_requests"], three) is None   # only a manager gets this list
    one = [json.dumps({"pending_requests": [emma]})]
    assert "1 request " in nudge("No pending requests right now.", sources=one) and "present Emma as waiting" in nudge("No pending requests right now.", sources=one)
    assert nudge("Three pending requests: Hugo, Clara, and Emma's, which is already approved.", sources=three) is not None   # named, but called decided
    for reply in ["Two pending requests remain:\n\n- **Hugo** (16 Nov): 1 casual day\n- **Clara** (12 Nov): 1 sick day\n\nEmma's leave (23 Nov) has been approved.",   # 5 of 6, with the confirmation lines out of the chat
                  "Two pending requests remain: Hugo and Clara. Emma's request is approved.", "Emma's leave request (23 Nov, casual) is already approved. The pending requests now are Hugo and Clara."]:
        assert nudge(reply, sources=three), reply
    for reply in ["Three pending requests:\n- Hugo (16 Nov)\n- Clara (12 Nov)\n- Emma (23 Nov): not yet approved",
                  "Three pending requests: Hugo, Clara and Emma. Emma's 23 Nov leave is waiting to be approved.",
                  "Three pending requests: Hugo, Clara and Emma, which will be approved once you confirm."]:
        assert nudge(reply, sources=three) is None, reply
    assert nudge("Three pending requests: Hugo, Clara and Emma. Emma's earlier request was approved yesterday.", sources=three) is None      # an earlier one, said as such


@test
def dates_already_requested_are_found_before_the_card_never_after_confirm():
    mon = next_monday()
    assert tools.existing_request("E001", d(mon), d(mon)) is None
    made = apply_leave("E001", "casual", d(mon), d(mon))["request"]["id"]
    found = tools.existing_request("E001", d(mon), d(mon))
    assert found["id"] == made and found["status"] == tools.PENDING
    assert tools.existing_request("E001", d(mon - timedelta(days=3)), d(mon + timedelta(days=3)))["id"] == made     # a range that touches it
    assert tools.existing_request("E001", d(mon + timedelta(days=1)), d(mon + timedelta(days=1))) is None           # the next day is free
    assert tools.existing_request("E002", d(mon), d(mon)) is None                                                    # someone else's day
    assert tools.already_requested(found) == {"request_id": made, "leave_type": "casual", "status": tools.PENDING, "when": tools.when_label(mon)}
    assert f"request {made} ({tools.PENDING})" in apply_leave("E001", "casual", d(mon), d(mon))["error"]            # apply_leave refuses with the same request
    assert decide_leave("M001", made, "approve").get("success")
    assert tools.existing_request("E001", d(mon), d(mon))["status"] == "approved"                                    # approved counts too
    assert cancel_leave("E001", made).get("cancelled")
    assert tools.existing_request("E001", d(mon), d(mon)) is None                                                    # a cancelled request leaves the day free
    assert apply_leave("E001", "casual", d(mon), d(mon)).get("success")


@test
def a_person_is_told_about_dates_they_already_asked_for_from_the_tools_not_the_old_chat():
    taken = json.dumps({"working_days": 1, "already_requested": {"request_id": 416, "leave_type": "casual", "status": "approved", "when": "Mon 23 Nov 2026"}})
    refused = json.dumps({"error": "You already have request 416 (approved), casual leave, Mon 23 Nov 2026. Nothing was submitted and no confirmation was shown.",
                          "already_requested": {"request_id": 416, "leave_type": "casual", "status": "approved", "when": "Mon 23 Nov 2026"}})
    listing = json.dumps({"my_requests": [{"request_id": 1, "status": "cancelled"}, {"request_id": 416, "status": "approved"}, {"request_id": 417, "status": "pending_manager_approval"}]})
    nudge = existing_first.nudge
    said = 'You already have a pending casual leave request for 23 November. Check your app under "My requests".'      # the real reply, 11 Nov
    assert nudge("employee", "causual", said, [], []) is existing_first.CHECK_FIRST          # no tool result shows it: it came from the old chat
    assert nudge("employee", "causual", "Vous avez déjà une demande en attente pour le 23 novembre.", [], []) is existing_first.CHECK_FIRST
    assert "approved" in nudge("employee", "causual", said, ["apply_leave"], [refused])       # the tool says approved: the reply is sent back with the real status
    assert "approved" in nudge("employee", "23 nov", said, ["count_leave_days"], [taken])
    assert nudge("employee", "causual", "You already have an approved casual leave request for 23 November.", ["apply_leave"], [refused]) is None
    assert nudge("employee", "23 nov", "You already have request 416 (approved) for Mon 23 Nov: nothing was booked.", ["count_leave_days"], [taken]) is None
    assert nudge("employee", "my requests", "You have an approved request for 23 Nov and a pending one for 25 Nov.", ["get_my_requests"], [listing]) is None   # not a claim
    assert nudge("employee", "my requests", "You already have a pending request for 25 Nov.", ["get_my_requests"], [listing]) is None
    assert nudge("employee", "my requests", "You already have a rejected request for 25 Nov.", ["get_my_requests"], [listing]) is not None   # none is rejected
    assert nudge("employee", "x", "You already have a request for 23 Nov.", ["get_my_requests"], [json.dumps({"my_requests": []})]) is None       # looked, and it says no status
    assert nudge("manager", "any pending requests", "You already have a pending request from Hugo.", ["get_pending_requests"], []) is None              # the team's, not theirs
    for inbox in ["You have 1 leave request waiting for your approval:\n\n**Emma** has requested 6 days of paid leave.",
                  "You have one pending leave request and one alert:", "You have one leave request waiting for your decision."]:
        assert nudge("manager", "What needs my attention?", inbox, ["get_needs_attention"], []) is None, inbox          # a manager's inbox is not a claim about their own dates
        assert nudge("manager", "What needs my attention?", inbox, [], []) is None, inbox
    assert nudge("employee", "23 nov", "You've already got casual leave approved for 23 November.", [], []) is existing_first.CHECK_FIRST
    assert nudge("employee", "23 nov", "You've already booked casual leave on 23 November: it is waiting for your manager.", [], []) is existing_first.CHECK_FIRST
    assert nudge("employee", "balance", "You already have 5 casual days left.", [], []) is None                     # a balance, not a request
    assert nudge("new_hire", "x", said, [], []) is None                                                            # a new hire cannot book leave at all
    # given a date, the assistant looks it up before asking which kind of leave
    ask = "I need to know which type of leave you'd like for 23 November: casual, sick, or paid?"
    for text in ["23 nov", "nov 23", "23rd of november", "2026-11-23", "I want to take 23/11 off", "tomorrow", "next friday", "le 23 novembre", "vendredi"]:
        assert existing_first.has_date(text), text
    for text in ["request time off please", "causual", "how many days do i have", "yes"]:
        assert not existing_first.has_date(text), text
    assert nudge("employee", "23 nov", ask, [], []) is existing_first.COUNT_FIRST
    assert nudge("employee", "23 nov", ask, ["get_leave_balance"], []) is existing_first.COUNT_FIRST          # a balance is not a look at the dates
    assert nudge("employee", "23 nov", ask, ["count_leave_days"], [taken]) is None                              # it looked
    assert nudge("employee", "request time off please", "What dates, and what type of leave: casual, sick, or paid?", [], []) is None   # no date yet
    assert nudge("employee", "23 nov", "You have 5 casual days left: taking 23 Nov leaves 4.", ["count_leave_days"], [taken]) is None
    # a card is claimed only when one was made
    nocard = "You have 5 casual days left. Review it and press Confirm to submit it to your manager."
    assert nudge("employee", "casual", nocard, ["get_leave_balance"], []) is existing_first.NO_CARD
    assert nudge("employee", "casual", "A confirmation card is now in your app for 23 November.", [], []) is existing_first.NO_CARD
    assert nudge("employee", "casual", nocard, ["apply_leave"], []) is None
    assert nudge("employee", "casual", nocard, ["cancel_leave"], []) is None
    assert nudge("employee", "ok", nocard, [], [], card_open=True) is None                                     # a card from an earlier turn is still waiting
    assert nudge("employee", "casual", "Your request was submitted.", [], []) is None


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