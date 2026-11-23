# welcome_reply.py: asked what they are still missing, a new hire hears only their own steps, and the line about HR's and the
# manager's steps is the app's, not the model's.
# Real, eval "welcome: after a reminder…", 5/10 on main on 24 Nov 2026: the reply listed her three documents, then "HR will also handle
# your employment contract and your work email and accounts". The prompt already says "list only their own steps"; the model sees the
# whole checklist and adds the rest anyway. So, in code, after the reply: while the new hire still has steps of their own to do and did
# not ask about anyone else's, every sentence naming a step that is still HR's or the manager's to do is taken out, and one fixed line
# says the others are taken care of. Asked "who handles my contract?", nothing is taken out. When nothing is left for the new hire, the
# prompt has the model say what HR and the manager still have to do, and nothing is taken out either. No AI here, so it is unit-tested.
import re

# the words that name each step that is not the new hire's (onboarding.TEMPLATE), in English and French
STEP_WORDS = {
    "contract": r"\bcontra(?:ct|t)s?\b",
    "equipment": r"\blaptops?\b|\bequipment\b|\bordinateur\b|\bmat[ée]riel\b",
    "accounts": r"\b(?:tool|work|email|e-mail|user|it) accounts?\b|\baccounts? (?:set up|created|ready)\b|\bwork e-?mail\b|\bcomptes? (?:utilisateur|informatiques?)\b|\bmessagerie\b",
    "welcome": r"\bwelcome message\b|\bmessage de bienvenue\b",
    "dpae": r"\bdpae\b|\bdeclaration\b|\bd[ée]claration\b",
    "checkin": r"\bcheck-?in\b|\bpoint de la premi[èe]re semaine\b",
}
# the new hire's own steps: a sentence that names one stays, whatever else it says ("the IBAN of the account where the salary is paid")
OWN_WORDS = re.compile(r"\bidentit[yé]\b|\bpassport\b|\bpasseport\b|\bID\b|\biban\b|\bbank\b|\bRIB\b|\bbancaires?\b|\bsocial security\b|"
                       r"\bs[ée]curit[ée] sociale\b", re.I)
LINE = "HR and your manager take care of their own steps; you don't need to do anything for them."
LINE_FR = "Les RH et votre manager s'occupent de leurs propres étapes ; vous n'avez rien à faire pour celles-ci."
_FRENCH = re.compile(r"\b(?:vous|votre|vos|les|des|est|pour|avec|il vous reste|manque)\b", re.I)


def _others_to_do(checklist):
    """The patterns of the steps still to do by HR or the manager."""
    return [re.compile(STEP_WORDS[s["key"]], re.I) for s in checklist.get("steps", [])
            if s.get("who") != "you" and s.get("status") == "todo" and s.get("key") in STEP_WORDS]


def own_steps_only(reply, user_text, checklist):
    """(the reply, how many sentences were taken out). Sentences are split inside each line, so a list keeps its shape."""
    if not isinstance(checklist, dict) or not checklist.get("next_for_you"):
        return reply, 0                      # nothing left for the new hire: the prompt has the model say what the others still do
    others = _others_to_do(checklist)
    if not others or any(p.search(str(user_text or "")) for p in others):
        return reply, 0                      # they asked about someone else's step: that is the answer
    removed, lines = 0, []
    for line in str(reply or "").split("\n"):
        kept = []
        for sentence in re.split(r"(?<!\b\d\.)(?<=[.!?])\s+", line):
            if any(p.search(sentence) for p in others) and not OWN_WORDS.search(sentence):
                removed += 1
            else:
                kept.append(sentence)
        if kept or not line.strip():
            lines.append(" ".join(kept))
    if not removed:
        return reply, 0
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    line = LINE_FR if len(_FRENCH.findall(text)) >= 3 else LINE
    return f"{text}\n\n{line}", removed
