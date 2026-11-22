# sensitive.py: harassment, discrimination, a person's health, disciplinary matters, dismissal and pay disputes about a specific person go to a
# human. The assistant gives no advice on them: it says it is passing the message to HR, gives HR's contact, opens a ticket marked sensitive
# and logs it. Checked here in code BEFORE the model is called, so nothing is searched or booked; the model's escalate_to_hr tool is only the
# backup for wording these patterns miss. Not for HR (the people it hands over to), a new hire's welcome chat or a candidate.
# Routine questions stay with the assistant: "how many sick days do I have?", "do I need a medical certificate?", "book me sick leave",
# "what is my notice period if I resign?", "my overtime is missing from my payslip" (a payslip question, answered with a ticket offer).
# No AI and no third-party package, so it is unit-tested in CI.
import re

import db
import employees
import tools

LABELS = {"harassment": "harassment", "discrimination": "discrimination", "health": "a health matter", "disciplinary": "a disciplinary matter",
          "dismissal": "dismissal", "pay_dispute": "a pay dispute"}
NOT_FOR = ("hr", "new_hire", "candidate")

_I = re.I
_PATTERNS = {
    "harassment": re.compile(
        r"\bharass|\bbull(?:y|ying|ied|ies)\b|\bkeeps?\s+(?:making|sending|saying|calling)\b.{0,40}\b(?:about|to)\s+me\b|"
        r"\b(?:comments?|remarks?|jokes?)\s+about\s+my\s+(?:body|looks|appearance|weight|clothes|accent|skin|religion|age)\b|"
        r"\bmakes?\s+me\s+(?:feel\s+)?uncomfortable\b|\b(?:touched|grabbed|groped)\s+me\b|\binappropriate(?:ly)?\s+(?:comments?|messages?|touch|behaviou?r|jokes?|remarks?)\b|"
        r"\b(?:yells?|shouts?|screams?|swears?)\s+at\s+me\b|\bhumiliat|\bintimidat|\bthreaten(?:s|ed|ing)?\s+me\b|\binsult(?:s|ed)?\s+me\b|"
        r"\bpicks?\s+on\s+me\b|\bhostile\s+(?:work\s+)?environment\b|\bsexual(?:ly)?\s+(?:assault|advances?|comments?|remarks?)\b|"
        r"\bharc[eè]l|\bm'humili|\bme\s+crie\s+dessus\b|\b(?:remarques|propos|blagues|commentaires|gestes)\s+(?:d[ée]plac[ée]s|sexistes|sur\s+mon|sur\s+ma|sur\s+mes)\b|"
        r"\bm'a\s+touch[ée]|\bme\s+touche\b|\bme\s+menace|\bm'insulte|\bmal\s+[àa]\s+l'aise\b|\bse\s+moque\s+de\s+moi\b", _I),
    "discrimination": re.compile(
        r"\bdiscriminat|\bracis[mt]|\bsexis[mt]|\bhomophob|\bageis[mt]|"
        r"\bbecause\s+(?:i'?m|i\s+am)\s+(?:a\s+woman|a\s+man|pregnant|black|muslim|jewish|gay|lesbian|trans|older|too\s+old|disabled|foreign|an?\s+immigrant)\b|"
        r"\bbecause\s+of\s+my\s+(?:age|gender|sex|race|religion|pregnancy|disability|origin|accent|skin|colou?r|sexual\s+orientation|nationality|name)\b|"
        r"\bdiscrimin|\bparce\s+que\s+je\s+suis\s+(?:une\s+femme|enceinte|noire?|musulmane?|juive?|homosexuelle?|trop\s+[âa]g[ée]e?|handicap[ée]e?|[ée]tranger|[ée]trang[èe]re)\b|"
        r"\b[àa]\s+cause\s+de\s+(?:mon|ma|mes)\s+(?:[âa]ge|sexe|origine|religion|grossesse|handicap|accent|couleur|nom|nationalit[ée])\b", _I),
    "health": re.compile(
        r"\bdepress(?:ed|ion)\b|\bburn(?:ed|t)?[\s-]?out\b|\banxiety\b|\bpanic\s+attacks?\b|\bmental\s+health\b|\bcan'?t\s+cope\b|\bsuicid|\bself[\s-]harm|"
        r"\bkill\s+myself\b|\bdiagnos(?:ed|is)\b|\bmy\s+(?:illness|condition|disease|cancer|treatment|chemo(?:therapy)?|therapy|therapist)\b|\bchronic\s+(?:illness|pain|condition)\b|"
        r"\bmedical\s+condition\b|\bd[ée]pression\b|\bd[ée]prim[ée]|\bburn[\s-]?out\b|\b[ée]puisement\s+professionnel\b|\banxi[ée]t[ée]\b|\bcrises?\s+d'angoisse\b|"
        r"\bsant[ée]\s+mentale\b|\bje\s+n'en\s+peux\s+plus\b|\bmaladie\s+(?:grave|chronique)\b|\bmon\s+(?:cancer|traitement)\b|\bme\s+suicider\b", _I),
    "disciplinary": re.compile(
        r"\bdisciplinary\b|\bwarning\s+letter\b|\b(?:written|verbal|final|formal)\s+warning\b|\b(?:got|received|given|get|give)\s+(?:a\s+|him\s+a\s+|her\s+a\s+|\w+\s+a\s+)?warning\b|"
        r"\bmisconduct\b|\binvestigation\s+(?:into|against|about)\s+(?:me|him|her)\b|\b(?:i\s+(?:was|am|got)|i'?ve\s+been)\s+suspended\b|"
        r"\bperformance\s+improvement\s+plan\b|\bdiscipline\s+(?:him|her|them|an?\s+employee|\w+)\b|"
        r"\bdisciplinaire\b|\bavertissement\b|\bmise\s+[àa]\s+pied\b|\bsanction(?:n[ée]e?)?\b|\bbl[âa]me\b|\bfaute\s+grave\b", _I),
    "dismissal": re.compile(
        r"\bfire\s+(?:me|him|her|them|\w+)\b|\bfired\b|\bfiring\b|\bdismiss(?:ed|al)?\b|\bsack(?:ed|ing)?\b|\blet\s+(?:me|him|her)\s+go\b|\blaid\s+off\b|\blay(?:ing)?\s+(?:me|him|her|\w+)?\s*off\b|"
        r"\bredundan|\bterminat(?:e|ed|ion)\s+(?:my|me|him|her|his|their)\b|\blose\s+my\s+job\b|"
        r"\blicenci|\bvir[ée]e?\b|\bme\s+virer\b|\brenvoy|\brupture\s+conventionnelle\b|\bperdre\s+mon\s+(?:emploi|travail|poste)\b|\bentretien\s+pr[ée]alable\b", _I),
    "pay_dispute": re.compile(
        r"\b(?:earns?|makes?|gets?|is\s+paid|are\s+paid)\s+more\s+than\s+me\b|\b(?:paid|earn(?:ing)?)\s+less\s+than\s+\w+|\bunderpaid\b|"
        r"\bowes?\s+me\s+(?:money|wages|salary|pay)\b|\bunpaid\s+(?:wages|salary)\b|\bwith(?:held|holding)\s+my\s+(?:pay|salary|wages)\b|"
        r"\bnot\s+(?:been\s+)?paid\s+(?:fairly|what\s+i'?m\s+owed)\b|\bunequal\s+pay\b|\bpay\s+(?:gap|discrimination)\b|"
        r"\bgagne\s+plus\s+que\s+moi\b|\bpay[ée]e?\s+moins\s+que\b|\bsous[\s-]pay[ée]e?\b|\bon\s+me\s+doit\b|\bsalaire\s+(?:impay[ée]|non\s+vers[ée])\b|"
        r"\b(?:pas|jamais)\s+(?:[ée]t[ée]\s+)?pay[ée]e?\s+(?:pour|ce\s+qu)", _I),
}
# routine questions that share words with the patterns above, answered by the assistant as usual
_ROUTINE = re.compile(r"\bif\s+i\s+resign\b|\bsi\s+je\s+d[ée]missionne\b|\bfire\s+(?:alarm|drill|exit|safety|extinguisher)\b|\b(?:is\s+there|what\s+is)\s+(?:a|the)\s+(?:policy|training)\b", _I)
# a question about the policy itself ("Where is the harassment policy?", "How do I report harassment?", "Quelle est la procédure disciplinaire ?") is
# answered from the policy, with HR's contact: it is passed on only when it also tells the person's own situation ("...by my manager", "on m'a...")
_ABOUT_POLICY = re.compile(
    r"\b(?:polic(?:y|ies)|procedures?|process|rules?|guidelines?|training|code\s+of\s+conduct|where\s+(?:is|are|can\s+i\s+find)|"
    r"how\s+(?:do|can|should|would)\s+(?:i|we|you|employees|someone|one)\s+(?:report|file|raise|make|flag)|"
    r"politique|proc[ée]dures?|r[èe]glement|charte|formation|o[ùu]\s+(?:est|sont|trouver)|comment\s+(?:signaler|d[ée]clarer|faire\s+un\s+signalement)|existe-t-il)\b", _I)
_OWN_SITUATION = re.compile(
    r"\b(?:me|my|myself|mine|i'?m|i\s+am|i\s+was|i'?ve|i\s+got|i\s+have\s+been|i\s+feel|j'ai|je\s+suis|je\s+me\s+sens|moi|"
    r"mon\s+(?:manager|chef|coll[èe]gue|responsable)|ma\s+(?:manager|cheffe|coll[èe]gue|responsable))\b|\bm'|\bon\s+m'", _I)
_FRENCH = re.compile(r"\b(?:je|j'|mon|ma|mes|est-ce|peut|suis|une?|le|la|les|des|du|qu'|avec|pour|pas)\b", _I)
_SELF_HARM = re.compile(r"\bsuicid|\bself[\s-]harm|\bkill\s+myself\b|\bme\s+suicider\b|\ben\s+finir\b", _I)


def classify(role, text):
    """The kind of sensitive matter the message is about, or None. Everyone but HR, a new hire and a candidate (an unknown role is an employee)."""
    text = str(text or "")
    if role in NOT_FOR or not text.strip() or _ROUTINE.search(text):
        return None
    if _ABOUT_POLICY.search(text) and not _OWN_SITUATION.search(text):
        return None
    for kind, pattern in _PATTERNS.items():
        if pattern.search(text):
            return kind
    return None


# the topics, asked about in general: the answer comes from the policy, and code adds HR's contact after it (agent.py)
_TOPIC = re.compile(
    r"\bharass|\bharc[eè]l|\bdiscrimin|\bdisciplin|\bdismiss|\blicenci|\bwarning\b|\bavertissement|\bsanction|\bon\s+sick\s+leave\b|"
    r"\ben\s+arr[êe]t\s+maladie\b|\bbull(?:y|ying)\b|\bunequal\s+pay\b", _I)


def policy_question(role, text):
    """True for a question about one of these topics in general ("How do I report harassment?", "Quelle est la procédure disciplinaire ?",
    "What happens if I'm on sick leave for more than 3 days?"): searched in the policy, then HR's contact is given. Never for a matter that is
    passed on (classify), nor for a routine question that does not name the topic ("How many sick days do I have?")."""
    return role not in NOT_FOR and classify(role, text) is None and bool(_TOPIC.search(str(text or "")))


def contact_line(text, contact):
    name, email = contact
    if not email:
        return ""
    if bool(_FRENCH.search(str(text or ""))) and not re.search(r"\b(?:the|my|is|are|i|you|what|can|how|where)\b", str(text or ""), re.I):
        return f"Si vous souhaitez en parler à quelqu'un, vous pouvez contacter {name} aux RH ({email})."
    return f"If you'd like to talk it through with someone, you can contact {name} at HR ({email})."


def hr_contact():
    """HR's contact: the first active HR person (name, email)."""
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT id, name, email FROM employees WHERE role = 'hr' ORDER BY id").fetchall()
    finally:
        conn.close()
    here = [r for r in rows if employees.is_active(r["id"])]
    return (here[0]["name"], here[0]["email"]) if here else ("HR", "")


def init_escalations():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensitive_escalations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,   -- UTC
                employee_id TEXT NOT NULL,
                category TEXT NOT NULL,              -- harassment, discrimination, health, disciplinary, dismissal, pay_dispute
                found_by TEXT NOT NULL,              -- code (the patterns here) or model (its escalate_to_hr tool)
                ticket_id INTEGER                    -- the HR ticket that carries the person's words
            )""")
    conn.close()


def escalate(employee_id, kind, text, found_by):
    """Open the HR ticket (the person's own words, marked sensitive), log it, and return the reply the person gets."""
    init_escalations()
    label = LABELS.get(kind, "a sensitive matter")
    ticket = tools.create_hr_ticket(employee_id, text, f"Sensitive ({label}): passed to HR by the assistant, no advice given")
    conn = db.get_conn()
    with conn:
        conn.execute("INSERT INTO sensitive_escalations (employee_id, category, found_by, ticket_id) VALUES (?, ?, ?, ?)",
                     (employee_id, kind if kind in LABELS else "other", found_by, ticket.get("id")))
    conn.close()
    return reply(kind, text, ticket.get("ticket"), hr_contact())


def reply(kind, text, ticket, contact):
    name, email = contact
    who = f"{name} ({email})" if email else name
    french = bool(_FRENCH.search(str(text or ""))) and not re.search(r"\b(?:the|my|is|are|i|you|what|can)\b", str(text or ""), re.I)
    urgent = _SELF_HARM.search(str(text or ""))
    if french:
        out = ("C'est une question qu'une personne des RH doit traiter, donc je ne donne pas de conseil ici : je la transmets aux RH maintenant"
               + (f" (demande {ticket})" if ticket else "") + f". Vous pouvez aussi contacter directement {who}.")
        if urgent:
            out += " Si vous pensez à vous faire du mal, appelez le 3114 (gratuit, 24 h/24) ou le 112 maintenant."
        return out
    out = ("This is something a person at HR should handle, so I won't give advice on it here: I'm passing it to HR now"
           + (f" (request {ticket})" if ticket else "") + f". You can also contact {who} directly.")
    if urgent:
        out += " If you are thinking of harming yourself, please call 3114 (free, 24/7) or 112 now."
    return out
