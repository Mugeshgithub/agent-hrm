# documents.py: the documents a new hire sends (onboarding PR 3): an identity document, bank details, a social
# security number. The order is always the same:
#   1. CODE checks the file first: type (PDF, JPG or PNG, read from the file itself, not its name), size (10 MB),
#      and that the step is the signed-in person's own.
#   2. CLAUDE reads the document (names, number, expiry date) and says nothing else.
#   3. CODE checks what Claude read: the name matches, the identity document has not expired, the IBAN passes
#      the mod-97 check, the social security number's 2-digit key is right.
#   4. The person is shown what was read and confirms it. Anything unreadable or failing a check means
#      "please send it again", and the file is not kept.
# Files live outside the database and outside git (HRM_FILES_DIR, default ./private_files, which is ignored by
# git), under random names. Only the person and HR can open one, every opening is logged, and a cancelled hire's
# files are deleted. Claude is never asked whether a document is real: it can only point out something that
# looks off, and that goes to a human (HR) in the Needs you inbox.
# This module has no third-party import at load time (the Claude call imports its library when it runs), so the
# checks are unit-tested in CI without any key.
import base64
import hashlib
import json
import os
import re
import secrets
import unicodedata
from datetime import date

import db
import hr_queries
import onboarding
from tools import get_employee

MAX_BYTES = 10 * 1024 * 1024
# the checklist steps that are a document, and what each one is
DOCUMENT_STEPS = {
    "id": {"kind": "identity", "label": "identity document"},
    "iban": {"kind": "bank_details", "label": "bank details"},
    "social_security": {"kind": "social_security", "label": "social security number"},
}
ID_TYPES = ("passport", "id_card", "residence_permit")
FILE_TYPES = {"pdf": "application/pdf", "jpeg": "image/jpeg", "png": "image/png"}
EXTENSIONS = {"pdf": ".pdf", "jpeg": ".jpg", "png": ".png"}
# how long an IBAN is in the countries a new hire is likely to bank in; any other country is checked by mod 97 only
IBAN_LENGTHS = {"FR": 27, "DE": 22, "BE": 16, "ES": 24, "IT": 27, "NL": 18, "LU": 20, "PT": 25, "CH": 21, "GB": 22,
                "IE": 22, "AT": 20, "MC": 27}
READER = None        # tests (and nothing else) put a fake reader here; None means "ask Claude"


def files_dir():
    return os.environ.get("HRM_FILES_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "private_files")


def init_documents():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id TEXT NOT NULL REFERENCES employees(id),
                step_id INTEGER NOT NULL REFERENCES onboarding_steps(id),
                step_key TEXT NOT NULL,
                status TEXT NOT NULL,              -- read (waits for the person), confirmed, rejected, replaced, deleted
                original_name TEXT NOT NULL DEFAULT '',
                media_type TEXT,
                size_bytes INTEGER,
                sha256 TEXT,
                file_name TEXT,                    -- the random name on disk; NULL once the file is deleted
                fields_json TEXT,                  -- what was read; cleared when the file is deleted
                problems_json TEXT,
                flagged_reason TEXT,               -- something looked off: for a human to look at
                flag_reviewed_by TEXT,
                flag_reviewed_at TEXT,
                uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                confirmed_at TEXT,
                deleted_at TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id),
                viewer_id TEXT NOT NULL,
                viewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
    conn.close()


# ---------- 1. the file itself ----------

def sniff_type(data):
    # what the file really is, from its first bytes: a renamed .exe is not a PDF
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


def check_file(data):
    """Returns (type, None) for a file we accept, else (None, the reason)."""
    if not data:
        return None, "That file is empty."
    if len(data) > MAX_BYTES:
        return None, f"That file is too big ({len(data) / 1024 / 1024:.1f} MB). The limit is 10 MB."
    kind = sniff_type(data)
    if kind is None:
        return None, "I can only read PDF, JPG or PNG files."
    return kind, None


# ---------- 3. what Claude read, checked by code ----------

def _plain(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


def names_match(person_name, name_on_document):
    # every part of the person's name must be on the document, in any order ("MARTIN Lea Sophie" matches "Léa Martin")
    wanted, found = _plain(person_name), set(_plain(name_on_document))
    return bool(wanted) and all(part in found for part in wanted)


def check_iban(text):
    """Returns (clean IBAN, None) or (None, the problem)."""
    iban = re.sub(r"[\s-]", "", str(text or "")).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
        return None, "That doesn't look like an IBAN."
    expected = IBAN_LENGTHS.get(iban[:2])
    if expected and len(iban) != expected:
        return None, f"An IBAN starting with {iban[:2]} has {expected} characters; this one has {len(iban)}."
    rearranged = iban[4:] + iban[:4]
    if int("".join(str(int(c, 36)) for c in rearranged)) % 97 != 1:
        return None, "The IBAN's check digits don't add up, so a character was probably misread or mistyped."
    return iban, None


def check_nir(text):
    """French social security number: 13 digits and a 2-digit key. Returns (clean number, None) or (None, problem)."""
    nir = re.sub(r"[\s.-]", "", str(text or "")).upper()
    if re.fullmatch(r"[12378]\d{4}(?:\d{2}|2A|2B)\d{6}", nir):
        return None, "The number is missing its 2-digit key at the end (a French social security number has 15 characters)."
    if not re.fullmatch(r"[12378]\d{4}(?:\d{2}|2A|2B)\d{6}\d{2}", nir):
        return None, "That doesn't look like a French social security number (15 characters)."
    number, key = nir[:13].replace("2A", "19").replace("2B", "18"), int(nir[13:])
    if 97 - int(number) % 97 != key:
        return None, "The number's 2-digit key doesn't match, so a digit was probably misread or mistyped."
    return nir, None


def check_expiry(text, today):
    try:
        expires = date.fromisoformat(str(text))
    except ValueError:
        return "I couldn't read the expiry date."
    if expires <= today:
        return f"This document expired on {expires:%d %B %Y}."
    return None


def verify(kind, read, person_name, today=None):
    """What Claude read -> (problems, what to show the person). No problem at all means it can be confirmed."""
    today = today or date.today()
    problems, shown = [], []
    if not read.get("readable"):
        return ["I couldn't read the document clearly enough. A sharper photo or a PDF usually helps."], []
    holder = read.get("holder_name")
    if not holder:
        problems.append("I couldn't find a name on the document.")
    elif not names_match(person_name, holder):
        problems.append(f"The name on the document ({holder}) doesn't match your name ({person_name}).")
    if holder:
        shown.append(("Name on the document", holder))
    if kind == "identity":
        kind_read = read.get("document_type")
        if kind_read not in ID_TYPES:
            problems.append("This doesn't look like an identity document (a passport, ID card or residence permit).")
        else:
            shown.append(("Document", {"passport": "Passport", "id_card": "ID card", "residence_permit": "Residence permit"}[kind_read]))
        if (problem := check_expiry(read.get("expiry_date"), today)):
            problems.append(problem)
        else:
            shown.append(("Valid until", date.fromisoformat(read["expiry_date"]).strftime("%d %B %Y")))
    elif kind == "bank_details":
        iban, problem = check_iban(read.get("iban"))
        if problem:
            problems.append(problem)
        else:
            shown.append(("IBAN", " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))))
    elif kind == "social_security":
        nir, problem = check_nir(read.get("ssn"))
        if problem:
            problems.append(problem)
        else:
            shown.append(("Social security number", f"{nir[0]} {nir[1:3]} {nir[3:5]} {nir[5:7]} {nir[7:10]} {nir[10:13]} {nir[13:]}"))
    return problems, shown


# The assistant must never say a document is genuine. It is told so, but the model does not always answer the
# question it is asked, so code checks: a question about genuineness gets an answer that says "I can't tell".
GENUINE_ANSWER = ("I can't tell whether a document is genuine. The app only reads the details and checks them (the name, "
                  "the expiry date, the IBAN or the number), and a person at HR looks at the documents too.")
GENUINE_RETRY = ("The person asked whether a document is genuine, was checked or is valid. Your reply must start with "
                 "\"I can't tell whether a document is genuine.\" and then say what the app did. Say nothing else.")
_DOC_WORDS = r"(passport|document|identity|\bid\b|id card|iban|rib|bank details|social security|papers|file|upload)"


def asks_if_genuine(text):
    text = str(text or "")
    return bool(re.search(r"(?i)\b(genuine|authentic|authenticity|fake|forged|forgery|counterfeit)\b", text)
                or re.search(rf"(?i)\b(real|valid|verified|checked|legit|legitimate)\b.*{_DOC_WORDS}|{_DOC_WORDS}.*\b(real|valid|verified|checked|legit|legitimate)\b", text))


def says_cannot_tell(reply):
    return bool(re.search(r"(?i)\b(can(?:'|no)t|unable to|not able to)\s+(tell|verify|confirm|determine|say|judge)\b", str(reply or "")))


def mask(value):
    # for the assistant: enough to recognise it, not enough to use it
    value = str(value or "")
    return value if len(value) <= 6 else f"{value[:4]} … {value[-4:]}"


# ---------- 2. Claude reads ----------

READ_PROMPT = """You read one document that a new hire uploaded for HR, and report what is printed on it.
Report only what you can actually read. Never guess, complete or correct a value: if you cannot read a field, leave it null.
Set readable to false when you cannot read the main fields (blurred, cut off, not a document, not the kind asked for).
You are NOT deciding whether the document is genuine, and you never say that it is. Set looks_off to true only when
something visibly seems inconsistent (text that looks edited, mixed fonts, a photo of a screen, a name that differs
between two places on the document) and give a short, neutral reason: a person at HR will look at it."""

READ_FIELDS = {
    "identity": {"document_type": {"type": "string", "enum": ["passport", "id_card", "residence_permit", "other"]},
                 "holder_name": {"type": ["string", "null"]}, "expiry_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"}},
    "bank_details": {"holder_name": {"type": ["string", "null"]}, "iban": {"type": ["string", "null"], "description": "exactly as printed"}},
    "social_security": {"holder_name": {"type": ["string", "null"]},
                        "ssn": {"type": ["string", "null"], "description": "the French social security number, 15 characters, exactly as printed"}},
}
READ_ASKED = {"identity": "an identity document (passport, national ID card or residence permit): the type, the holder's name and the expiry date",
              "bank_details": "bank details (a RIB or bank certificate): the account holder's name and the IBAN",
              "social_security": "a social security certificate or card: the holder's name and the 15-character social security number"}
CLAUDE_MODEL = "claude-haiku-4-5"


def read_with_claude(kind, data, file_type):
    import anthropic            # imported here so the checks above load without the library or a key
    block = {"type": "document" if file_type == "pdf" else "image",
             "source": {"type": "base64", "media_type": FILE_TYPES[file_type], "data": base64.b64encode(data).decode()}}
    tool = {"name": "report_document", "description": "Report what is printed on the document.",
            "input_schema": {"type": "object", "required": ["readable", "looks_off"],
                             "properties": {"readable": {"type": "boolean"}, **READ_FIELDS[kind],
                                            "looks_off": {"type": "boolean"}, "looks_off_reason": {"type": ["string", "null"]}}}}
    response = anthropic.Anthropic().messages.create(
        model=CLAUDE_MODEL, max_tokens=600, system=READ_PROMPT, tools=[tool],
        tool_choice={"type": "tool", "name": "report_document"},
        messages=[{"role": "user", "content": [block, {"type": "text", "text": f"This should be {READ_ASKED[kind]}."}]}])
    return next(b.input for b in response.content if b.type == "tool_use")


def _read(kind, data, file_type):
    return (READER or read_with_claude)(kind, data, file_type)


# ---------- the whole flow ----------

def _log(conn, emp_id, actor_id, label, old, new, reason):
    onboarding._log(conn, emp_id, actor_id, f"document: {label}", old, new, reason)


def _rows(sql, params=()):
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def _delete_files(names):
    for name in names:
        try:
            os.remove(os.path.join(files_dir(), name))
        except FileNotFoundError:
            pass


def _retire(conn, doc_ids, status, now_sql="CURRENT_TIMESTAMP"):
    # the files of these documents stop existing; the row stays (status, when), without what was read
    names = []
    for doc_id in doc_ids:
        row = conn.execute("SELECT file_name FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if row and row["file_name"]:
            names.append(row["file_name"])
        conn.execute(f"""UPDATE documents SET status = ?, file_name = NULL, fields_json = NULL, deleted_at = {now_sql}
                         WHERE id = ?""", (status, doc_id))
    return names


def submit(emp_id, step_key, data, original_name="", today=None):
    """A new hire sends a file for one of their own steps. Returns {"accepted": True, "document_id", "read",
    "flagged"} (waiting for them to confirm), {"accepted": False, "problems": [...]} (send it again), or
    {"error": ...} (the request itself was wrong). The person is whoever the welcome link was made for."""
    today = today or date.today()
    spec = DOCUMENT_STEPS.get(step_key)
    hire = hr_queries.person(emp_id)
    if spec is None:
        return {"error": "That step doesn't take a document."}
    if hire is None or not hire["active"]:
        return {"error": "There is no onboarding for you."}
    steps = [s for s in onboarding._rows(onboarding.STEP + " WHERE o.emp_id = ? AND s.step_key = ?", (emp_id, step_key))]
    if not steps:
        return {"error": "There is no such step in your checklist."}
    step = steps[0]
    if step["owner"] != onboarding.NEW_HIRE or step["status"] != onboarding.TODO:
        return {"error": "That step is already done. Ask HR if it has to be redone."}
    file_type, problem = check_file(data)
    if problem:
        return {"error": problem}

    try:
        read = _read(spec["kind"], data, file_type)
    except Exception:                                   # the reader is down, or the file is one it can't take
        read = {"readable": False}
    problems, shown = verify(spec["kind"], read, hire["name"], today)
    flagged = (read.get("looks_off_reason") or "Something on the document looks off.") if read.get("looks_off") else None
    name = re.sub(r"[^\w .()-]", "_", os.path.basename(original_name or ""))[:120]
    sha = hashlib.sha256(data).hexdigest()
    conn = db.get_conn()
    to_delete = []
    try:
        with conn:
            earlier = [r["id"] for r in conn.execute(
                "SELECT id FROM documents WHERE step_id = ? AND status = 'read'", (step["id"],)).fetchall()]
            to_delete += _retire(conn, earlier, "replaced")            # a new file replaces one still waiting
            if problems:
                # nothing is kept: not the file, and not what was read (it may be someone else's document)
                doc_id = conn.execute("""INSERT INTO documents (emp_id, step_id, step_key, status, original_name, media_type,
                                         size_bytes, sha256, problems_json, deleted_at)
                                         VALUES (?, ?, ?, 'rejected', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                                      (emp_id, step["id"], step_key, name, FILE_TYPES[file_type], len(data), sha,
                                       json.dumps(problems))).lastrowid
                _log(conn, emp_id, emp_id, spec["label"], None, "rejected: send again", "; ".join(problems))
            else:
                os.makedirs(files_dir(), mode=0o700, exist_ok=True)
                stored = secrets.token_hex(16) + EXTENSIONS[file_type]
                path = os.path.join(files_dir(), stored)
                with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
                    f.write(data)
                fields = {label: value for label, value in shown}
                doc_id = conn.execute("""INSERT INTO documents (emp_id, step_id, step_key, status, original_name, media_type,
                                         size_bytes, sha256, file_name, fields_json, flagged_reason)
                                         VALUES (?, ?, ?, 'read', ?, ?, ?, ?, ?, ?, ?)""",
                                      (emp_id, step["id"], step_key, name, FILE_TYPES[file_type], len(data), sha, stored,
                                       json.dumps(fields, ensure_ascii=False), flagged)).lastrowid
                _log(conn, emp_id, emp_id, spec["label"], None, "received, waiting for confirmation",
                     "Checks passed" + ("; flagged for HR to look at" if flagged else ""))
    finally:
        conn.close()
    _delete_files(to_delete)
    if problems:
        return {"accepted": False, "problems": problems, "document_id": doc_id}
    return {"accepted": True, "document_id": doc_id, "read": [{"label": l, "value": v} for l, v in shown],
            "flagged": bool(flagged)}


def _own_waiting(emp_id, doc_id):
    rows = _rows("SELECT * FROM documents WHERE id = ? AND emp_id = ?", (doc_id, emp_id))
    if not rows:
        return None, {"error": "No such document."}
    if rows[0]["status"] != "read":
        return None, {"error": "That document isn't waiting for your confirmation."}
    return rows[0], None


def confirm(emp_id, doc_id):
    # "yes, that is what my document says": the step is done, and the file stays for HR
    doc, err = _own_waiting(emp_id, doc_id)
    if err:
        return err
    label = DOCUMENT_STEPS[doc["step_key"]]["label"]
    conn = db.get_conn()
    try:
        with conn:
            older = [r["id"] for r in conn.execute(
                "SELECT id FROM documents WHERE step_id = ? AND status = 'confirmed' AND id != ?", (doc["step_id"], doc_id)).fetchall()]
            to_delete = _retire(conn, older, "replaced")
            conn.execute("UPDATE documents SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP WHERE id = ?", (doc_id,))
            done = onboarding.complete_by_new_hire(emp_id, doc["step_id"], f"Confirmed by the new hire from the {label} they sent", conn=conn)
            if "error" in done:
                raise ValueError(done["error"])
            _log(conn, emp_id, emp_id, label, "received, waiting for confirmation", "confirmed", "The new hire confirmed what was read")
    except ValueError as e:
        return {"error": str(e)}
    finally:
        conn.close()
    _delete_files(to_delete)
    return {"success": True}


def decline(emp_id, doc_id):
    # "no, that is not right": the file is removed and they send another
    doc, err = _own_waiting(emp_id, doc_id)
    if err:
        return err
    conn = db.get_conn()
    try:
        with conn:
            names = _retire(conn, [doc_id], "replaced")
            _log(conn, emp_id, emp_id, DOCUMENT_STEPS[doc["step_key"]]["label"], "received, waiting for confirmation",
                 "not confirmed: send again", "The new hire said what was read is not right")
    finally:
        conn.close()
    _delete_files(names)
    return {"success": True}


# said to the new hire (through the assistant) so nobody mistakes "checked by the app" for "known to be genuine"
WHAT_WAS_CHECKED = ("The app read the details and checked them: the name, the expiry date, and that the IBAN or number is "
                    "well formed. It cannot tell whether a document is genuine. A person at HR looks at the documents too.")


def status_for(emp_id):
    """For the new hire's checklist (and so for the assistant): where each document step stands, in words,
    with the numbers shortened. {step_key: {...}}"""
    out = {}
    for key in DOCUMENT_STEPS:
        rows = _rows("""SELECT * FROM documents WHERE emp_id = ? AND step_key = ? AND status IN ('read', 'confirmed', 'rejected')
                        ORDER BY id DESC LIMIT 1""", (emp_id, key))
        if not rows:
            continue
        doc = rows[0]
        if doc["status"] == "rejected":
            out[key] = {"status": "needs resending", "problems": json.loads(doc["problems_json"] or "[]")}
        else:
            fields = json.loads(doc["fields_json"] or "{}")
            seen = {k: (mask(v) if k in ("IBAN", "Social security number") else v) for k, v in fields.items()}
            out[key] = {"status": "waiting for you to confirm" if doc["status"] == "read" else "received",
                        "what_was_read": seen, "what_was_checked": WHAT_WAS_CHECKED}
    return out


# ---------- who opens a file, and HR's view ----------

def _can_open(viewer_id, doc):
    viewer = get_employee(viewer_id)
    person = hr_queries.person(viewer_id) if viewer else None
    if not person or not person["active"]:
        return False
    return viewer["role"] == "hr" or viewer_id == doc["emp_id"]


def open_file(viewer_id, doc_id):
    """The file's path, type and name, for the person or for HR only. Every opening is logged."""
    rows = _rows("SELECT * FROM documents WHERE id = ?", (doc_id,))
    doc = rows[0] if rows else None
    if doc is None or not _can_open(viewer_id, doc):
        return {"error": "No such document."}                     # the same answer for "not yours" and "doesn't exist"
    if not doc["file_name"] or doc["status"] not in ("read", "confirmed"):
        return {"error": "That file no longer exists."}
    path = os.path.join(files_dir(), doc["file_name"])
    if not os.path.exists(path):
        return {"error": "That file no longer exists."}
    conn = db.get_conn()
    with conn:
        conn.execute("INSERT INTO document_views (document_id, viewer_id) VALUES (?, ?)", (doc_id, viewer_id))
    conn.close()
    ext = EXTENSIONS[next(k for k, v in FILE_TYPES.items() if v == doc["media_type"])]
    return {"path": path, "media_type": doc["media_type"], "filename": f"{DOCUMENT_STEPS[doc['step_key']]['label'].replace(' ', '-')}{ext}"}


def hr_documents(hr_id, emp_id):
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can see a person's documents."}
    out = []
    for d in _rows("""SELECT * FROM documents WHERE emp_id = ? AND status IN ('read', 'confirmed') ORDER BY id""", (emp_id,)):
        views = _rows("""SELECT v.viewed_at, e.name AS viewer_name FROM document_views v LEFT JOIN employees e ON e.id = v.viewer_id
                         WHERE v.document_id = ? ORDER BY v.id DESC LIMIT 5""", (d["id"],))
        reviewer = get_employee(d["flag_reviewed_by"]) if d["flag_reviewed_by"] else None
        out.append({"id": d["id"], "step_key": d["step_key"], "label": DOCUMENT_STEPS[d["step_key"]]["label"], "status": d["status"],
                    "uploaded_at": d["uploaded_at"], "confirmed_at": d["confirmed_at"], "read": json.loads(d["fields_json"] or "{}"),
                    "flagged_reason": d["flagged_reason"], "flag_reviewed_by": reviewer["name"] if reviewer else None,
                    "flag_reviewed_at": d["flag_reviewed_at"], "views": views})
    return out


def flagged_for_review(hr_id):
    # the documents something looked off about, that no human has looked at yet: for the Needs you inbox
    if not onboarding.is_hr(hr_id):
        return []
    items = []
    for d in _rows("""SELECT * FROM documents WHERE flagged_reason IS NOT NULL AND flag_reviewed_at IS NULL
                      AND status IN ('read', 'confirmed') ORDER BY id"""):
        hire = hr_queries.person(d["emp_id"])
        if hire and hire["active"]:
            items.append({"document_id": d["id"], "employee_id": d["emp_id"], "employee_name": hire["name"],
                          "label": DOCUMENT_STEPS[d["step_key"]]["label"], "reason": d["flagged_reason"]})
    return items


def mark_reviewed(hr_id, doc_id, note):
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can review a document."}
    if not (note or "").strip():
        return {"error": "Write what you found. It goes in the change history."}
    rows = _rows("SELECT * FROM documents WHERE id = ? AND flagged_reason IS NOT NULL", (doc_id,))
    if not rows:
        return {"error": "No flagged document with that ID."}
    if rows[0]["flag_reviewed_at"]:
        return {"error": "A person at HR already looked at this one."}
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("UPDATE documents SET flag_reviewed_by = ?, flag_reviewed_at = CURRENT_TIMESTAMP WHERE id = ?", (hr_id, doc_id))
            _log(conn, rows[0]["emp_id"], hr_id, DOCUMENT_STEPS[rows[0]["step_key"]]["label"], "flagged", "looked at by HR", note.strip())
    finally:
        conn.close()
    return {"success": True}


def delete_for_person(conn, emp_id):
    """A cancelled hire: every file is deleted, and what was read goes with it. Returns the file names to remove
    from disk once the caller's transaction has been saved."""
    ids = [r["id"] for r in conn.execute("SELECT id FROM documents WHERE emp_id = ? AND file_name IS NOT NULL", (emp_id,)).fetchall()]
    names = _retire(conn, ids, "deleted")
    conn.execute("UPDATE documents SET fields_json = NULL, problems_json = NULL WHERE emp_id = ?", (emp_id,))
    return names


def remove_files(names):
    _delete_files(names)
