# recruitment_guard.py: the assistant never ranks, scores or compares candidates and never says who to hire or reject, whoever asks
# (the founder included). Recruitment is high-risk under the EU AI Act: the app informs and a person decides. A prompt alone does not
# hold that line (the model sometimes gives in to a confident request), so code recognises the question and answers it with a fixed
# text before the model is called. No AI and no third-party package, so it is unit-tested in CI.
import re

REFUSAL = ("I don't rank, score or compare candidates, and I can't say who to hire or reject. That decision is yours, and it has to rest on "
           "what each application shows against the job's requirements, not on a ranking from me. I can tell you who is in the pipeline "
           "and which stage they are at. To see what a candidate's CV says about each requirement, open the candidate and choose Summary: "
           "it quotes the CV word for word and leaves the judgement to you.")
NOT_YOURS = "Recruitment isn't something I can help with here: candidates are handled by HR and the hiring manager."

# a question about candidates ...
ABOUT_CANDIDATES = re.compile(
    r"\b(?:candidates?|applicants?|applications?|cvs?|r[eé]sum[eé]s?|hir(?:e|ing|ed)|recruit\w*|shortlist\w*|interview\w*|candidat\w*|"
    r"embauch\w*|recrut\w*|postulant\w*)\b", re.I)
# ... that asks for a judgement of people
JUDGEMENT = re.compile(
    r"\b(?:rank\w*|scor(?:e|es|ed|ing)|rat(?:e|es|ed|ing)|compar\w*|best|strongest|weakest|worst|better|worse|top|"
    r"most (?:qualified|experienced|suitable|promising|talented|impressive)|least (?:qualified|experienced|suitable)|"
    r"more (?:experienced|qualified|senior|suitable)|(?:more|most|less|least|fewer) (?:experience|qualifications?|skills?)|stronger|weaker|"
    r"shortlist\w*|recommend\w*|good fit|right fit|best fit|suitable|promising|worth (?:hiring|interviewing)|chances?|likel(?:y|ihood)|"
    r"should (?:we|i|you) (?:hire|reject|interview|pick|choose|select|offer|take|go with|pursue|invite|drop)|"
    r"who (?:should|would you|do you)|which (?:one|candidate|applicant)s? (?:should|would|is)|"
    r"(?:which|who)\b[^.?!]{0,40}\b(?:to|shall|do (?:i|we)) (?:pick|hire|choose|select|take|interview|reject|go with|invite|shortlist|offer)|"
    r"meilleur\w*|classe\w*|classement|compar\w*|recommand\w*|faut-il (?:embaucher|recruter|retenir)|devrait-on|devons-nous (?:embaucher|recruter|retenir))\b", re.I)
# "the best way to hire" is not about people
HARMLESS = re.compile(r"\b(?:best|better|top) (?:way|ways|practice|practices|time|day|channel|channels|tool|tools|place|places)\b", re.I)


def _names_in(text, names):
    # a candidate named in the question counts as "about candidates": full name, or a first or last name of at least 3 letters
    low = text.lower()
    for name in names or ():
        parts = [name.lower(), *(p for p in re.split(r"[\s-]+", name.lower()) if len(p) >= 3)]
        if any(re.search(rf"(?<!\w){re.escape(p)}(?!\w)", low) for p in parts):
            return True
    return False


def asks_for_judgement(text, names=()):
    """True for a question that asks to rank, score, compare or choose between people: "who is the best candidate", "rank them",
    "should I hire Inès", "compare Karim and Théo". `names` are the candidates this person may see: naming one is enough context."""
    text = HARMLESS.sub(" ", str(text or ""))
    return bool((ABOUT_CANDIDATES.search(text) or _names_in(text, names)) and JUDGEMENT.search(text))


def refusal(role, user_text, names=()):
    """The fixed reply for such a question, or None. HR and managers hear why; anyone else is told it is not theirs, and nothing about
    whether candidates exist."""
    if role == "new_hire" or not asks_for_judgement(user_text, names if role in ("hr", "manager") else ()):
        return None
    return REFUSAL if role in ("hr", "manager") else NOT_YOURS
