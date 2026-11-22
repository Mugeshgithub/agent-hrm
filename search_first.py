# search_first.py: a question about the company's rules is searched before it is answered, asked back or handed to HR.
# The prompt already says "call search_policy FIRST, before asking any clarifying question", and the model still skipped it now
# and then (measured: "Do I need a doctor's note?" 2 times in 10, "Can I get a company car?" 1 time in 10): it asked "which
# situation?" or said the topic was outside its tools and offered a ticket. So code checks the final reply: a rules question answered with no tool
# at all. Sent back with a note, the model sometimes replied "I'll search the policy first" and stopped (Nov 22, once the full history was kept), so
# now the code runs the search itself (agent.py): when a search earlier in this conversation already returned the section it finds first, the reply
# stands and is checked against those results; otherwise the reply is dropped, the code's search goes into the turn and the model answers again.
# No AI and no third-party package, so it is unit-tested in CI.
import json
import re

import roles

# the person asked something: a question mark, or a sentence that opens like a question
_QUESTION = re.compile(r"\?|^\s*(?:do|does|did|can|could|may|might|must|should|shall|is|are|am|will|would|how|what|when|which|where|who|why)\b", re.I)
# and it is about what the rules are or what is allowed (not "show my balance", not a greeting)
_RULES = re.compile(
    r"\b(?:need|needs|needed|require|requires|required|allowed|allow|permit|permitted|entitled|entitlement|eligible|eligibility|"
    r"policy|policies|rule|rules|have to|has to|must|obliged|mandatory|"
    r"(?:can|could|may|am|are|is|do|does) (?:i|we|they|an employee|employees|the company|hr)|"
    r"how (?:many|much|long|often|early|late|soon)|what happens|is there|are there)\b", re.I)

SEARCH_TOOLS = ("search_policy", "search_labour_code", "search_collective_agreement")
# tools that answer nothing about the rules: HR asked "Does Hugo's contract allow remote work?", looked Hugo up (or where a new hire's contract
# stands) and offered to search, or said "his contract hasn't been drafted yet" (22 Nov)
NOT_RULES_TOOLS = ("lookup_colleague", "get_contract_status")

# a question about seeing or acting on someone else's data ("Can I see my team's pending leave requests?", "les congés de Hugo") is about the
# person's role, not the rules: the role's tools and the prompt answer it, and a forced search only gave the grounding check a policy to reshape the
# reply around (Nov 22: the reply then named neither the manager nor HR, 1 in 5). Whose data: the team, colleagues, someone else, or a name
_DATA = (r"(?:pending |leave |sick |paid |casual )*(?:leave|requests?|balances?|absences?|holidays?|vacations?|time off|days off|payslips?|pay|"
         r"salar(?:y|ies)|contracts?|files?|data|calendars?|schedules?)")
# a capitalised word is a name, except the words of holidays and dates ("New Year's Day", "Labour Day's leave", "leave for Monday")
_NAME = (r"(?!(?:New|Day|Year|Eve|Christmas|Easter|Labour|Labor|Mother|Father|Valentine|Saint|St|Monday|Tuesday|Wednesday|Thursday|Friday|"
         r"Saturday|Sunday|January|February|March|April|May|June|July|August|September|October|November|December|HR|France)\b)[A-Z][a-zà-ÿ]+")
_OTHERS = (r"(?i:my (?:team|teams|colleagues?|co-?workers?|(?:direct )?reports|employees|staff)|someone else|somebody else|other people|others|"
           r"a colleague|another employee)")
_DONNEES = (r"(?:cong[ée]s|demandes?|soldes?|absences?|arr[êe]ts?(?: maladie)?|fiches? de paie|salaires?|contrats?|dossiers?|donn[ée]es|plannings?|calendriers?)"
            r"(?: en attente)?")
# not a bare "autres": "Puis-je voir les autres types de congés ?" is a rules question
_AUTRES = (r"(?i:mon [ée]quipe|mes [ée]quipes|mes collaborat(?:eurs|rices)|mes coll[èe]gues|mes employ[ée]s|mon coll[èe]gue|ma coll[èe]gue|"
           r"une? coll[èe]gue|un autre salari[ée]|les autres salari[ée]s|quelqu'un d'autre)")
_FR_NAME = r"(?!(?:No[ëe]l|P[âa]ques|Toussaint|Pentec[ôo]te|l'Ascension|Ascension)\b)[A-ZÀ-Ý][a-zà-ÿ]+"
_ABOUT_OTHERS = re.compile(
    rf"(?:{_OTHERS}|\b{_NAME})(?:'s?|’s?) (?i:{_DATA})\b"                                # my team's leave, Hugo's requests
    rf"|(?i:\b{_DATA}) (?:of|for|from) (?:{_OTHERS}|{_NAME})\b"                         # the requests of my team, the leave for Hugo
    rf"|\b(?i:does|did|has) (?:{_OTHERS}|{_NAME}) (?i:have|get|take|got|taken)\b"       # how much leave does Hugo have
    rf"|(?i:\b{_DONNEES}) (?:(?:de |d'|d’|du |des )(?:{_AUTRES}|{_FR_NAME})|(?i:des autres))\b"   # les congés de mon équipe, la demande d'Emma
    rf"|(?i:\b(?:see|view|access|approve|reject|manage)\b[^.?]{{0,30}}){_OTHERS}\b"     # view what my direct reports booked
    rf"|(?i:\b(?:voir|consulter|approuver|valider|refuser|g[ée]rer)\b[^.?]{{0,30}}){_AUTRES}")


def about_others(text):
    """True for a question about someone else's (or the team's) leave, requests, balance or file: a role question, not a rules one."""
    return bool(_ABOUT_OTHERS.search(str(text or "")))


def asks_about_rules(text):
    text = str(text or "")
    return bool(_QUESTION.search(text) and _RULES.search(text))


def needs_lookup(role, user_text, called):
    """True when the reply must not go out as it is: a rules question answered with no tool at all. `called` is the tools used in this answer:
    any tool (a balance, a document, a search) means it looked something up, so only an answer with no tool is this guard's business; finding a
    person or a contract's stage (NOT_RULES_TOOLS) is not looking up the rules. A question
    about someone else's data is a role question for an employee (about_others), who can see only their own; a manager or HR asking a rules
    question that names someone ("Does Hugo's contract allow remote work?") is still searched."""
    others = role == "employee" and about_others(user_text)
    looked_up = [t for t in called if t not in NOT_RULES_TOOLS]
    return not looked_up and roles.allowed(role, "search_policy") and asks_about_rules(user_text) and not others


def earlier_results(messages):
    """The search results already in this conversation's history, as (tool name, result) pairs: each tool_result matched to the search
    tool_use it answers. Only a search counts: a balance or a request from an earlier message may have changed since."""
    asked, found = {}, []
    for m in messages:
        if not isinstance(m["content"], list):
            continue
        for b in m["content"]:
            if b.get("type") == "tool_use" and b.get("name") in SEARCH_TOOLS:
                asked[b["id"]] = b["name"]
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in asked:
                try:
                    found.append((asked[b["tool_use_id"]], json.loads(b["content"])))
                except (TypeError, ValueError):
                    pass
    return found


def already_found(sections, earlier, agreement_on_file=False):
    """True when the history answers it: a policy search earlier in this conversation returned the section the code's search finds first now
    (and the agreement was searched too, when there is one). Only the first: the search always returns its two closest sections, and the
    second is often a weak neighbour ("And how many sick days?": Sick leave 0.65, then Carry forward 0.45, which no earlier search returned).
    A search that finds nothing now has nothing to add to the policy search already in the history. Otherwise the reply came from memory or
    from another topic, and a new search is needed."""
    if agreement_on_file and not any(name == "search_collective_agreement" for name, _ in earlier):
        return False
    titles = {s.get("title") for name, r in earlier if name == "search_policy" and isinstance(r, dict) for s in r.get("sections", [])}
    searched = any(name == "search_policy" for name, _ in earlier)
    return searched and (not sections or sections[0].get("title") in titles)
