# number_guard.py: the assistant may only state a number of days or an amount in euros that a tool returned in THIS turn (or that the
# person wrote in their own question). Like citations.py for articles. Pure functions, no AI and no third-party package, so they are
# unit-tested in CI and the same code runs in the agent and the evals.
#
# In scope: "8 days", "12.5 paid leave days", "2,5 jours", "€2,195.00", "2 195 €", and a count of things a tool listed in this turn: "Two pending
# requests" when get_pending_requests returned three (real, main 17 Nov: it then listed all three). Out of scope: dates, article numbers, months, and
# a count of something no tool listed in this turn. There is no arithmetic here: a reply that needs a derived figure ("8 now, 6 once it is approved")
# gets it from the TOOL (available_after_pending, balance_after_approval, available_after, short_by ...), so the check stays exact.
import itertools
import json
import re
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from document_answers import HIDDEN

RETRY_MESSAGE = HIDDEN + ("Your reply states {figures}, which no tool returned in this turn. {returned}Answer the question again, in the same language, using only "
                          "figures a tool returned, exactly as returned. If you need a figure from a tool, call it now (get_leave_balance for balances, "
                          "count_leave_days for the days of given dates and what is left after them, get_my_requests for requests, get_payslip for pay). If they "
                          "asked for something no tool returns (a total, a difference, a figure for other dates), give the figures you have and say you do not "
                          "have that one. Start with the answer itself: never say that you calculated, checked or changed anything.")

NOT_CONFIRMED = "I can't confirm that figure, so I won't quote it."
NOT_CONFIRMED_FR = "Je ne peux pas confirmer ce chiffre, je ne le cite donc pas."


class Fact(NamedTuple):
    kind: str          # "days" or "money"
    label: str         # "casual leave balance", "net paid"
    text: str          # "8 days", "€2,195.00"
    value: Decimal


class Figure(NamedTuple):
    kind: str          # "days" or "money"
    value: Decimal
    text: str          # as written in the reply: "12 days", "€2,540.00"
    start: int
    end: int


# ---------- reading numbers ----------

def _decimal(token):
    """2,195.00 and 2.195,00 -> 2195.00; 2,5 -> 2.5; 2,195 -> 2195; 12.5 -> 12.5."""
    token = re.sub(r"[\s\u00a0\u202f]", "", token)
    if "," in token and "." in token:                         # the last mark is the decimal one
        decimal_mark = "," if token.rfind(",") > token.rfind(".") else "."
        thousands = "." if decimal_mark == "," else ","
        token = token.replace(thousands, "").replace(decimal_mark, ".")
    elif "," in token or token.count(".") > 1:
        mark = "," if "," in token else "."
        if re.fullmatch(rf"\d{{1,3}}(?:{re.escape(mark)}\d{{3}})+", token):
            token = token.replace(mark, "")                   # 2,195 is a thousand, not 2.195
        else:
            token = token.replace(mark, ".")                  # 2,5 is 2.5
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def _show(value):
    value = value.normalize()
    return format(value, "f")


# dates, times and article numbers hold digits that are not figures: taken out before any number is read
_NOT_FIGURES = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}(?::\d{2})?\b|\b[LRD]\.?\s?\d{4}(?:-\d+)+\b")
_TOKEN = re.compile(r"(?<![\w.,/:])(?<![A-Za-z]-)\d+(?:[.,]\d+)*(?![\w/:])")

_AMOUNT = r"\d[\d\s\u00a0\u202f.,]*\d|\d"
_MONEY = re.compile(rf"€\s?({_AMOUNT})|({_AMOUNT})\s?(?:€|euros?\b|EUR\b)", re.I)

_MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
           "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
           "janvier", "février", "fevrier", "mars", "avril", "mai", "juin", "juillet", "août", "aout", "septembre", "octobre", "novembre",
           "décembre", "decembre"}
_DAYS = re.compile(r"(?<![\w.,/:€$])(?<![A-Za-z]-)(\d+(?:[.,]\d+)?)(?:\*{1,2})?(?:\s+|-)((?:[^\W\d_]+\s+){0,3}?)(?:days?|jours?)\b", re.I)

# number words, so that "trente jours" in an article makes "30 days" in the answer a confirmed figure (the labour code is in French)
_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
          "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
          "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
          "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12,
          "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16, "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40,
          "cinquante": 50, "soixante": 60, "septante": 70, "huitante": 80, "octante": 80, "nonante": 90, "cent": 100, "cents": 100}
_ONE = re.compile(r"\b(?:un|une|one)\s+(?:[^\W\d_]+\s+)?(?:jours?|days?)\b", re.I)
_WORD_RUN = re.compile(r"\b(?:%s)(?:(?:-et-|-|\s+et\s+)(?:%s|une?))*\b" % ("|".join(_WORDS), "|".join(_WORDS)), re.I)
_AND_A_HALF = re.compile(r"\b(%s)\s+(?:jours?|days?)\s+(?:et\s+demi[e]?|and\s+a\s+half)\b" % "|".join([*_WORDS, "un", "une", "one"]), re.I)
_EIGHTY = re.compile(r"\bquatre[-\s]vingts?(?:[-\s](onze|douze|treize|quatorze|quinze|seize|dix(?:[-\s](?:sept|huit|neuf))?))?\b", re.I)


def _words_in(text):
    found = set()
    for match in _EIGHTY.finditer(text):                      # quatre-vingt is 80, not 4 + 20; quatre-vingt-dix is 90
        rest = (match.group(1) or "").lower().replace(" ", "-")
        found.add(Decimal(80 + sum(_WORDS.get(p, 0) for p in rest.split("-") if p)))
    text = _EIGHTY.sub(" ", text)
    for match in _WORD_RUN.finditer(text):
        parts = re.split(r"-et-|-|\s+et\s+", match.group(0).lower())
        values = [(_WORDS | {"un": 1, "une": 1})[p] for p in parts if p in _WORDS or p in ("un", "une")]
        found.update(Decimal(v) for v in values)
        if len(values) > 1:
            found.add(Decimal(sum(values)))                   # trente-six, vingt et un
    if _ONE.search(text):
        found.add(Decimal(1))
    for match in _AND_A_HALF.finditer(text):                  # "deux jours et demi" is 2.5
        word = match.group(1).lower()
        found.add(Decimal(_WORDS.get(word, 1)) + Decimal("0.5"))
    return found


def _numbers_in(text):
    text = _NOT_FIGURES.sub(" ", text)
    numbers = {n for token in _TOKEN.findall(text) if (n := _decimal(token)) is not None}
    return numbers | _words_in(text)


def _amounts_in(text):
    amounts = set()
    for match in _MONEY.finditer(text):
        value = _decimal(match.group(1) or match.group(2))
        if value is not None:
            amounts.add(abs(value))
    return amounts


# ---------- what the reply states ----------

def figures_in(reply):
    """Every number of days and amount in euros the reply states, in the order written."""
    text = str(reply or "")
    out = []
    for match in _DAYS.finditer(text):
        if any(word.lower() in _MONTHS for word in match.group(2).split()):
            continue                                          # "25 December days" is a date, not a count
        value = _decimal(match.group(1))
        if value is not None:
            out.append(Figure("days", value, re.sub(r"[*_]", "", match.group(0)), match.start(), match.end()))
    for match in _MONEY.finditer(text):
        value = _decimal(match.group(1) or match.group(2))
        if value is not None:
            out.append(Figure("money", abs(value), re.sub(r"[*_]", "", match.group(0)).strip(), match.start(), match.end()))
    return sorted(out, key=lambda f: f.start)


# ---------- what a tool returned ----------

def _load(source):
    try:
        return json.loads(source)
    except (TypeError, ValueError):
        return None


def _is_id(key):
    key = str(key or "")
    return key == "id" or key.endswith("_id") or key in ("year", "ticket")


def _leaves(node, key=None):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, k)
    elif isinstance(node, list):
        for item in node:
            yield from _leaves(item, key)
    else:
        yield key, node


def allowed_figures(sources, user_text=""):
    """(numbers, amounts) the reply may state: everything the tools returned in this turn, and what the person wrote themselves.
    An id, a year or a date in a result does not make a number of days true."""
    numbers, amounts = set(), set()
    for source in sources:
        data = _load(source)
        if data is None:
            numbers |= _numbers_in(str(source))
            amounts |= _amounts_in(str(source))
            continue
        for key, leaf in _leaves(data):
            if isinstance(leaf, bool) or leaf is None:
                continue
            if isinstance(leaf, (int, float)):
                if _is_id(key):
                    continue
                value = Decimal(str(leaf))
                numbers.add(abs(value))                       # "short_by" or a difference may be written either way
                if str(key).endswith("_cents"):
                    amounts.add(abs(value) / 100)
                elif str(key).endswith("_eur"):               # "salary_eur": 3200 is an amount in euros without the sign
                    amounts.add(abs(value))
            else:
                numbers |= _numbers_in(str(leaf))
                amounts |= _amounts_in(str(leaf))
    numbers |= _numbers_in(str(user_text or ""))
    amounts |= _amounts_in(str(user_text or ""))
    return numbers, amounts


# ---------- counts: "Two pending requests" is how many a tool listed in this turn ----------

# the things a tool returns as a list, and how a reply counts them. A list is one of them when its key holds the name ("pending_requests", "my_requests")
_COUNTED = {"request": r"requests?|demandes?", "ticket": r"tickets?", "document": r"documents?", "candidate": r"candidates?|candidat(?:e|s|es)?",
            "job": r"jobs?|postes?", "slot": r"slots?|cr[ée]neaux|cr[ée]neau", "people": r"people|persons?|personnes?|employees?|salari[ée]e?s?"}
_KEYS = {"people": ("people", "found", "reports")}            # "found": 2 is how many people a search found; "reports" is a manager's team
_COUNT_WORDS = {**_WORDS, "one": 1, "un": 1, "une": 1}
# "two pending requests", "**3** open tickets"; not "one of the requests", "two of your requests"
_NOT_QUALIFIER = r"(?!(?:of|the|these|those|your|their|his|her|my|our|from|for|to|in|on|at|and|or|with|by|des|les|de|du|vos|ses|leurs)\b)"
_COUNT = re.compile(r"(?<![\w.,/:€$-])\**(\d+|%s)\**\s+((?:%s[^\W\d_]+\s+){0,2}?)(?:%s)\b"
                    % ("|".join(sorted(_COUNT_WORDS, key=len, reverse=True)), _NOT_QUALIFIER,
                       "|".join(f"(?P<{name}>{words})" for name, words in _COUNTED.items())), re.I)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")


def _count_phrases(text):
    """Every count the text gives of a kind of thing a tool lists: (Figure("count", ...), the kind)."""
    out = []
    for match in _COUNT.finditer(str(text or "")):
        if any(word.lower() in _MONTHS for word in match.group(2).split()):
            continue                                          # "2 December requests" is a date
        word = match.group(1).lower()
        value = Decimal(word) if word.isdigit() else Decimal(_COUNT_WORDS[word])
        kind = next(name for name in _COUNTED if match.group(name))
        out.append((Figure("count", value, re.sub(r"[*_]", "", match.group(0)).strip(), match.start(), match.end()), kind))
    return out


def _listed(sources, kind):
    """What the tools returned in this turn about this kind of thing: the lists under a key that names it, and the numbers under such a key."""
    stems = _KEYS.get(kind, (kind,))
    lists, numbers = [], set()

    def walk(node, key):
        named = any(stem in str(key).lower() for stem in stems) and not _is_id(key)
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            if named:
                lists.append(node)
            for item in node:
                if isinstance(item, (dict, list)):
                    walk(item, key)
        elif named and isinstance(node, (int, float)) and not isinstance(node, bool):
            numbers.add(abs(Decimal(str(node))))
    for source in sources:
        if (data := _load(source)) is not None:
            walk(data, "")
    return lists, numbers


def _named_in(value, sentence):
    value = value.strip().lower()
    first = value.split()[0] if value.split() else ""
    return bool(value) and (re.search(rf"\b{re.escape(value)}\b", sentence) is not None
                            or (len(first) >= 3 and re.search(rf"\b{re.escape(first)}\b", sentence) is not None))


def _true_counts(lists, numbers, sentence):
    """The counts a sentence may give: how many each list holds, and how many of them have what the sentence names (a status, a type, a
    person: "Emma has two requests", "one pending request" in a list of five, "Hugo and Clara: two requests")."""
    said = sentence.lower()
    allowed = set(numbers)
    for rows in lists:
        allowed.add(Decimal(len(rows)))
        rows = [row for row in rows if isinstance(row, dict)]
        values = sorted({v.strip().lower() for row in rows for v in row.values() if isinstance(v, str) and _named_in(v, said)})[:8]
        has = lambda row, value: any(isinstance(v, str) and v.strip().lower() == value for v in row.values())
        for group in [*itertools.combinations(values, 1), *itertools.combinations(values, 2), tuple(values)]:
            if group:
                allowed.add(Decimal(sum(1 for row in rows if all(has(row, g) for g in group))))      # Emma's casual requests
                allowed.add(Decimal(sum(1 for row in rows if any(has(row, g) for g in group))))      # Hugo's and Clara's
    return allowed


def counts_in(reply, turn_sources, user_text=""):
    """Each count the reply gives of a kind of thing a tool listed in this turn, with the counts that are true. A count of something no tool listed
    in this turn is not checked."""
    text = str(reply or "")
    extra = _numbers_in(str(user_text or "")) | {n for s in turn_sources if _load(s) is None for n in _numbers_in(str(s))}
    out = []
    for sentence in _SENTENCE.finditer(text):
        for figure, kind in _count_phrases(sentence.group(0)):
            lists, numbers = _listed(turn_sources, kind)
            if lists or numbers:
                out.append((figure._replace(start=figure.start + sentence.start(), end=figure.end + sentence.start()),
                            _true_counts(lists, numbers, sentence.group(0)) | extra))
    return out


def ungrounded_figures(reply, turn_sources, user_text=""):
    """The figures in the reply that no tool returned in this turn (and the person did not write)."""
    numbers, amounts = allowed_figures(turn_sources, user_text)
    out = [f for f in figures_in(reply) if f.value not in (numbers if f.kind == "days" else amounts)]
    out += [f for f, allowed in counts_in(reply, turn_sources, user_text) if f.value not in allowed]
    return sorted(out, key=lambda f: f.start)


def ungrounded(reply, turn_sources, user_text=""):
    """The same, as the text of each figure, once each: ["12 days", "€2,540.00"]."""
    seen = []
    for f in ungrounded_figures(reply, turn_sources, user_text):
        if f.text not in seen:
            seen.append(f.text)
    return seen


# ---------- what to tell the assistant, and what to show when it still gets it wrong ----------

_GROUPS = {"leave_balance", "all_balances", "current_balance", "balance_after_approval", "available_after_pending", "pending_days"}
_DAY_KEYS = {"working_days", "days_returned", "short_by", "employee_new_balance", "balance_after", "available", "available_after"}
_LEAVE_TYPES = ("casual", "sick", "paid")


def _label(path):
    words = lambda key: str(key).replace("_", " ")
    if path[-1] in _LEAVE_TYPES and len(path) > 1:
        return f"{path[-1]} {words(path[-2])}"
    return words(path[-1])


def _collect(node, path, out):
    if isinstance(node, dict):
        for key, value in node.items():
            _collect(value, path + [key], out)
    elif isinstance(node, list) or isinstance(node, bool) or node is None or not path:
        return                                                # a row of a list (a payslip line, a request) is not THE figure
    else:
        last = str(path[-1])
        if isinstance(node, (int, float)) and not _is_id(last) and (
                last in _DAY_KEYS or last.endswith("_balance") or (last in _LEAVE_TYPES and any(p in _GROUPS for p in path[:-1]))):
            out.append(Fact("days", _label(path), f"{_show(Decimal(str(node)))} days", Decimal(str(node))))
        elif isinstance(node, str) and last.endswith("_eur") and "€" in node and (amount := _amounts_in(node)):
            out.append(Fact("money", last[:-4].replace("_", " "), node, next(iter(amount))))


def _collect_counts(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, list) and any(stem in key.lower() for name in _COUNTED for stem in _KEYS.get(name, (name,))):
                out.append(Fact("count", re.sub(r"^my ", "your ", key.replace("_", " ")), str(len(value)), Decimal(len(value))))
            _collect_counts(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_counts(item, out)


def facts(sources):
    """What the tools returned about days, money and the lists it counts in this turn: Fact("days", "casual leave balance", "8 days", Decimal(8)),
    Fact("count", "pending requests", "3", Decimal(3))."""
    out = []
    for source in sources:
        if (data := _load(source)) is not None:
            _collect(data, [], out)
            _collect_counts(data, out)
    unique = []
    for item in out:
        if item not in unique:
            unique.append(item)
    return unique


def _facts_line(items):
    return "; ".join(f"{item.label}: {item.text}" for item in items)


def retry_message(bad, turn_sources):
    """The note that sends the reply back once. `bad` is ungrounded()."""
    items = facts(turn_sources)[:12]
    returned = (f"The tools returned these figures in this turn: {_facts_line(items)}. " if items
                else "No tool has returned any figure in this turn. ")
    return RETRY_MESSAGE.format(figures=", ".join(bad), returned=returned)


def correct(reply, bad_figures, turn_sources):
    """The reply with each sentence that states an unconfirmed figure replaced: by the figures a tool returned (the first such sentence),
    or by "I can't confirm that figure" when no tool returned any. `bad_figures` is ungrounded_figures(). Nothing unconfirmed gets through."""
    bad = {(f.kind, f.value) for f in bad_figures}
    parts = re.split(r"((?<=[.!?])\s+|\n+)", str(reply or ""))
    available = facts(turn_sources)
    given = lambda text: figures_in(text) + [f for f, _ in _count_phrases(text)]
    stated = {(f.kind, f.value) for f in given(reply)} - bad                # figures the reply already gives that a tool confirmed
    shown = False
    for i in range(0, len(parts), 2):
        sentence = parts[i]
        hit = [f for f in given(sentence) if (f.kind, f.value) in bad]
        if not hit:
            continue
        french = bool(re.search(r"\bjours?\b|\bvous\b|\bvotre\b", sentence, re.I))
        wanted = [item for item in available if item.kind in {f.kind for f in hit}]
        types = [t for t in _LEAVE_TYPES if re.search(rf"\b{t}\b", sentence, re.I)]
        said = sentence.lower()
        narrowed = [item for item in wanted if (any(t in item.label for t in types) if item.kind == "days"
                                                else any(w in said for w in item.label.split() if w not in ("paid", "before")))]
        wanted = (narrowed or wanted)[:6]
        if wanted and all((item.kind, item.value) in stated for item in wanted):
            parts[i] = ""                                                  # the reply already gives those figures: drop the wrong sentence
            shown = True
        elif wanted and not shown:
            shown = True
            parts[i] = (f"Le système indique : {_facts_line(wanted)}." if french else f"The system shows: {_facts_line(wanted)}.")
        else:
            parts[i] = "" if shown else (NOT_CONFIRMED_FR if french else NOT_CONFIRMED)
            shown = shown or not wanted
    fixed = re.sub(r"[ \t]{2,}", " ", "".join(parts)).strip()
    return fixed or NOT_CONFIRMED


def review(reply, turn_sources, user_text, retried):
    """What the answer loop does with a finished reply: ("ok", reply) when every figure is confirmed; ("retry", note) the first time one
    is not (the loop sends the note and asks again); ("replaced", fixed reply) when it still is not after that one retry."""
    unconfirmed = ungrounded_figures(reply, turn_sources, user_text)
    if not unconfirmed:
        return "ok", reply
    if not retried:
        return "retry", retry_message(ungrounded(reply, turn_sources, user_text), turn_sources)
    return "replaced", correct(reply, unconfirmed, turn_sources)
