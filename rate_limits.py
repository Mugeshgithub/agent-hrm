# rate_limits.py: at most `limit` tries per `seconds` for a key (a person, an address, a link), counted in the database, so every copy of the
# server counts the same tries and a restart forgets none. Each try is one row in rate_events; rows older than the window are deleted as it goes.
import time

from db import get_conn


def init_rate_limits():
    conn = get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bucket TEXT NOT NULL,      -- which limit: assistant_by_person, sign_in_by_address, ...
                key TEXT NOT NULL,         -- who: a person's id, an address, a link
                at REAL NOT NULL           -- seconds since 1970
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS rate_events_by_key ON rate_events (bucket, key, at)")
    conn.close()


class Throttle:
    def __init__(self, bucket, limit, seconds):
        self.bucket, self.limit, self.seconds = bucket, limit, seconds

    def allow(self, key, now=None):
        """Counts this try and says yes, or says no and counts nothing (so asking again does not push the wait back).
        The count and the insert are ONE statement: two copies cannot both take the last try."""
        now = time.time() if now is None else now
        conn = get_conn()
        with conn:
            conn.execute("DELETE FROM rate_events WHERE bucket = ? AND key = ? AND at <= ?", (self.bucket, str(key), now - self.seconds))
            counted = conn.execute("""INSERT INTO rate_events (bucket, key, at)
                                      SELECT ?, ?, ? WHERE (SELECT COUNT(*) FROM rate_events WHERE bucket = ? AND key = ? AND at > ?) < ?""",
                                   (self.bucket, str(key), now, self.bucket, str(key), now - self.seconds, self.limit)).rowcount
        conn.close()
        return counted == 1

    def clear(self):
        """Forget every try of this limit (the tests start each one from nothing)."""
        conn = get_conn()
        with conn:
            conn.execute("DELETE FROM rate_events WHERE bucket = ?", (self.bucket,))
        conn.close()

    def wait(self, key, now=None):
        """Seconds until `key` may try again: 0 when it may now."""
        now = time.time() if now is None else now
        conn = get_conn()
        recent = [r["at"] for r in conn.execute("SELECT at FROM rate_events WHERE bucket = ? AND key = ? AND at > ? ORDER BY at",
                                                (self.bucket, str(key), now - self.seconds))]
        conn.close()
        return 0 if len(recent) < self.limit else recent[len(recent) - self.limit] + self.seconds - now
