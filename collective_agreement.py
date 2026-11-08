# collective_agreement.py: the company's convention collective (collective agreement), which HR uploads and the assistant searches.
#   - HR uploads it as text (.txt, .md) or a PDF. One is current at a time; uploading another replaces it. Only HR can upload or remove.
#   - Anyone signed in can open the file: an employer has to make its collective agreement available to its employees.
#   - The text is cut into articles (at "Article 12 - ...", "Art. 12", "# ..." headings; a long article is cut into parts), each embedded
#     once when it is uploaded, so a question finds the right articles by meaning, as in search_policy (policy.py).
#   - The assistant answers from the first source that answers, in this order: the person's contract, the collective agreement, the
#     company policy, the law. It names the source, and says first which sources above it do not specify the question (agreement_prompt).
#     That is the order to ask them in, not an opinion on which one the law makes binding.
#   - With no agreement uploaded, the tool is not offered to the model at all (available_tools) and the prompt does not mention it:
#     nothing about the assistant changes until HR uploads one.
# A PDF's text is read with pypdf when it has a text layer; a scanned PDF is read by Claude, like the documents in employee_files.py.
# No third-party import at load time: pypdf, anthropic and the embedding are loaded when they run (or injected: EMBED_MANY, READER).
import hashlib
import io
import json
import math
import os
import re
import secrets

import db
import documents
import employee_files
import onboarding
from tools import get_employee

TOOL = "search_collective_agreement"
NAME_MAX = 120
MIN_TEXT_CHARS = 200        # a PDF with less text than this has no text layer: Claude reads it instead
MAX_SECTION_CHARS = 1800    # a longer article is cut into parts, so a search lands on the right paragraph
MAX_SECTIONS = 2000
EMBED_BATCH = 96
MIN_SCORE = 0.30            # as policy.py: below this a section is "not related". It cannot tell a good match from a vague one (see
                            # policy-answer findings), so the assistant is told to say "the agreement does not specify it" itself.
TOP_K = 3
EMBED_MANY = None           # list of texts -> list of vectors. agent.py sets it (policy.embed); tests put a fake here
READER = None               # bytes, kind -> (text, complete). Tests put a fake here; None means pypdf, then Claude for a scan

HEADING = re.compile(r"^\s*(?:#{1,4}\s+\S.*|(?:article|art\.)\s*\d+[A-Za-z0-9.\-]*\b.*|annexe\s+[\dIVXivx]+\b.*)$", re.I)

RULES = ("These articles are from the company's collective agreement (convention collective). Name the agreement and the article you used, "
         "for example: \"From the collective agreement ({name}, article 7): ...\". If they do not answer the question, say that the agreement "
         "does not specify it, and go on to the company policy. Never present them as the person's contract or as the company policy. "
         "If an article depends on the person's category (for example employee or cadre), seniority or status: use the category HR has "
         "recorded for them (it is in the note about the logged-in user). If HR hasn't set it and their documents do not say which applies to "
         "them, say \"HR hasn't set your category\", give each case and say that HR can confirm which one applies to them. Never decide it from "
         "their job title.")


def init_agreement():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS collective_agreements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                original_name TEXT NOT NULL DEFAULT '',
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                file_name TEXT,                  -- the random name on disk; NULL once replaced or removed
                sections INTEGER NOT NULL,
                text_status TEXT NOT NULL,       -- ready, or partial (a scan that Claude read only the start of)
                status TEXT NOT NULL DEFAULT 'current',   -- current, replaced or removed
                uploaded_by TEXT NOT NULL,
                uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ended_by TEXT,
                ended_at TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agreement_sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agreement_id INTEGER NOT NULL REFERENCES collective_agreements(id),
                ord INTEGER NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                vector_json TEXT NOT NULL
            )""")
    conn.close()


# ---------- the text, cut into articles ----------

def check_upload(data):
    """('pdf' | 'text', None) for a file we accept, else (None, the reason)."""
    if not data:
        return None, "That file is empty."
    if len(data) > documents.MAX_BYTES:
        return None, f"That file is too big ({len(data) / 1024 / 1024:.1f} MB). The limit is 10 MB."
    if data[:5] == b"%PDF-":
        return "pdf", None
    text = _decode(data)
    if text is None:
        return None, "I can only read a PDF or a text file (.txt or .md)."
    return "text", None


def _decode(data):
    if b"\x00" in data[:4096]:
        return None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        shown = sum(1 for c in text[:20000] if c.isprintable() or c in "\n\r\t")
        return text if shown >= 0.97 * len(text[:20000]) else None
    return None


def _pdf_text(data):
    try:
        from pypdf import PdfReader
        return "\n\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages)
    except Exception:                       # no pypdf, an encrypted or broken file: the caller falls back to Claude
        return ""


def _read(data, kind):
    """(text, complete) of an accepted file."""
    if READER is not None:
        return READER(data, kind)
    if kind == "text":
        return _decode(data) or "", True
    text = _pdf_text(data)
    if len(text.strip()) >= MIN_TEXT_CHARS:
        return text, True
    return employee_files.transcribe_with_claude(data, "pdf")          # a scan: Claude reads it (long ones come back partial)


def split_sections(text):
    """[{"title", "text"}, ...]: one per article, a long article in parts. Text before the first heading is "Introduction"."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    found, title, body = [], None, []

    def flush():
        content = "\n".join(body).strip()
        if title is not None or content:
            found.append((title, content))

    for line in lines:
        if HEADING.match(line):
            flush()
            title, body = re.sub(r"^\s*#{1,4}\s+", "", line).strip(), []
        else:
            body.append(line)
    flush()
    if not any(t is not None for t, _ in found):          # no headings at all: plain parts
        return [{"title": f"Part {n}", "text": p} for n, p in enumerate(_pieces(found[0][1]) if found else [], start=1) if p.strip()]
    sections = []
    for title, content in found:
        label = title if title is not None else "Introduction"
        pieces = _pieces(content or label)                 # a heading with nothing under it ("Article 3 - ... 5 jours") is its own text
        for part, piece in enumerate(pieces, start=1):
            sections.append({"title": (label + (f" (part {part})" if len(pieces) > 1 else ""))[:200], "text": piece})
    return [s for s in sections if s["text"].strip()]


def _pieces(text):
    """The text in pieces of at most MAX_SECTION_CHARS, cut between paragraphs (inside one only if it alone is too long)."""
    if len(text) <= MAX_SECTION_CHARS:
        return [text]
    pieces, current = [], ""
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if len(paragraph) > MAX_SECTION_CHARS:
            if current:
                pieces.append(current)
                current = ""
            while len(paragraph) > MAX_SECTION_CHARS:           # cut at a sentence end, else at a space, else anywhere
                cut = paragraph.rfind(". ", 0, MAX_SECTION_CHARS) + 1
                if cut < MAX_SECTION_CHARS // 2:
                    cut = paragraph.rfind(" ", 0, MAX_SECTION_CHARS) + 1
                if cut < MAX_SECTION_CHARS // 2:
                    cut = MAX_SECTION_CHARS
                pieces.append(paragraph[:cut].strip())
                paragraph = paragraph[cut:].strip()
        if current and len(current) + len(paragraph) + 2 > MAX_SECTION_CHARS:
            pieces.append(current)
            current = paragraph
        else:
            current = (current + "\n\n" + paragraph) if current else paragraph
    if current:
        pieces.append(current)
    return [p for p in pieces if p.strip()]


# ---------- embeddings ----------

def _embed(texts):
    embed = EMBED_MANY
    if embed is None:
        from policy import embed             # imported here: policy.py needs the OpenAI library and a key
    vectors = []
    for start in range(0, len(texts), EMBED_BATCH):
        vectors += embed([t[:6000] for t in texts[start:start + EMBED_BATCH]])
    return vectors


def _cosine(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


# ---------- HR uploads, replaces and removes ----------

def add(hr_id, name, data, original_name=""):
    """HR uploads the agreement. Returns {"success", "agreement_id", "sections", "assistant": "ready" | "partial"} or {"error"}."""
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can add the collective agreement."}
    name = " ".join((name or "").split())
    if not name:
        return {"error": "Give the agreement its name, for example \"Convention collective Syntec\"."}
    if len(name) > NAME_MAX:
        return {"error": f"Keep the name under {NAME_MAX} characters."}
    kind, problem = check_upload(data)
    if problem:
        return {"error": problem}
    try:
        text, complete = _read(data, kind)
    except Exception:
        return {"error": "I couldn't read that file just now. Try again in a moment."}
    sections = split_sections(text)
    if not sections:
        return {"error": "I couldn't find any text in that file. If it is a scan, try a clearer one."}
    if len(sections) > MAX_SECTIONS:
        return {"error": f"That agreement is too long to index ({len(sections)} parts; the limit is {MAX_SECTIONS})."}
    try:
        vectors = _embed([s["title"] + "\n" + s["text"] for s in sections])
    except Exception:
        return {"error": "I couldn't index it just now (the search service is down). Nothing was changed. Try again in a moment."}
    stem = secrets.token_hex(16)
    file_name = stem + (".pdf" if kind == "pdf" else ".txt")
    employee_files._write(file_name, data)
    original = re.sub(r"[^\w .()-]", "_", os.path.basename(original_name or ""))[:120]
    conn = db.get_conn()
    try:
        with conn:
            old = conn.execute("SELECT id, file_name FROM collective_agreements WHERE status = 'current'").fetchall()
            for r in old:                       # the one it replaces: kept as a line of history, its file and text deleted
                conn.execute("UPDATE collective_agreements SET status = 'replaced', file_name = NULL, ended_by = ?, ended_at = CURRENT_TIMESTAMP "
                             "WHERE id = ?", (hr_id, r["id"]))
                conn.execute("DELETE FROM agreement_sections WHERE agreement_id = ?", (r["id"],))
            agreement_id = conn.execute(
                """INSERT INTO collective_agreements (name, original_name, media_type, size_bytes, sha256, file_name, sections, text_status, uploaded_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, original, "application/pdf" if kind == "pdf" else "text/plain", len(data), hashlib.sha256(data).hexdigest(), file_name,
                 len(sections), "ready" if complete else "partial", hr_id)).lastrowid
            conn.executemany("INSERT INTO agreement_sections (agreement_id, ord, title, text, vector_json) VALUES (?, ?, ?, ?, ?)",
                             [(agreement_id, n, s["title"], s["text"], json.dumps(v)) for n, (s, v) in enumerate(zip(sections, vectors), start=1)])
    except Exception:
        employee_files._remove(file_name)
        raise
    finally:
        conn.close()
    employee_files._remove(*[r["file_name"] for r in old])
    return {"success": True, "agreement_id": agreement_id, "sections": len(sections), "assistant": "ready" if complete else "partial"}


def remove(hr_id):
    """HR removes the current agreement: the assistant stops using it, and its file and text are deleted."""
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can remove the collective agreement."}
    conn = db.get_conn()
    try:
        with conn:
            row = conn.execute("SELECT id, file_name FROM collective_agreements WHERE status = 'current'").fetchone()
            if row is None:
                return {"error": "There is no collective agreement to remove."}
            conn.execute("UPDATE collective_agreements SET status = 'removed', file_name = NULL, ended_by = ?, ended_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (hr_id, row["id"]))
            conn.execute("DELETE FROM agreement_sections WHERE agreement_id = ?", (row["id"],))
    finally:
        conn.close()
    employee_files._remove(row["file_name"])
    return {"success": True}


# ---------- everyone reads it ----------

def _current():
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM collective_agreements WHERE status = 'current'").fetchone()
    conn.close()
    return dict(row) if row else None


def summary():
    """What the page shows, or None when HR has not uploaded one."""
    a = _current()
    if a is None:
        return None
    by = get_employee(a["uploaded_by"])
    return {"id": a["id"], "name": a["name"], "original_name": a["original_name"], "media_type": a["media_type"], "size_bytes": a["size_bytes"],
            "sections": a["sections"], "uploaded_by": by["name"] if by else None, "uploaded_at": a["uploaded_at"], "assistant": a["text_status"]}


def open_file(viewer_id):
    """The current file's path, type and name, for anyone who works here."""
    a = _current()
    viewer = get_employee(viewer_id)
    if a is None or viewer is None or not employee_files._active(viewer_id):
        return {"error": "There is no collective agreement."}
    path = os.path.join(documents.files_dir(), a["file_name"] or "")
    if not a["file_name"] or not os.path.exists(path):
        return {"error": "That file no longer exists."}
    slug = re.sub(r"[^a-z0-9]+", "-", a["name"].lower()).strip("-") or "collective-agreement"
    return {"path": path, "media_type": a["media_type"], "filename": slug + (".pdf" if a["media_type"] == "application/pdf" else ".txt")}


# ---------- the assistant ----------

def on_file():
    return _current() is not None


# The model searches the agreement and then answers from the policy or the law without a word about the agreement, or says the agreement is silent
# and stops. Like the document guard (document_answers.py), the code checks the final reply and asks once for the missing part.
SILENT = re.compile(r"(?:agreement|convention)[^.\n]{0,80}?(?:doesn't|does not|don't|do not|not|no|never)\s+(?:\w+\s+)?(?:say|mention|specify|state|include|cover|contain|address|provide|give|define|have|speak)"
                    r"|(?:isn't|is not|not)\s+(?:mentioned|specified|stated|addressed|covered)[^.\n]{0,40}(?:agreement|convention)|nothing[^.\n]{0,40}(?:agreement|convention)|(?:agreement|convention)[^.\n]{0,40}\bnothing\b", re.I)
MENTIONS = re.compile(r"agreement|convention", re.I)       # any word about it: answered from it, or said it is silent. Only a reply that never mentions it is sent back
HIDDEN = ("(A note from the app, not from the person. They have not seen your reply and must not hear about this note: do not apologise, "
          "do not say you corrected anything, answer their question directly.) ")
SAY_AGREEMENT_SILENT = HIDDEN + ("You searched the collective agreement and it did not answer, but your reply does not say so. Start with \"The collective "
                                 "agreement doesn't specify it.\" and then give the company policy or the law as a separate part, clearly labelled with "
                                 "its source (\"From the company policy: ...\").")
SEARCH_ON = HIDDEN + ("Your reply says that the collective agreement does not specify it. Before you answer, call search_policy, and search_labour_code "
                      "if it is a legal question. Then answer in two parts: that the agreement does not specify it, then what the company policy or the "
                      "law says, clearly labelled with its source. If neither says it, say that too. Do not invent anything.")


FROM_POLICY_OR_LAW = re.compile(r"\b(?:company|HR)\s+policy|handbook|Code du travail|labou?r (?:code|law)", re.I)


def says_agreement_silent(reply):
    return bool(SILENT.search(str(reply or "")))


def nudge(called, reply):
    """What to tell the assistant once, before its reply is shown, or None when the reply has the right shape."""
    if TOOL not in called:
        return None
    later = any(t in called for t in ("search_policy", "search_labour_code"))
    silent = says_agreement_silent(reply)
    if silent and not later:
        return SEARCH_ON
    reply = str(reply or "")
    if later and not MENTIONS.search(reply) and (FROM_POLICY_OR_LAW.search(reply) or "read_my_document" not in called):
        return SAY_AGREEMENT_SILENT                    # the policy or the law answered, and not a word about the agreement it searched first
    return None


def available_tools(all_tools):
    """The tool list for the model: without the agreement search when HR has not uploaded one."""
    if _current() is not None:
        return all_tools
    return [t for t in all_tools if t["name"] != TOOL]


def agreement_prompt():
    """The order of sources, for the system prompt, or None while there is no agreement."""
    a = _current()
    if a is None:
        return None
    return (f"This company has a collective agreement on file: \"{a['name']}\" (search_collective_agreement). For a question about a rule, a right or a "
            "condition that applies to the person (leave, sick pay, notice, pay, working time, benefits), the sources come in this order: "
            "1. their own contract (their documents), but only for what a contract normally holds (their notice period, salary, hours, trial period, "
            "leave); 2. the collective agreement; 3. the company policy (search_policy); 4. the law (search_labour_code, for legal questions). "
            "Go down the list and answer from the FIRST source that answers, and name it: \"Your contract says: ...\", \"From the collective agreement "
            f"({a['name']}, article N): ...\", \"From the company policy: ...\", \"From the Code du travail: ...\". If you looked at a source above it, "
            "say first that it does not specify the question (\"The collective agreement doesn't specify it\"). Do not add the sources below the one "
            "that answered unless the person asks. Never present an agreement, policy or law answer as what their contract says. If they ask what "
            "the collective agreement says, search it. Never answer from memory about the agreement.")


def search(question, top_k=TOP_K):
    """The agreement's best-matching articles for a question: [{"title", "text", "score"}]."""
    a = _current()
    if a is None:
        return []
    conn = db.get_conn()
    rows = conn.execute("SELECT title, text, vector_json FROM agreement_sections WHERE agreement_id = ? ORDER BY ord", (a["id"],)).fetchall()
    conn.close()
    question_vector = _embed([question])[0]
    scored = sorted(((_cosine(question_vector, json.loads(r["vector_json"])), r) for r in rows), key=lambda pair: pair[0], reverse=True)
    return [{"title": r["title"], "text": r["text"], "score": round(score, 3)} for score, r in scored[:top_k] if score >= MIN_SCORE]


def tool_result(question):
    """What search_collective_agreement returns to the model."""
    a = _current()
    if a is None:
        return {"error": "No collective agreement has been uploaded."}
    found = search(question)
    if not found:
        return {"found": False, "agreement": a["name"], "message": f"No matching article in {a['name']}.",
                "rules": "Say that the collective agreement does not specify it, then go on to the company policy."}
    return {"found": True, "agreement": a["name"], "articles": [{"article": f["title"], "text": f["text"]} for f in found],
            "rules": RULES.format(name=a["name"])}
