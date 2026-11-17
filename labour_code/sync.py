# labour_code/sync.py: the nightly update of the labour code from Légifrance. Run by a scheduler (cron), not by people.
#   python labour_code/sync.py      -> fetch, clean, build the new data beside the live table, check it, switch in one step
# Example crontab line (02:30 every night; alerts are printed in the log):
#   30 2 * * *  cd /path/to/agent-hrm && python labour_code/sync.py >> labour_code/sync.log 2>&1
# A search never sees half an update: the new code is built whole in <live>_next (so repealed articles are simply not in it)
# and swapped in with one transaction, only after the recall questions find their articles at least as often as on the live
# data. A held or failed night alerts the developer (the log, and a GitHub issue when LAW_SYNC_GITHUB_TOKEN and
# LAW_SYNC_GITHUB_REPO are set), never HR: HR only hears when the data is over a week old (law_status.py).
# The live table is search.TABLE: "chunks", or LABOUR_TABLE=... for a test copy. Plan: docs/plan-law-sync.md.
# .env: PISTE_CLIENT_ID, PISTE_CLIENT_SECRET, PISTE_ENV (sandbox | production); optional LAW_SYNC_GITHUB_TOKEN (fine-grained,
# Issues read/write on this repository only) and LAW_SYNC_GITHUB_REPO (owner/name).
import hashlib
import json
import os
import sys
import types
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # its neighbours, also when imported from the repo root (tests)
import chunk
import legifrance
from retrieval_cases import CASES, HOLDOUT, SITUATIONS

SETS = {"tuning": CASES, "held-out": HOLDOUT, "situations": SITUATIONS}
AGENT_QUERIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_queries.json")
TOP = 5                          # the agent gets the 5 best articles
MAX_SIZE_CHANGE = 0.02           # articles in force may change by 2% between two applied syncs
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
    result          text NOT NULL,          -- applied | no change | held | failed
    reason          text NOT NULL DEFAULT '',
    articles        int,
    added           int,
    changed         int,
    removed         int,
    recall          jsonb,
    cost_usd        numeric(10, 4) NOT NULL DEFAULT 0
);
-- the recall check's model answers: the same request at temperature 0 gets the same answer, so it is paid for once
CREATE TABLE IF NOT EXISTS law_sync_cache (key text PRIMARY KEY, answer text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
"""


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


def recall_verdict(live, new):
    worse = [f"{name} {live[name]['hits']}/{live[name]['of']} -> {new[name]['hits']}/{new[name]['of']}"
             for name in live if new[name]["hits"] < live[name]["hits"]]
    return f"recall dropped: {', '.join(worse)}" if worse else ""


def changes(old, new):
    # old, new: {article number: text}
    changed = [n for n in new.keys() & old.keys() if not legifrance.same_words(new[n], old[n])]
    return {"added": len(new.keys() - old.keys()), "changed": len(changed), "removed": len(old.keys() - new.keys())}


def cost(usage):
    return round((usage["haiku_in"] * HAIKU_IN + usage["haiku_out"] * HAIKU_OUT + usage["embed"] * EMBED) / 1_000_000, 4)


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

class CachedClaude:
    """search.claude during the recall check: each distinct request is asked once and its answer kept (law_sync_cache).
    Rewrites of the 84 questions, and reranks whose candidates did not change, cost nothing after the first night."""

    def __init__(self, real, conn, usage):
        self.real, self.conn, self.usage = real, conn, usage
        self.messages, self.asked, self.from_cache, self.failed = self, 0, 0, 0

    def create(self, **request):
        key = "claude:" + hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
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


def run(today=None, fetch=None):
    import search
    from load_api import current_articles, store, text_hash
    today = today or date.today()
    live = search.TABLE
    nxt, prev = f"{live}_next", f"{live}_prev"
    env = os.environ.get("PISTE_ENV", "sandbox")
    usage = {"haiku_in": 0, "haiku_out": 0, "embed": 0}
    with search.connect() as conn:
        conn.execute(TABLES)
        conn.execute("DELETE FROM law_sync_cache WHERE created_at < now() - make_interval(days => %s)", (CACHE_DAYS,))
        conn.commit()
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK,)).fetchone()["ok"]:
            print("Another sync is running: nothing done.")
            return "skipped"
        try:
            def finish(result, reason="", **numbers):
                conn.rollback()                  # whatever a failed step left open; the run row is its own transaction
                spent = cost(usage)
                conn.execute("""INSERT INTO law_sync_runs (live_table, legifrance_date, env, result, reason, articles, added, changed,
                                                         removed, recall, cost_usd)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                             (live, today if result in ("applied", "no change") else None, env, result, reason, numbers.get("articles"),
                              numbers.get("added"), numbers.get("changed"), numbers.get("removed"),
                              json.dumps(numbers["recall"]) if numbers.get("recall") else None, spent))
                conn.commit()
                report(conn, search, live, result, reason, numbers, spent, today)
                return result

            def steps():
                try:
                    code = (fetch or legifrance.fetch_code)(today)
                except Exception as e:
                    return finish("failed", f"Légifrance did not answer ({type(e).__name__}); the live data is unchanged")
                articles, _, _, _ = legifrance.articles_from(code, today)
                if not articles:
                    return finish("failed", "Légifrance answered with no article in force; the live data is unchanged")
                chunks = [c for a in articles for c in chunk.chunks_for(a)]
                numbers = {"articles": len(articles), **changes(current_articles(conn, live), {a["num"]: a["texte"] for a in articles})}

                live_hashes = {r["h"] for r in conn.execute(
                    f"SELECT encode(sha256(convert_to(search_text, 'UTF8')), 'hex') AS h FROM {live}").fetchall()}
                if live_hashes == {text_hash(c["search_text"]) for c in chunks}:
                    return finish("no change", **numbers)

                last = conn.execute("""SELECT articles FROM law_sync_runs WHERE live_table = %s AND result = 'applied'
                                       ORDER BY id DESC LIMIT 1""", (live,)).fetchone()
                reason = spot_verdict(articles) or size_verdict(last and last["articles"], len(articles))
                if reason:
                    return finish("held", reason, **numbers)

                _, _, tokens = store(conn, chunks, articles, table=nxt, reuse_from=(nxt, live))   # the whole new code, beside the live one
                usage["embed"] += tokens

                aside = set_aside({a["num"] for a in articles})
                real_claude, real_embed = search.claude, search.embed
                search.claude, search.embed = CachedClaude(real_claude, conn, usage), cached_embedding(conn, search, usage)
                try:
                    numbers["recall"] = {"live": recall(conn, search, live, aside), "new": recall(conn, search, nxt, aside), "set aside": aside}
                    failed, asked, cached = search.claude.failed, search.claude.asked, search.claude.from_cache
                finally:
                    search.claude, search.embed, search.TABLE = real_claude, real_embed, live
                conn.commit()                        # the cached answers
                numbers["model answers"] = f"{asked} asked, {cached} from cache"
                if failed:
                    return finish("held", f"the recall check could not run: {failed} model calls failed", **numbers)
                reason = recall_verdict(numbers["recall"]["live"], numbers["recall"]["new"])
                if reason:
                    return finish("held", reason, **numbers)

                switch(conn, live, nxt, prev)
                return finish("applied", **numbers)

            try:
                return steps()
            except Exception as e:                   # e.g. the embedding service is down: recorded and alerted, the live data is unchanged
                return finish("failed", f"{type(e).__name__}: {str(e)[:200]}; the live data is unchanged")
        finally:
            conn.rollback()
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))
            conn.commit()


def report(conn, search, live, result, reason, numbers, spent, today):
    # the log line every night, and the developer's alert on a held or failed one
    good = conn.execute("""SELECT legifrance_date FROM law_sync_runs WHERE live_table = %s AND result IN ('applied', 'no change')
                           ORDER BY id DESC LIMIT 1""", (live,)).fetchone()
    last_good = good["legifrance_date"] if good else None
    lines = [f"Labour code sync {today}: {result}" + (f" ({reason})" if reason else ""),
             f"  articles {numbers.get('articles', '-')}, added {numbers.get('added', '-')}, changed {numbers.get('changed', '-')}, "
             f"removed {numbers.get('removed', '-')}",
             f"  cost tonight ${spent:.4f}" + (f" ({numbers['model answers']})" if numbers.get("model answers") else "")]
    if numbers.get("recall"):
        for name, live_score in numbers["recall"]["live"].items():
            new_score = numbers["recall"]["new"][name]
            lines.append(f"  recall {name}: live {live_score['hits']}/{live_score['of']}, new {new_score['hits']}/{new_score['of']}")
        if numbers["recall"]["set aside"]:
            lines.append(f"  set aside (expected article no longer in the code, update the test): {numbers['recall']['set aside']}")
    lines.append(f"  last good sync: {last_good or 'never'}" + (f" ({(today - last_good).days} days ago)" if last_good else ""))
    print("\n".join(lines))
    try:
        if result in ("held", "failed"):
            print(f"  [ALERT] {github_issue(f'Labour code sync {result}: {reason}'[:120], '```\n' + chr(10).join(lines) + '\n```')}")
        else:
            outcome = github_issue("", f"Fixed: the sync of {today} was {result}.", resolved=True)
            if outcome.startswith("closed"):
                print(f"  {outcome}")
    except Exception as e:
        print(f"  [ALERT] the GitHub issue could not be written ({type(e).__name__}); this log is the alert")


if __name__ == "__main__":
    sys.exit(0 if run() in ("applied", "no change", "skipped") else 1)
