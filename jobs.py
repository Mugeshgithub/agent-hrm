# jobs.py: recruitment, step 1: the job posts and the pipeline of the people who applied to them.
#
# Recruitment is high-risk under the EU AI Act, and French law forbids discrimination in hiring (Code du travail L1132-1; L5321-2: no job
# offer may refer to one of those characteristics). So the app informs and a person decides:
#   - only a person moves a candidate from one stage to the next, and every move is logged with who and when;
#   - "Not selected" always has an internal reason, and nothing is ever rejected by the app (closing a job rejects nobody);
#   - candidates are listed in the order they applied, never by any score: there is no score, rank or recommendation anywhere;
#   - HR sees every job, a hiring manager only their own, everyone else nothing about candidates.
# The job text is checked against the characteristics of L1132-1 before it is opened (published).
# No AI and no third-party package, so it is unit-tested in CI.
import json
import re
import sqlite3
from datetime import date

import db
import employees
import retention
from tools import get_employee

STAGES = ("applied", "screening", "interview", "offer", "hired", "not_selected")
LABELS = {"applied": "Applied", "screening": "Screening", "interview": "Interview", "offer": "Offer", "hired": "Hired",
          "not_selected": "Not selected"}
ACTIVE = ("applied", "screening", "interview", "offer")
STATUSES = ("draft", "open", "closed")
CONTRACT_TYPES = ("CDI", "CDD", "Internship", "Apprenticeship")
MAX_REQUIREMENTS = 15
NOT_FOUND = "No such job."                       # the same answer for a job that does not exist and one you may not see

# what the job text may not refer to: L5321-2 (no job offer may refer to a characteristic listed in L1132-1). A help, not a clearance: it
# catches the plain cases and leaves ordinary text alone ("single sign-on", "race conditions", "a young company", "H/F" are fine), and a
# person still reads the text. (pattern, what to say)
PROTECTED = [
    (r"\b(?:young (?:and (?:dynamic|energetic) )?(?:people|person|candidates?|professionals?|graduates?|team members?)|young and (?:dynamic|energetic)|"
     r"jeunes? (?:et )?dynamiques?|jeunes? (?:candidat|diplômé)\w*|digital natives?|youthful|"
     r"(?:under|below|younger than|older than|moins de|plus de)\s+[2-6]\d\s*(?:years?(?:\s+old)?|ans)\b)",
     "age (ask for experience, not for an age)"),
    (r"\b(?:an? (?:man|woman)|un homme|une femme|men only|women only|(?:male|female) (?:candidates?|applicants?)|"
     r"(?:homme|femme)s? uniquement|candidat(?:e)? (?:masculin|féminin))\b", "sex"),
    (r"\b(?:married|unmarried|célibataire|marié\w*|no children|without children|childless|pregnan\w*|enceinte|sans enfants?|marital status|situation familiale)\b",
     "family situation or pregnancy"),
    (r"\b(?:french (?:national|nationality|citizen\w*)|nationalité française|racial|ethnic\w*|origine ethnique|of \w+ origin|d'origine \w+)\b",
     "origin or nationality (ask whether the person has the right to work in France, not their nationality)"),
    (r"\b(?:native (?:english|french|\w+) speakers?|mother[- ]tongue|langue maternelle|locuteurs? natifs?)\b",
     "the language a person speaks from birth (ask for a level, such as fluent or C1)"),
    (r"\b(?:good health|bonne santé|physically fit|healthy (?:candidate|person|individual)s?|(?:no|sans) (?:disabilit|handicap)\w*)\b", "health or disability"),
    (r"\b(?:good[- ]looking|attractive appearance|well[- ]presented|bonne présentation|physique agréable)\b", "physical appearance"),
    (r"\b(?:christian|muslim|jewish|catholic|atheist|religious (?:beliefs?|people)|syndiqué|union member)\b", "religion, political opinions or union membership"),
    (r"\b(?:must (?:live|reside) (?:in|within)|résider (?:à|en)|habiter (?:à|en))\b", "place of residence (say where the job is based, not where the person lives)"),
]
# a sentence that says the company does NOT discriminate lists the very same words: it is skipped
EQUALITY = re.compile(r"regardless|irrespective|without (?:regard|discrimination)|sans (?:distinction|discrimination)|quel que soit|equal opportunit|"
                      r"égalité|non[- ]discrimination|inclusive|h/f|f/h|toutes? et tous?", re.I)


def init_jobs():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                team TEXT NOT NULL,
                location TEXT NOT NULL,
                contract_type TEXT NOT NULL,
                description TEXT NOT NULL,
                must_have TEXT NOT NULL DEFAULT '[]',
                nice_to_have TEXT NOT NULL DEFAULT '[]',
                salary_min INTEGER,
                salary_max INTEGER,
                hiring_manager_id TEXT NOT NULL REFERENCES employees(id),
                status TEXT NOT NULL DEFAULT 'draft',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                opened_at TEXT,
                closed_at TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                actor_id TEXT NOT NULL,
                event TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'added by HR',
                stage TEXT NOT NULL DEFAULT 'applied',
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                stage_changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
        # what the careers page adds (careers.py): the candidate's own details, the consent, the private tracking link and the CV on disk
        for column, definition in (("cover_letter", "TEXT NOT NULL DEFAULT ''"), ("cv_file", "TEXT"), ("track_hash", "TEXT"), ("consent_at", "TEXT"),
                                   ("notice_version", "TEXT"), ("talent_pool", "INTEGER NOT NULL DEFAULT 0"), ("last_contact_at", "TEXT"),
                                   ("erased_at", "TEXT")):
            db.add_column_if_missing(conn, "applications", column, definition)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS applications_track ON applications(track_hash) WHERE track_hash IS NOT NULL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candidate_erasures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL,
                job_id INTEGER NOT NULL,
                stage TEXT NOT NULL,
                reason TEXT NOT NULL,
                erased_by TEXT,
                erased_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recruitment_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_by TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
        # the candidate assistant (candidate_assistant.py): what a candidate and the assistant said, and the notes they asked it to pass to HR.
        # Both belong to the application: erasing the candidate deletes them (careers.erase).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candidate_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL REFERENCES applications(id),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candidate_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL REFERENCES applications(id),
                text TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                handled_by TEXT,
                handled_at TEXT
            )""")
        # the summary of an application against the job's requirements (application_summary.py): one per application, quotes only, erased with the application
        conn.execute("""
            CREATE TABLE IF NOT EXISTS application_summaries (
                application_id INTEGER PRIMARY KEY REFERENCES applications(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT,
                requirements_hash TEXT NOT NULL,
                result TEXT NOT NULL
            )""")
        # interviews (interviews.py): the times offered, the one the candidate chose, and the emails for the candidate that wait for HR to send them.
        # Both belong to the application: erasing the candidate deletes them (careers.erase). Times are Paris wall time.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS interviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL REFERENCES applications(id),
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL,
                slots TEXT NOT NULL,
                chosen TEXT,
                duration_min INTEGER NOT NULL,
                place TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                cancelled_by TEXT,
                cancelled_at TEXT,
                reminded_at TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candidate_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL REFERENCES applications(id),
                to_email TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT,
                sent_by TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS application_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL REFERENCES applications(id),
                actor_id TEXT,
                event TEXT NOT NULL,
                from_stage TEXT,
                to_stage TEXT,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
    conn.close()


# ---------- who may do what ----------

def _rows(sql, params=()):
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def _actor(actor_id):
    emp = get_employee(actor_id)
    if emp is None or not employees.is_active(actor_id):
        return None
    return emp


def _job(job_id):
    rows = _rows("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return rows[0] if rows else None


def can_see(actor, job):
    # HR sees every job; a hiring manager sees their own; nobody else sees anything
    return bool(actor and job and (actor["role"] == "hr" or (actor["role"] == "manager" and job["hiring_manager_id"] == actor["id"])))


def _log_job(conn, job_id, actor_id, event, detail=""):
    conn.execute("INSERT INTO job_events (job_id, actor_id, event, detail) VALUES (?, ?, ?, ?)", (job_id, actor_id, event, detail))


# ---------- the job text ----------

def text_problems(job):
    """What in the job text refers to a characteristic that L1132-1 lists: [{"words": "young people", "about": "age ..."}]. Empty when it is clean."""
    parts = [job.get("title"), job.get("description"), *(job.get("must_have") or []), *(job.get("nice_to_have") or [])]
    sentences = [x for p in parts if p for x in re.split(r"(?<=[.!?])\s+|\n+", str(p)) if x.strip()]
    text = "\n".join(x for x in sentences if not EQUALITY.search(x))
    found = []
    for pattern, about in PROTECTED:
        for match in re.finditer(pattern, text, re.I):
            found.append({"words": match.group(0), "about": about})
    return found


def _problem_text(found):
    return ("The text refers to " + "; ".join(f"\"{f['words']}\" ({f['about']})" for f in found[:4])
            + ". No job offer may refer to a characteristic listed in the Code du travail L1132-1 (L5321-2): please say what the job needs instead.")


def _lines(value):
    # requirements come as a list, or as text with one requirement per line
    items = value.splitlines() if isinstance(value, str) else (value or [])
    return [re.sub(r"^[-*•\s]+", "", str(i)).strip() for i in items if str(i).strip()]


def _clean(data, base=None):
    """The fields of a job from what the form sent, or {"error": ...}."""
    base = base or {}
    out = {}
    for key, label, limit in (("title", "title", 120), ("team", "team", 80), ("location", "location", 80), ("description", "description", 4000)):
        value = str(data.get(key, base.get(key, "")) or "").strip()
        if not value:
            return {"error": f"A job needs a {label}."}
        if len(value) > limit:
            return {"error": f"The {label} is too long (at most {limit} characters)."}
        out[key] = value
    if len(out["title"]) < 3:
        return {"error": "The title is too short."}
    out["contract_type"] = data.get("contract_type", base.get("contract_type"))
    if out["contract_type"] not in CONTRACT_TYPES:
        return {"error": f"Choose a contract type: {', '.join(CONTRACT_TYPES)}."}
    for key, label in (("must_have", "must-have"), ("nice_to_have", "nice-to-have")):
        items = _lines(data.get(key, base.get(key, [])))
        if len(items) > MAX_REQUIREMENTS or any(len(i) > 200 for i in items):
            return {"error": f"At most {MAX_REQUIREMENTS} {label} requirements, each up to 200 characters."}
        out[key] = items
    if not out["must_have"]:
        return {"error": "Give at least one must-have requirement: an application is read against the requirements, one by one."}
    low, high = data.get("salary_min", base.get("salary_min")), data.get("salary_max", base.get("salary_max"))
    low, high = (None if v in (None, "") else v for v in (low, high))
    if (low is None) != (high is None):
        return {"error": "Give both ends of the salary range, or neither."}
    if low is not None:
        try:
            low, high = int(float(low)), int(float(high))
        except (TypeError, ValueError):
            return {"error": "The salary range must be numbers (gross euros a year)."}
        if low < 0 or high < low:
            return {"error": "The salary range is not valid: the top must be at least the bottom."}
    out["salary_min"], out["salary_max"] = low, high
    return out


def _manager_choice(actor, data, base=None):
    # a manager is always their own job's hiring manager; HR chooses a manager or HR person who still works here
    if actor["role"] != "hr":
        return actor["id"]
    chosen = data.get("hiring_manager_id") or (base or {}).get("hiring_manager_id")
    person = get_employee(chosen) if chosen else None
    if person is None or person["role"] not in ("manager", "hr") or not employees.is_active(chosen):
        return {"error": "Choose the hiring manager: a manager or an HR person who works here."}
    return chosen


# ---------- jobs ----------

def _present(job, actor, counts=None):
    people = {p["id"]: p["name"] for p in _rows("SELECT id, name FROM employees")}
    out = {**job, "must_have": json.loads(job["must_have"]), "nice_to_have": json.loads(job["nice_to_have"]),
           "hiring_manager_name": people.get(job["hiring_manager_id"]), "created_by_name": people.get(job["created_by"]),
           "can_open": actor["role"] == "hr", "can_edit": job["status"] != "closed" or actor["role"] == "hr",
           "warnings": text_problems({**job, "must_have": json.loads(job["must_have"]), "nice_to_have": json.loads(job["nice_to_have"])})}
    if counts is not None:
        out["counts"] = counts
        out["new"] = counts.get("applied", 0)
    return out


def _counts(job_id):
    counts = {s: 0 for s in STAGES}
    for r in _rows("SELECT stage, COUNT(*) AS n FROM applications WHERE job_id = ? AND erased_at IS NULL GROUP BY stage", (job_id,)):
        counts[r["stage"]] = r["n"]
    return counts


def create_job(actor_id, data):
    actor = _actor(actor_id)
    if actor is None or actor["role"] not in ("hr", "manager"):
        return {"error": "Only HR and managers can create a job."}
    fields = _clean(data)
    if "error" in fields:
        return fields
    manager = _manager_choice(actor, data)
    if isinstance(manager, dict):
        return manager
    conn = db.get_conn()
    try:
        with conn:
            cur = conn.execute(
                """INSERT INTO jobs (title, team, location, contract_type, description, must_have, nice_to_have, salary_min, salary_max,
                                     hiring_manager_id, status, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)""",
                (fields["title"], fields["team"], fields["location"], fields["contract_type"], fields["description"],
                 json.dumps(fields["must_have"]), json.dumps(fields["nice_to_have"]), fields["salary_min"], fields["salary_max"], manager, actor_id))
            _log_job(conn, cur.lastrowid, actor_id, "created", fields["title"])
    finally:
        conn.close()
    return _present(_job(cur.lastrowid), actor, _counts(cur.lastrowid))


def update_job(actor_id, job_id, data):
    actor, job = _actor(actor_id), _job(job_id)
    if not can_see(actor, job):
        return {"error": NOT_FOUND}
    if job["status"] == "closed" and actor["role"] != "hr":
        return {"error": "This job is closed. Ask HR to reopen it to change it."}
    current = {**job, "must_have": json.loads(job["must_have"]), "nice_to_have": json.loads(job["nice_to_have"])}
    fields = _clean(data, current)
    if "error" in fields:
        return fields
    manager = job["hiring_manager_id"]
    if actor["role"] == "hr" and data.get("hiring_manager_id") and data["hiring_manager_id"] != manager:
        manager = _manager_choice(actor, data, current)
        if isinstance(manager, dict):
            return manager
    changed = [k for k in ("title", "team", "location", "contract_type", "description", "salary_min", "salary_max") if fields[k] != job[k]]
    changed += [k for k in ("must_have", "nice_to_have") if json.dumps(fields[k]) != job[k]]
    if manager != job["hiring_manager_id"]:
        changed.append("hiring manager")
    conn = db.get_conn()
    try:
        with conn:
            conn.execute(
                """UPDATE jobs SET title = ?, team = ?, location = ?, contract_type = ?, description = ?, must_have = ?, nice_to_have = ?,
                          salary_min = ?, salary_max = ?, hiring_manager_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (fields["title"], fields["team"], fields["location"], fields["contract_type"], fields["description"],
                 json.dumps(fields["must_have"]), json.dumps(fields["nice_to_have"]), fields["salary_min"], fields["salary_max"], manager, job_id))
            if changed:
                _log_job(conn, job_id, actor_id, "edited", ", ".join(changed))
    finally:
        conn.close()
    return _present(_job(job_id), actor, _counts(job_id))


def set_status(actor_id, job_id, status):
    """draft -> open (HR only, and the text must be clean), open -> closed (HR or the hiring manager), closed -> open (HR only).
    Closing a job rejects nobody: its candidates stay where they are until a person decides."""
    actor, job = _actor(actor_id), _job(job_id)
    if not can_see(actor, job):
        return {"error": NOT_FOUND}
    if status not in STATUSES or status == "draft":
        return {"error": "A job can be opened or closed."}
    if status == job["status"]:
        return {"error": f"This job is already {status}."}
    if status == "open":
        if actor["role"] != "hr":
            return {"error": "Only HR opens a job: opening makes it public."}
        found = text_problems({**job, "must_have": json.loads(job["must_have"]), "nice_to_have": json.loads(job["nice_to_have"])})
        if found:
            return {"error": _problem_text(found)}
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP, opened_at = CASE WHEN ? = 'open' THEN CURRENT_TIMESTAMP ELSE opened_at END, "
                         "closed_at = CASE WHEN ? = 'closed' THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id = ?", (status, status, status, job_id))
            _log_job(conn, job_id, actor_id, status)
    finally:
        conn.close()
    result = _present(_job(job_id), actor, _counts(job_id))
    result["still_waiting"] = sum(result["counts"][s] for s in ACTIVE) if status == "closed" else 0
    return result


def list_jobs(actor_id):
    actor = _actor(actor_id)
    if actor is None or actor["role"] not in ("hr", "manager"):
        return {"error": "Recruitment is for HR and hiring managers."}
    where, params = ("", ()) if actor["role"] == "hr" else ("WHERE hiring_manager_id = ?", (actor_id,))
    jobs = _rows(f"SELECT * FROM jobs {where} ORDER BY (status = 'closed'), (status = 'draft'), id DESC", params)
    return [_present(j, actor, _counts(j["id"])) for j in jobs]


def hiring_managers(actor_id):
    # who HR can pick as a job's hiring manager
    actor = _actor(actor_id)
    if actor is None or actor["role"] != "hr":
        return {"error": "Only HR picks the hiring manager."}
    return [{"id": r["id"], "name": r["name"], "role": r["role"]} for r in
            _rows("SELECT id, name, role FROM employees WHERE role IN ('manager', 'hr') ORDER BY name") if employees.is_active(r["id"])]


# ---------- candidates ----------

def retention_months():
    """How many months a talent-pool candidate's data is kept after the last contact (HR's setting, 24 at the most: see retention.py)."""
    rows = _rows("SELECT value FROM recruitment_settings WHERE key = 'retention_months'")
    return retention.valid_months(rows[0]["value"]) if rows and retention.valid_months(rows[0]["value"]) else retention.DEFAULT_MONTHS


def _day(sql):
    return date.fromisoformat(str(sql)[:10]) if sql else None


def erase_plan(row, job, months):
    """(the day this candidate's personal data is erased, the rule that says so) from retention.py; (None, None) for a hired candidate."""
    last = max(_day(row["applied_at"]), _day(row.get("last_contact_at")) or _day(row["applied_at"]))
    return retention.erase_plan(row["stage"], bool(row.get("talent_pool")), last, _day(row["stage_changed_at"]) if row["stage"] == "not_selected" else None,
                                _day(job["closed_at"]) if job and job["status"] == "closed" else None, months)


def erase_date(row, job, months):
    return erase_plan(row, job, months)[0]


def _candidate(row, with_contact, job=None, months=None):
    days = (date.today() - date.fromisoformat(row["stage_changed_at"][:10])).days
    out = {"id": row["id"], "name": row["name"], "stage": row["stage"], "applied_at": row["applied_at"],
           "stage_changed_at": row["stage_changed_at"], "days_in_stage": days, "source": row["source"]}
    if with_contact:
        erase = erase_date(row, job, months or retention.DEFAULT_MONTHS) if job else None
        out.update(email=row["email"], phone=row["phone"], cover_letter=row.get("cover_letter") or "", has_cv=bool(row.get("cv_file")),
                   talent_pool=bool(row.get("talent_pool")), consented_on=(row.get("consent_at") or "")[:10] or None,
                   erase_on=erase.isoformat() if erase else None)
    return out


def pipeline(actor_id, job_id):
    """The job and its candidates by stage. Each stage lists them in the order they applied: nothing here ranks anyone."""
    actor, job = _actor(actor_id), _job(job_id)
    if not can_see(actor, job):
        return {"error": NOT_FOUND}
    rows = _rows("SELECT * FROM applications WHERE job_id = ? AND erased_at IS NULL ORDER BY applied_at, id", (job_id,))
    months = retention_months()
    notes = {r["application_id"]: r["n"] for r in _rows(
        "SELECT application_id, COUNT(*) AS n FROM candidate_notes WHERE handled_at IS NULL GROUP BY application_id")}       # notes from the candidate waiting for a person
    latest = {r["application_id"]: r for r in _rows("SELECT application_id, status, chosen FROM interviews WHERE id IN (SELECT MAX(id) FROM interviews GROUP BY application_id)")}
    return {"job": _present(job, actor, _counts(job_id)), "stages": [{"key": s, "label": LABELS[s]} for s in STAGES],
            "candidates": [{**_candidate(r, True, job, months), "notes_open": notes.get(r["id"], 0),
                            "interview": {"status": latest[r["id"]]["status"], "at": latest[r["id"]]["chosen"]} if r["id"] in latest else None} for r in rows],
            "events": _rows("""SELECT e.event, e.detail, e.created_at, p.name AS actor_name FROM job_events e
                               LEFT JOIN employees p ON p.id = e.actor_id WHERE e.job_id = ? ORDER BY e.id DESC LIMIT 30""", (job_id,))}


def _valid_email(email):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}", email))


def add_candidate(actor_id, job_id, data, source="added by HR"):
    """HR or the hiring manager adds someone who applied by another way (until people apply on the careers page). The job must be open."""
    actor, job = _actor(actor_id), _job(job_id)
    if not can_see(actor, job):
        return {"error": NOT_FOUND}
    if job["status"] != "open":
        return {"error": "Candidates can only be added to a job that is open."}
    name, email, phone = (str(data.get(k) or "").strip() for k in ("name", "email", "phone"))
    if len(name) < 2 or len(name) > 120:
        return {"error": "Give the candidate's name."}
    if not _valid_email(email.lower()):
        return {"error": "Give a valid email address."}
    if len(phone) > 40:
        return {"error": "The phone number is too long."}
    if _rows("SELECT id FROM applications WHERE job_id = ? AND erased_at IS NULL AND lower(email) = ?", (job_id, email.lower())):
        return {"error": "This person is already in this job's pipeline."}
    conn = db.get_conn()
    try:
        with conn:
            cur = conn.execute("INSERT INTO applications (job_id, name, email, phone, source) VALUES (?, ?, ?, ?, ?)", (job_id, name, email.lower(), phone, source))
            conn.execute("INSERT INTO application_events (application_id, actor_id, event, to_stage, reason) VALUES (?, ?, 'added', 'applied', ?)",
                         (cur.lastrowid, actor_id, source))
    finally:
        conn.close()
    return _candidate(_rows("SELECT * FROM applications WHERE id = ?", (cur.lastrowid,))[0], True, job, retention_months())


def move(actor_id, application_id, to_stage, reason=""):
    """A person moves a candidate to another stage. "Not selected" needs an internal reason, and so does bringing someone back from it.
    Hired is final, and only comes after an offer. The app never moves anyone by itself."""
    rows = _rows("SELECT * FROM applications WHERE id = ?", (application_id,))
    job = _job(rows[0]["job_id"]) if rows else None
    actor = _actor(actor_id)
    if not can_see(actor, job):
        return {"error": "No such candidate."}
    app, reason = rows[0], str(reason or "").strip()
    if to_stage not in STAGES:
        return {"error": "Unknown stage."}
    if to_stage == app["stage"]:
        return {"error": f"This candidate is already in {LABELS[to_stage]}."}
    if app["stage"] == "hired":
        return {"error": "A candidate who is hired can't be moved."}
    if to_stage == "hired" and app["stage"] != "offer":
        return {"error": "A candidate is hired after an offer: move them to Offer first."}
    if to_stage == "not_selected" and not reason:
        return {"error": "Give the reason this candidate is not selected. It stays internal: the candidate does not see it."}
    if app["stage"] == "not_selected" and not reason:
        return {"error": "Give the reason for bringing this candidate back."}
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("UPDATE applications SET stage = ?, stage_changed_at = CURRENT_TIMESTAMP, "
                         "last_contact_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE last_contact_at END WHERE id = ?",
                         (to_stage, to_stage in retention.CONTACT_STAGES, application_id))
            conn.execute("INSERT INTO application_events (application_id, actor_id, event, from_stage, to_stage, reason) VALUES (?, ?, 'moved', ?, ?, ?)",
                         (application_id, actor_id, app["stage"], to_stage, reason))
    finally:
        conn.close()
    return _candidate(_rows("SELECT * FROM applications WHERE id = ?", (application_id,))[0], True, job, retention_months())


def history(actor_id, application_id):
    rows = _rows("SELECT * FROM applications WHERE id = ?", (application_id,))
    if not rows or not can_see(_actor(actor_id), _job(rows[0]["job_id"])):
        return {"error": "No such candidate."}
    return _rows("""SELECT e.event, e.from_stage, e.to_stage, e.reason, e.created_at, p.name AS actor_name FROM application_events e
                    LEFT JOIN employees p ON p.id = e.actor_id WHERE e.application_id = ? ORDER BY e.id""", (application_id,))


# ---------- what is waiting on a person, and what the assistant may read ----------

def new_applications(actor_id):
    """Candidates still in Applied, for the jobs this person may see: someone has to look at them and move them (the Needs you inbox)."""
    actor = _actor(actor_id)
    if actor is None or actor["role"] not in ("hr", "manager"):
        return []
    where, params = ("", ()) if actor["role"] == "hr" else ("AND j.hiring_manager_id = ?", (actor_id,))
    return _rows(f"""SELECT a.id AS application_id, a.name, a.applied_at, j.id AS job_id, j.title AS job_title FROM applications a
                     JOIN jobs j ON j.id = a.job_id WHERE a.stage = 'applied' AND a.erased_at IS NULL {where} ORDER BY a.applied_at, a.id""", params)


def candidate_names(actor_id):
    """The names of the candidates this person may see (HR: all, a hiring manager: their own jobs'), for recruitment_guard."""
    actor = _actor(actor_id)
    if actor is None or actor["role"] not in ("hr", "manager"):
        return []
    where, params = ("WHERE a.erased_at IS NULL", ()) if actor["role"] == "hr" else ("WHERE a.erased_at IS NULL AND j.hiring_manager_id = ?", (actor_id,))
    try:
        return [r["name"] for r in _rows(f"SELECT DISTINCT a.name FROM applications a JOIN jobs j ON j.id = a.job_id {where}", params)]
    except sqlite3.Error:
        return []                                  # a database without the recruitment tables yet: nobody to protect


def for_assistant(actor_id, job=None):
    """What the assistant reads out: the jobs this person may see with their counts, or one job's candidates with stage and days in stage.
    Names and stages only, in the order they applied. No contact details, no notes, and nothing that ranks anyone."""
    actor = _actor(actor_id)
    if actor is None or actor["role"] not in ("hr", "manager"):
        return {"error": "Recruitment is for HR and hiring managers."}
    mine = list_jobs(actor_id)
    wanted = str(job or "").strip().lower()
    note = "Candidates are listed in the order they applied. The app does not rank or score anyone; moving a candidate is a person's decision."
    if not wanted:
        return {"jobs": [{"job_id": j["id"], "title": j["title"], "status": j["status"], "hiring_manager": j["hiring_manager_name"],
                          "candidates_by_stage": {LABELS[s]: n for s, n in j["counts"].items() if n}} for j in mine], "note": note}
    found = [j for j in mine if wanted == str(j["id"]) or wanted in j["title"].lower()]
    if not found:
        return {"error": "No job of that name that you can see."}
    out = []
    for j in found[:3]:
        rows = _rows("SELECT * FROM applications WHERE job_id = ? AND erased_at IS NULL ORDER BY applied_at, id", (j["id"],))
        out.append({"job_id": j["id"], "title": j["title"], "status": j["status"], "hiring_manager": j["hiring_manager_name"],
                    "candidates": [{"name": c["name"], "stage": LABELS[c["stage"]], "applied_on": c["applied_at"][:10], "days_in_stage": c["days_in_stage"]}
                                   for c in (_candidate(r, False) for r in rows)]})
    return {"jobs": out, "note": note}
