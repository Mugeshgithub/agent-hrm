# existing_first.py: what a person is told about dates they already asked for comes from the tools, never from the old chat. Measured on main (11 Nov 2026).
# Real: Emma wrote "request time off please", "23 nov", "casual". The assistant asked which kind of leave when it had the date, and then said "You already
# have a pending casual leave request for 23 November": the request was approved, and no tool had been called to know either. The chat said "Done." for it.
# 1. Given a date, the assistant asked which kind of leave before looking at anything: count_leave_days now says when the dates are already requested
#    (already_requested), and the assistant must call it before asking.
# 2. A reply that says "you already have a request" with no tool result in the turn that shows it, or with a status the tool did not return.
# 3. A reply that says a confirmation card is ready when no card tool was called (seen twice in ten replays: "Review it and press Confirm", no card).
# And apply_leave now refuses dates that already have a request BEFORE the card (tools.existing_request), as it does for a weekend. So code checks the
# final reply and sends it back once. No AI and no third-party package, so it is unit-tested in CI.
import json
import re

import roles
from document_answers import HIDDEN

TOOL = "apply_leave"
LOOKUPS = ("count_leave_days", "get_my_requests", "apply_leave")
CARD_TOOLS = ("apply_leave", "cancel_leave", "decide_leave")
TEAM_TOOLS = ("get_pending_requests", "get_team_member_balance", "decide_leave", "get_needs_attention")

# the person gave a date: "23 nov", "nov 23", "23rd of november", "2026-11-23", "23/11", "tomorrow", "next friday", "demain"
_MONTHS = (r"jan(?:uary|vier)?|feb(?:ruary)?|f[ée]v(?:rier)?|mar(?:ch|s)?|apr(?:il)?|avr(?:il)?|may|mai|june?|juin|july|juil(?:let)?|aug(?:ust)?|ao[uû]t|"
           r"sep(?:t(?:ember)?)?|oct(?:ober|obre)?|nov(?:ember|embre)?|dec(?:ember)?|d[ée]c(?:embre)?")
_DATE = re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s*(?:of\s+)?(?:{_MONTHS})\b|\b(?:{_MONTHS})\.?\s*\d{{1,2}}\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b\d{{1,2}}/\d{{1,2}}\b|"
                   r"\b(?:tomorrow|demain|next (?:week|(?:mon|tues|wednes|thurs|fri)day)|(?:mon|tues|wednes|thurs|fri)day|lundi|mardi|mercredi|jeudi|vendredi)\b", re.I)
# a reply that asks which kind of leave: "which type of leave", "casual, sick, or paid"
_ASKS_KIND = re.compile(r"\b(?:which|what) (?:kind|type|sort) of (?:leave|time off)\b|\bcasual,? (?:sick,? )?(?:or|and) (?:sick|paid)\b|\bquel (?:type|genre) de cong[ée]\b", re.I)
# a reply that says a request already exists: "You already have a pending casual leave request", "You've already booked casual leave", "vous avez déjà une demande".
# Only "already": "You have 1 leave request waiting for your approval" is a manager's inbox, not a claim about the person's own dates.
_CLAIM = re.compile(r"\byou(?:'ve| have)? already (?:have|got|booked|requested|asked for|submitted)\b[^.\n]{0,60}?\b(?:requests?|bookings?|leave|time off|days? off)\b|"
                    r"\b(?:vous avez|tu as) d[\u00e9e]j[\u00e0a]\b[^.\n]{0,60}?\b(?:demandes?|cong[\u00e9e]s?)\b", re.I)
_STATUS_WORD = re.compile(r"\b(pending|approved|rejected|declined|cancell?ed|en attente|approuv[ée]e?|refus[ée]e?|annul[ée]e?)\b", re.I)
_WORD = {"pending_manager_approval": "pending", "approved": "approved", "rejected": "rejected", "cancelled": "cancelled"}
_SAYS = {"pending": "pending", "en attente": "pending", "approved": "approved", "approuve": "approved", "approuvee": "approved", "rejected": "rejected",
         "declined": "rejected", "refuse": "rejected", "refusee": "rejected", "cancelled": "cancelled", "canceled": "cancelled", "annule": "cancelled",
         "annulee": "cancelled"}
# a reply that says a confirmation card is there: "press Confirm", "a confirmation card is now in your app", "the card is ready"
_CARD = re.compile(r"\bpress confirm\b|\bconfirmation card\b[^.\n]{0,30}\b(?:is|has been)\b|\bcard (?:is|has been) (?:now )?(?:in|on|ready)\b|"
                   r"\b(?:appuie[zr]|cliquez) sur confirm|\bcarte de confirmation\b[^.\n]{0,30}\b(?:est|se trouve)\b", re.I)

COUNT_FIRST = HIDDEN + ("The person gave you dates. Do not ask which kind of leave first: call count_leave_days now with those dates (leave leave_type out) "
                        "and tell them what it returns. If the result has already_requested, those dates already have a request (its id, status and dates "
                        "are in the result): say that first, in the same language, and do not offer to book them again. Ask which kind of leave only "
                        "afterwards, and only if you still need it to book.")
CHECK_FIRST = HIDDEN + ("You told the person they already have a request, but no tool result in this turn shows one. That comes from the earlier chat, which "
                        "is old: requests are cancelled, approved or sent again in the meantime. Call get_my_requests now, or count_leave_days with the "
                        "dates, and say only what it returns, with each request's real status. If no pending or approved request covers those dates, "
                        "carry on and book them.")
WRONG_STATUS = HIDDEN + ("The tool results in this turn say the status is {returned}, which is not what your reply says. Say the status the tool returned, "
                         "and nothing else about it.")
NO_CARD = HIDDEN + ("Your reply says a confirmation card is ready, but you did not call apply_leave (or cancel_leave) in this turn, so no card exists. Do not "
                    "say there is one. If the person asked to book or cancel and you have what you need, call the tool now; if something is missing, ask "
                    "for exactly that.")


def _plain(text):
    return str(text or "").lower().replace("é", "e").replace("è", "e").replace("ê", "e")


def has_date(text):
    return bool(_DATE.search(str(text or "")))


def asks_kind(reply):
    return bool(_ASKS_KIND.search(str(reply or "")))


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", str(text or "")) if s.strip()]


def claims(reply):
    """The sentences of a reply that say the person already has a request."""
    return [s for s in _sentences(reply) if _CLAIM.search(s)]


def says_card(reply):
    return bool(_CARD.search(str(reply or "")))


def returned(sources):
    """The request statuses ('pending', 'approved', ...) that this turn's tools returned for the person's own requests, or None when no tool looked at them.
    `sources` are this turn's tool results."""
    found = None
    for source in sources:
        try:
            result = json.loads(source)
        except (TypeError, ValueError):
            continue
        if not isinstance(result, dict):
            continue
        rows = []
        if isinstance(result.get("already_requested"), dict):
            rows.append(result["already_requested"])
        if isinstance(result.get("my_requests"), list):
            rows.extend(r for r in result["my_requests"] if isinstance(r, dict))
        if rows or isinstance(result.get("my_requests"), list):
            found = (found or set()) | {_WORD.get(r.get("status"), r.get("status")) for r in rows}
    return found


def nudge(role, user_text, reply, called, sources, card_open=False):
    """What to tell the assistant once, before its reply is shown, or None. `called` is the tools it used in this answer; `card_open` is whether a card is
    already waiting for the person from an earlier turn."""
    if not roles.allowed(role, TOOL):
        return None
    if (claimed := claims(reply)) and not any(t in called for t in TEAM_TOOLS):       # a manager telling what the team has is not about their own dates
        statuses = returned(sources)
        if statuses is None:
            return CHECK_FIRST
        says = {_SAYS[w] for s in claimed for w in (_plain(m) for m in _STATUS_WORD.findall(s)) if w in _SAYS}
        if says and not says & statuses:
            return WRONG_STATUS.format(returned=" and ".join(sorted(statuses)) or "that no such request exists")
        return None
    if has_date(user_text) and asks_kind(reply) and not any(t in called for t in LOOKUPS):
        return COUNT_FIRST
    if says_card(reply) and not any(t in called for t in CARD_TOOLS) and not card_open:
        return NO_CARD
    return None
