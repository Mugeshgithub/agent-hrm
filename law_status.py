# law_status.py: how up to date the labour-law data is. The nightly job (labour_code/sync.py) records every check in
# law_sync_runs; a law answer ends with "Code du travail (Légifrance), as of <date>", added by code, and HR sees one line
# in "Needs you" when the data is more than a week old. No AI, so it is unit-tested in CI.
import json
import os
import time
from datetime import date

from candidate_assistant import _french
from citations import cited_articles

STALE_AFTER_DAYS = 7
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
CACHE_SECONDS = 600        # the page asks on every load; the date changes once a night


def _read_from_database():
    # the last successful check, or None (no labour database, as in CI, or never synced)
    if not os.environ.get("LABOUR_DB_URL"):
        return None
    try:
        from labour_code.search import as_of, connect
        with connect() as conn:
            return as_of(conn)
    except Exception:
        return None


READ = _read_from_database        # tests replace it
_cache = {"at": 0.0, "value": None}


def last_synced():
    if time.time() - _cache["at"] > CACHE_SECONDS:
        _cache.update(at=time.time(), value=READ())
    return _cache["value"]


def line_for(reply, sources, user_text):
    """The line code adds under a law answer, or None. Only when the reply cites an article that this turn's labour search
    returned; the date comes from that search result, so it is the date of the data that answered."""
    cited = cited_articles(reply)
    for source in sources:
        try:
            result = json.loads(source)
        except (TypeError, ValueError):
            continue
        if isinstance(result, dict) and result.get("as_of") and {a.get("article") for a in result.get("articles") or []} & cited:
            day = date.fromisoformat(result["as_of"])
            if _french(user_text):
                return f"Code du travail (Légifrance), à jour au {day.day} {MONTHS_FR[day.month - 1]} {day.year}."
            return f"Code du travail (Légifrance), as of {day.day} {MONTHS_EN[day.month - 1]} {day.year}."
    return None


def limited_line(sources, user_text):
    """The line code adds under an answer when this turn's labour search was keyword-only (the embedding service was down),
    or None. The search result carries keyword_only (agent.py)."""
    for source in sources:
        try:
            result = json.loads(source)
        except (TypeError, ValueError):
            continue
        if isinstance(result, dict) and result.get("keyword_only"):
            if _french(user_text):
                return "La recherche dans le Code du travail est limitée en ce moment : cette réponse peut être incomplète."
            return "The labour-law search is limited right now, so this answer may be incomplete."
    return None


def hr_notice(today=None):
    """One line for HR's "Needs you" when the data is more than a week old, else None. Worked out on every page load,
    so it also shows when the nightly job stops running."""
    synced = last_synced()
    if synced is None:
        return None
    age = ((today or date.today()) - synced).days
    if age <= STALE_AFTER_DAYS:
        return None
    return (f"The labour-law data was last updated {age} days ago ({synced.day} {MONTHS_EN[synced.month - 1][:3]}). "
            f"The assistant still answers from it. Ask the developer to check the nightly update.")
