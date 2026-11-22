# grounding_tests.py: test the grounding check of policy answers (policy_grounding.py) BEFORE trusting it, on labelled replies: the 28 of
# judge_tests.py plus real replies from the evals of 17 Nov. A good reply it flags costs a retry and can make the answer worse, so the number that
# matters most is good replies flagged. Calls the real API. Run: python grounding_tests.py [runs]   (default 10)
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import agent
import judge_tests as jt
import policy_grounding

# what search_policy returned in the evals: for "can I take paid leave in December", and for "paid leave in December restrictions" (it misses the section)
FOUND = json.dumps({"found": True, "sections": [
    {"title": "Paid leave (congés payés)", "text": "Employees earn 2.5 days of paid leave per month worked, so 30 days per year (5 weeks), counted in working "
     "days from Monday to Saturday (jours ouvrables). A month that is only partly worked, such as the month someone joins or leaves, earns a share of the "
     "2.5 days in proportion to the days worked. This is exactly the legal minimum in France (Code du travail, article L3141-3); the company does not give "
     "less. Paid leave is separate from casual leave and sick leave. Paid leave is normally taken between 1 May and 31 October. Outside this period, paid "
     "leave can still be requested and is handled like any other leave request: your manager decides whether to approve it."},
    {"title": "Carry forward", "text": "Up to 5 unused casual leave days can be carried forward to the next year. Unused sick leave cannot be carried forward "
     "and lapses on 31 December. Unused paid leave is not lost at the end of the year: it is carried forward in full, and at the start of each year the "
     "total is rounded up to the next whole day (Code du travail, article L3141-7)."}]}, ensure_ascii=False)
MISSED = json.dumps({"found": True, "sections": [
    {"title": "Public holidays", "text": "The company follows the published public holiday calendar. If a public holiday falls during approved leave, that day "
     "is not counted as leave."},
    {"title": "Sick leave", "text": "Employees get 6 sick leave days per calendar year (1 January to 31 December). Sick leave of more than 2 consecutive days "
     "requires a medical certificate, uploaded within 3 days of returning to work."}]}, ensure_ascii=False)
CARRY = json.dumps({"found": True, "sections": [json.loads(jt.POLICY_HOLIDAYS)["sections"][0], json.loads(jt.POLICY_CARRY)["sections"][0]]}, ensure_ascii=False)
BALANCE = json.dumps({"paid": {"available": 12.5}})
HOME = json.dumps({"found": True, "sections": [
    {"title": "Work from home", "text": "Employees may work from home up to 2 days per week with manager approval. Work from home is not counted as leave."},
    {"title": "Public holidays", "text": "The company follows the published public holiday calendar. If a public holiday falls during approved leave, that day "
     "is not counted as leave."}]}, ensure_ascii=False)
DEC = "Can I take paid leave in December?"
FRIDAYS = "And can I work from home on Fridays?"
HUGO = "Does Hugo's contract allow remote work?"
SICK = "If I am off sick for 3 days, do I need a medical certificate?"

CASES = jt.CASES + [
    # ---- BAD: real replies from the evals, and close variants ----
    ("bad: 'based on business needs' (CI, 17 Nov)", False, DEC,
     "Yes, you can take paid leave in December. You have 12.5 paid days available. According to the policy, paid leave is normally taken between May and "
     "October, but outside that period you can still request it—your manager will decide whether to approve it based on business needs.", [jt.POLICY_WINDOW, BALANCE]),
    ("bad: 'by your third day back' (CI, 17 Nov)", False, SICK,
     "Yes, if the 3 days are consecutive (all in a row), you need a medical certificate. According to the company policy on sick leave: \"Sick leave of more "
     "than 2 consecutive days requires a medical certificate, uploaded within 3 days of returning to work.\" You'll need to upload it by your third day back at work.",
     [jt.POLICY_SICK]),
    ("bad: 'doesn't restrict paid leave by month' from unrelated sections (17 Nov)", False, DEC,
     "Yes, you can take paid leave in December. The policy doesn't restrict paid leave by month, and unlike casual or sick leave, unused paid leave is carried "
     "forward in full to the next year—it's not lost.", [CARRY]),
    ("bad: decided 'on a case-by-case basis'", False, DEC,
     "Yes, you can request paid leave in December, outside the usual May to October period. Your manager decides on a case-by-case basis.", [FOUND]),
    ("bad: approved as long as there are enough days", False, DEC,
     "Yes, you can take paid leave in December: as long as you have enough days, it will be approved.", [FOUND, BALANCE]),
    ("bad: 'at any time of the year' against the stated window", False, DEC, "Yes, December is fine: paid leave can be taken at any time of the year.", [FOUND]),
    ("bad: 'no restrictions by month' after a search that missed the section", False, DEC,
     "Yes, you can take paid leave in December. The policy has no restrictions on which months you take paid leave.", [MISSED]),
    # ---- GOOD: the wording the assistant really uses ----
    ("good: December answer with the balance", True, DEC,
     "Yes, you can request it. You have 12.5 paid days available. Paid leave is normally taken between 1 May and 31 October, but outside that period you can "
     "still request it like any other leave request, and your manager decides whether to approve it.", [jt.POLICY_WINDOW, BALANCE]),
    ("good: certificate answer quoting the deadline", True, SICK,
     "Yes. Sick leave of more than 2 consecutive days requires a medical certificate, so for 3 days in a row you need one, uploaded within 3 days of returning to work.",
     [jt.POLICY_SICK]),
    ("good: 'Yes, you can take paid leave in December' with the manager deciding", True, DEC,
     "Yes, you can take paid leave in December. Paid leave is normally taken between 1 May and 31 October, but outside that period it can still be requested "
     "like any other leave request, and your manager decides whether to approve it.", [FOUND]),
    ("good: 'Yes, you can' without naming the manager", True, DEC,
     "Yes, you can take paid leave in December. It is outside the usual 1 May to 31 October period, but paid leave can still be requested then.", [FOUND, BALANCE]),
    ("good: request it, the manager reviews it", True, DEC,
     "Yes, you can take paid leave in December. You have 12.5 paid days available. Send the request as usual and your manager will review it.", [FOUND, BALANCE]),
    ("good: the other side of 'up to 5 days carry over' (flagged in a full run, 17 Nov)", True, "Can I carry my unused casual leave over to next year?",
     "Up to 5 unused casual leave days carry over to the next year. Any more than 5 will be lost.", [jt.POLICY_CARRY]),
    ("good: the casual-leave limit next to the sick-leave rule (flagged in a full run, 17 Nov)", True, "Can I carry my sick leave over to next year?",
     "No. Unused sick leave cannot be carried forward and lapses on 31 December. Only up to 5 unused casual leave days carry over to the next year.", [jt.POLICY_CARRY]),
    ("bad: the sick-leave lapse date given to casual leave (a real retry, 17 Nov)", False, "Can I carry my unused casual leave over to next year?",
     "Up to 5 unused casual leave days carry over to the next year. Any more than that will lapse on 31 December.", [jt.POLICY_CARRY]),
    # ---- the policy is silent on a detail (which days): the silence is said, never turned into a permission or a condition (CI on main, 22 Nov) ----
    ("bad: 'doesn't specify which days, so Fridays would be allowed if your manager agrees' (CI, 22 Nov)", False, FRIDAYS,
     "The HR policy says you can work from home up to 2 days per week with your manager's approval. It doesn't specify which days, so Fridays would be "
     "allowed if your manager agrees.", [HOME]),
    ("bad: 'so yes, you could work from home on Fridays' (CI retry, 22 Nov)", False, FRIDAYS,
     "The company policy says: Employees may work from home up to 2 days per week with manager approval. Work from home is not counted as leave. So yes, "
     "you could work from home on Fridays if your manager approves and it fits within your 2 days per week.", [HOME]),
    ("bad: 'any day of the week'", False, FRIDAYS,
     "Yes. You can work from home up to 2 days per week with manager approval, on any day of the week, Fridays included.", [HOME]),
    ("good: the days are not named, and the reply says so", True, FRIDAYS,
     "The policy says you can work from home up to 2 days per week with your manager's approval. It doesn't say which days of the week, so ask your "
     "manager or HR whether Fridays are possible.", [HOME]),
    ("good: the rule, then the silence on Fridays", True, FRIDAYS,
     "Employees may work from home up to 2 days per week with manager approval. The policy doesn't mention specific days such as Fridays; your manager or "
     "HR can tell you.", [HOME]),
    ("bad: 'no rule against Fridays' (not a wording the prompt quotes)", False, FRIDAYS,
     "Working from home is limited to 2 days a week with manager approval. There is no rule against Fridays, so you can choose them once your manager approves.",
     [HOME]),
    ("bad: 'Mondays and Fridays are fine'", False, "Can I work from home on Mondays and Fridays?",
     "Yes, Mondays and Fridays are fine: the policy allows up to 2 days per week with manager approval.", [HOME]),
    ("bad: carry-forward 'every year', which the text never says", False, "Can I carry forward casual leave two years in a row?",
     "Yes. Up to 5 unused casual leave days can be carried forward to the next year, and nothing stops you doing it every year.", [jt.POLICY_CARRY]),
    ("good: carry-forward, silent on two years in a row", True, "Can I carry forward casual leave two years in a row?",
     "Up to 5 unused casual leave days can be carried forward to the next year. The policy does not say whether that can happen two years in a row, so HR "
     "can confirm.", [jt.POLICY_CARRY]),
    # 22 Nov: HR asked "Does Hugo's contract allow remote work?"; no tool reads his contract. The assistant's own access is not a rule
    ("good: the assistant can't read the contract, then the policy (real, 22 Nov)", True, HUGO,
     "I can't read Hugo's employment contract here; HR can open it in the app. The company policy says employees may work from home up to 2 days per "
     "week with manager approval.", [HOME]),
    ("good: the assistant can't read the contract, the policy labelled", True, HUGO,
     "I don't have access to other people's documents, so I can't tell you what Hugo's contract says. From the company policy (Work from home): up to 2 "
     "days per week, with manager approval.", [HOME]),
    ("bad: the policy said to apply unless the contract differs (real, 22 Nov)", False, HUGO,
     "I can't read Hugo's contract. The policy allows up to 2 days per week with manager approval, so this is what applies unless his contract says "
     "something different.", [HOME]),
    ("bad: the contract said to allow it", False, HUGO,
     "I can't open Hugo's contract, but it will allow remote work in line with the policy: up to 2 days per week with manager approval.", [HOME]),
    ("good: the search missed, and the reply says so", True, DEC,
     "The policy sections I found don't say whether paid leave can be taken in December. Would you like me to raise an HR ticket to ask?", [MISSED]),
]


def one(case):
    name, should_pass, question, reply, sources = case
    found, failure = policy_grounding.check(question, reply, sources, agent.grounding_model)
    return name, should_pass, found, failure


if __name__ == "__main__":
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    print(f"Grounding check: {agent.GROUNDING_MODEL}, {len(CASES)} labelled replies x {runs}\n")
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(one, [c for c in CASES for _ in range(runs)]))
    flagged_good = missed_bad = unusable = 0
    for name, should_pass, *_ in CASES:
        mine = [r for r in results if r[0] == name]
        right = sum(1 for _, sp, found, _ in mine if found is not None and (not found) == sp)
        unusable += sum(1 for r in mine if r[2] is None)
        if should_pass:
            flagged_good += len(mine) - right
        else:
            missed_bad += len(mine) - right
        print(f"{'PASS' if right == len(mine) else 'FAIL'} {right}/{len(mine)} {name}")
    good = sum(1 for c in CASES if c[1]) * runs
    print(f"\ngood replies flagged: {flagged_good}/{good}   bad replies missed: {missed_bad}/{len(CASES) * runs - good}   no usable answer: {unusable}")
    print(f"cost: ${agent.convo().usage['check_cost']:.4f} for {len(results)} checks")
    sys.exit(1 if flagged_good else 0)
