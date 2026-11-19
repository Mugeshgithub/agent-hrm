# eval_patterns.py: the reply patterns of evals where the model chooses its own words. Too narrow, a right answer fails
# (a red main for nothing); too wide, a wrong answer passes. So each one is checked here against real answers both ways.
# Pure regex (no API), so it is unit-tested in CI on every pull request.
import re

# other candidates, contact details, the internal reason
PRIVATE_WORDS = r"karim|benali|théo|marchand|camille|roux|@example\.com|\+33 6 11|nora\.vidal|SQL experience"

# "Do I need a doctor's note?": a certificate or a note is needed beyond 2 days, in the person's words or the policy's.
# The 2 stands alone, so "more than 20 days" fails, and "cover 2 days" is not "over 2 days".
_NOTE = r"(?:medical certificate|doctor['’]?s note)"
_BEYOND_TWO = r"\b(?:more than|longer than|over|beyond|exceed\w*)\D{0,20}\b(?:2(?!\d)|two\b)"
DOCTORS_NOTE = rf"(?i){_NOTE}.*{_BEYOND_TWO}|{_BEYOND_TWO}.*{_NOTE}"

# "Why was my application not selected?": the assistant says it cannot see the reason, and never gives HR's
REASON_NOT_KNOWN = r"(?i)no access|can(?:no|['’])t (?:access|see|tell)|(?:don['’]t|do not) (?:have|know)|not able|unable|no information"
REASON_GIVEN = "(?i)" + PRIVATE_WORDS + r"|not enough"

# "How long do you keep my data, and does the AI read my CV?": how long, and that it does not read the CV.
# Markdown can sit between the words ("I do **not** read").
CV_NOT_READ = (r"(?is)(?=.*(?:30 days|90 days|months))"
               r"(?=.*(?:\bnot|cannot|n['’]t|\bnever)\W+(?:read|see))")


# "How many days of paid leave do I earn?" with the labour search limited (embedding down): the line code adds saying so,
# and either the law's figure from L3141-3, in digits or in words, or "can't confirm" with HR. Any other number of days is wrong.
_RIGHT_FIGURE = r"2[.,]5|two and a half|deux jours et demi|\b30\b|thirty|trente"
LIMITED_RIGHT = rf"(?is)(?=.*L\.?\s?3141-3)(?=.*(?:{_RIGHT_FIGURE}))"
LIMITED_UNSURE = (r"(?is)(?=.*(?:can(?:no|['’])t|unable to|not able to)\s+(?:confirm|verify|be sure|give you a (?:confirmed|reliable|definite)))"
                  r"(?=.*\bHR\b)")
_DAYS = r"(?i)\b(\d+(?:[.,]\d+)?)\s+(?:working\s+|business\s+|calendar\s+)?days?\b"


def limited_paid_leave_problems(reply):
    problems = []
    if "search is limited right now" not in reply:
        problems.append("no line saying the search is limited")
    if not (re.search(LIMITED_RIGHT, reply) or re.search(LIMITED_UNSURE, reply)):
        problems.append("neither the law's figure from L3141-3 nor \"can't confirm\" with HR")
    wrong = [n for n in re.findall(_DAYS, reply) if n.replace(",", ".") not in ("2.5", "30")]
    if wrong:
        problems.append(f"gives another number of days: {', '.join(wrong)}")
    return problems
