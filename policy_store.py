# policy_store.py: the company policy as HR edits it in the Handbook.
#   - hr_policy.md is the policy as shipped: the first version. The app never changes that file (so git stays clean).
#   - HR edits, adds, removes or restores a section; every save stores the WHOLE policy as a new version (who, when, what changed,
#     an optional note). The current policy is the latest version, or the file while nobody has edited it. Nothing is overwritten:
#     any earlier version can be read and restored.
#   - Three things read the current policy: the Handbook page, the assistant's policy search (policy.py) and the paid-leave window
#     (tools.py), so the text stays the only place that rule is written.
#   - Some rules are enforced in code (the yearly leave allowances, the monthly accrual, counting paid leave Monday to Saturday) and written
#     in the text too. A save that takes such a sentence out is refused: the text would say one thing and the app do another.
#   - After a save the new sections are embedded at once (REINDEX), so nobody's question waits for it. If that fails the save stands and the
#     search embeds the new text on its next question.
# No AI and no third-party import at load time, so it is unit-tested in CI.
import calendar
import json
import os
import re
import sqlite3

import db

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hr_policy.md")
TITLE_MAX = 100
TEXT_MAX = 4000
NOTE_MAX = 300
SECTIONS_MAX = 30
REINDEX = None          # called after a save to embed the new text; None means policy.section_vectors. Tests put a fake here

# what the leave rules in code say, as the sentences that must stay somewhere in the policy: (pattern, what to tell HR)
ENFORCED = [
    (r"12 casual leave days per year", "\"12 casual leave days per year\" (the casual allowance the app gives every January)"),
    (r"6 sick leave days per year", "\"6 sick leave days per year\" (the sick allowance the app gives every January)"),
    (r"Up to 5 unused casual leave days", "\"Up to 5 unused casual leave days\" (the carry-forward cap the app applies on 1 January)"),
    (r"2\.5 days of paid leave per month", "\"2.5 days of paid leave per month\" (the monthly accrual the app credits)"),
    (r"from Monday to Saturday", "\"from Monday to Saturday\" (how the app counts paid leave days)"),
]
PAID_WINDOW = re.compile(r"Paid leave is normally taken between (\d+) (\w+) and (\d+) (\w+)\..*?your manager decides whether to approve it\.", re.S)
PAID_WINDOW_HELP = ("the leave rules read the paid-leave window from the text, so one section must keep \"Paid leave is normally taken between "
                    "1 May and 31 October. ... your manager decides whether to approve it.\" (the dates can change)")


def init_policy():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS policy_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sections_json TEXT NOT NULL,         -- the whole policy after this save
                saved_by TEXT NOT NULL,
                saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                change TEXT NOT NULL,                -- what this save did, in words
                note TEXT NOT NULL DEFAULT '',
                restored_from INTEGER                -- the version this one is a copy of, when it was restored
            )""")
    conn.close()


# ---------- reading ----------

def default_sections():
    """The policy as shipped: the sections of hr_policy.md."""
    text = open(DEFAULT_PATH, encoding="utf-8").read()
    sections = []
    for part in text.split("\n## ")[1:]:
        title, _, body = part.partition("\n")
        sections.append({"title": title.strip(), "text": body.strip()})
    return sections


def _latest():
    try:
        conn = db.get_conn()
        row = conn.execute("SELECT * FROM policy_versions ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
    except sqlite3.OperationalError:        # a database made before the editor existed
        return None
    return row


def current():
    """{"version", "sections", "saved_by", "saved_at", "change", "original"}. Version 0 is the file as shipped."""
    row = _latest()
    if row is None:
        return {"version": 0, "sections": default_sections(), "saved_by": None, "saved_at": None, "change": "The policy as shipped", "original": True}
    return {"version": row["id"], "sections": json.loads(row["sections_json"]), "saved_by": row["saved_by"], "saved_at": row["saved_at"],
            "change": row["change"], "original": False}


def sections():
    return current()["sections"]


def get_version(version_id):
    """The sections of one version, or None. Version 0 is the file as shipped."""
    if version_id == 0:
        return default_sections()
    conn = db.get_conn()
    row = conn.execute("SELECT sections_json FROM policy_versions WHERE id = ?", (version_id,)).fetchone()
    conn.close()
    return json.loads(row["sections_json"]) if row else None


def versions():
    """Newest first, ending with the policy as shipped. `current` marks the one in force."""
    from tools import get_employee
    conn = db.get_conn()
    rows = conn.execute("SELECT id, saved_by, saved_at, change, note, restored_from FROM policy_versions ORDER BY id DESC").fetchall()
    conn.close()
    out = []
    for r in rows:
        who = get_employee(r["saved_by"])
        out.append({"id": r["id"], "saved_by": who["name"] if who else r["saved_by"], "saved_at": r["saved_at"], "change": r["change"],
                    "note": r["note"], "restored_from": r["restored_from"], "current": False})
    out.append({"id": 0, "saved_by": None, "saved_at": None, "change": "The policy as shipped", "note": "", "restored_from": None, "current": False})
    out[0]["current"] = True
    return out


def paid_policy(policy_sections=None):
    """((month, day), (month, day)), note: the paid-leave window and the note shown with paid leave, from the current policy.
    Raises ValueError when no section has the sentences the leave rules read."""
    text = "\n\n".join(s["text"] for s in (policy_sections if policy_sections is not None else sections()))
    m = PAID_WINDOW.search(text)
    if not m:
        raise ValueError(PAID_WINDOW_HELP)
    months = list(calendar.month_name)
    try:
        window = ((months.index(m.group(2)), int(m.group(1))), (months.index(m.group(4)), int(m.group(3))))
    except ValueError:
        raise ValueError("the paid-leave window must name its months in English, for example \"1 May and 31 October\"")
    return window, " ".join(m.group(0).split())


# ---------- the rules a policy must keep ----------

def problem_with(policy_sections):
    """Why this list of sections cannot be saved, or None."""
    if not policy_sections:
        return "The policy needs at least one section."
    if len(policy_sections) > SECTIONS_MAX:
        return f"The policy can have at most {SECTIONS_MAX} sections."
    seen = set()
    for s in policy_sections:
        title, text = s["title"], s["text"]
        if not title:
            return "Every section needs a title."
        if len(title) > TITLE_MAX or "\n" in title:
            return f"A title is one line of at most {TITLE_MAX} characters."
        if title.lower() in seen:
            return f"There is already a section called \"{title}\"."
        seen.add(title.lower())
        if not text:
            return f"\"{title}\" needs some text."
        if len(text) > TEXT_MAX:
            return f"\"{title}\" is too long ({len(text)} characters; the limit is {TEXT_MAX})."
    everything = "\n".join(s["text"] for s in policy_sections)
    for pattern, what in ENFORCED:
        if not re.search(pattern, everything):
            return f"The policy has to keep {what}: the app enforces it, so the text must say the same. To change the rule itself, ask a developer."
    try:
        paid_policy(policy_sections)
    except ValueError as e:
        return f"The policy has to keep this sentence: {e}."
    return None


# ---------- saving ----------

def _hr(hr_id):
    import onboarding            # imported here: onboarding imports tools, which reads the paid-leave window from this module
    return onboarding.is_hr(hr_id)


def _clean(text):
    return "\n".join(line.rstrip() for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip().split("\n"))


def _reindex():
    try:
        hook = REINDEX
        if hook is None:
            from policy import section_vectors as hook
        hook()
        return True
    except Exception:
        return False


def _store(hr_id, base_version, new_sections, change, note, restored_from=None):
    """Save `new_sections` as the next version, or return {"error"}. `base_version` is the version the person was looking at."""
    if not _hr(hr_id):
        return {"error": "Only HR can edit the policy."}
    now = current()
    if base_version != now["version"]:
        return {"error": "The policy was changed since you opened it. Reload the page to see the latest version, then make your change again."}
    problem = problem_with(new_sections)
    if problem:
        return {"error": problem}
    if new_sections == now["sections"]:
        return {"error": "Nothing changed."}
    conn = db.get_conn()
    try:
        with conn:
            version = conn.execute("INSERT INTO policy_versions (sections_json, saved_by, change, note, restored_from) VALUES (?, ?, ?, ?, ?)",
                                   (json.dumps(new_sections, ensure_ascii=False), hr_id, change, (note or "").strip()[:NOTE_MAX], restored_from)).lastrowid
    finally:
        conn.close()
    return {"success": True, "version": version, "change": change, "indexed": _reindex()}


def save_section(hr_id, base_version, original_title, title, text, note=""):
    """Edit the section called `original_title`, or add a new one when `original_title` is empty."""
    title, text = " ".join(str(title or "").split()), _clean(text)
    old = current()["sections"]
    if not original_title:
        return _store(hr_id, base_version, old + [{"title": title, "text": text}], f"Added \"{title}\"", note)
    index = next((i for i, s in enumerate(old) if s["title"] == original_title), None)
    if index is None:
        return {"error": "That section no longer exists. Reload the page."}
    new = [dict(s) for s in old]
    new[index] = {"title": title, "text": text}
    renamed = f"Renamed \"{original_title}\" to \"{title}\" and edited it" if title != original_title and text != old[index]["text"] \
        else f"Renamed \"{original_title}\" to \"{title}\"" if title != original_title else f"Edited \"{title}\""
    return _store(hr_id, base_version, new, renamed, note)


def delete_section(hr_id, base_version, title, note=""):
    old = current()["sections"]
    if not any(s["title"] == title for s in old):
        return {"error": "That section no longer exists. Reload the page."}
    return _store(hr_id, base_version, [s for s in old if s["title"] != title], f"Removed \"{title}\"", note)


def restore(hr_id, base_version, version_id, note=""):
    """Make an earlier version the current one again (as a new version: the history only grows)."""
    target = get_version(version_id) if isinstance(version_id, int) else None
    if target is None:
        return {"error": "No such version."}
    label = "the policy as shipped" if version_id == 0 else f"version {version_id}"
    return _store(hr_id, base_version, target, f"Restored {label}", note, restored_from=version_id)
