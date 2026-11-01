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
    # ---- GOOD: the judge must pass these ----
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
