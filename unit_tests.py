# unit_tests.py: fast checks of the business rules. No AI, no API keys, no cost.
import json
import sys
from datetime import date, timedelta

import db
db.DB_PATH = "unit_test_hr.db"   # its own database, never the real hr.db

import collective_agreement
import contracts
import document_answers
import documents
import employee_files
import jobs
import number_guard
import pdf_text
import policy_gaps
import policy_store
import recruitment_guard
import search_first
import tool_guard
import tools
from eval_retry import MAX_RETRIED, retry_report, run_with_retry
from judge_parse import verdict
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
    assert "No days" in apply_leave("E001", "paid", d(SUN), d(SUN))["error"]


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