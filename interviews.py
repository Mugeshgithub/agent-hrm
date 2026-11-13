# interviews.py: recruitment, step 5: scheduling an interview. The hiring manager (or HR) offers a few times; the candidate picks one on their private
# page; the confirmation and the reminder wait in the candidate outbox for HR to copy and send (the app sends no email, like outbox.py); either side can
# cancel, and the way to change a time is to offer new ones. The confirmed time can be downloaded as an .ics file. There is no calendar integration.
# No AI is involved: nothing here decides anything about a candidate, it only fixes a time that people chose. Rules:
#   - only HR or the job's hiring manager offers or cancels, and only for a candidate still in the process; times are Paris time, in the future, at most
#     90 days ahead, 1 to 5 of them; a new offer replaces the open one
#   - choosing is atomic (two clicks cannot both win) and a time the offering person already has another interview at is refused, since nothing reads a calendar
#   - everything about an interview belongs to the application and is erased with it (careers.erase); the .ics carries no name, only the job and the time
# No third-party package, so it is unit-tested in CI.
import json
from datetime import datetime, timedelta, timezone

import careers
import db
import jobs
import onboarding
from jobs import _rows, get_employee

ZONE = "Europe/Paris"
DURATIONS = (15, 30, 45, 60, 90, 120)
MAX_SLOTS = 5
MAX_AHEAD_DAYS = 90
MIN_NOTICE_HOURS = 1
MAX_PLACE = 200
MAX_NOTE = 500
ACTIVE = jobs.ACTIVE                    # an interview is only arranged for someone still in the process
NOT_FOUND = "No such candidate."
GONE = "This interview is no longer open."
TAKEN = "That time has just been taken. Please choose another one, or tell HR."

SLOT_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M")


# ---------- times ----------

def now_paris():
    """The wall clock in Paris, with no zone attached (every time here is Paris time)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(ZONE)).replace(tzinfo=None, second=0, microsecond=0)
    except Exception:                   # no time zone data on this machine: the machine's own clock
        return datetime.now().replace(second=0, microsecond=0)


def parse_slot(text):
    """A datetime for 'YYYY-MM-DDTHH:MM' or 'YYYY-MM-DD HH:MM', else None."""
    for fmt in SLOT_FORMATS:
        try:
            return datetime.strptime(str(text or "").strip(), fmt)
        except ValueError:
            continue
    return None


def key(moment):
    return moment.strftime("%Y-%m-%d %H:%M")


def label(moment):
    return f"{moment:%A} {moment.day} {moment:%B %Y}, {moment:%H:%M}"


def _short(moment):
    return f"{moment:%a} {moment.day} {moment:%b}, {moment:%H:%M}"


def _overlaps(a_start, a_minutes, b_start, b_minutes):
    return a_start < b_start + timedelta(minutes=b_minutes) and b_start < a_start + timedelta(minutes=a_minutes)


# ---------- reading ----------

def _clean_text(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _present(row, now=None):
    """One interview as the candidate and the staff see it: nothing but the times, the place and the state."""
    slots = [parse_slot(s) for s in json.loads(row["slots"])]
    chosen = parse_slot(row["chosen"]) if row["chosen"] else None
    now = now or now_paris()
    return {"id": row["id"], "status": row["status"], "duration_min": row["duration_min"], "place": row["place"], "note": row["note"],
            "slots": [{"value": key(s), "label": label(s), "past": s <= now} for s in slots] if row["status"] == "offered" else [],
            "chosen": {"value": key(chosen), "label": label(chosen)} if chosen else None, "cancelled_by": row["cancelled_by"],
            "created_at": row["created_at"]}


def _current(application_id):
    rows = _rows("SELECT * FROM interviews WHERE application_id = ? ORDER BY id DESC LIMIT 1", (application_id,))
    return rows[0] if rows else None


def for_candidate(app, now=None):
    """What the candidate's page (and the assistant, which only reads) shows: the latest interview, or {"status": "none"}."""
    row = _current(app["id"])
    return _present(row, now) if row else {"status": "none"}


def _application(application_id):
    rows = _rows("SELECT * FROM applications WHERE id = ? AND erased_at IS NULL", (application_id,))
    return rows[0] if rows else None


def _staff(actor_id, application_id):
    """(application, job) when this person is HR or the job's hiring manager, else (None, None)."""
    app = _application(application_id)
    job = jobs._job(app["job_id"]) if app else None
    return (app, job) if app and jobs.can_see(jobs._actor(actor_id), job) else (None, None)


def for_staff(actor_id, application_id, now=None):
    """The candidate's interview for HR or the hiring manager: the latest, the earlier ones, and whether times can be offered now."""
    app, job = _staff(actor_id, application_id)
    if app is None:
        return {"error": NOT_FOUND}
    rows = _rows("""SELECT i.*, p.name AS by_name FROM interviews i LEFT JOIN employees p ON p.id = i.created_by
                    WHERE i.application_id = ? ORDER BY i.id DESC""", (application_id,))
    shown = [{**_present(r, now), "offered_by": r["by_name"]} for r in rows]
    reason = None if app["stage"] in ACTIVE else "This candidate is no longer in the process."
    if not reason and not app["email"]:
        reason = "This candidate has no email address, so nothing can be sent to them."
    return {"current": shown[0] if shown else None, "earlier": shown[1:], "can_offer": reason is None, "reason": reason,
            "job_title": job["title"], "zone": ZONE, "durations": list(DURATIONS), "max_slots": MAX_SLOTS}


# ---------- messages and events ----------

def _event(conn, application_id, actor_id, event, reason):
    conn.execute("INSERT INTO application_events (application_id, actor_id, event, reason) VALUES (?, ?, ?, ?)", (application_id, actor_id, event, reason))


def _queue(conn, app, subject, body, kind):
    conn.execute("INSERT INTO candidate_outbox (application_id, to_email, subject, body, kind) VALUES (?, ?, ?, ?, ?)", (app["id"], app["email"], subject, body, kind))


def _notify(conn, employee_ids, message):
    for emp_id in dict.fromkeys(i for i in employee_ids if i and get_employee(i) is not None):
        conn.execute("INSERT INTO notifications (employee_id, message) VALUES (?, ?)", (emp_id, message))


def _details(row_or_view):
    lines = [f"Duration: {row_or_view['duration_min']} minutes (Paris time)"]
    if row_or_view["place"]:
        lines.append(f"Where: {row_or_view['place']}")
    if row_or_view["note"]:
        lines.append(f"Note: {row_or_view['note']}")
    return "\n".join(lines)


# ---------- offering, choosing, cancelling ----------

def offer(actor_id, application_id, data, now=None):
    """HR or the hiring manager offers 1 to 5 times. {"slots": ["2026-11-24T14:00", ...], "duration_min": 45, "place": "...", "note": "..."}.
    Replaces an interview that was still open. Returns the staff view, or {"error"}."""
    app, job = _staff(actor_id, application_id)
    if app is None:
        return {"error": NOT_FOUND}
    if app["stage"] not in ACTIVE:
        return {"error": "This candidate is no longer in the process."}
    if not app["email"]:
        return {"error": "This candidate has no email address, so nothing can be sent to them."}
    now = now or now_paris()
    duration = data.get("duration_min")
    if duration not in DURATIONS:
        return {"error": f"Choose a duration of {', '.join(str(d) for d in DURATIONS)} minutes."}
    raw = data.get("slots") or []
    moments = [parse_slot(s) for s in raw] if isinstance(raw, list) else []
    if not moments or len(moments) > MAX_SLOTS or any(m is None for m in moments):
        return {"error": f"Offer between 1 and {MAX_SLOTS} times."}
    if len({key(m) for m in moments}) != len(moments):
        return {"error": "The same time is offered twice."}
    for m in moments:
        if m < now + timedelta(hours=MIN_NOTICE_HOURS):
            return {"error": f"{label(m)} is too soon: offer a time at least {MIN_NOTICE_HOURS} hour ahead."}
        if m > now + timedelta(days=MAX_AHEAD_DAYS):
            return {"error": f"{label(m)} is more than {MAX_AHEAD_DAYS} days away."}
    conn = db.get_conn()
    try:
        for m in moments:
            clash = _clash(conn, actor_id, m, duration, ignore_application=application_id)
            if clash:
                return {"error": f"You already have an interview at {label(m)}. Choose a different time."}
        place, note = _clean_text(data.get("place"), MAX_PLACE), _clean_text(data.get("note"), MAX_NOTE)
        with conn:
            old = conn.execute("SELECT id FROM interviews WHERE application_id = ? AND status IN ('offered', 'confirmed')", (application_id,)).fetchall()
            for row in old:
                conn.execute("UPDATE interviews SET status = 'cancelled', cancelled_by = 'staff', cancelled_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            conn.execute("INSERT INTO interviews (application_id, created_by, status, slots, duration_min, place, note) VALUES (?, ?, 'offered', ?, ?, ?, ?)",
                         (application_id, actor_id, json.dumps([key(m) for m in sorted(moments)]), duration, place, note))
            _event(conn, application_id, actor_id, "interview offered", f"Offered {len(moments)} time{'s' if len(moments) > 1 else ''}" + (" instead of the earlier ones" if old else ""))
            times = "\n".join(f"- {label(m)}" for m in sorted(moments))
            _queue(conn, app, f"Your interview for {job['title']} at CognivexAI: please choose a time",
                   f"Hello {app['name']},\n\nWe would like to meet you for the {job['title']} role. Please choose the time that suits you best:\n\n{times}\n\n"
                   f"{_details({'duration_min': duration, 'place': place, 'note': note})}\n\nOpen your private tracking page (the link we gave you when you applied) "
                   f"and press the time you choose. If none of them suits you, tell us there and we will offer others.\n\nCognivexAI recruitment", "interview offered")
    finally:
        conn.close()
    return for_staff(actor_id, application_id, now)


def _clash(conn, offerer_id, moment, minutes, ignore_application=None):
    """The confirmed interview of this person that overlaps the time, or None (nothing reads a calendar, so this is the only double-booking check)."""
    for row in conn.execute("SELECT * FROM interviews WHERE created_by = ? AND status = 'confirmed' AND application_id != ?", (offerer_id, ignore_application or -1)).fetchall():
        start = parse_slot(row["chosen"])
        if start and _overlaps(moment, minutes, start, row["duration_min"]):
            return row
    return None


def choose(token, slot, now=None):
    """The candidate picks one of the offered times. Atomic: the first choice wins. Returns the candidate's view or {"error"}."""
    app = careers._by_token(token)
    if app is None:
        return {"error": careers.NOT_FOUND}
    row = _current(app["id"])
    if row is None or row["status"] != "offered":
        return {"error": GONE}
    moment, now = parse_slot(slot), now or now_paris()
    if moment is None or key(moment) not in json.loads(row["slots"]):
        return {"error": "That is not one of the times offered."}
    if moment <= now:
        return {"error": "That time has passed. Please choose another one, or tell HR."}
    job = jobs._job(app["job_id"])
    conn = db.get_conn()
    try:
        if _clash(conn, row["created_by"], moment, row["duration_min"], ignore_application=app["id"]):
            return {"error": TAKEN}
        with conn:
            won = conn.execute("UPDATE interviews SET status = 'confirmed', chosen = ? WHERE id = ? AND status = 'offered'", (key(moment), row["id"])).rowcount
            if not won:
                return {"error": GONE}
            _event(conn, app["id"], None, "interview confirmed", f"The candidate chose {label(moment)}")
            _queue(conn, app, f"Your interview for {job['title']} at CognivexAI is confirmed",
                   f"Hello {app['name']},\n\nYour interview for the {job['title']} role is confirmed:\n\n{label(moment)}\n"
                   f"{_details(row)}\n\nYou can add it to your calendar from your private tracking page (the .ics file). If you cannot come, cancel it there as soon as you can "
                   f"and we will offer you other times.\n\nCognivexAI recruitment", "interview confirmed")
            _notify(conn, [row["created_by"], job["hiring_manager_id"]], f"{app['name']} chose {_short(moment)} for the {job['title']} interview.")
    finally:
        conn.close()
    return for_candidate(app, now)


def cancel(actor_id, application_id, reason="", now=None):
    """HR or the hiring manager cancels the open interview. The candidate is told through the outbox."""
    app, job = _staff(actor_id, application_id)
    if app is None:
        return {"error": NOT_FOUND}
    row = _current(application_id)
    if row is None or row["status"] == "cancelled":
        return {"error": "There is no open interview to cancel."}
    reason = _clean_text(reason, 300)
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("UPDATE interviews SET status = 'cancelled', cancelled_by = 'staff', cancelled_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            _event(conn, application_id, actor_id, "interview cancelled", "Cancelled by " + ("HR" if jobs._actor(actor_id)["role"] == "hr" else "the hiring manager") + (f": {reason}" if reason else ""))
            _queue(conn, app, f"Your interview for {job['title']} at CognivexAI is cancelled",
                   f"Hello {app['name']},\n\nWe have to cancel the interview for the {job['title']} role." + (f"\n\n{reason}" if reason else "") +
                   "\n\nWe will offer you other times soon.\n\nCognivexAI recruitment", "interview cancelled")
    finally:
        conn.close()
    return for_staff(actor_id, application_id, now)


def decline(token, now=None):
    """The candidate cannot make the offered or the confirmed time: the interview is cancelled, HR sees a note, and the staff are told. Returns the candidate's view."""
    app = careers._by_token(token)
    if app is None:
        return {"error": careers.NOT_FOUND}
    row = _current(app["id"])
    if row is None or row["status"] not in ("offered", "confirmed"):
        return {"error": GONE}
    job = jobs._job(app["job_id"])
    chosen = parse_slot(row["chosen"]) if row["chosen"] else None
    text = (f"I can no longer attend the interview on {label(chosen)}. Please offer me other times." if chosen
            else "None of the interview times you offered suit me. Please offer me other times.")
    conn = db.get_conn()
    try:
        with conn:
            done = conn.execute("UPDATE interviews SET status = 'cancelled', cancelled_by = 'candidate', cancelled_at = CURRENT_TIMESTAMP WHERE id = ? AND status IN ('offered', 'confirmed')",
                                (row["id"],)).rowcount
            if not done:
                return {"error": GONE}
            _event(conn, app["id"], None, "interview cancelled", "The candidate cannot make it" if chosen else "The candidate asked for other times")
            conn.execute("INSERT INTO candidate_notes (application_id, text) VALUES (?, ?)", (app["id"], text))
            _notify(conn, [row["created_by"], job["hiring_manager_id"]], f"{app['name']} asked for other interview times for the {job['title']} role.")
    finally:
        conn.close()
    return for_candidate(app, now)


# ---------- reminders (the daily job) ----------

def run_reminders(now=None):
    """Queue a reminder for every confirmed interview that is today or tomorrow and has none yet. Safe to run more than once a day. Returns how many."""
    now = now or now_paris()
    queued = 0
    conn = db.get_conn()
    try:
        with conn:
            for row in conn.execute("SELECT * FROM interviews WHERE status = 'confirmed' AND reminded_at IS NULL").fetchall():
                start = parse_slot(row["chosen"])
                app = _application(row["application_id"])
                if start is None or app is None or app["stage"] not in ACTIVE or start <= now or start.date() > now.date() + timedelta(days=1):
                    continue
                job = jobs._job(app["job_id"])
                day_word = "today" if start.date() == now.date() else "tomorrow"
                conn.execute("UPDATE interviews SET reminded_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
                _event(conn, app["id"], None, "interview reminder", f"A reminder was prepared ({day_word})")
                _queue(conn, app, f"Reminder: your interview for {job['title']} is {day_word}",
                       f"Hello {app['name']},\n\nThis is a reminder of your interview for the {job['title']} role {day_word}:\n\n{label(start)}\n{_details(row)}\n\n"
                       f"If you cannot come, please cancel it on your private tracking page.\n\nCognivexAI recruitment", "interview reminder")
                _notify(conn, [row["created_by"], job["hiring_manager_id"]], f"Interview {day_word}: {app['name']}, {_short(start)}, {job['title']}.")
                queued += 1
    finally:
        conn.close()
    return queued


# ---------- the calendar file ----------

def _escape(text):
    return str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line):
    """RFC 5545: lines are at most 75 octets, longer ones continue on the next line after a space."""
    out, current = [], ""
    for ch in line:
        if len((current + ch).encode("utf-8")) > (75 if not out else 74):
            out.append(current)
            current = ch
        else:
            current += ch
    out.append(current)
    return "\r\n ".join(out)


def _stamp(moment):
    """A Paris wall time as UTC ('20261124T130000Z'), or as a floating local time when this machine has no time zone data."""
    try:
        from zoneinfo import ZoneInfo
        return moment.replace(tzinfo=ZoneInfo(ZONE)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    except Exception:
        return moment.strftime("%Y%m%dT%H%M%S")


def calendar_file(interview, job_title, stamp=None):
    """The .ics text for a confirmed interview. It carries the job, the time, the place and the note: no name, no email."""
    start = parse_slot(interview["chosen"]["value"])
    end = start + timedelta(minutes=interview["duration_min"])
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//CognivexAI//Recruitment//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "BEGIN:VEVENT",
             f"UID:interview-{interview['id']}@cognivexai", f"DTSTAMP:{stamp or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
             f"DTSTART:{_stamp(start)}", f"DTEND:{_stamp(end)}", f"SUMMARY:{_escape('Interview: ' + job_title + ' at CognivexAI')}"]
    if interview["place"]:
        lines.append(f"LOCATION:{_escape(interview['place'])}")
    if interview["note"]:
        lines.append(f"DESCRIPTION:{_escape(interview['note'])}")
    lines += ["STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def calendar_for_candidate(token):
    """{"text", "filename"} for the candidate's confirmed interview, or {"error"}."""
    app = careers._by_token(token)
    if app is None:
        return {"error": careers.NOT_FOUND}
    view = for_candidate(app)
    if view["status"] != "confirmed":
        return {"error": "There is no confirmed interview to add to a calendar."}
    return {"text": calendar_file(view, jobs._job(app["job_id"])["title"]), "filename": "interview.ics"}


def calendar_for_staff(actor_id, application_id):
    app, job = _staff(actor_id, application_id)
    if app is None:
        return {"error": NOT_FOUND}
    row = _current(application_id)
    if row is None or row["status"] != "confirmed":
        return {"error": "There is no confirmed interview to add to a calendar."}
    return {"text": calendar_file(_present(row), job["title"]), "filename": "interview.ics"}


# ---------- the candidate outbox (HR copies each message and sends it) ----------

def outbox(hr_id, limit=100):
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can see the outbox."}
    return _rows("""SELECT o.id, o.application_id, a.name AS candidate_name, o.to_email, o.subject, o.body, o.kind, o.created_at, o.sent_at, s.name AS sent_by_name
                    FROM candidate_outbox o LEFT JOIN applications a ON a.id = o.application_id LEFT JOIN employees s ON s.id = o.sent_by
                    ORDER BY o.id DESC LIMIT ?""", (limit,))


def mark_sent(hr_id, message_id):
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can change the outbox."}
    rows = _rows("SELECT * FROM candidate_outbox WHERE id = ?", (message_id,))
    if not rows:
        return {"error": "No such message."}
    if rows[0]["sent_at"]:
        return {"error": "This message is already marked as sent."}
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("UPDATE candidate_outbox SET sent_at = CURRENT_TIMESTAMP, sent_by = ? WHERE id = ?", (hr_id, message_id))
    finally:
        conn.close()
    return {"success": True, "id": message_id}


def waiting_messages():
    """How many candidate emails wait for HR to send them."""
    return _rows("SELECT COUNT(*) AS n FROM candidate_outbox WHERE sent_at IS NULL")[0]["n"]
