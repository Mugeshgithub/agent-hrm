# careers.py: recruitment, step 2: the public careers page and applying to a job. Anyone can read an open job and apply, no login. The person
# gets a private tracking link (a random token, stored only as a hash, like the welcome link) to see their stage, remove themselves from the
# talent pool, or withdraw and erase everything. The app never emails: the link is shown once on the confirmation page, and HR can issue a
# new one.
#
# What this page promises is only what the app does: the privacy notice (notice()) is built from the code's real behaviour (retention.py for
# how long data is kept, AI_ASSISTS for what an AI does with an application), so it cannot say more or less than the app does.
# Personal data is kept in as few places as possible: the CV is a file in the private folder with a random name; erasing a candidate (they
# withdraw, or retention.py says so) blanks the name, contact details, cover letter and tracking link, deletes the CV file, and keeps only a
# record with no personal data (stage, dates, reasons, and a line in candidate_erasures).
# No AI and no third-party package, so it is unit-tested in CI.
import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import time
from datetime import date, datetime, timezone

import db
import documents
import jobs
import retention
from jobs import _rows

MAX_CV_BYTES = 5 * 1024 * 1024
NOTICE_VERSION = "2026-11c"
# the first notice that says what an AI does with an application: the candidate assistant only talks to people who accepted a notice at least this new
AI_NOTICE_VERSION = "2026-11b"
# the first notice that says an AI reads the CV and the cover letter: a summary (application_summary.py) is only made for people who accepted one at least this new
SUMMARY_NOTICE_VERSION = "2026-11c"
# what an AI does with an application: the candidate assistant is "questions", the summary for the hiring team is "summaries".
AI_ASSISTS = ("questions", "summaries")
NOT_OPEN = "This job is not open for applications."
NOT_FOUND = "This link isn't valid."
STAGE_TEXT = {
    "applied": ("Received", "We have received your application. A person at CognivexAI will read it."),
    "screening": ("Being reviewed", "Your application is being reviewed."),
    "interview": ("Interview stage", "You are at the interview stage. We will be in touch to arrange it."),
    "offer": ("Offer stage", "You are at the offer stage. We will be in touch."),
    "hired": ("Selected", "You have been selected for this role. HR will be in touch."),
    "not_selected": ("Not selected", "Your application was not selected for this role. Thank you for the time you gave it."),
}
# a PDF that carries a script, a launch action or an attachment is refused: a person opens it. A plain check, not a virus scan.
# A marker counts only as a whole PDF name: "/AA << ... >>" is an action, "/AAAAAA+Arial" is the tag every exporter puts on an embedded font.
ACTIVE_CONTENT = re.compile(rb"/(?:JavaScript|JS|Launch|OpenAction|EmbeddedFile|RichMedia|AA)(?=[\s\x00/<>\[\]()%{}]|$)")
STREAM_START = re.compile(rb">>\s*stream\r?\n")


# ---------- the privacy notice ----------

def notice(months=None):
    """The notice shown on every job page, from what the app really does. `months` is the talent-pool limit HR set."""
    months = months or jobs.retention_months()
    ai = (" ".join(
        {"questions": "On your tracking page, an AI assistant answers your questions about the job and about your application. It does not read your CV "
                      "or your cover letter. What you write to it is kept with your application and erased with it, and a note you ask it to pass on "
                      "is read by HR.",
         "summaries": "When HR or the hiring manager asks for it, an AI reads your CV and your cover letter and shows, for each requirement of the job, "
                      "the passages that relate to it, word for word, or says that it found none. It does not score, rank or recommend anyone, and it "
                      "leaves out details such as age, gender, nationality, family situation, health and address. The summary is kept with your "
                      "application and erased with it."}[a] for a in AI_ASSISTS)
          + " No AI ever ranks, scores or rejects anyone: people read every application and decide."
          if AI_ASSISTS else
          "No AI is used on your application today: people read it and decide. If an AI assistant is added, this notice will say what it does "
          "before it handles your application.")
    return {"version": NOTICE_VERSION, "sections": [
        {"title": "Who reads your application", "text": "HR and the hiring manager of the job. Nobody else at the company sees it."},
        {"title": "Artificial intelligence", "text": ai},
        {"title": "What we ask for, and what not to send",
         "text": "Your name, email, a CV in PDF format and, if you wish, a phone number and a cover letter. Please leave out your photo, age, marital "
                  "status, nationality and any other detail that has nothing to do with the job: we do not use them and a job decision may not "
                  "rest on them (Code du travail, L1132-1)."},
        {"title": "How long we keep it",
         "text": f"If you do not tick the talent-pool box, we erase your application 30 days after we mark it as not selected (or 90 days after the "
                 f"job closes, if it never got a decision). If you tick the box, we may contact you about other jobs and keep your application "
                 f"for up to {months} months after our last contact with you. The CNIL recommends keeping a talent pool for two years at the "
                 f"most from the last contact, and only for people who agreed. You can leave the talent pool, or withdraw and erase your "
                 f"application, at any time from your tracking page."},
        {"title": "Your rights",
         "text": "You can ask HR for a copy of your data, to correct it, or to erase it. Erasing it yourself takes one click on your tracking page. "
                 "If you think we handle your data wrongly you can complain to the CNIL (cnil.fr)."}]}


# ---------- reading a job ----------

def public_job(row):
    # only what a candidate may see: never the hiring manager, the creator or the candidates
    return {"id": row["id"], "title": row["title"], "team": row["team"], "location": row["location"], "contract_type": row["contract_type"],
            "description": row["description"], "must_have": json.loads(row["must_have"]), "nice_to_have": json.loads(row["nice_to_have"]),
            "salary_min": row["salary_min"], "salary_max": row["salary_max"]}


def open_jobs():
    return [public_job(r) for r in _rows("SELECT * FROM jobs WHERE status = 'open' ORDER BY opened_at DESC, id DESC")]


def open_job(job_id):
    rows = _rows("SELECT * FROM jobs WHERE id = ? AND status = 'open'", (job_id,))
    return public_job(rows[0]) if rows else {"error": NOT_OPEN}


# ---------- applying ----------

def _outside_streams(data):
    """The PDF without the bodies of its streams: compressed data (fonts, photos) can hold the bytes of any marker by chance.
    A loop with find(), not one big regex, so a crafted file cannot make it quadratic."""
    kept, pos = [], 0
    while True:
        start = STREAM_START.search(data, pos)
        end = data.find(b"endstream", start.end()) if start else -1
        if end < 0:                                         # no more streams (or one never closes: keep the rest and check it)
            kept.append(data[pos:])
            return b"".join(kept)
        kept.append(data[pos:start.end()])
        pos = end


def check_cv(data):
    """None for a CV we accept, else the reason in words the candidate can act on."""
    if not data:
        return "Attach your CV as a PDF file."
    if len(data) > MAX_CV_BYTES:
        return f"That file is too big ({len(data) / 1024 / 1024:.1f} MB). The limit is {MAX_CV_BYTES // 1024 // 1024} MB."
    if documents.sniff_type(data) != "pdf":
        return "Your CV must be a PDF file."
    if b"/Encrypt" in data:
        return "That PDF is password-protected, so nobody could read it. Send it without a password."
    if ACTIVE_CONTENT.search(_outside_streams(data)):
        return "That PDF contains a script or an attachment, which we can't accept. Export your CV again as a plain PDF."
    return None


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _text(data, key, low, high, label):
    value = str(data.get(key) or "").strip()
    return value if low <= len(value) <= high else {"error": label}


def apply(job_id, data, cv, now=None):
    """Create the application. `data`: name, email, phone, cover_letter, consent (must be true), talent_pool. `cv`: the PDF bytes.
    Returns {"token": ...} (shown once: it is the tracking link) or {"error": ...}."""
    rows = _rows("SELECT * FROM jobs WHERE id = ? AND status = 'open'", (job_id,))
    if not rows:
        return {"error": NOT_OPEN}
    name = _text(data, "name", 2, 120, "Give your name.")
    email = _text(data, "email", 3, 200, "Give a valid email address.")
    phone = _text(data, "phone", 0, 40, "That phone number is too long.")
    letter = _text(data, "cover_letter", 0, 3000, "The cover letter is too long (at most 3000 characters).")
    for value in (name, email, phone, letter):
        if isinstance(value, dict):
            return value
    if not jobs._valid_email(email.lower()):
        return {"error": "Give a valid email address."}
    if data.get("consent") is not True:
        return {"error": "Tick the box to confirm you have read the privacy notice and agree that we handle your application."}
    problem = check_cv(cv)
    if problem:
        return {"error": problem}
    token = secrets.token_urlsafe(32)
    file_name = f"cv_{secrets.token_hex(16)}.pdf"
    folder = documents.files_dir()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, file_name)
    with open(path, "wb") as f:
        f.write(cv)
    stamp = (now or datetime.now(timezone.utc).replace(tzinfo=None)).strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_conn()
    try:
        with conn:
            cur = conn.execute(
                """INSERT INTO applications (job_id, name, email, phone, source, applied_at, stage_changed_at, last_contact_at, cover_letter, cv_file,
                                             track_hash, consent_at, notice_version, talent_pool)
                   VALUES (?, ?, ?, ?, 'careers page', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, name, email.lower(), phone, stamp, stamp, stamp, letter, file_name, _hash(token), stamp, NOTICE_VERSION,
                 1 if data.get("talent_pool") is True else 0))
            conn.execute("INSERT INTO application_events (application_id, actor_id, event, to_stage, reason) VALUES (?, NULL, 'added', 'applied', ?)",
                         (cur.lastrowid, "Applied on the careers page" + (" (talent pool: yes)" if data.get("talent_pool") is True else "")))
    except Exception:
        os.remove(path)                                             # no application, so no file left behind
        raise
    finally:
        conn.close()
    return {"token": token}


class Throttle:
    """At most `limit` tries per `seconds` for a key (an address, an email). In memory: enough to stop a script, not a distributed attack."""
    def __init__(self, limit, seconds):
        self.limit, self.seconds, self.seen = limit, seconds, {}

    def allow(self, key, now=None):
        now = time.time() if now is None else now
        recent = [t for t in self.seen.get(key, []) if now - t < self.seconds]
        if len(recent) >= self.limit:
            self.seen[key] = recent
            return False
        self.seen[key] = recent + [now]
        return True


def decode_cv(text):
    """The CV from the JSON body (base64), or None when it is not valid base64."""
    try:
        return base64.b64decode(str(text or ""), validate=True)
    except (binascii.Error, ValueError):
        return None


# ---------- the tracking link ----------

def _by_token(token):
    rows = _rows("SELECT * FROM applications WHERE track_hash = ? AND erased_at IS NULL", (_hash(str(token)),)) if token else []
    return rows[0] if rows else None


def tracking(token):
    """What a candidate sees with their private link: the job, where they stand in plain words, and when their data is erased."""
    app = _by_token(token)
    if app is None:
        return {"error": NOT_FOUND}
    return tracking_for(app)


def tracking_for(app):
    job = _rows("SELECT * FROM jobs WHERE id = ?", (app["job_id"],))[0]
    label, message = STAGE_TEXT[app["stage"]]
    erase = jobs.erase_date(app, job, jobs.retention_months())
    summary = _rows("SELECT created_at FROM application_summaries WHERE application_id = ?", (app["id"],))        # the candidate may know that an AI read their CV, and when
    return {"name": app["name"], "job_title": job["title"], "applied_on": app["applied_at"][:10], "stage": app["stage"], "stage_label": label,
            "message": message, "talent_pool": bool(app["talent_pool"]), "erase_on": erase.isoformat() if erase else None,
            "can_withdraw": app["stage"] != "hired", "ai_summary_on": summary[0]["created_at"][:10] if summary else None}


def leave_talent_pool(token):
    app = _by_token(token)
    if app is None:
        return {"error": NOT_FOUND}
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("UPDATE applications SET talent_pool = 0 WHERE id = ?", (app["id"],))
            conn.execute("INSERT INTO application_events (application_id, actor_id, event, reason) VALUES (?, NULL, 'talent pool left', "
                         "'The candidate left the talent pool')", (app["id"],))
    finally:
        conn.close()
    return tracking(token)


def erase(conn, app, reason, by=None):
    """Erase one candidate's personal data inside the caller's transaction. Returns the CV file name to delete once it is saved."""
    conn.execute("UPDATE applications SET name = '(erased)', email = '', phone = '', cover_letter = '', cv_file = NULL, track_hash = NULL, "
                 "erased_at = CURRENT_TIMESTAMP WHERE id = ?", (app["id"],))
    conn.execute("DELETE FROM candidate_messages WHERE application_id = ?", (app["id"],))        # what they wrote to the assistant is personal data too
    conn.execute("DELETE FROM candidate_notes WHERE application_id = ?", (app["id"],))
    conn.execute("DELETE FROM application_summaries WHERE application_id = ?", (app["id"],))            # quotes from the CV are personal data too
    conn.execute("DELETE FROM interviews WHERE application_id = ?", (app["id"],))                       # the interview and the emails about it (their address, their name)
    conn.execute("DELETE FROM candidate_outbox WHERE application_id = ?", (app["id"],))
    conn.execute("INSERT INTO candidate_erasures (application_id, job_id, stage, reason, erased_by) VALUES (?, ?, ?, ?, ?)",
                 (app["id"], app["job_id"], app["stage"], reason, by))
    conn.execute("INSERT INTO application_events (application_id, actor_id, event, reason) VALUES (?, ?, 'erased', ?)", (app["id"], by, reason))
    return app.get("cv_file")


def _remove_file(name):
    if name:
        try:
            os.remove(os.path.join(documents.files_dir(), name))
        except FileNotFoundError:
            pass


def withdraw(token):
    """The candidate withdraws and erases everything, from their own link. Not for someone who is hired: they are an employee now."""
    app = _by_token(token)
    if app is None:
        return {"error": NOT_FOUND}
    if app["stage"] == "hired":
        return {"error": "You have been selected for this role, so please contact HR about your data."}
    conn = db.get_conn()
    try:
        with conn:
            name = erase(conn, app, "Withdrawn and erased by the candidate")
    finally:
        conn.close()
    _remove_file(name)
    return {"erased": True}


# ---------- what HR and the hiring manager can do ----------

def new_tracking_token(actor_id, application_id):
    """HR gives a candidate a new tracking link (the old one stops working). Returns the token once."""
    rows = _rows("SELECT * FROM applications WHERE id = ? AND erased_at IS NULL", (application_id,))
    job = _rows("SELECT * FROM jobs WHERE id = ?", (rows[0]["job_id"],))[0] if rows else None
    actor = jobs._actor(actor_id)
    if not jobs.can_see(actor, job):
        return {"error": "No such candidate."}
    if actor["role"] != "hr":
        return {"error": "Only HR gives a candidate a new link."}
    token = secrets.token_urlsafe(32)
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("UPDATE applications SET track_hash = ? WHERE id = ?", (_hash(token), application_id))
            conn.execute("INSERT INTO application_events (application_id, actor_id, event, reason) VALUES (?, ?, 'new link', 'A new tracking link was issued')",
                         (application_id, actor_id))
    finally:
        conn.close()
    return {"token": token}


def open_cv(actor_id, application_id):
    """The CV file for HR or the job's hiring manager; each opening is logged."""
    rows = _rows("SELECT * FROM applications WHERE id = ? AND erased_at IS NULL", (application_id,))
    job = _rows("SELECT * FROM jobs WHERE id = ?", (rows[0]["job_id"],))[0] if rows else None
    if not jobs.can_see(jobs._actor(actor_id), job):
        return {"error": "No such candidate."}
    if not rows[0]["cv_file"]:
        return {"error": "This candidate has no CV."}
    path = os.path.join(documents.files_dir(), rows[0]["cv_file"])
    if not os.path.exists(path):
        return {"error": "The CV file is missing."}
    conn = db.get_conn()
    with conn:
        conn.execute("INSERT INTO application_events (application_id, actor_id, event, reason) VALUES (?, ?, 'cv opened', 'Opened the CV')",
                     (application_id, actor_id))
    conn.close()
    return {"path": path, "filename": f"cv-{application_id}.pdf"}


# ---------- retention ----------

def set_retention(hr_id, months):
    actor = jobs._actor(hr_id)
    if actor is None or actor["role"] != "hr":
        return {"error": "Only HR can change how long candidate data is kept."}
    value = retention.valid_months(months)
    if value is None:
        return {"error": f"Give a number of months from 1 to {retention.MAX_MONTHS}: the CNIL recommends keeping a talent pool two years at the most from the last contact."}
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("""INSERT INTO recruitment_settings (key, value, updated_by) VALUES ('retention_months', ?, ?)
                            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, updated_at = CURRENT_TIMESTAMP""",
                         (str(value), hr_id))
    finally:
        conn.close()
    return settings(hr_id)


def settings(hr_id):
    actor = jobs._actor(hr_id)
    if actor is None or actor["role"] != "hr":
        return {"error": "Only HR can see this."}
    return {"retention_months": jobs.retention_months(), "max_months": retention.MAX_MONTHS, "default_months": retention.DEFAULT_MONTHS,
            "not_selected_days": retention.NOT_SELECTED_DAYS, "closed_job_days": retention.CLOSED_JOB_DAYS, "cite": retention.CNIL_CITE,
            "url": retention.CNIL_URL,
            "erased_so_far": _rows("SELECT COUNT(*) AS n FROM candidate_erasures")[0]["n"]}


def run_retention(today=None, ran_by=None):
    """Erase every candidate whose day has come (retention.py). Safe to run every day. Returns how many, by rule, and nothing personal."""
    today = today or date.today()
    months = jobs.retention_months()
    done, files = {}, []
    conn = db.get_conn()
    try:
        with conn:
            for app in _rows("SELECT * FROM applications WHERE erased_at IS NULL AND stage != 'hired'"):
                job = _rows("SELECT * FROM jobs WHERE id = ?", (app["job_id"],))[0]
                due, rule = jobs.erase_plan(app, job, months)
                if due is None or due > today:
                    continue
                files.append(erase(conn, app, f"Retention ({rule}): erased on {today.isoformat()}", ran_by))
                done[rule] = done.get(rule, 0) + 1
    finally:
        conn.close()
    for name in files:
        _remove_file(name)
    return {"erased": sum(done.values()), "by_rule": done, "on": today.isoformat()}
