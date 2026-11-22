# approval_first.py: a manager is told which requests are pending NOW, from the tool, never from the chat. Two mistakes, both measured on main (Nov 2026).
# Real, 11 Nov: "approve emma's leave request" was answered "Emma's leave request (23 Nov, casual) is already approved. The three pending requests now
# are Hugo, Clara, and Emma's new one for 23 Nov". Last night's chat said "Emma ... 23 Nov ... Done." for a request that was cancelled afterwards;
# Emma then asked again for the same day. The assistant called get_pending_requests, saw the new request, and still trusted the chat over the tool.
# 1. Asked to approve or reject a named person's request: 7 of 10 replies wrong with that history, 0 of 8 without it. The request is put on the card.
# 2. Asked what is pending ("any pending request"): "Two pending requests" and the new one left out, 8 of 8. Every request the tool returned is listed.
# So code checks the final reply and sends it back once. No AI and no third-party package, so it is unit-tested in CI.
# Asked to decide with nothing looked up, the code calls get_pending_requests itself (agent.py), as the policy search is run (search_first.py): the
# note that asked the model to look first used up the one retry (main and PR #63, 22 Nov), and the model then listed the requests or asked what to do.
import json
import re
import unicodedata

import roles
import tools
from document_answers import HIDDEN

TOOL = "decide_leave"
LIST_TOOL = "get_pending_requests"

# a request to decide, not a question about deciding: the message starts with the verb, after a polite word or two
_ASK = re.compile(
    r"^\W*(?:(?:yes|ok(?:ay)?|please|pls|can you|could you|would you|will you|go ahead and|i want to|i'd like to|i would like to|let's|lets|"
    r"s'il (?:te|vous) pla[iî]t|peux-tu|pouvez-vous|je veux|je voudrais)[\s,]+)*"
    r"(?P<verb>approve|accept|reject|decline|deny|refuse|approuv(?:e|er)|accepte|accepter|valide|valider|refuser|rejette|rejeter)\b", re.I)
_APPROVING = re.compile(r"^(?:approu?v|accept|valid)", re.I)
# a reply that tells the manager what is pending, so it claims to be complete: "Two pending requests", "the pending requests are", "no pending request"
_NUMBERS = r"one|two|three|four|five|six|seven|eight|nine|ten|\d+|un|une|deux|trois|quatre|cinq"
_CLAIM = re.compile(r"\bpending (?:leave )?requests?\b|\b(?:no|nothing|none)\b[^.\n]{0,25}\bpending\b|\bdemandes? en attente\b|"
                    rf"\b(?:{_NUMBERS}) (?:\w+ ){{0,2}}(?:requests?|demandes?)\b|\brequests? (?:remains?|are waiting|is waiting|are pending)\b", re.I)

# a sentence saying a request is decided ("already approved", "has been approved", "is approved"): what the old chat says, and what no waiting request is.
# Not "is not yet approved", "is waiting to be approved", "will be approved".
_GAP = r"(?:(?!not\b|yet\b|to\b|be\b|will\b|would\b|if\b|once\b|when\b|after\b|until\b|can\b|could\b|may\b)\w+\s+){0,2}"
_DECIDED = re.compile(rf"\b(?:already|has been|have been|was|is|are)\s+{_GAP}(?:approved|rejected|declined|denied|decided|cancelled|canceled|withdrawn|processed|confirmed)\b|"
                      r"\bd[ée]j[àa]\b[^.\n]{0,30}\b(?:approuv|refus|rejet|annul|valid)", re.I)

APPROVE_NOW = HIDDEN + ("The tool result in this turn shows that {name} has a leave request waiting for the manager's decision right now: request {id}, "
                        "{kind} leave, {when}. That is the request, whatever the earlier messages say about approving or cancelling an earlier request "
                        "on the same dates, so never say it is already approved. Call decide_leave for request {id} now with decision \"approve\": the "
                        "app shows a confirmation card, which is where the manager confirms. Then tell them to review the card and press Confirm, "
                        "quoting only the figures the tools returned.")
REJECT_NOW = HIDDEN + ("The tool result in this turn shows that {name} has a leave request waiting for the manager's decision right now: request {id}, "
                       "{kind} leave, {when}. That is the request, whatever the earlier messages say about an earlier request on the same dates, so "
                       "never say it was already decided. If the manager gave a reason, call decide_leave for request {id} now with decision \"reject\" "
                       "and that reason: the app shows a confirmation card, which is where the manager confirms. If they gave none, ask for the "
                       "reason, naming this request ({name}, {when}): a rejection always needs one.")
LIST_ALL = HIDDEN + ("The tool result in this turn lists {count} waiting for the manager's decision right now: {all}. Your reply does not present {missing} as waiting. "
                     "Tell them about every request the tool returned, each with its own dates and figures, and no other. Whatever the earlier messages "
                     "say about approving or cancelling an earlier request on the same dates, these are the requests waiting now.")


def _plain(text):
    return unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()


def asked_to_decide(text):
    """'approve' or 'reject' when the message asks for it, else None. 'Which requests can I approve?' is a question: it is answered, not decided."""
    found = _ASK.match(str(text or ""))
    if not found:
        return None
    return "approve" if _APPROVING.match(found.group("verb")) else "reject"


def names(text, employee_name):
    """True when the text names this person by first name ('emma' in \"approve emma's leave request\")."""
    parts = _plain(employee_name).split()
    return bool(parts) and bool(re.search(rf"\b{re.escape(parts[0])}\b", _plain(text)))


def pending_requests(sources):
    """The pending requests get_pending_requests returned in this turn (the latest result), or None when it was not called. `sources` are this turn's tool results."""
    found = None
    for source in sources:
        try:
            result = json.loads(source)
        except (TypeError, ValueError):
            continue
        if isinstance(result, dict) and isinstance(result.get("pending_requests"), list):
            found = result["pending_requests"]
    return found


def left_out(user_text, reply, pending):
    """The waiting requests a reply that tells what is pending leaves out, or never presents as waiting ("has been approved"). [] for a question about one
    person, or a reply that is not a list. A person counts as shown when one sentence names them without calling the request decided."""
    if not pending or not _CLAIM.search(str(reply or "")):
        return []
    if any(names(user_text, r.get("employee_name")) for r in pending):
        return []
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", str(reply)) if s.strip()]
    return [r for r in pending
            if not any(names(s, r.get("employee_name")) and not _DECIDED.search(s) for s in sentences)]


def _line(request):
    return f"{request['employee_name']}, request {request['request_id']}, {request['leave_type']} leave, {tools.when_label(request['start'], request['end'])}"


def needs_pending(role, user_text, called):
    """True when the code must look up what is waiting before the reply goes out: asked to approve or reject, and neither decided nor looked up."""
    return (TOOL not in called and LIST_TOOL not in called and roles.allowed(role, TOOL) and roles.allowed(role, LIST_TOOL)
            and asked_to_decide(user_text) is not None)


def nudge(role, user_text, reply, called, sources):
    """What to tell the assistant once, before its reply is shown, or None. `called` is the tools it used in this answer."""
    if TOOL in called or not roles.allowed(role, TOOL):
        return None
    verb = asked_to_decide(user_text)
    pending = pending_requests(sources)
    if verb is None:
        if not (missing := left_out(user_text, reply, pending)):
            return None
        count = f"{len(pending)} request{'s' if len(pending) != 1 else ''}"
        return LIST_ALL.format(count=count, all="; ".join(_line(r) for r in pending), missing=", ".join(r["employee_name"] for r in missing))
    if pending is None:          # nothing to go on (the code's lookup comes first, in agent.py)
        return None
    named = [r for r in pending if names(user_text, r.get("employee_name"))]
    if len(named) != 1:          # nobody named, none waiting, or several waiting: asking which one is the right answer
        return None
    request = named[0]
    text = APPROVE_NOW if verb == "approve" else REJECT_NOW
    return text.format(name=request["employee_name"], id=request["request_id"], kind=request["leave_type"],
                       when=tools.when_label(request["start"], request["end"]))
