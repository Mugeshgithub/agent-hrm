# memory.py: saves chat messages so conversations survive restarts
from db import get_conn


def init_memory():
    conn = get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT NOT NULL,
                role TEXT NOT NULL,          -- 'user' or 'assistant'
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.close()


def save_message(employee_id, role, content):
    conn = get_conn()
    with conn:
        conn.execute(
            "INSERT INTO chat_messages (employee_id, role, content) VALUES (?, ?, ?)",
            (employee_id, role, content),
        )
    conn.close()


def load_recent(employee_id, limit=10):
    # newest first from the database, then flipped back into time order
    conn = get_conn()
    rows = conn.execute(
        """SELECT role, content FROM chat_messages
           WHERE employee_id = ? ORDER BY id DESC LIMIT ?""",
        (employee_id, limit),
    ).fetchall()
    conn.close()

    messages = []
    for r in reversed(rows):
        content = r["content"]
        if r["role"] == "assistant":
            # label old replies so the model never reuses their numbers as facts
            content = ("[Saved reply from earlier. Its numbers may be wrong or out of date. "
                       "Do not reuse them; call the tools again.]\n" + content)
        messages.append({"role": r["role"], "content": content})

    # Claude needs the history to start with a user message
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


SUMMARY_PROMPT = """Summarise this HR assistant conversation in under 80 words.
Keep: what the employee wants, plans they mentioned, unresolved questions.
Do NOT include leave balances, request statuses or numbers - those change and are looked up fresh.
Do NOT include passwords, secret words or other sensitive personal details."""


def init_summaries():
    conn = get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_summaries (
                employee_id TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                last_message_id INTEGER NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.close()


def build_context(employee_id, client, model, keep=10):
    """Returns (summary_text_or_None, recent_messages)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, role, content FROM chat_messages WHERE employee_id = ? ORDER BY id",
        (employee_id,),
    ).fetchall()
    saved = conn.execute(
        "SELECT summary, last_message_id FROM chat_summaries WHERE employee_id = ?",
        (employee_id,),
    ).fetchone()
    conn.close()

    summary = saved["summary"] if saved else None
    last_done = saved["last_message_id"] if saved else 0

    # everything except the last few messages is "older"
    older = rows[:-keep] if len(rows) > keep else []
    # only the older messages not already folded into the summary
    new_older = [r for r in older if r["id"] > last_done]

    print(f"(Memory: {len(rows)} saved messages, {len(new_older)} new ones to summarise)")

    # only call the model when there are new old messages to fold in
    if new_older:
        transcript = "\n".join(f"{r['role']}: {r['content']}" for r in new_older)
        text = f"Previous summary: {summary}\n\nNew messages:\n{transcript}" if summary else transcript
        response = client.messages.create(
            model=model,
            max_tokens=200,
            system=SUMMARY_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        summary = "".join(b.text for b in response.content if b.type == "text")
        print(f"(Summarised {len(new_older)} older messages)")

        conn = get_conn()
        with conn:
            conn.execute(
                """INSERT INTO chat_summaries (employee_id, summary, last_message_id)
                   VALUES (?, ?, ?)
                   ON CONFLICT(employee_id) DO UPDATE SET
                     summary = excluded.summary,
                     last_message_id = excluded.last_message_id,
                     updated_at = CURRENT_TIMESTAMP""",
                (employee_id, summary, new_older[-1]["id"]),
            )
        conn.close()

    return summary, load_recent(employee_id, keep)