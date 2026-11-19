# guard_events.py: one row each time a guardrail in code acts on the agent's reply, so "how often does a guard fire, and does the
# retry fix it?" has an answer. agent.py notes what each guard did during an answer (Turn) and writes the rows once the reply is final.
# guard_report.py shows them per day.
#
# Outcomes:
#   retried_ok       the guard asked the agent again and the new reply passed it
#   retried_unfixed  the guard asked again and the new reply still fails it. Only the guards that ask once and have no fallback
#                    (search_first, booking_first, ...): the reply was sent anyway
#   safe_reply       still failing after the retry: the fixed safe reply was sent instead (citations)
#   replaced         still failing after the retry: code replaced the reply or the sentences at fault (numbers, grounding, "can't tell")
#
# What is kept: when, who (employee id and role), which guard, the outcome, the Langfuse trace id, and a short detail that is never the
# rejected reply: the article numbers for citations (public law), otherwise how many figures or claims failed.
# No AI call and no third-party import here, so it is unit-tested in CI.
import json
import uuid

import db

OUTCOMES = ("retried_ok", "retried_unfixed", "safe_reply", "replaced")


def init_events():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS guard_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,   -- UTC
                turn TEXT NOT NULL,                  -- one id per answer: several guards can act on the same one
                employee_id TEXT,
                role TEXT,
                guard TEXT NOT NULL,                 -- citation, number, grounding, genuine, search, contract, booking, ...
                outcome TEXT NOT NULL,               -- see OUTCOMES
                detail TEXT,                         -- JSON: article numbers, or a count; never the rejected reply
                trace_id TEXT                        -- the Langfuse trace, when tracing is on
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS guard_events_at ON guard_events (at)")
    conn.close()


class Turn:
    """What the guards did during one answer. agent.py calls retried() and ended() as they act, then write() once the reply is final."""

    def __init__(self):
        self.id = uuid.uuid4().hex
        self.events = {}            # guard -> {"outcome", "detail", "recheck"}

    def retried(self, guard, detail=None, recheck=None):
        # recheck: for a guard that asks once and never looks again, a function that says whether the final reply still fails it.
        # Without one, the agent loop checks again itself and calls ended() if the retry did not fix it.
        self.events[guard] = {"outcome": "retried_ok", "detail": detail, "recheck": recheck}

    def ended(self, guard, outcome, detail=None):
        # every guard asks again once before it gives up, so an ended guard was retried first
        self.events[guard] = {"outcome": outcome, "detail": detail, "recheck": None}

    def outcomes(self):
        """{guard: outcome}, the rechecks done. A recheck that fails counts as not fixed: the guard can't say the retry worked."""
        found = {}
        for guard, event in self.events.items():
            outcome = event["outcome"]
            if event["recheck"] is not None:
                try:
                    outcome = "retried_unfixed" if event["recheck"]() else "retried_ok"
                except Exception:
                    outcome = "retried_unfixed"
            found[guard] = outcome
        return found

    def write(self, employee_id, role, trace_id=None):
        """Called once the reply is final; returns {guard: outcome}. The reply is already on its way: nothing here may break it."""
        found = self.outcomes()
        if not found:
            return found
        try:
            conn = db.get_conn()
            try:
                with conn:
                    conn.executemany(
                        "INSERT INTO guard_events (turn, employee_id, role, guard, outcome, detail, trace_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(self.id, employee_id, role, guard, outcome,
                          None if self.events[guard]["detail"] is None else json.dumps(self.events[guard]["detail"]), trace_id)
                         for guard, outcome in found.items()])
            finally:
                conn.close()
        except Exception as error:          # a full disk or a locked database must not become an error for the person
            print(f"  [guard events] could not record {found}: {error}")
        return found


def tags(found):
    """Langfuse tags for an answer the guards acted on: "guard", one per guard and one per outcome."""
    if not found:
        return []
    return ["guard", *sorted(f"guard:{g}" for g in found), *sorted({f"guard:{o}" for o in found.values()})]


def per_day(days=30):
    """For guard_report.py: per day (UTC), the events per guard and outcome, and the rates over all the assistant's answers that day.
    retry rate = answers where a guard asked again / answers; safe-reply rate = answers that ended in the safe reply / answers."""
    conn = db.get_conn()
    since = f"-{int(days)} days"
    rows = conn.execute("""SELECT date(at) AS day, guard, outcome, COUNT(*) AS n FROM guard_events
                           WHERE at >= datetime('now', ?) GROUP BY day, guard, outcome ORDER BY day, guard, outcome""", (since,)).fetchall()
    turns = conn.execute("""SELECT date(at) AS day, COUNT(DISTINCT turn) AS retried,     -- every outcome comes after one retry
                                   COUNT(DISTINCT CASE WHEN outcome = 'safe_reply' THEN turn END) AS safe
                            FROM guard_events WHERE at >= datetime('now', ?) GROUP BY day""", (since,)).fetchall()
    answers = conn.execute("""SELECT date(created_at) AS day, COUNT(*) AS n FROM chat_messages
                              WHERE role = 'assistant' AND created_at >= datetime('now', ?) GROUP BY day""", (since,)).fetchall()
    conn.close()
    report = {}
    for r in rows:
        day = report.setdefault(r["day"], {"guards": {}, "answers": 0, "retried": 0, "safe": 0})
        day["guards"].setdefault(r["guard"], {o: 0 for o in OUTCOMES})[r["outcome"]] = r["n"]
    for r in turns:
        report[r["day"]].update(retried=r["retried"], safe=r["safe"])
    for r in answers:
        if r["day"] in report:
            report[r["day"]]["answers"] = r["n"]
    for day in report.values():
        day["retry_rate"] = day["retried"] / day["answers"] if day["answers"] else None
        day["safe_reply_rate"] = day["safe"] / day["answers"] if day["answers"] else None
    return report
