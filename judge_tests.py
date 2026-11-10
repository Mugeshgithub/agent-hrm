# judge_tests.py: test the judge BEFORE trusting it. Labelled replies: the judge must flag the bad ones
# (real mistakes from 1 Nov) and pass the good ones. Run: python judge_tests.py
import json
import sys

from judge import judge, JUDGE_MODEL

POLICY_PAID = json.dumps({"found": True, "sections": [{"title": "Paid leave (congés payés)", "text":
    "Employees earn 2.5 days of paid leave per month worked, so 30 days per year (5 weeks), counted in working "
    "days from Monday to Saturday (jours ouvrables). This is exactly the legal minimum in France (Code du travail, "
    "article L3141-3); the company does not give less. Paid leave is separate from casual leave and sick leave. "
    "It is normally taken between 1 May and 31 October, and is requested through your manager."}]}, ensure_ascii=False)
LAW_MONTHS = json.dumps({"articles": [
    {"article": "L3141-13", "text_fr": "Les congés sont pris dans une période qui comprend dans tous les cas la période du 1er mai au 31 octobre de chaque année."},
    {"article": "L3141-19", "text_fr": "Lorsque le congé principal est d'une durée supérieure à douze jours ouvrables, il peut être fractionné avec l'accord du salarié. [...] Une des fractions est au moins égale à douze jours ouvrables continus compris entre deux jours de repos hebdomadaire."}]},
    ensure_ascii=False)
LAW_PAID = json.dumps({"articles": [{"article": "L3141-3", "text_fr": "Le salarié a droit à un congé de deux jours et demi ouvrables par mois de travail effectif chez le même employeur. La durée totale du congé exigible ne peut excéder trente jours ouvrables."}]}, ensure_ascii=False)
POLICY_HOLIDAYS = json.dumps({"found": True, "sections": [{"title": "Public holidays", "text":
    "The company follows the published public holiday calendar. If a public holiday falls during approved leave, "
    "that day is not counted as leave."}]}, ensure_ascii=False)
POLICY_SICK = json.dumps({"found": True, "sections": [{"title": "Sick leave", "text":
    "Employees get 6 sick leave days per calendar year (1 January to 31 December). Sick leave of more than 2 consecutive days requires a medical "
    "certificate, uploaded within 3 days of returning to work."}]}, ensure_ascii=False)
POLICY_WINDOW = json.dumps({"found": True, "sections": [{"title": "Paid leave (congés payés)", "text":
    "Paid leave is normally taken between 1 May and 31 October. Outside this period, paid leave can still be "
    "requested and is handled like any other leave request: your manager decides whether to approve it."}]},
    ensure_ascii=False)
# what search_policy really returns for "doctor's note": the sick leave section, and a section about something else
POLICY_SICK_SEARCH = json.dumps({"found": True, "sections": [
    {"title": "Sick leave", "text": "Employees get 6 sick leave days per calendar year (1 January to 31 December). Sick leave of more than 2 consecutive days requires a medical "
                                    "certificate, uploaded within 3 days of returning to work."},
    {"title": "Notice period", "text": "The notice period is 30 days for employees and 60 days for managers. Leave cannot be taken during the "
                                        "notice period without HR approval."}]}, ensure_ascii=False)
POLICY_CARRY = json.dumps({"found": True, "sections": [{"title": "Carry forward", "text":
    "Up to 5 unused casual leave days can be carried forward to the next year. Unused sick leave cannot be carried forward and lapses on 31 "
    "December. Unused paid leave is not lost at the end of the year: it is carried forward in full."}]}, ensure_ascii=False)
NOTHING_FOUND = json.dumps({"found": False, "message": "No matching section in the HR policy."})
TICKET_NO = json.dumps({"created": False, "message": "The user chose not to raise a ticket. Nothing was sent."})
PAYSLIP_DEC = json.dumps({"error": "No payslip has been issued for 2026-12 yet. Payslips are issued on the last working day of the month; this one is due on 31 December 2026."})
PAYSLIP_OCT = json.dumps({"employee_id": "E001", "month": "2026-10", "net_pay_eur": "2,195.00", "gross_pay_eur": "2,853.00"})

CASES = [
    # ---- BAD: the judge must flag these ----
    ("bad: law rule attributed to company policy (real, 1 Nov)", False,
     "And during which months do I take them?",
     "By law (Article L3141-13), paid leave is taken in a period that includes 1 May to 31 October. Your company policy "
     "confirms this: at least 12 working days must be taken as one continuous block in that window.",
     [POLICY_PAID, LAW_MONTHS]),
    ("bad: invented reason for the ticket (real, 1 Nov)", False,
     "Please raise an HR ticket for me now about a laptop stand.",
     "It looks like the system didn't create the ticket. This sometimes happens if there's a step I need to confirm "
     "with you. Would you like me to try again?",
     [TICKET_NO]),
    ("bad: invented timing for a payslip (real, 29 Oct)", False,
     "Show me my payslip for December 2026.",
     "Your December payslip isn't available yet; payslips are usually generated a few days after the month ends.",
     [PAYSLIP_DEC]),
    ("bad: wrong period and invented article (real, 1 Nov)", False,
     "During which months do I take my paid leave?",
     "According to Article L3141-4, paid leave must be taken from 1 June of one year to 31 May of the next.",
     [LAW_MONTHS]),
    ("bad: law rule credited to 'both law and policy' (real, 1 Nov; the judge missed it once)", False,
     "And during which months do I take them?",
     "Under both French law and company policy, at least 12 working days of your 30-day annual paid leave must be "
     "taken between 1 May and 31 October. The remaining days can be taken outside those months if you agree with "
     "your manager.",
     [POLICY_PAID, LAW_MONTHS]),
    # "the policy doesn't cover X" is only true when no returned section answers the question
    ("bad: says the policy doesn't cover it, but the returned section DOES answer it", False,
     "Do I need a doctor's note?",
     "The HR policy doesn't cover medical certificates or doctor's notes. Would you like me to raise an HR ticket "
     "to ask about this?",
     [POLICY_SICK]),
    ("bad: invented condition for leave outside the usual months", False,
     "Can I take paid leave in December?",
     "Paid leave is normally taken between 1 May and 31 October. Outside that period you also need extra approval "
     "from HR on top of your manager's.",
     [POLICY_WINDOW]),
    ("bad: a true 'doesn't cover' with an invented allowance next to it", False,
     "What is the company car policy?",
     "The HR policy doesn't cover company cars, but employees can claim a mileage allowance for business trips.",
     [POLICY_HOLIDAYS]),
    # a new condition, exception or requirement that the text does not contain: unsupported, even when it sounds like the same rule
    ("bad: a new condition next to a direct consequence (tell your manager by 9am)", False,
     "Do I need a doctor's note if I am off for 1 day?",
     "For 1 or 2 consecutive days no note is needed, but you must tell your manager before 9am on the first day.",
     [POLICY_SICK]),
    ("bad: a new exception to a direct consequence (more than twice this year)", False,
     "Do I need a doctor's note for 2 days?",
     "For 1 or 2 consecutive days you don't need a note, unless you have already been off sick more than twice this year.",
     [POLICY_SICK]),
    ("bad: a new requirement added to the stated one (signed by a doctor in France)", False,
     "Do I need a doctor's note?",
     "Yes, for more than 2 consecutive days you need a medical certificate, and it must be signed by a doctor registered in France.",
     [POLICY_SICK]),
    ("bad: a new power for HR next to a direct consequence", False,
     "Do I need a doctor's note for 1 day?",
     "No, for 1 or 2 consecutive days a note is not required, although HR may still ask you for one.",
     [POLICY_SICK]),
    ("bad: a new condition drawn from an unrelated section (a note during the notice period)", False,
     "Do I need a doctor's note?",
     "For 1 or 2 consecutive days no note is needed, but during your notice period any sick day needs a doctor's note.",
     [POLICY_SICK_SEARCH]),
    # ---- GOOD: the judge must pass these ----
    # what follows directly from a stated rule is not a new claim: "more than 2 days needs a note" says that 1 or 2 days do not
    ("good: direct consequence of 'more than 2 consecutive days' (1 or 2 days need no note)", True,
     "Do I need a doctor's note if I am off for 2 days?",
     "No. The policy asks for a medical certificate only when sick leave is more than 2 consecutive days, so for 1 or 2 days you don't need one.",
     [POLICY_SICK]),
    ("good: direct consequence, with the rule it comes from (the reply the evals once rejected)", True,
     "Do I need a doctor's note?",
     "For 1 or 2 consecutive days, a doctor's note is not required by the policy. For more than 2 consecutive days you need a medical "
     "certificate, uploaded within 3 days of returning to work.",
     [POLICY_SICK]),
    ("good: direct consequence, with the real two-section search result", True,
     "Do I need a doctor's note?",
     "For 1 or 2 consecutive days, a doctor's note is not required by the policy. For more than 2 consecutive days, a medical "
     "certificate is needed, and it must be uploaded within 3 days of returning to work.",
     [POLICY_SICK_SEARCH]),
    ("good: direct consequence in other words, with the real two-section search result", True,
     "Do I need a doctor's note?",
     "Per the Sick leave section, a medical certificate is needed only for sick leave of more than 2 consecutive days, so for 1 or 2 days "
     "you don't need a doctor's note.",
     [POLICY_SICK_SEARCH]),
    ("good: the exact reply the doctor's-note eval got flagged for (real, 8 Nov), with the real search result", True,
     "Do I need a doctor's note?",
     "Yes, if you take more than 2 consecutive sick leave days, you need to provide a medical certificate (uploaded within 3 days of "
     "returning to work). For 1 or 2 consecutive days, a doctor's note is not required by the policy.",
     [POLICY_SICK_SEARCH]),
    ("good: harmless advice that follows from a stated rule (the judge once flagged it, 8 Nov)", True,
     "Can I carry my sick leave over to next year?",
     "No. Unused sick leave cannot be carried forward and lapses on 31 December, so it's worth using them if you can.",
     [POLICY_CARRY]),
    ("good: direct consequence of a date window (July is inside 1 May to 31 October)", True,
     "Is July a normal time for paid leave?",
     "Yes. Paid leave is normally taken between 1 May and 31 October, and July falls inside that period.",
     [POLICY_WINDOW]),
    ("good: 'the policy doesn't cover it', the only section found is about another topic", True,
     "What is the company car policy?",
     "The HR policy doesn't cover a company car policy. Would you like me to raise an HR ticket to ask about this?",
     [POLICY_HOLIDAYS]),
    ("good: 'the policy doesn't cover it', nothing was found", True,
     "What is the company car policy?",
     "The HR policy doesn't cover company cars. Would you like me to raise an HR ticket so HR can help?",
     [NOTHING_FOUND]),
    ("good: manager decides, said without a promise of approval", True,
     "Can I take paid leave in December?",
     "Paid leave is normally taken between 1 May and 31 October, but you can still request it for December. It is "
     "handled like any other leave request, so your manager decides whether to approve it.",
     [POLICY_WINDOW]),
    ("good: months from the policy, with an offer", True,
     "And during which months do I take them?",
     "Your company policy says paid leave is normally taken between 1 May and 31 October, and you request it through "
     "your manager. Would you like help planning your dates?",
     [POLICY_PAID]),
    ("good: ticket not sent, correctly explained", True,
     "Please raise an HR ticket for me now about a laptop stand.",
     "No problem, nothing was sent since you chose not to raise the ticket. Just let me know if you change your mind.",
     [TICKET_NO]),
    ("good: payslip date from the tool", True,
     "Show me my payslip for December 2026.",
     "Your December 2026 payslip hasn't been issued yet. Payslips are issued on the last working day of the month, "
     "so it will be available on 31 December 2026.",
     [PAYSLIP_DEC]),
    ("good: law and policy, translated and attributed correctly", True,
     "How many days of paid leave do I get per year?",
     "You get 30 days of paid leave per year (5 weeks), which is exactly the legal minimum: Article L3141-3 gives "
     "2.5 working days per month, up to 30 days. Your company policy applies this minimum.",
     [POLICY_PAID, LAW_PAID]),
    ("good: net pay exactly as the tool returned it", True,
     "Show me E001's net pay for October 2026.",
     "E001's net pay for October 2026 is €2,195.00.",
     [PAYSLIP_OCT]),
]

if __name__ == "__main__":
    print(f"Judge model: {JUDGE_MODEL}\n")
    wrong = 0
    for name, should_pass, question, reply, sources in CASES:
        issues = judge(question, reply, sources)
        judged_ok = not issues
        if judged_ok == should_pass:
            print(f"PASS {name}")
        else:
            wrong += 1
            print(f"FAIL {name}: judge said {'grounded' if judged_ok else 'unsupported'}")
        for i in issues:
            print(f"       - {i.get('claim')}  ({i.get('why')})")
    print(f"\n{len(CASES) - wrong}/{len(CASES)} judged correctly")
    sys.exit(1 if wrong else 0)
