# confirm_batch.py: the confirmation card of the web app holds a LIST of actions, not one.
# Measured (10 Nov): "cancel my approved leave on 20, 23 and 24" made the assistant call cancel_leave three times in one turn. The app kept ONE
# parked action, so each call replaced the one before: pressing Confirm cancelled the last date only, and the assistant had told the person
# there were "confirmation cards for all three". Rules (the user's):
#   - one card lists every item, with its date and weekday; one Confirm runs them one by one and reports each result, failures included;
#   - the same kind of action per card (all cancellations, or all approvals, or all rejections), at most MAX_ITEMS;
#   - an item that would fail is checked BEFORE the card and left off it (the reply says why): the caller does that, with tools.*_problem;
#   - a second action that cannot join the card is refused out loud, never silently replacing the first;
#   - the assistant must not say there are several cards.
# No AI and no third-party package, so it is unit-tested in CI.
import re

from document_answers import HIDDEN

MAX_ITEMS = 5
BATCHABLE = ("cancel_leave", "decide_leave")           # booking is not batched: each booking changes the balance the next one is checked against
NOUN = {"cancel_leave": "cancellations", "decide_leave:approve": "approvals", "decide_leave:reject": "rejections"}


def kind_of(tool, tool_input):
    return f"decide_leave:{(tool_input or {}).get('decision')}" if tool == "decide_leave" else tool


def _days(n):
    return f"{n} day" if n == 1 else f"{n} days"


def item_label(tool, details):
    """One item in words, with its dates and weekday: 'Fri 20 Nov 2026 (casual, 1 day)', or 'Anna Roy: Mon 23 Nov to Tue 24 Nov 2026 (paid, 2 days)'."""
    d = details or {}
    if tool in BATCHABLE and d.get("when"):
        who = f"{d['employee_name']}: " if tool == "decide_leave" and d.get("employee_name") else ""
        return f"{who}{d['when']} ({d.get('leave_type')}, {_days(d.get('days'))})"
    return ""


def add(pending, tool, tool_input, details):
    """Put an action on the card. `pending` is the dict the app parks: the first item's tool, input and details, plus `kind` and `items`.
    Returns (status, count): 'started', 'added', 'duplicate' (already on the card), 'full' (MAX_ITEMS) or 'other_kind' (a different kind of action, or a
    second action of a kind that is not batched): in the last two nothing changes."""
    kind = kind_of(tool, tool_input)
    if not pending:
        pending.update(tool=tool, input=dict(tool_input), details=details, kind=kind, items=[{"input": dict(tool_input), "details": details}])
        return "started", 1
    items = pending["items"]
    if pending["kind"] != kind or tool not in BATCHABLE:
        return "other_kind", len(items)
    if any(item["input"] == tool_input for item in items):
        return "duplicate", len(items)
    if len(items) >= MAX_ITEMS:
        return "full", len(items)
    items.append({"input": dict(tool_input), "details": details})
    return "added", len(items)


def tool_result(status, count, pending, tool, details):
    """What the assistant is told after trying to put an action on the card: the truth about the card (there is ONE) and about this action."""
    labels = [item_label(pending["tool"], item["details"]) for item in pending.get("items", [])]
    on_card = {"card_items": count, **({"on_the_card": labels} if any(labels) else {})}
    if status == "started":
        more = (f" More {NOUN.get(pending['kind'], 'actions')} asked for in this same turn go on this same card, up to {MAX_ITEMS}."
                if tool in BATCHABLE else "")
        return {"awaiting_confirmation": True, **on_card, "card": details,
                "message": "Nothing has been done yet. The app is showing the person ONE confirmation card for this action; tell them to review it "
                           "and press Confirm." + more}
    if status == "added":
        return {"awaiting_confirmation": True, **on_card, "card": details,
                "message": f"Added to the same card: it now lists {count} items, and there is still only ONE card. Nothing has been done yet: the "
                           f"person presses Confirm once and all {count} run. Never say there are several cards."}
    if status == "duplicate":
        return {"awaiting_confirmation": True, **on_card, "message": "That one is already on the card. Nothing was added."}
    if status == "full":
        return {"error": f"The card is full: it holds at most {MAX_ITEMS} items. This one is NOT on the card and nothing was done for it. Tell the "
                         f"person, and that once they have confirmed or cancelled this card they can ask for the rest.", **on_card}
    why = ("Only one of these can be on a card, and one is already waiting. " if tool not in BATCHABLE
           else "A card holds one kind of action only (all cancellations, or all approvals, or all rejections), and a different kind is already "
                "waiting. ")
    return {"error": why + "This one is NOT on the card and nothing was done for it. Tell the person that it comes next, after they confirm or "
                           "cancel the current card.", **on_card}


def outcome(tool, item, result):
    """(ok, reason, line) for one item after Confirm. `reason` is why it failed; `line` is the sentence for the person."""
    result = result if isinstance(result, dict) else {}
    label = item_label(tool, item.get("details"))
    if tool == "cancel_leave":
        ok = bool(result.get("cancelled"))
        reason = "" if ok else (result.get("message") or result.get("error") or "it could not be cancelled")
        returned = result.get("days_returned")
        if ok:
            return ok, reason, f"Cancelled {label}" + (f", {_days(returned)} returned to your balance" if returned else "") + "."
        return ok, reason, f"Not cancelled, {label}: {reason}"
    if tool == "decide_leave":
        ok = bool(result.get("success"))
        approve = (item.get("input") or {}).get("decision") == "approve"
        reason = "" if ok else (result.get("error") or result.get("message") or "it could not be saved")
        if ok:
            return ok, reason, f"{'Approved' if approve else 'Rejected'} {label}."
        return ok, reason, f"Not {'approved' if approve else 'rejected'}, {label}: {reason}"
    ok = bool(result) and "error" not in result and result.get("success", result.get("submitted", True)) is not False
    reason = "" if ok else (result.get("error") or result.get("message") or "it could not be done")
    return ok, reason, "Done." if ok else f"Couldn't complete it: {reason}"


def note(kind, outcomes):
    """The message after Confirm: 'Done.' for a single item (or why it failed), and for several items a count and one line each."""
    if len(outcomes) == 1:
        ok, reason, _ = outcomes[0]
        return "Done." if ok else f"Couldn't complete it: {reason}"
    noun = NOUN.get(kind, "actions")
    done = sum(1 for ok, _, _ in outcomes if ok)
    head = (f"Done: all {len(outcomes)} {noun}." if done == len(outcomes)
            else f"None of the {len(outcomes)} {noun} could be done." if done == 0
            else f"{done} of {len(outcomes)} {noun} done, {len(outcomes) - done} not.")
    return head + "\n\n" + "\n".join(f"- {line}" for _, _, line in outcomes)


# the assistant has said there are several cards or screens, when there is one
_SEVERAL = re.compile(r"\b(?:cards|screens|cartes|[ée]crans)\b|\b(?:each|every|chaque)\s+(?:confirmation\s+)?(?:card|screen|carte|[ée]cran)\b", re.I)

ONE_CARD = HIDDEN + ("There is exactly ONE confirmation card in the app, and it lists {n} item{s}. Say that: one card, one Confirm that runs everything on it. "
                     "Do not say there are several cards or screens. Start with the answer itself.")


def nudge(pending, reply):
    """What to tell the assistant once when its reply talks about several cards while the app shows one, or None."""
    if not pending or not _SEVERAL.search(str(reply or "")):
        return None
    n = len(pending.get("items", []))
    return ONE_CARD.format(n=n, s="" if n == 1 else "s")
