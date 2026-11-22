# conversations.py: each person's conversation with the assistant, kept in the database, so any copy of the server can answer any request, a
# restart loses nothing and many people can chat at once. The agent works on one Conversation (agent.convo()); the server opens it here at the
# start of each message and saves it at the end.
#   conversations          one row per conversation: whose (tenant_id, user_id), which assistant (channel), its summary, its cost so far, and
#                          busy_until while a message is being answered (one at a time per conversation)
#   conversation_messages  append-only: exactly what was sent to Claude, tool calls and results included (JSON). Nothing is ever changed or
#                          deleted, so the next request sends the same prefix and the prompt cache keeps working, on any copy
#   pending_actions        the confirmation card: pending -> confirmed, cancelled or expired, each by ONE conditional UPDATE, so a double click
#                          or two copies can never run the same card twice
#   daily_spend            what each person's messages cost today (UTC), added after every message: the daily cap holds across conversations
#                          and copies of the server
# Every query names the tenant AND the user: another person's conversation or card id finds nothing.
# Plain SQL that SQLite and Postgres both run (the Postgres move changes only the id columns and the connection, in db.py).
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from db import get_conn

TENANT = os.getenv("HRM_TENANT", "default")   # one company per deployment for now; every query is scoped by it all the same
CARD_MINUTES = 30      # a card nobody pressed expires: an old card never runs a decision taken on old figures
IDLE_HOURS = 2         # a message after this long starts a new conversation: fresh budget, the earlier chat comes back as the summary (as at sign-in)
BUSY_SECONDS = 180     # a copy that stopped mid-answer frees the conversation after this long (8 model calls fit well inside it)
ASSISTANT, WELCOME = "assistant", "welcome"     # a welcome-link session never shares the person's other conversation


def fresh_usage():
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_write": 0, "cache_read": 0, "check_cost": 0}


def fresh_turn():
    # refused: calls the code refused (not counted as called); earlier_sources: the searches from earlier messages an answer rested on (search_first.py)
    return {"tools_called": [], "sources": [], "refused": [], "policy_searches": [], "earlier_sources": []}


@dataclass
class Conversation:
    """Everything the agent knows about one person's conversation. The web server opens it from the database for each message and saves it
    after; the terminal and the evals keep one in memory."""
    user_id: str
    role: str | None = None          # "new_hire" for someone signed in with a welcome link; None = the role in the database
    session_id: str = ""             # groups the conversation in Langfuse
    messages: list = field(default_factory=list)    # exactly what is sent to Claude, tool calls and results included, as plain dicts
    summary: str | None = None
    last_turn: dict = field(default_factory=fresh_turn)    # what happened in the last answered message: read by the guards, the server and the evals
    pending: dict = field(default_factory=dict)     # the confirmation card: the first item's tool, input and details, plus `kind` and `items` (confirm_batch.py)
    usage: dict = field(default_factory=fresh_usage)
    confirm: object = None           # how a write action is confirmed; None = agent.confirm_with_human (the terminal)
    id: int | None = None            # its row in `conversations`; None when it is only in memory
    tenant: str = TENANT
    channel: str = ASSISTANT
    card_id: int | None = None       # the `pending_actions` row of the card, once saved
    stored: int = 0                  # how many of `messages` are saved already (save() appends the rest)
    restarted: bool = False          # the history was rebuilt from the summary: the model's history starts again from the next saved message


def init_conversations():
    conn = get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                channel TEXT NOT NULL,                  -- 'assistant', or 'welcome' for a welcome-link session
                status TEXT NOT NULL DEFAULT 'active',  -- active, closed
                session_id TEXT NOT NULL,
                summary TEXT,
                history_from INTEGER NOT NULL DEFAULT 0,   -- the first message sent to Claude; the ones before are in the summary
                usage TEXT NOT NULL DEFAULT '{}',
                busy_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # one active conversation per person and assistant: two first messages at once cannot both start one
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS conversations_one_active ON conversations (tenant_id, user_id, channel)
                        WHERE status = 'active'""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id),
                role TEXT NOT NULL,              -- 'user' or 'assistant', as sent to Claude
                content TEXT NOT NULL,           -- JSON: a string, or the blocks (text, tool_use, tool_result)
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS conversation_messages_by_conversation ON conversation_messages (conversation_id, id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id),
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,            -- the tool: apply_leave, cancel_leave, decide_leave, ...
                payload TEXT NOT NULL,           -- JSON: the whole card, every item on it
                status TEXT NOT NULL DEFAULT 'pending',    -- pending, confirmed, cancelled, expired
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                decided_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS pending_actions_by_conversation ON pending_actions (conversation_id, status)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_spend (
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                day TEXT NOT NULL,               -- UTC date, 2026-11-22
                dollars REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (tenant_id, user_id, day)
            )
        """)
    conn.close()


def now_utc():
    return datetime.now(timezone.utc)


def stamp(moment):
    # one text format everywhere, so times compare as text in SQLite and Postgres alike
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _active(conn, tenant, user_id, channel):
    return conn.execute("""SELECT * FROM conversations WHERE tenant_id = ? AND user_id = ? AND channel = ? AND status = 'active'""",
                        (tenant, user_id, channel)).fetchone()


def messages_of(tenant, user_id, conversation_id):
    """The model's history of one conversation, from its history_from on. Empty for a conversation that is not this person's."""
    conn = get_conn()
    rows = conn.execute("""SELECT m.role, m.content FROM conversation_messages m JOIN conversations c ON c.id = m.conversation_id
                           WHERE c.id = ? AND c.tenant_id = ? AND c.user_id = ? AND m.id >= c.history_from ORDER BY m.id""",
                        (conversation_id, tenant, user_id)).fetchall()
    conn.close()
    return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]


def card_of(tenant, user_id, conversation_id, now=None):
    """(id, card) of the card waiting in this person's conversation, or (None, {}) when there is none or it expired."""
    conn = get_conn()
    row = conn.execute("""SELECT id, payload FROM pending_actions WHERE conversation_id = ? AND tenant_id = ? AND user_id = ?
                          AND status = 'pending' AND expires_at > ? ORDER BY id DESC LIMIT 1""",
                       (conversation_id, tenant, user_id, stamp(now or now_utc()))).fetchone()
    conn.close()
    return (row["id"], json.loads(row["payload"])) if row else (None, {})


def open_conversation(user_id, start, channel=ASSISTANT, role=None, tenant=TENANT, session_id=None, now=None):
    """The person's active conversation, taken for one message: None while another request is still answering in it.
    A new conversation when there is none or the last one went idle: start(user_id) gives its (summary, first messages)."""
    now = now or now_utc()
    busy = stamp(now + timedelta(seconds=BUSY_SECONDS))
    conn = get_conn()
    row = _active(conn, tenant, user_id, channel)
    conn.close()
    idle = row is not None and row["updated_at"] < stamp(now - timedelta(hours=IDLE_HOURS))
    if row is not None and not idle:
        conn = get_conn()
        with conn:
            taken = conn.execute("""UPDATE conversations SET busy_until = ? WHERE id = ? AND tenant_id = ? AND user_id = ?
                                    AND (busy_until IS NULL OR busy_until < ?)""", (busy, row["id"], tenant, user_id, stamp(now))).rowcount
        conn.close()
        if not taken:
            return None
        card_id, card = card_of(tenant, user_id, row["id"], now)
        messages = messages_of(tenant, user_id, row["id"])
        return Conversation(user_id, role=role, session_id=row["session_id"], messages=messages, summary=row["summary"], pending=card,
                            usage={**fresh_usage(), **json.loads(row["usage"])}, id=row["id"], tenant=tenant, channel=channel,
                            card_id=card_id, stored=len(messages))

    # a new conversation: the summary call (when there is something to fold in) happens before the database is touched
    summary, messages = start(user_id)
    session_id = session_id or f"{user_id}-{now:%Y%m%d-%H%M%S}"
    conn = get_conn()
    try:
        with conn:
            if row is not None:     # the idle one is closed, unless a request took it meanwhile
                closed = conn.execute("""UPDATE conversations SET status = 'closed', updated_at = ? WHERE id = ? AND tenant_id = ? AND user_id = ?
                                         AND status = 'active' AND (busy_until IS NULL OR busy_until < ?)""",
                                      (stamp(now), row["id"], tenant, user_id, stamp(now))).rowcount
                if not closed:
                    return None
            conn.execute("""UPDATE pending_actions SET status = 'expired' WHERE tenant_id = ? AND user_id = ? AND status = 'pending'
                            AND conversation_id IN (SELECT id FROM conversations WHERE tenant_id = ? AND user_id = ? AND channel = ?)""",
                         (tenant, user_id, tenant, user_id, channel))
            new_id = conn.execute("""INSERT INTO conversations (tenant_id, user_id, channel, session_id, summary, busy_until, created_at, updated_at)
                                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                  (tenant, user_id, channel, session_id, summary, busy,
                                   stamp(now), stamp(now))).lastrowid
    except sqlite3.IntegrityError:      # the one-active index: another request started this person's conversation at the same moment
        return None
    finally:
        conn.close()
    return Conversation(user_id, role=role, session_id=session_id, messages=list(messages), summary=summary,
                        id=new_id, tenant=tenant, channel=channel, stored=0, restarted=True)


def save(c, now=None):
    """After the message: append the new messages, keep the summary and the cost, save a new card, and free the conversation for the next message."""
    moment = now or now_utc()
    now = stamp(moment)
    conn = get_conn()
    with conn:
        first = None
        for m in c.messages[c.stored:]:
            cur = conn.execute("INSERT INTO conversation_messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                               (c.id, m["role"], json.dumps(m["content"], ensure_ascii=False), now))
            first = first or cur.lastrowid
        history = ", history_from = ?" if c.restarted and first else ""
        conn.execute(f"""UPDATE conversations SET summary = ?, usage = ?, updated_at = ?, busy_until = NULL{history}
                         WHERE id = ? AND tenant_id = ? AND user_id = ?""",
                     (c.summary, json.dumps(c.usage), now, *((first,) if history else ()), c.id, c.tenant, c.user_id))
        if c.pending and c.card_id is None:
            c.card_id = conn.execute("""INSERT INTO pending_actions (conversation_id, tenant_id, user_id, action, payload, created_at, expires_at)
                                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                     (c.id, c.tenant, c.user_id, c.pending["tool"], json.dumps(c.pending, ensure_ascii=False), now,
                                      stamp(moment + timedelta(minutes=CARD_MINUTES)))).lastrowid
    conn.close()
    c.stored, c.restarted = len(c.messages), False


def _day(now=None):
    return f"{(now or now_utc()).astimezone(timezone.utc):%Y-%m-%d}"


def spent_today(tenant, user_id, now=None):
    """Dollars this person's messages cost today (UTC), in every conversation."""
    conn = get_conn()
    row = conn.execute("SELECT dollars FROM daily_spend WHERE tenant_id = ? AND user_id = ? AND day = ?",
                       (tenant, user_id, _day(now))).fetchone()
    conn.close()
    return row["dollars"] if row else 0.0


def add_spend(tenant, user_id, dollars, now=None):
    """Add one message's cost to today's total, in ONE statement: two messages finishing at once both count."""
    if dollars <= 0:
        return
    conn = get_conn()
    with conn:
        conn.execute("""INSERT INTO daily_spend (tenant_id, user_id, day, dollars) VALUES (?, ?, ?, ?)
                        ON CONFLICT (tenant_id, user_id, day) DO UPDATE SET dollars = daily_spend.dollars + excluded.dollars""",
                     (tenant, user_id, _day(now), dollars))
    conn.close()


def release(c):
    """The message failed (the API is down, a bug): free the conversation and keep nothing of the half-done turn."""
    conn = get_conn()
    with conn:
        conn.execute("UPDATE conversations SET busy_until = NULL WHERE id = ? AND tenant_id = ? AND user_id = ?", (c.id, c.tenant, c.user_id))
    conn.close()


def drop_card(c, now=None):
    """A new message: the card still waiting expires (the person moved on), as the web app always did."""
    conn = get_conn()
    with conn:
        conn.execute("""UPDATE pending_actions SET status = 'expired', decided_at = ? WHERE conversation_id = ? AND tenant_id = ? AND user_id = ?
                        AND status = 'pending'""", (stamp(now or now_utc()), c.id, c.tenant, c.user_id))
    conn.close()
    c.pending, c.card_id = {}, None


def claim_card(tenant, user_id, card_id, accept, now=None):
    """Confirm or Cancel was pressed. pending -> confirmed (or cancelled) in ONE update, only for this person's card and only before it expires.
    Returns the card to the one request that changed it, and None to every other: a double click, an expired card, someone else's card id."""
    now = stamp(now or now_utc())
    conn = get_conn()
    with conn:
        changed = conn.execute("""UPDATE pending_actions SET status = ?, decided_at = ? WHERE id = ? AND tenant_id = ? AND user_id = ?
                                  AND status = 'pending' AND expires_at > ?""",
                               ("confirmed" if accept else "cancelled", now, card_id, tenant, user_id, now)).rowcount
        row = conn.execute("SELECT payload FROM pending_actions WHERE id = ?", (card_id,)).fetchone() if changed else None
    conn.close()
    return json.loads(row["payload"]) if row else None
