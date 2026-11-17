# labour_code/sync.py: the nightly update of the labour code from Légifrance. Run by a scheduler (cron), not by people.
#   python labour_code/sync.py           -> fetch, clean, build the new data beside the live table, check it, switch in one step
#   python labour_code/sync.py approve   -> switch to the data a "review" night built and tested (more than 50 articles moved)
# Example crontab line (02:30 every night; alerts are printed in the log):
#   30 2 * * *  cd /path/to/agent-hrm && python labour_code/sync.py >> labour_code/sync.log 2>&1
# A search never sees half an update: the new code is built whole in <live>_next (so repealed articles are simply not in it)
# and swapped in with one transaction, only after: the spot and size checks; the recall questions finding their articles at
# least as often as on the live data (no more than 2 set aside); every new or changed article found from its own opening.
# Haiku also writes one employee question per new or changed article; those that find their article are saved to
# generated_questions.jsonl for a person to review (never added to the suite by themselves). More than 50 articles moved:
# built and tested, then it waits for `approve`. A held, failed or review night alerts the developer (the log, and a GitHub
# issue when LAW_SYNC_GITHUB_TOKEN and LAW_SYNC_GITHUB_REPO are set), never HR: HR only hears when the data is over a week old
# (law_status.py).
# The live table is search.TABLE: "chunks", or LABOUR_TABLE=... for a test copy. Plan: docs/plan-law-sync.md.
# .env: PISTE_CLIENT_ID, PISTE_CLIENT_SECRET, PISTE_ENV (sandbox | production); optional LAW_SYNC_GITHUB_TOKEN (fine-grained,
# Issues read/write on this repository only) and LAW_SYNC_GITHUB_REPO (owner/name).
import hashlib
import json
import os
import re
import sys
import types
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # its neighbours, also when imported from the repo root (tests)
import chunk
import legifrance
from retrieval_cases import CASES, HOLDOUT, SITUATIONS

SETS = {"tuning": CASES, "held-out": HOLDOUT, "situations": SITUATIONS}
HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_QUERIES = os.path.join(HERE, "agent_queries.json")
GENERATED = os.path.join(HERE, "generated_questions.jsonl")    # git-ignored: a person reviews it and copies good ones into retrieval_cases.py
TOP = 5                          # the agent gets the 5 best articles
MAX_SIZE_CHANGE = 0.02           # articles in force may change by 2% between two applied syncs
FIRST_SYNC_MAX_SHRINK = 0.02     # the first sync holds when the new data has 2% fewer articles than the live table
MAX_ASIDE = 2                    # more recall questions set aside than this: their articles may be missing, not repealed
REVIEW_ABOVE = 50                # more articles added, changed or removed than this in one night: built and tested, switched on approval
MAX_NEW_QUESTIONS = 50           # questions Haiku writes per night; articles left over get theirs on the next nights (free once written)
OPENING_AT_LEAST = 150           # the self-check searches with an article's opening sentences, at least this many characters
SELF_CHECK_MISSES = (2, 0.05)    # hold above 2 misses or 5% of those checked, whichever is more. Healthy data misses some: on the
                                 # 2,022 new or changed articles of Nov 2026, 47 (2.3%) had openings too generic to single them out
                                 # ("Le préfet soumet pour avis…"). Broken storage (text not indexed, vectors on the wrong rows) misses far more
HAIKU_IN, HAIKU_OUT, EMBED = 1.0, 5.0, 0.02      # dollars per million tokens
CACHE_DAYS = 90
LOCK = 4615                      # pg_try_advisory_lock key: one sync at a time
TABLES = """
CREATE TABLE IF NOT EXISTS law_sync_runs (
    id              serial PRIMARY KEY,
    ran_at          timestamptz NOT NULL DEFAULT now(),
    live_table      text NOT NULL,
    legifrance_date date,
    env             text,
    result          text NOT NULL,          -- applied | no change | review | held | failed
    reason          text NOT NULL DEFAULT '',
    articles        int,
    added           int,
    changed         int,
    removed         int,
    recall          jsonb,
    cost_usd        numeric(10, 4) NOT NULL DEFAULT 0
);
ALTER TABLE law_sync_runs ADD COLUMN IF NOT EXISTS checks jsonb;         -- self-check and generated questions
ALTER TABLE law_sync_runs ADD COLUMN IF NOT EXISTS costs jsonb;          -- tonight's cost, step by step
ALTER TABLE law_sync_runs ADD COLUMN IF NOT EXISTS fingerprint text;     -- what a review night built: approve switches only that
-- the recall check's model answers: the same request at temperature 0 gets the same answer, so it is paid for once
CREATE TABLE IF NOT EXISTS law_sync_cache (key text PRIMARY KEY, answer text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
"""
SENTENCE_END = re.compile(r"(?<=[.;:])\s+(?=[A-ZÀ-Ý«\"(]|\d+°|[IVX]+\.)")   # before a capital, "1°" or "II."; not inside "article L. 1225-4"
QUESTION_PROMPT = (
    "You write test questions for a search engine over the French Code du travail. You get the text of one article. "
    "Write ONE question that an employee in France could ask in plain English, in their own words, and that this article answers. "
    "No article number, no legal jargon, at most 25 words. Output only the question."
)


# ---------- the decisions, kept apart from the database so CI tests them ----------

def spot_verdict(articles):
    texts = {a["num"]: a["texte"] for a in articles}
    bad = [num for num, words in legifrance.SPOT_CHECKS.items() if words not in texts.get(num, "")]
    return f"spot check failed on {', '.join(bad)}: the words every HR person knows are missing" if bad else ""


def size_verdict(previous, now):
    if not previous:
        return ""                                # the first sync replaces the old copy: nothing to compare with
    change = abs(now - previous) / previous
    if change > MAX_SIZE_CHANGE:
        return f"articles in force changed by {change:.1%} ({previous} -> {now}), more than {MAX_SIZE_CHANGE:.0%} in one go"
    return ""


def set_aside(nums):
    # a question whose expected article is no longer in the code cannot be found anywhere: left out, and named for a person
    return [q for cases in SETS.values() for q, _, expected in cases if not any(e in nums for e in expected.split("|"))]


def aside_verdict(aside):
    # one or two lost questions are a real repeal to update the test for; more look like missing data
    if len(aside) > MAX_ASIDE:
        return f"{len(aside)} recall questions lost their article (more than {MAX_ASIDE}): articles may be missing from the new data"
    return ""


def first_sync_verdict(live_count, new_count):
    # the first sync has no earlier sync to compare sizes with: the live table (the old copy) is the yardstick
    shrink = (live_count - new_count) / live_count if live_count else 0
    if shrink >= FIRST_SYNC_MAX_SHRINK:
        return f"first sync: the new data has {shrink:.1%} fewer articles than the live table ({live_count} -> {new_count})"
    return ""


def recall_verdict(live, new):
    worse = [f"{name} {live[name]['hits']}/{live[name]['of']} -> {new[name]['hits']}/{new[name]['of']}"
             for name in live if new[name]["hits"] < live[name]["hits"]]
    return f"recall dropped: {', '.join(worse)}" if worse else ""


def review_verdict(numbers):
    moved = numbers["added"] + numbers["changed"] + numbers["removed"]
    if moved > REVIEW_ABOVE:
        return f"{moved} articles added, changed or removed (more than {REVIEW_ABOVE}): review, then run `python labour_code/sync.py approve`"
    return ""


def opening(text):
    # the first sentences, at least OPENING_AT_LEAST characters: one short sentence ("Le montant de l'aide est de 305 euros.")
    # fits dozens of articles, so alone it would fail articles that are stored perfectly well
    out = ""
    for part in SENTENCE_END.split(text):
        out = f"{out} {part}".strip()
        if len(out) >= OPENING_AT_LEAST:
            break
    return out[:400]


def self_check(todo, articles, find):
    """Each new or changed article must come back in the top 5 when its own opening is searched (find: text -> article
    numbers in order). An opening that other articles share word for word cannot single one out: counted, not checked."""
    shared = Counter(opening(a["texte"]) for a in articles)
    checkable = [a for a in todo if shared[opening(a["texte"])] == 1]
    missed = [a["num"] for a in checkable if a["num"] not in find(opening(a["texte"]))[:TOP]]
    return {"checked": len(checkable), "not found": missed, "shared opening": len(todo) - len(checkable)}


def self_check_verdict(result):
    missed, allowed = result["not found"], max(SELF_CHECK_MISSES[0], int(SELF_CHECK_MISSES[1] * result["checked"]))
    if len(missed) > allowed:
        return (f"self-check: {len(missed)} of {result['checked']} new or changed articles not found from their own opening "
                f"(more than {allowed}): {', '.join(missed[:10])}")
    return ""


def usable_question(text):
    # one plain question, no article number (it would find the article by its number, not by its meaning)
    question = " ".join((text or "").split()).strip("\"“” ")
    if not question.endswith("?") or len(question) > 250 or re.search(r"\b[LRD]\.?\s?\d{3,4}", question):
        return None
    return question


def save_generated(passing, path=GENERATED):
    # append the questions that found their article, once each; returns how many are new in the file
    known = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            known = {(d["article_id"], d["question"]) for d in map(json.loads, f)}
    fresh = [p for p in passing if (p["article_id"], p["question"]) not in known]
    with open(path, "a", encoding="utf-8") as f:
        for p in fresh:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    return len(fresh)


def changes(old, new):
    # old, new: {article number: text}
    changed = [n for n in new.keys() & old.keys() if not legifrance.same_words(new[n], old[n])]
    return {"added": len(new.keys() - old.keys()), "changed": len(changed), "removed": len(old.keys() - new.keys())}


def cost(usage):
    return round((usage["haiku_in"] * HAIKU_IN + usage["haiku_out"] * HAIKU_OUT + usage["embed"] * EMBED) / 1_000_000, 4)


def cost_since(usage, before):
    # what one step of the night cost: the usage it added
    return cost({k: usage[k] - before[k] for k in usage})


def github_issue(title, body, resolved=False, http=None):
    """One open issue labelled law-sync at a time: open one, or comment on the open one; a good night closes it.
    Returns what it did. The token is a fine-grained one with Issues: read and write on this repository only."""
    token, repo = os.environ.get("LAW_SYNC_GITHUB_TOKEN"), os.environ.get("LAW_SYNC_GITHUB_REPO")
    if not (token and repo):
        return "logged only (no LAW_SYNC_GITHUB_TOKEN / LAW_SYNC_GITHUB_REPO in .env)"
    if http is None:
        import httpx as http
    api = f"https://api.github.com/repos/{repo}/issues"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    found = http.get(api, params={"labels": "law-sync", "state": "open"}, headers=headers, timeout=30)
    found.raise_for_status()
    open_issues = found.json()
    if resolved:
        for issue in open_issues:
            http.post(f"{api}/{issue['number']}/comments", json={"body": body}, headers=headers, timeout=30).raise_for_status()
            http.patch(f"{api}/{issue['number']}", json={"state": "closed"}, headers=headers, timeout=30).raise_for_status()
        return f"closed #{', #'.join(str(i['number']) for i in open_issues)}" if open_issues else "no open issue"
    if open_issues:
        number = open_issues[0]["number"]
        http.post(f"{api}/{number}/comments", json={"body": f"**{title}**\n\n{body}"}, headers=headers, timeout=30).raise_for_status()
        return f"commented on #{number}"
    made = http.post(api, json={"title": title, "body": body, "labels": ["law-sync"]}, headers=headers, timeout=30)
    made.raise_for_status()
    return f"opened #{made.json()['number']}"


# ---------- the database side ----------

def cache_key(request):
    return "claude:" + hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class CachedClaude:
    """search.claude during the recall check: each distinct request is asked once and its answer kept (law_sync_cache).
    Rewrites of the 84 questions, and reranks whose candidates did not change, cost nothing after the first night."""

    def __init__(self, real, conn, usage):
        self.real, self.conn, self.usage = real, conn, usage
        self.messages, self.asked, self.from_cache, self.failed = self, 0, 0, 0

    def create(self, **request):
        key = cache_key(request)
        hit = self.conn.execute("SELECT answer FROM law_sync_cache WHERE key = %s", (key,)).fetchone()
        if hit:
            self.from_cache += 1
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=hit["answer"])])
        try:
            resp = self.real.messages.create(**request)
        except Exception:
            self.failed += 1                     # search falls back to its own order; the night is then held (see check)
            raise
        self.asked += 1
        self.usage["haiku_in"] += resp.usage.input_tokens
        self.usage["haiku_out"] += resp.usage.output_tokens
        answer = "".join(b.text for b in resp.content if b.type == "text")
        self.conn.execute("INSERT INTO law_sync_cache (key, answer) VALUES (%s, %s) ON CONFLICT DO NOTHING", (key, answer))
        return resp


def recall(conn, search, table, aside):
    # the production path ("user+agent"), top 5, on every test question that can still be found
    queries = json.load(open(AGENT_QUERIES, encoding="utf-8"))
    search.TABLE = table
    scores = {}
    for name, cases in SETS.items():
        asked = [(q, expected) for q, _, expected in cases if q not in aside]
        hits = 0
        for question, expected in asked:
            first = {}
            for row in search.search_for_agent(conn, question, queries[question]):
                first.setdefault(row["num"], row)
            hits += any(e in list(first)[:TOP] for e in expected.split("|"))
        scores[name] = {"hits": hits, "of": len(asked)}
    return scores


def cached_embedding(conn, search, usage):
    # the questions' vectors never change: asked once, kept with the model answers
    def embed(text):
        key = "embed:" + hashlib.sha256(text.encode()).hexdigest()
        hit = conn.execute("SELECT answer FROM law_sync_cache WHERE key = %s", (key,)).fetchone()
        if hit:
            return hit["answer"]
        resp = search.client.embeddings.create(model=search.MODEL, input=[text])
        usage["embed"] += resp.usage.total_tokens
        vector = str(resp.data[0].embedding)
        conn.execute("INSERT INTO law_sync_cache (key, answer) VALUES (%s, %s) ON CONFLICT DO NOTHING", (key, vector))
        return vector
    return embed


def prefill_embeddings(conn, search, texts, usage):
    # many texts at once (the self-check's openings: 2,000 on the first sync), 100 per request, into the same cache embed() reads
    keys = {t: "embed:" + hashlib.sha256(t.encode()).hexdigest() for t in dict.fromkeys(texts)}
    have = {r["key"] for r in conn.execute("SELECT key FROM law_sync_cache WHERE key = ANY(%s)", (list(keys.values()),)).fetchall()}
    missing = [t for t, k in keys.items() if k not in have]
    for start in range(0, len(missing), 100):
        batch = missing[start:start + 100]
        resp = search.client.embeddings.create(model=search.MODEL, input=batch)
        usage["embed"] += resp.usage.total_tokens
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO law_sync_cache (key, answer) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            [(keys[t], str(e.embedding)) for t, e in zip(batch, resp.data)])


def top_articles(rows):
    first = {}
    for row in rows:
        first.setdefault(row["num"], row)
    return list(first)[:TOP]


def run_self_check(conn, search, todo, articles, usage):
    # meaning + keywords on the new table, no model: does each new or changed article come back from its own opening?
    prefill_embeddings(conn, search, [opening(a["texte"]) for a in todo], usage)
    return self_check(todo, articles, lambda text: top_articles(search.hybrid_search(conn, text, 20)))


def question_request(article):
    # the text only, never the number: a question that names the article would find it by its number
    return {"model": "claude-haiku-4-5", "max_tokens": 80, "temperature": 0, "system": QUESTION_PROMPT,
            "messages": [{"role": "user", "content": article["texte"][:3000]}]}


def generate_questions(conn, search, todo, today):
    """Haiku writes one employee question per new or changed article (cached: written once per article version), and the
    production search must bring the article back in the top 5. Those that pass go to GENERATED for a person to review."""
    have = {r["key"] for r in conn.execute("SELECT key FROM law_sync_cache WHERE key = ANY(%s)",
                                           ([cache_key(question_request(a)) for a in todo],)).fetchall()}
    written = [a for a in todo if cache_key(question_request(a)) in have]
    waiting = [a for a in todo if cache_key(question_request(a)) not in have]
    chosen = written + waiting[:MAX_NEW_QUESTIONS]
    passing, failing = [], []
    for a in chosen:
        resp = search.claude.messages.create(**question_request(a))
        question = usable_question("".join(b.text for b in resp.content if b.type == "text"))
        if question and a["num"] in top_articles(search.search_for_agent(conn, question, question)):
            passing.append({"question": question, "lang": "en", "expected": a["num"], "article_id": a["id"], "written_on": today.isoformat()})
        else:
            failing.append(a["num"])
    return {"written": len(chosen), "found": len(passing), "not found": failing,
            "saved": save_generated(passing), "left for later nights": len(waiting) - min(len(waiting), MAX_NEW_QUESTIONS)}


def fingerprint(conn, table):
    row = conn.execute(f"SELECT md5(string_agg(text_hash, ',' ORDER BY chunk_id)) AS f FROM {table}").fetchone()
    return row["f"]


def rename(conn, old, new):
    # a table and its indexes (chunks_embedding_idx -> chunks_prev_embedding_idx), so tomorrow's chunks_next can be made again
    for row in conn.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", (old,)).fetchall():
        if row["indexname"].startswith(old + "_"):
            conn.execute(f"ALTER INDEX {row['indexname']} RENAME TO {new}{row['indexname'][len(old):]}")
    conn.execute(f"ALTER TABLE {old} RENAME TO {new}")


def switch(conn, live, nxt, prev):
    # one transaction: a search sees the old data or the new, never half; the old data is kept as <live>_prev for a rollback by hand
    conn.execute(f"DROP TABLE IF EXISTS {prev}")
    rename(conn, live, prev)
    rename(conn, nxt, live)
    conn.commit()


def record(conn, search, night, result, reason="", numbers=None):
    # one row per night (its own transaction), the log, and the developer's alert
    conn.rollback()                              # whatever a failed step left open
    numbers = numbers or {}
    costs = {**night.costs, "total": cost(night.usage)}
    conn.execute("""INSERT INTO law_sync_runs (live_table, legifrance_date, env, result, reason, articles, added, changed, removed,
                                             recall, cost_usd, checks, costs, fingerprint)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                 (night.live, night.data_date if numbers else None, night.env, result, reason, numbers.get("articles"), numbers.get("added"),
                  numbers.get("changed"), numbers.get("removed"), json.dumps(numbers["recall"]) if numbers.get("recall") else None,
                  costs["total"], json.dumps(night.checks) if night.checks else None, json.dumps(costs), night.fingerprint))
    conn.commit()
    report(conn, night, result, reason, numbers, costs)
    return result


def nightly(conn, search, night, fetch):
    from load_api import current_articles, store, text_hash
    try:
        code = (fetch or legifrance.fetch_code)(night.today)
    except Exception as e:
        return record(conn, search, night, "failed", f"Légifrance did not answer ({type(e).__name__}); the live data is unchanged")
    articles, _, _, _ = legifrance.articles_from(code, night.today)
    if not articles:
        return record(conn, search, night, "failed", "Légifrance answered with no article in force; the live data is unchanged")
    chunks = [c for a in articles for c in chunk.chunks_for(a)]
    live_texts = current_articles(conn, night.live)
    numbers = {"articles": len(articles), **changes(live_texts, {a["num"]: a["texte"] for a in articles})}
    todo = [a for a in articles if a["num"] not in live_texts or not legifrance.same_words(a["texte"], live_texts[a["num"]])]

    live_hashes = {r["h"] for r in conn.execute(
        f"SELECT encode(sha256(convert_to(search_text, 'UTF8')), 'hex') AS h FROM {night.live}").fetchall()}
    if live_hashes == {text_hash(c["search_text"]) for c in chunks}:
        return record(conn, search, night, "no change", numbers=numbers)

    last = conn.execute("""SELECT articles FROM law_sync_runs WHERE live_table = %s AND result = 'applied'
                           ORDER BY id DESC LIMIT 1""", (night.live,)).fetchone()
    reason = spot_verdict(articles) or (size_verdict(last["articles"], len(articles)) if last
                                        else first_sync_verdict(len(live_texts), len(articles)))
    if reason:
        return record(conn, search, night, "held", reason, numbers)

    before = dict(night.usage)
    _, _, tokens = store(conn, chunks, articles, table=night.nxt, reuse_from=(night.nxt, night.live))   # the whole new code, beside the live one
    night.usage["embed"] += tokens
    night.costs["embeddings (new data)"] = cost_since(night.usage, before)
    night.fingerprint = fingerprint(conn, night.nxt)

    aside = set_aside({a["num"] for a in articles})
    real_claude, real_embed = search.claude, search.embed
    search.claude, search.embed = CachedClaude(real_claude, conn, night.usage), cached_embedding(conn, search, night.usage)
    try:
        before = dict(night.usage)
        numbers["recall"] = {"live": recall(conn, search, night.live, aside), "new": recall(conn, search, night.nxt, aside), "set aside": aside}
        night.costs["recall check"] = cost_since(night.usage, before)
        before = dict(night.usage)
        search.TABLE = night.nxt
        night.checks["self-check"] = run_self_check(conn, search, todo, articles, night.usage)
        night.costs["self-check"] = cost_since(night.usage, before)
        before = dict(night.usage)
        night.checks["generated questions"] = generate_questions(conn, search, todo, night.today)
        night.costs["generated questions"] = cost_since(night.usage, before)
        failed, asked, cached = search.claude.failed, search.claude.asked, search.claude.from_cache
    finally:
        search.claude, search.embed, search.TABLE = real_claude, real_embed, night.live
    conn.commit()                                # the cached answers
    numbers["model answers"] = f"{asked} asked, {cached} from cache"
    if failed:
        return record(conn, search, night, "held", f"the checks could not run: {failed} model calls failed", numbers)
    reason = (aside_verdict(aside) or recall_verdict(numbers["recall"]["live"], numbers["recall"]["new"])
              or self_check_verdict(night.checks["self-check"]))
    if reason:
        return record(conn, search, night, "held", reason, numbers)
    reason = review_verdict(numbers)
    if reason:
        return record(conn, search, night, "review", reason, numbers)   # built and tested: switched only by `approve`

    switch(conn, night.live, night.nxt, night.prev)
    return record(conn, search, night, "applied", numbers=numbers)


def start(search, today):
    live = search.TABLE
    return types.SimpleNamespace(live=live, nxt=f"{live}_next", prev=f"{live}_prev", env=os.environ.get("PISTE_ENV", "sandbox"),
                                 today=today, data_date=today, usage={"haiku_in": 0, "haiku_out": 0, "embed": 0},
                                 costs={}, checks={}, fingerprint=None)


def locked(conn, work):
    # one sync (or approval) at a time; any surprise is recorded as failed, with the live data unchanged
    conn.execute(TABLES)
    conn.execute("DELETE FROM law_sync_cache WHERE created_at < now() - make_interval(days => %s)", (CACHE_DAYS,))
    conn.commit()
    if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK,)).fetchone()["ok"]:
        print("Another sync is running: nothing done.")
        return "skipped"
    try:
        return work()
    finally:
        conn.rollback()
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))
        conn.commit()


def run(today=None, fetch=None):
    import search
    night = start(search, today or date.today())
    with search.connect() as conn:
        def work():
            try:
                return nightly(conn, search, night, fetch)
            except Exception as e:               # e.g. the embedding service is down: recorded and alerted, the live data is unchanged
                return record(conn, search, night, "failed", f"{type(e).__name__}: {str(e)[:200]}; the live data is unchanged")
        return locked(conn, work)


def approve(today=None):
    """Switch to what the last "review" night built and tested, if that is still exactly what waits in <live>_next."""
    import search
    night = start(search, today or date.today())
    with search.connect() as conn:
        def work():
            last = conn.execute("SELECT * FROM law_sync_runs WHERE live_table = %s ORDER BY id DESC LIMIT 1", (night.live,)).fetchone()
            if not last or last["result"] != "review":
                print(f"Nothing waits for approval: the last sync was {last['result'] if last else 'never run'}.")
                return "nothing to approve"
            exists = conn.execute("SELECT to_regclass(%s) AS t", (night.nxt,)).fetchone()["t"]
            if not exists or fingerprint(conn, night.nxt) != last["fingerprint"]:
                print("The data waiting is not what the review night tested: run the sync again, then approve.")
                return "nothing to approve"
            night.data_date, night.fingerprint = last["legifrance_date"], last["fingerprint"]
            switch(conn, night.live, night.nxt, night.prev)
            numbers = {k: last[k] for k in ("articles", "added", "changed", "removed")}
            return record(conn, search, night, "applied", f"approved by hand after review (sync run {last['id']})", numbers)
        return locked(conn, work)


def report(conn, night, result, reason, numbers, costs):
    # the log every night, and the developer's alert on a held, failed or review night
    good = conn.execute("""SELECT legifrance_date FROM law_sync_runs WHERE live_table = %s AND result IN ('applied', 'no change')
                           ORDER BY id DESC LIMIT 1""", (night.live,)).fetchone()
    last_good = good["legifrance_date"] if good else None
    extra = sum(costs.get(k, 0) for k in ("self-check", "generated questions"))
    lines = [f"Labour code sync {night.today}: {result}" + (f" ({reason})" if reason else ""),
             f"  articles {numbers.get('articles', '-')}, added {numbers.get('added', '-')}, changed {numbers.get('changed', '-')}, "
             f"removed {numbers.get('removed', '-')}",
             f"  cost tonight ${costs['total']:.4f}" + "".join(f", {k} ${v:.4f}" for k, v in costs.items() if k != "total")
             + (f" (the new checks: ${extra:.4f})" if extra else "")
             + (f"; model answers {numbers['model answers']}" if numbers.get("model answers") else "")]
    if numbers.get("recall"):
        for name, live_score in numbers["recall"]["live"].items():
            new_score = numbers["recall"]["new"][name]
            lines.append(f"  recall {name}: live {live_score['hits']}/{live_score['of']}, new {new_score['hits']}/{new_score['of']}")
        if numbers["recall"]["set aside"]:
            lines.append(f"  set aside (expected article no longer in the code, update the test): {numbers['recall']['set aside']}")
    if (check := night.checks.get("self-check")):
        lines.append(f"  self-check: {check['checked'] - len(check['not found'])}/{check['checked']} new or changed articles found from their "
                     f"opening ({check['shared opening']} share their opening with another article: not checked)"
                     + (f"; not found: {', '.join(check['not found'][:20])}" if check["not found"] else ""))
    if (gen := night.checks.get("generated questions")):
        lines.append(f"  generated questions: {gen['found']}/{gen['written']} found their article, {gen['saved']} new in "
                     f"{os.path.basename(GENERATED)} for review; {gen['left for later nights']} articles wait for later nights"
                     + (f"; not found: {', '.join(gen['not found'][:20])}" if gen["not found"] else ""))
    lines.append(f"  last good sync: {last_good or 'never'}" + (f" ({(night.today - last_good).days} days ago)" if last_good else ""))
    print("\n".join(lines))
    try:
        if result in ("held", "failed", "review"):
            title = f"Labour code sync {'waits for your approval' if result == 'review' else result}: {reason}"[:120]
            body = "```\n" + "\n".join(lines) + "\n```"
            print(f"  [ALERT] {github_issue(title, body)}")
        else:
            outcome = github_issue("", f"Fixed: the sync of {night.today} was {result}.", resolved=True)
            if outcome.startswith("closed"):
                print(f"  {outcome}")
    except Exception as e:
        print(f"  [ALERT] the GitHub issue could not be written ({type(e).__name__}); this log is the alert")


if __name__ == "__main__":
    if sys.argv[1:] == ["approve"]:
        sys.exit(0 if approve() == "applied" else 1)
    sys.exit(0 if run() in ("applied", "no change", "review", "skipped") else 1)
