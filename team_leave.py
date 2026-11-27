# team_leave.py: an employee asking to see the team's leave gets a fixed answer from code, in their language: only the manager sees it,
# and they see their own. The model is not called.
# Issue #73: "Puis-je voir les congés de mon équipe ?" was 13/15 on main by the old check, and 6 of 10 replies sent the employee to
# "les fonctionnalités manager", "votre accès manager" or "la section dédiée aux managers" in the app; employees have no Team page
# (web/components/Shell.tsx). None said who does see it. Only for the employee role: a manager has the team's leave, HR everyone's.
import re

_EN = re.compile(
    r"\b(?:my|our|the)\s+(?:team|colleagues|co-?workers)(?:'s|s')?\b[^.?!]{0,40}(?<!\bmy )(?<!\bmy own )\b(?:leaves?|holidays?|time off|absences?|requests?|balances?|vacations?|days off)\b"
    r"|\b(?:leaves?|holidays?|time off|absences?|requests?|balances?|vacations?|days off)\b[^.?!]{0,25}\bof\s+(?:my|our|the)\s+(?:team|colleagues)\b"
    r"|\bwho\b[^.?!]{0,20}\b(?:in|on|from)\s+(?:my|our|the)\s+team\b[^.?!]{0,30}\b(?:off|away|on leave|on holiday)\b", re.I)
_FR = re.compile(
    r"\b(?:cong[ée]s?|absences?|demandes?|soldes?|vacances|jours? de repos)\b[^.?!]{0,40}\b(?:de|des membres de|dans)\s+(?:mon|notre|l')\s*[ée]quipe\b"
    r"|\b(?:mon|notre)\s+[ée]quipe\b[^.?!]{0,40}(?<!\bmes )(?<!\bmon )\b(?:cong[ée]s?|absences?|vacances|demandes? de cong[ée]s?)\b"
    r"|\b(?:cong[ée]s?|absences?|demandes?|soldes?)\b[^.?!]{0,25}\bde mes coll[èe]gues\b"
    r"|\bqui\b[^.?!]{0,20}\b(?:de|dans)\s+(?:mon|notre|l')\s*[ée]quipe\b[^.?!]{0,30}\b(?:absent|en cong[ée]|en vacances)", re.I)
_FRENCH = re.compile(r"\b(?:je|puis-je|est-ce|mon|ma|mes|notre|nos|les|des|de|du|voir|qui|quels?|quelles?)\b", re.I)

ANSWER = ("Only your manager can see your team's leave. You can see your own leave and requests: ask me here, or open the Time off page.")
ANSWER_FR = ("Seul votre manager peut voir les congés de l'équipe. Vous pouvez voir vos propres congés et demandes : demandez-moi ici, "
             "ou ouvrez la page Time off.")


def asks_team_leave(text):
    text = str(text or "")
    return bool(_EN.search(text) or _FR.search(text))


def fixed_answer(role, text):
    """The fixed answer for an employee asking about the team's leave, or None."""
    if role != "employee" or not asks_team_leave(text):
        return None
    return ANSWER_FR if (_FR.search(str(text)) or len(_FRENCH.findall(str(text))) >= 2) and not _EN.search(str(text)) else ANSWER
