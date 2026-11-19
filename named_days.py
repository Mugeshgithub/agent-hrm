# named_days.py: the weekdays a person names when they ask for leave ("next Thursday and Friday", "du lundi au mercredi") are checked in code
# against the dates the assistant counts or books. The model works the dates out from "Today is Thursday 2026-11-19" and gets the weekday
# wrong: on Fri 20 Nov, "Book me casual leave next Thursday and Friday" was booked as Fri 27 and Sat 28 Nov in 4 runs of 10.
# Only full day names count ("Fri" is left alone), and "next" or "this" is not judged: only that the dates fall on the days named.
# No AI here, so it is unit-tested in CI.
import re
from datetime import timedelta

NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_WORDS = {"monday": 0, "lundi": 0, "tuesday": 1, "tues": 1, "mardi": 1, "wednesday": 2, "mercredi": 2, "thursday": 3, "thurs": 3, "jeudi": 3,
          "friday": 4, "vendredi": 4, "saturday": 5, "samedi": 5, "sunday": 6, "dimanche": 6}
_DAY = re.compile(r"\b(" + "|".join(_WORDS) + r")s?\b", re.I)
LOOK_AHEAD = 14     # the dates listed in the note: the named days in the next two weeks


def named(text):
    """The weekdays (0 = Monday) the text names, as a set."""
    return {_WORDS[m.lower()] for m in _DAY.findall(str(text or ""))}


def mismatch(text, start, end, today):
    """Why the dates don't match the days the person named, as a note for the assistant, or None.
    One day named ("until Friday", "Monday for three days"): the start or the end falls on it. Two or more ("Thursday and Friday",
    "Monday to Wednesday"): the start and the end both fall on one of them."""
    days = named(text)
    if not days:
        return None
    if len(days) == 1:
        wrong = [] if start.weekday() in days or end.weekday() in days else [start, end]
    else:
        wrong = [d for d in dict.fromkeys([start, end]) if d.weekday() not in days]
    if not wrong:
        return None
    said = " and ".join(NAMES[i] for i in sorted(days))
    upcoming = [today + timedelta(days=n) for n in range(LOOK_AHEAD + 1) if (today + timedelta(days=n)).weekday() in days]
    return (f"The person named {said}, but {' and '.join(f'{d.isoformat()} is a {NAMES[d.weekday()]}' for d in wrong)}. "
            f"Today is {NAMES[today.weekday()]} {today.isoformat()}. The {said} dates in the next two weeks: "
            f"{', '.join(f'{NAMES[d.weekday()]} {d.isoformat()}' for d in upcoming)}. Use the dates of the days they named.")


def words_naming_days(said):
    """Which of the person's messages to check, newest first: this one, or, when it names no day and gives no date ("casual", after
    "next Thursday and Friday"), the one before it. Returns "" when neither names a day."""
    said = [str(t or "") for t in said][:2]
    if said and named(said[0]):
        return said[0]
    if len(said) == 2 and not re.search(r"\d", said[0]) and named(said[1]):
        return said[1]
    return ""
