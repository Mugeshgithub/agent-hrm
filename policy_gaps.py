# policy_gaps.py: the questions people ask that the company policy does not answer, shown to HR in "Needs you".
# "Asked 3 times about the company car; the policy doesn't say." HR then either dismisses it (the policy should not cover
# it) or edits the policy and marks it fixed.
#
# Nobody is named. This is what is kept, and what is not:
#   - the wording of the question, with names, e-mail addresses, employee numbers and long numbers taken out (scrub);
#     it is the assistant's own short search ("company car"), not the person's message
#   - how many times it was asked, when first and last, up to EXAMPLES_KEPT different wordings
#   - NOT who asked: no employee id, no name, no token. So it counts questions, not people: one person asking three
#     times counts three. (Counting people would need a marker tied to the person, which is what "anonymised" rules out.)
# Similar questions fall in the same gap, by meaning (embeddings, injected: EMBED), or by shared words when there is no embedding.
#   - A gap HR marked fixed opens again if someone asks again: the policy still does not answer.
#   - A gap HR dismissed stays quiet: further questions like it are not shown.
# No AI call and no third-party import here (agent.py puts the embedding function in EMBED), so it is unit-tested in CI.
import json
import math
import re

import db
import onboarding

EMBED = None            # text -> vector; agent.py sets it. None (or an error) means: group by shared words
SAME_TOPIC = 0.60       # cosine of two question embeddings. Measured on text-embedding-3-small: the assistant's keyword-style rewordings of one question
                        # 0.62-0.67 ("gym membership" / "reimbursement gym sports classes"), plain paraphrases 0.55-0.79, unrelated topics 0.27 or less
                        # (a few reach 0.41). Neighbouring topics overlap with paraphrases (company car / company laptop 0.64, paternity / maternity 0.81),
                        # so no threshold is perfect: the card lists the different wordings it holds, a fixed gap opens again when asked again, and a
                        # dismissed one only absorbs close paraphrases (DISMISSED_SAME), so a dismissed topic never swallows its neighbour.
DISMISSED_SAME = 0.78
SAME_WORDS = 0.6        # without an embedding: share of words in common
DISMISSED_SAME_WORDS = 0.8
TOPIC_MAX = 120
EXAMPLES_KEPT = 4
STOP = {"the", "and", "for", "can", "does", "did", "how", "what", "are", "you", "your", "our", "with", "that", "this", "have", "has", "from",
        "about", "any", "there", "will", "get", "company", "policy"}

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
EMPLOYEE_ID = re.compile(r"\b[A-Za-z]\d{3}\b")
LONG_NUMBER = re.compile(r"\d[\d\s.-]{2,}\d")        # 4+ digits, with the separators of a phone number or an IBAN

# The assistant says the policy has nothing on it. "policy" comes first in the sentence ("the policy doesn't cover ...") or the
# sentence says nothing was found in it. Not matched on purpose: "doesn't require" (that is a rule the policy does give), and
# "isn't covered by the paid-leave policy" (that says which section applies).
_POLICY = r"(?:company |HR |hr )?(?:policy|handbook)"
_SILENT_VERBS = r"(?:say|mention|specify|state|include|cover|contain|address|provide|give|define|have|speak|explain|describe|list|offer|discuss)"
POLICY_SILENT = re.compile(
    rf"\b{_POLICY}\b[^.\n]{{0,80}}?\b(?:doesn't|does not|don't|do not|isn't|is not|never|no longer)\s+(?:\w+\s+){{0,2}}{_SILENT_VERBS}"
    rf"|\b{_POLICY}\b[^.\n]{{0,40}}?\b(?:is silent|has nothing|says nothing|contains nothing|nothing (?:on|about))"
    rf"|\b(?:not|never)\s+(?:mentioned|specified|stated|addressed|covered)\s+(?:in|by)\s+(?:the |our )?{_POLICY}"
    rf"|\bno\s+(?:matching|relevant)\s+(?:section|information|rule)[^.\n]{{0,40}}{_POLICY}"
    rf"|\b(?:didn't|did not|couldn't|could not|can't|cannot|unable to)\s+find[^.\n]{{0,80}}\b{_POLICY}\b"
    rf"|\bnothing\s+(?:in|about)[^.\n]{{0,30}}\b{_POLICY}\b"
    rf"|\bneither\b[^.\n]{{0,80}}\b{_POLICY}\b|\b{_POLICY}\b[^.\n]{{0,80}}\bneither\b", re.I)


def says_policy_silent(reply):
    return bool(POLICY_SILENT.search(str(reply or "")))


def topic_from_turn(searches, reply):
    """The question to keep, or None. `searches` are this answer's calls to search_policy, in order:
    [{"query": "company car", "found": False}, ...]. It is a gap when every search came back empty, or when the
    assistant says the policy does not say it (the search score cannot tell: an irrelevant section still comes back)."""
    searches = [s for s in (searches or []) if str(s.get("query", "")).strip()]
    if not searches:
        return None
    if all(not s.get("found") for s in searches) or says_policy_silent(reply):
        return searches[-1]["query"]
    return None


def scrub(text, names=()):
    """The wording without anything that points at a person."""
    t = " ".join(str(text or "").split())
    t = EMAIL.sub("[email]", t)
    t = EMPLOYEE_ID.sub("[id]", t)
    t = LONG_NUMBER.sub("[number]", t)
    for name in sorted({n for n in names if len(n) >= 3}, key=len, reverse=True):
        t = re.sub(rf"(?i)(?<!\w){re.escape(name)}(?!\w)", "[name]", t)
    t = re.sub(r"\[name\](?:\s+\[name\])+", "[name]", t)            # "Emma Laurent" is one name
    return t[:TOPIC_MAX].strip()


def init_gaps():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS policy_gaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,                 -- the first wording, scrubbed
                examples_json TEXT NOT NULL,         -- up to EXAMPLES_KEPT different wordings, scrubbed
                vector_json TEXT,                    -- the topic's embedding, to group by meaning; NULL if there was none
                asks INTEGER NOT NULL DEFAULT 1,     -- since it was opened (or opened again)
                first_asked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_asked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'open', -- open, dismissed or fixed
                closed_by TEXT,
                closed_at TEXT,
                closed_note TEXT,
                reopened_at TEXT
                -- no employee column, on purpose
            )""")
    conn.close()


def _words(text):
    return {w for w in re.findall(r"[a-zà-ÿ]{3,}", text.lower()) if w not in STOP}


def _cosine(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def _embed(text):
    if EMBED is None:
        return None
    try:
        return EMBED(text)
    except Exception:
        return None             # recording a question never fails because the embedding service did


def _closeness(topic, vector, row):
    """0..1, how much the new wording is the same question as this gap; below its threshold it is not."""
    dismissed = row["status"] == "dismissed"
    if vector is not None and row["vector_json"]:
        score = _cosine(vector, json.loads(row["vector_json"]))
        return score if score >= (DISMISSED_SAME if dismissed else SAME_TOPIC) else 0.0
    mine, theirs = _words(topic), _words(row["topic"])
    if not mine or not theirs:
        return 0.0
    score = len(mine & theirs) / len(mine | theirs)
    return score if score >= (DISMISSED_SAME_WORDS if dismissed else SAME_WORDS) else 0.0


def _known_names(conn):
    names = set()
    for r in conn.execute("SELECT name FROM employees").fetchall():
        names.update(part for part in re.split(r"[\s-]+", r["name"] or "") if part)
    return names


def record(question):
    """Keep one question the policy does not answer. Returns what happened; never raises on bad input."""
    conn = db.get_conn()
    try:
        topic = scrub(question, _known_names(conn))
        if len(re.sub(r"[^A-Za-zÀ-ÿ]", "", topic)) < 3:
            return {"recorded": False}
        vector = _embed(topic)
        with conn:
            rows = conn.execute("SELECT * FROM policy_gaps ORDER BY id").fetchall()
            scored = [(_closeness(topic, vector, r), r) for r in rows]
            score, row = max(scored, key=lambda pair: pair[0], default=(0.0, None))
            if row is None or score == 0.0:
                cur = conn.execute("INSERT INTO policy_gaps (topic, examples_json, vector_json) VALUES (?, ?, ?)",
                                   (topic, json.dumps([topic]), json.dumps(vector) if vector is not None else None))
                return {"recorded": True, "gap_id": cur.lastrowid, "new": True}
            if row["status"] == "dismissed":                    # HR said the policy need not cover it
                return {"recorded": False, "gap_id": row["id"], "dismissed": True}
            examples = json.loads(row["examples_json"])
            if topic.lower() not in {e.lower() for e in examples} and len(examples) < EXAMPLES_KEPT:
                examples.append(topic)
            if row["status"] == "fixed":                        # marked fixed, and people still get "the policy doesn't say"
                conn.execute("UPDATE policy_gaps SET status = 'open', asks = 1, examples_json = ?, last_asked_at = CURRENT_TIMESTAMP, "
                             "reopened_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps(examples), row["id"]))
                return {"recorded": True, "gap_id": row["id"], "reopened": True}
            conn.execute("UPDATE policy_gaps SET asks = asks + 1, examples_json = ?, last_asked_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (json.dumps(examples), row["id"]))
            return {"recorded": True, "gap_id": row["id"]}
    finally:
        conn.close()


def record_from_turn(searches, reply):
    """Called once an answer is final. The answer is already on its way to the person: nothing here may break it."""
    topic = topic_from_turn(searches, reply)
    if topic is None:
        return None
    try:
        return record(topic)
    except Exception as error:                  # a full disk or a locked database must not become an error for the person
        print(f"  [policy gaps] could not record the question: {error}")
        return None


def open_gaps():
    """For HR: the open gaps, most asked first."""
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM policy_gaps WHERE status = 'open' ORDER BY asks DESC, last_asked_at DESC, id").fetchall()
    conn.close()
    return [{"id": r["id"], "topic": r["topic"], "asks": r["asks"], "examples": json.loads(r["examples_json"]),
             "first_asked_at": r["first_asked_at"], "last_asked_at": r["last_asked_at"], "reopened": bool(r["reopened_at"])}
            for r in rows]


def count_open():
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) FROM policy_gaps WHERE status = 'open'").fetchone()[0]
    conn.close()
    return n


def close(hr_id, gap_id, status, note=""):
    """HR dismisses a gap ('dismissed') or says the policy now answers it ('fixed')."""
    if status not in ("dismissed", "fixed"):
        return {"error": "A question the policy doesn't answer is dismissed or marked fixed."}
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can close a question the policy doesn't answer."}
    conn = db.get_conn()
    try:
        with conn:
            row = conn.execute("SELECT status FROM policy_gaps WHERE id = ?", (gap_id,)).fetchone()
            if row is None:
                return {"error": "No question with that ID."}
            if row["status"] != "open":
                return {"error": "That question is already closed."}
            conn.execute("UPDATE policy_gaps SET status = ?, closed_by = ?, closed_at = CURRENT_TIMESTAMP, closed_note = ? WHERE id = ?",
                         (status, hr_id, (note or "").strip()[:300], gap_id))
    finally:
        conn.close()
    return {"success": True, "status": status}
