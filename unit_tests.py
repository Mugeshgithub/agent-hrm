# unit_tests.py: fast checks of the business rules. No AI, no API keys, no cost.
import sys
from datetime import date, timedelta

import db
db.DB_PATH = "unit_test_hr.db"   # its own database, never the real hr.db

import contracts
import documents
import employee_files
import pdf_text
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
    note = tools.PAID_POLICY_NOTE
    n = tools.paid_policy_note
    assert n(date(2030, 5, 1), date(2030, 5, 4)) is None          # the first day of the window
    assert n(date(2030, 7, 1), date(2030, 7, 6)) is None
    assert n(date(2030, 10, 28), date(2030, 10, 31)) is None      # the last day of the window
    assert n(date(2030, 12, 2), date(2030, 12, 7)) == note
    assert n(date(2030, 4, 29), date(2030, 5, 3)) == note         # starts before the window
    assert n(date(2030, 10, 30), date(2030, 11, 2)) == note       # runs past it
    assert n(date(2030, 12, 30), date(2031, 1, 3)) == note        # across New Year


@test
def the_note_and_the_window_are_read_from_hr_policy_md_and_the_note_carries_the_whole_policy():
    # hr_policy.md is the only place they are written: the note is that text, the window comes from its dates
    text = open("hr_policy.md", encoding="utf-8").read()
    assert tools.PAID_POLICY_NOTE in text
    assert tools.PAID_WINDOW == ((5, 1), (10, 31))
    # the note must not read as a blocker, or as a promise: outside the window it can still be requested, and
    # the manager decides
    assert "normally taken between 1 May and 31 October" in tools.PAID_POLICY_NOTE
    assert "can still be requested" in tools.PAID_POLICY_NOTE
    assert "your manager decides whether to approve it" in tools.PAID_POLICY_NOTE and "approves it" not in tools.PAID_POLICY_NOTE


@test
def paid_leave_results_carry_the_note_outside_the_window_and_other_leave_never_does():
    december = apply_leave("E001", "paid", "2030-12-02", "2030-12-04")
    assert december["success"] and december["policy_note"] == tools.PAID_POLICY_NOTE
    summer = apply_leave("E001", "paid", "2030-07-01", "2030-07-03")
    assert summer["success"] and "policy_note" not in summer
    casual = apply_leave("E001", "casual", "2030-12-09", "2030-12-09")       # a December casual day is normal
    assert casual["success"] and "policy_note" not in casual


@test
def a_balance_answer_about_paid_leave_has_the_note_and_other_balance_answers_do_not():
    assert get_leave_balance("E001", "paid")["policy_note"] == tools.PAID_POLICY_NOTE
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
             "get_needs_attention", "get_my_onboarding", "get_contract_status", "get_my_documents", "read_my_document"]
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
STAFF_ONLY = {"get_needs_attention"}      # the Needs you inbox: managers and HR, never employees


def names_for(role):
    import roles
    return {t["name"] for t in roles.tools_for(role, fake_tools())}


@test
def employee_gets_no_manager_or_hr_tools():
    names = names_for("employee")
    assert not names & (MANAGER_ONLY | HR_ONLY | STAFF_ONLY), names
    assert "apply_leave" in names and len(names) == 17, names


@test
def manager_gets_team_tools_but_not_hr_tools():
    names = names_for("manager")
    assert MANAGER_ONLY <= names and not names & HR_ONLY, names
    assert len(names) == 21, names


@test
def hr_gets_ticket_tools_but_not_manager_tools():
    names = names_for("hr")
    assert HR_ONLY <= names and not names & MANAGER_ONLY, names
    assert len(names) == 21 and "get_contract_status" in names, names


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
def after_a_document_has_been_read_the_policy_and_law_searches_are_not_offered_for_that_answer():
    tools = [{"name": n} for n in ("get_my_documents", "read_my_document", "search_policy", "search_labour_code", "get_leave_balance")]
    names = lambda called: [t["name"] for t in employee_files.tools_after(called, tools)]
    assert names([]) == [t["name"] for t in tools] and names(["search_policy"]) == [t["name"] for t in tools]       # not about a document yet
    assert names(["get_my_documents"]) == [t["name"] for t in tools]                                              # listing is not reading
    after = names(["get_my_documents", "read_my_document"])
    assert after == ["get_my_documents", "read_my_document", "get_leave_balance"]                                  # the document is the only source
    assert employee_files.DOCUMENT_ONLY_BLOCKS == {"search_policy", "search_labour_code"} and len(tools) == 5      # the list itself is not changed


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