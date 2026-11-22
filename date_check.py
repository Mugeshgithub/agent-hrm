# date_check.py: the dates the assistant may name, as a table made by code, and a check of every weekday it writes next to a date.
# Real, Sun 22 Nov 2026: "Friday 29 November" (29 November is a Sunday) for a person who asked for Friday 27 November. The model worked the
# date out from "Today is Sunday 2026-11-22" and got it wrong; the booking evals for "next Thursday and Friday" and "Monday to Wednesday next
# week" failed on Sundays only, for the same reason. So the prompt gets a table of today and the next 14 days with their weekdays and weeks,
# and rules that turn each way of naming a day into a look-up in it (never a sum); and a reply that pairs a weekday with a date is checked
# here with real date maths: one retry naming the right weekdays, then a safe reply. No AI here, so it is unit-tested in CI.
import re
from datetime import date, timedelta

NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_FR_NAMES = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                                       "november", "december"], 1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_MONTHS.update({"sept": 9})
_MONTHS.update({m: i for i, m in enumerate(["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
                                           "novembre", "décembre"], 1)})
_MONTHS.update({"fevrier": 2, "aout": 8, "decembre": 12, "janv": 1, "févr": 2, "fevr": 2, "avr": 4, "juil": 7, "déc": 12, "dec": 12})
DAYS_AHEAD = 14

_WEEKDAY_WORDS = {n.lower(): i for i, n in enumerate(NAMES)}
_WEEKDAY_WORDS.update({n[:3].lower(): i for i, n in enumerate(NAMES)})
_WEEKDAY_WORDS.update({"tues": 1, "wed": 2, "weds": 2, "thur": 3, "thurs": 3})
_WEEKDAY_WORDS.update({n: i for i, n in enumerate(_FR_NAMES)})
_WEEKDAY_WORDS.update({"lun": 0, "mar": 1, "mer": 2, "jeu": 3, "ven": 4, "sam": 5, "dim": 6})

_WD = r"(?P<wd>" + "|".join(sorted(_WEEKDAY_WORDS, key=len, reverse=True)) + r")\.?"
_MON = r"(?P<mon>" + "|".join(sorted((re.escape(m) for m in _MONTHS), key=len, reverse=True)) + r")\.?"
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th|er)?"
_YEAR = r"(?:,?\s+(?P<year>20\d\d))?"
# "Friday 29 November", "Fri 27 Nov 2026", "Friday, the 27th of November", "Friday, November 27", "vendredi 27 novembre", "Friday 2026-11-27",
# "Friday (27 November)", "27 November (Friday)"
_PAIRS = [re.compile(p, re.I) for p in (
    rf"\b{_WD},?\s+\(?(?:the\s+|le\s+)?{_DAY}(?:\s+of)?\s+{_MON}{_YEAR}\b",
    rf"\b{_WD},?\s+{_MON}\s+{_DAY}{_YEAR}\b",
    rf"\b{_WD},?\s+\(?(?P<iso>20\d\d-\d\d-\d\d)\b",
    rf"\b(?P<iso>20\d\d-\d\d-\d\d)\s*\(\s*{_WD}\s*\)",
    rf"\b{_DAY}\s+{_MON}{_YEAR}\s*\(\s*{_WD}\s*\)",
)]


def table(today):
    """The dates the assistant may name: today and the next DAYS_AHEAD days, each with its weekday and its week, for the prompt."""
    week = lambda d: (d - timedelta(days=d.weekday()) - (today - timedelta(days=today.weekday()))).days // 7
    label = {0: "this week", 1: "next week", 2: "the week after next"}
    rows = [f"- {NAMES[d.weekday()]} {d.day} {d:%B %Y} ({d.isoformat()}), {label.get(week(d), 'later')}{' (today)' if d == today else ''}"
            for d in (today + timedelta(days=n) for n in range(DAYS_AHEAD + 1))]
    return ("Dates (made by code; weeks run Monday to Sunday). Take every date and weekday from this list, never work one out:\n" + "\n".join(rows)
            + "\nHow to read what the person says, with this list (each is a look-up by the week labels, never a sum): a weekday on its own "
            "(\"Friday\", \"book me Friday\") is the first Friday in the list after today. \"This Friday\" is the Friday marked this week. "
            "\"Next Friday\" is the Friday marked next week, and \"the Friday after next\" the Friday marked the week after next. \"Next week\" is "
            "the days marked next week (on a Sunday, the week that starts tomorrow): \"next Thursday and Friday\" is the Thursday and Friday marked "
            "next week, \"Monday to Wednesday next week\" the Monday to Wednesday marked next week. "
            "Ask which day they mean only when the list cannot answer it (a date further away, or two readings that both fit). When you write a "
            "date with its weekday, copy both from the same line.")


# ---- the days a person names, worked out by code ----
# The rules of the table, applied in code: the model kept its own reading of the phrases people read differently (on a Monday it booked
# next week's Thursday for "next Thursday" whatever the rule said, and this week's Friday for "the Friday after next"). So the dates
# are worked out here and given to it with the message; it copies them.
_EN = {n.lower(): i for i, n in enumerate(NAMES)}
_FR = {n: i for i, n in enumerate(_FR_NAMES)}
_W = r"(" + "|".join(list(_EN) + list(_FR)) + r")s?"
_TO = r"\s*(?:to|through|until|till|-|–|au|à|jusqu'au)\s*"
_NAMED = [(re.compile(p, re.I), kind) for p, kind in (
    (rf"\b(?:the\s+)?{_W}\s+after\s+next\b", "after_next"),
    (rf"\b{_W}\s+en\s+huit\b", "first_plus_week"),
    (rf"\b(?:from\s+)?{_W}{_TO}{_W}\s+(?:next\s+week|de\s+la\s+semaine\s+prochaine|la\s+semaine\s+prochaine)\b", "range_next_week"),
    (rf"\bnext\s+{_W}(?:\s+(?:and|&|to|through|until|-)\s+{_W})?", "next_week"),
    (rf"\b{_W}(?:\s+et\s+{_W})?\s+prochains?\b", "first"),
    (rf"\bthis\s+{_W}", "this_week"),
    (rf"\b(?:from\s+|du\s+)?{_W}{_TO}{_W}\b", "range"),
    (rf"\b{_W}\b", "first"),
)]


def _wd(word):
    word = word.lower().rstrip("s") if word.lower().rstrip("s") in _EN or word.lower().rstrip("s") in _FR else word.lower()
    return _EN.get(word, _FR.get(word))


def _first_after(today, wd):
    return today + timedelta(days=(wd - today.weekday() - 1) % 7 + 1)


def _in_week(today, weeks_on, wd):
    return today - timedelta(days=today.weekday()) + timedelta(days=7 * weeks_on + wd)


def named_dates(text, today):
    """[(the words, [dates])] for every day the text names by its weekday, by the rules of the table: a weekday on its own (or "vendredi
    prochain") is the first one after today; "this", "next" and "after next" are that day in this week, next week and the week after;
    "Monday to Wednesday next week" is next week's. A day of this week that is already past is left out (nothing to work out)."""
    text, taken, found = str(text or ""), [], []
    for pattern, kind in _NAMED:
        for m in pattern.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            days = [_wd(g) for g in m.groups() if g]
            if any(d is None for d in days):
                continue
            if kind == "after_next":
                dates = [_in_week(today, 2, days[0])]
            elif kind == "first_plus_week":
                dates = [_first_after(today, days[0]) + timedelta(days=7)]
            elif kind in ("range_next_week", "next_week"):
                dates = [_in_week(today, 1, d) for d in days]
            elif kind == "this_week":
                dates = [_in_week(today, 0, days[0])]
                if dates[0] < today:
                    continue
            elif kind == "range":
                start = _first_after(today, days[0])
                dates = [start, start + timedelta(days=(days[1] - start.weekday()) % 7)]
            else:
                start = _first_after(today, days[0])
                dates = [start] + [start + timedelta(days=(d - start.weekday()) % 7) for d in days[1:]]
            taken.append(m.span())
            found.append((m.start(), m.group(0).strip(), dates))
    return [(words, dates) for _, words, dates in sorted(found)]


def dates_note(text, today):
    """The note given to the model with the person's message, or "" when it names no weekday."""
    named = named_dates(text, today)
    if not named:
        return ""
    said = "; ".join(f"\"{w}\" = " + " and ".join(f"{NAMES[d.weekday()]} {d.day} {d:%B %Y} ({d.isoformat()})" for d in ds) for w, ds in named)
    return (f"\n\n(Dates worked out by the app, today being {NAMES[today.weekday()]} {today.day} {today:%B %Y}: {said}. Use exactly these dates; "
            "for a range, they are its first and last days. Ask only if the person's words could mean something else.)")


def _year_for(month, day, today):
    """The year of a date written without one: the one nearest to today (late December read in early January is last year's)."""
    best = None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((d - today).days) < abs((best - today).days):
            best = d
    return best


def wrong_pairs(reply, today):
    """Every weekday the reply writes next to a date that falls on another day, as (what it says, the date, the date's real weekday)."""
    found, seen = [], set()
    for pattern in _PAIRS:
        for m in pattern.finditer(str(reply or "")):
            wd = _WEEKDAY_WORDS[m.group("wd").lower().rstrip(".")]
            try:
                if m.groupdict().get("iso"):
                    d = date.fromisoformat(m.group("iso"))
                else:
                    month, day = _MONTHS[m.group("mon").lower().rstrip(".")], int(m.group("day"))
                    d = date(int(m.group("year")), month, day) if m.group("year") else _year_for(month, day, today)
            except (ValueError, KeyError):
                continue
            if d is None or (m.start(), d) in seen:
                continue
            seen.add((m.start(), d))
            if d.weekday() != wd:
                found.append((m.group(0).strip(), d, NAMES[d.weekday()]))
    return found


def retry_note(wrong, today):
    said = "; ".join(f"\"{text}\": {d.day} {d:%B %Y} is a {real}" for text, d, real in wrong)
    return (f"Your reply pairs a weekday with a date that falls on another day: {said}. Today is {NAMES[today.weekday()]} {today.day} "
            f"{today:%B %Y}. Take the date and its weekday from the same line of the date list in your instructions, and answer again. If the "
            "person named a weekday, use the date of that weekday. Do not mention this note.")


def safe_reply(today, card_waiting):
    if card_waiting:
        return ("Sorry, I mixed up a weekday and a date in my answer. The card below shows the exact dates with their weekdays: please check "
                "them before you confirm.")
    return (f"Sorry, I mixed up a weekday and a date, so I won't guess. Today is {NAMES[today.weekday()]} {today.day} {today:%B %Y}. Which "
            "dates do you mean? You can give them as numbers, for example 27 November.")


def review(reply, today, retried, card_waiting):
    """("ok", reply), ("retry", note) the first time a weekday and a date disagree, ("replaced", safe reply) when they still do."""
    wrong = wrong_pairs(reply, today)
    if not wrong:
        return "ok", reply
    if not retried:
        return "retry", retry_note(wrong, today)
    return "replaced", safe_reply(today, card_waiting)
