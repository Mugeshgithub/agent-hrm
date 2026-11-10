# booking_first.py: two mistakes in answering a request to book leave, both measured on main (Nov 2026).
# 1. Asked to book, the assistant asked "Shall I submit this request?" in the chat and did not call apply_leave (paid leave outside the usual
#    months, 17 of 60 attempts). apply_leave itself shows the person a confirmation card, and that card is where they confirm: a question in the
#    chat first is a second confirmation nobody asked for.
# 2. Dates that use no days (a Saturday, for casual leave) were offered anyway ("Would you like me to book it anyway?"): the card appeared and the
#    refusal only came after Confirm. tools.no_days_reason now refuses before the card, and the offer must never be made.
# So code checks the final reply and sends it back once. No AI and no third-party package, so it is unit-tested in CI.
import json
import re

import roles
from document_answers import HIDDEN

TOOL = "apply_leave"
# a request to book (not a question about leave): "book", "apply", "submit", "put me down", "I want leave on ..." (and the French ones)
_REQUEST = re.compile(
    r"\b(?:book|apply|submit|put me down|r[ée]serv\w*|poser)\b"
    r"|\bi (?:want|need|wish|would like|'d like)\b[^.?!]{0,30}\b(?:leave|time off|days? off|holidays?|vacation)\b"
    r"|\bje (?:veux|voudrais|souhaite)\b[^.?!]{0,30}\bcong[ée]s?\b", re.I)
# a question about leave, not a request: it is answered, not booked
_ONLY_ASKING = re.compile(r"\b(?:how many|how much|what if|if i|can i|could i|may i|am i allowed|do i have|check|combien|puis-je|est-ce que je peux)\b", re.I)
# a reply that ends by asking whether to go ahead
_PROCEED = re.compile(r"\b(?:shall i|should i|would you like me to|do you want me to|want me to|would you like to proceed|do you want to proceed|may i|"
                      r"ok(?:ay)? to|ready (?:for me )?to|go ahead|proceed|voulez-vous que|souhaitez-vous que|dois-je)\b", re.I)
# a reply that offers to book
_OFFER = re.compile(r"\b(?:book|submit|apply|proceed|go ahead|r[ée]server|poser)\b", re.I)
_ANYWAY = re.compile(r"\b(?:book|submit|apply for)\b[^.?!]{0,20}\banyway\b", re.I)

BOOK_NOW = HIDDEN + ("The person asked you to book this leave. Do not ask them in the chat whether to submit it or to go ahead: call apply_leave now "
                     "with the dates, and the app shows them a confirmation card, which is where they confirm. Then tell them to review the card and "
                     "press Confirm, quoting only the figures the tool returned. Ask first only if something is missing (which kind of leave, or the "
                     "dates). If apply_leave refuses, tell them exactly what it says.")
NO_DAYS = HIDDEN + ("{why} Nothing can be booked for those dates, so do not offer to book them, not even \"anyway\". Say so plainly, in the same "
                    "language, and ask which working day they would like instead. Start with the answer itself.")


def _last_sentence(text):
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", str(text or "")) if p.strip()]
    return parts[-1] if parts else ""


def asks_to_book(text):
    text = str(text or "")
    return bool(_REQUEST.search(text) and not _ONLY_ASKING.search(text))


def asks_before_booking(reply):
    last = _last_sentence(reply)
    return last.endswith("?") and bool(_PROCEED.search(last))


def offers_to_book(reply):
    last = _last_sentence(reply)
    return (last.endswith("?") and bool(_OFFER.search(last))) or bool(_ANYWAY.search(str(reply or "")))


def unbookable(sources):
    """What count_leave_days said about dates that use no days (a weekend, a public holiday), or None. `sources` are this turn's tool results."""
    for source in sources:
        try:
            result = json.loads(source)
        except (TypeError, ValueError):
            continue
        if isinstance(result, dict) and result.get("cannot_be_booked"):
            return result["cannot_be_booked"]
    return None


def nudge(role, user_text, reply, called, sources):
    """What to tell the assistant once, before its reply is shown, or None. `called` is the tools it used in this answer."""
    if TOOL in called or not roles.allowed(role, TOOL):
        return None
    if (why := unbookable(sources)) and offers_to_book(reply):
        return NO_DAYS.format(why=why)
    if asks_to_book(user_text) and asks_before_booking(reply):
        return BOOK_NOW
    return None


# ---------- leave asked for on a Saturday, a Sunday or a weekend, with no date ----------
# Nothing needs counting here, so the model is not called: the answer is fixed (as in recruitment_guard.py). Measured on main (10 Nov): the model asked
# "Which Saturday would you like?" and listed Saturdays, wrongly ("this Saturday (15 November)" when 15 November 2026 is a Sunday), and never said
# that a weekend cannot be booked. A request with a date, a weekday, or "paid" still goes through the model and the tools: paid leave counts Saturdays.
_LEAVE_WORD = re.compile(r"\b(?:leave|time off|days? off|day off|off|holidays?|vacation|cong[ée]s?|poser)\b", re.I)
_WANTS = re.compile(r"\b(?:take|taking|book|apply|want|need|wish|would like|like to|request|put me|can i|could i|may i|je veux|je voudrais|puis-je|"
                    r"prendre|poser|r[ée]server)\b", re.I)
_SATURDAY = re.compile(r"\b(?:sat|saturdays?|samedis?)\b", re.I)
_SUNDAY = re.compile(r"\b(?:sun|sundays?|dimanches?)\b", re.I)
_WEEKEND = re.compile(r"\bweek[- ]?ends?\b", re.I)
# anything that means the model has to look at it: a weekday or a day word, a date, paid leave (Saturdays count), a question about the rules
_NEEDS_THE_MODEL = re.compile(
    r"\b(?:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|lundi|mardi|mercredi|jeudi|vendredi|today|tomorrow|"
    r"demain|aujourd'hui|paid|cong[ée]s? pay[ée]s?|cp|policy|rules?|law|legal|code du travail|why|explain|pourquoi|politique|r[eè]gles?)\b|\d", re.I)
_FRENCH = re.compile(r"\b(?:samedis?|dimanches?|je veux|je voudrais|cong[ée]s?|puis-je|week-end|poser|prendre)\b", re.I)

WEEKEND_EN = ("Casual and sick leave don't count Saturdays or Sundays, so there is nothing to book on a weekend. Paid leave counts Saturdays (never "
              "Sundays): if you mean paid leave on a Saturday, tell me which one. Otherwise, which weekday would you like?")
SUNDAY_EN = "No kind of leave counts Sundays, so there is nothing to book on a Sunday. Which weekday would you like instead?"
WEEKEND_FR = ("Les congés ordinaires et maladie ne comptent ni les samedis ni les dimanches : il n'y a rien à poser un week-end. Les congés payés comptent "
              "les samedis (jamais les dimanches) : si c'est un congé payé un samedi, dites-moi lequel. Sinon, quel jour de la semaine souhaitez-vous ?")
SUNDAY_FR = "Aucun type de congé ne compte les dimanches : il n'y a rien à poser un dimanche. Quel jour de la semaine souhaitez-vous ?"


def weekend_answer(role, text):
    """The fixed answer to a request for leave on a Saturday, a Sunday or a weekend with no date, or None when the model has to answer."""
    text = str(text or "")
    if not roles.allowed(role, TOOL):
        return None
    saturday, sunday, weekend = _SATURDAY.search(text), _SUNDAY.search(text), _WEEKEND.search(text)
    if not (saturday or sunday or weekend):
        return None
    if not (_LEAVE_WORD.search(text) and _WANTS.search(text)) or _NEEDS_THE_MODEL.search(text):
        return None
    sunday_only = bool(sunday and not (saturday or weekend))
    if _FRENCH.search(text):
        return SUNDAY_FR if sunday_only else WEEKEND_FR
    return SUNDAY_EN if sunday_only else WEEKEND_EN
