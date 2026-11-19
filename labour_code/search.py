# labour_code/search.py: vector, keyword and hybrid search over the labour code
import os
import re
from functools import lru_cache

import psycopg
from dotenv import load_dotenv
from openai import OpenAI
from psycopg.rows import dict_row

load_dotenv()
DB_URL = os.environ["LABOUR_DB_URL"]
TABLE = os.environ.get("LABOUR_TABLE", "chunks")       # chunks_api: measure the Légifrance load (load_api.py) before it goes live
assert re.fullmatch(r"[a-z_]+", TABLE), TABLE           # a table name goes into the SQL text, so letters and _ only
MODEL = "text-embedding-3-small"
client = OpenAI()


def connect():
    return psycopg.connect(DB_URL, row_factory=dict_row)


def as_of(conn):
    # the day the live table was last checked against Légifrance (sync.py), or None before the first sync
    if conn.execute("SELECT to_regclass('law_sync_runs') AS t").fetchone()["t"] is None:
        return None
    row = conn.execute("""SELECT legifrance_date FROM law_sync_runs WHERE live_table = %s AND result IN ('applied', 'no change')
                          ORDER BY id DESC LIMIT 1""", (TABLE,)).fetchone()
    return row["legifrance_date"] if row else None


class EmbeddingFailed(Exception):
    """The embedding service did not give a vector (down, timed out, key refused). The assistant's search goes on with keywords."""


@lru_cache(maxsize=1000)
def embed(question):
    # 10 s and one retry, like the Haiku steps: an answer must not wait minutes for a vector (the default is 10 minutes, 2 retries)
    return str(client.with_options(timeout=10, max_retries=1).embeddings.create(model=MODEL, input=[question]).data[0].embedding)


def vector_search(conn, question, k=20):
    # meaning: closest embeddings first (<=> is cosine distance)
    try:
        v = embed(question)
    except Exception as e:
        raise EmbeddingFailed(f"{type(e).__name__}: {e}") from e
    return conn.execute(
        f"""SELECT chunk_id, num, text FROM {TABLE}
           ORDER BY embedding <=> %s::vector LIMIT %s""", (v, k)).fetchall()


def keyword_search(conn, question, k=20):
    # words: French stemming; any word may match (OR); text outweighs path; long chunks don't win by size
    words = re.findall(r"\w+", question.lower())
    if not words:
        return []
    query = " | ".join(words)
    return conn.execute(
        f"""SELECT chunk_id, num, text FROM {TABLE}, to_tsquery('french', %s) q
           WHERE tsv @@ q ORDER BY ts_rank(tsv, q, 1) DESC LIMIT %s""", (query, k)).fetchall()


def fuse(result_lists, k=20, rrf_k=60):
    # Reciprocal Rank Fusion: each list gives 1/(60 + rank) points; add them up
    scores, rows = {}, {}
    for results in result_lists:
        for rank, row in enumerate(results, start=1):
            scores[row["chunk_id"]] = scores.get(row["chunk_id"], 0) + 1 / (rrf_k + rank)
            rows[row["chunk_id"]] = row
    best = sorted(scores, key=scores.get, reverse=True)[:k]
    return [rows[c] for c in best]


def hybrid_search(conn, question, k=20):
    return fuse([vector_search(conn, question, k), keyword_search(conn, question, k)], k)

import anthropic

# the rewrite and the rerank only prepare the search: a slow or failed call falls back instead of holding up the answer
claude = anthropic.Anthropic(timeout=10, max_retries=1)

REWRITE_PROMPT = (
    "Rewrite the user's question as a short French search query for the French Code du travail. "
    "Use the specific legal terms the code itself uses (for example: congés payés, repos quotidien, "
    "repos hebdomadaire, période d'essai, harcèlement moral). "
    "Do NOT add generic filler such as 'Code du travail', 'durée légale', 'travail' or 'légal' "
    "unless the question is specifically about that. "
    "Output only the query, at most 10 words, no explanation."
)


@lru_cache(maxsize=1000)
def _rewrite(question):
    # the model only prepares the SEARCH; it never answers here
    resp = claude.messages.create(
        model="claude-haiku-4-5", max_tokens=60, temperature=0, system=REWRITE_PROMPT,
        messages=[{"role": "user", "content": question}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def rewrite_query(question):
    # None when the call fails or says nothing (lru_cache keeps only successes, so the next search tries again)
    try:
        return _rewrite(question) or None
    except Exception as e:
        print(f"  [labour search] query rewrite failed ({type(e).__name__}): searching with the user's words only")
        return None


def multi_query_search(conn, question, k=20):
    # keep the user's own words AND add the law's vocabulary; keyword search only on the French rewrite
    rewritten = rewrite_query(question)
    if not rewritten:                           # the user's words alone found 45 of 50 test questions (top 8)
        return vector_search(conn, question, k)
    return fuse([
        vector_search(conn, question, k),
        vector_search(conn, rewritten, k),
        keyword_search(conn, rewritten, k),
    ], k)


def french_search(conn, question, k=20):
    # fix 1: search ONLY with the French rewrite; the English question pulled in look-alike articles
    rewritten = rewrite_query(question) or question
    return fuse([
        vector_search(conn, rewritten, k),
        keyword_search(conn, rewritten, k),
    ], k)


RERANK_PROMPT = (
    "You rank articles of the French Code du travail for a search engine. "
    "You get a question (in English or French) and numbered candidate articles. "
    "Order the candidates by how directly each one ANSWERS the question. "
    "Put first the article that states the rule asked about (the general rule, the number, the definition), "
    "before articles about exceptions, special cases, procedures, sanctions or other kinds of leave. "
    "Output only the candidate numbers, most relevant first, separated by commas (for example: 4,1,7). "
    "Include every number exactly once. No explanation."
)
POSITIONS = re.compile(r"\s*\d+(\s*,\s*\d+)*\s*")     # "4,1,7" or "4, 1, 7" and nothing else


def rerank(question, rows, snippet=600):
    # second step: a model READS the question and each candidate together and reorders them.
    # the search only finds "same topic"; this judges "does it answer the question?"
    first = {}
    for r in rows:                              # one row per article, in search order
        first.setdefault(r["num"], r)
    articles = list(first.values())
    if not articles:
        return []
    listing = "\n\n".join(f"[{i}] {r['num']}: {r['text'][:snippet]}" for i, r in enumerate(articles, start=1))
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5", max_tokens=150, temperature=0, system=RERANK_PROMPT,
            messages=[{"role": "user", "content": f"Question: {question}\n\nCandidates:\n\n{listing}"}],
        )
    except Exception as e:                      # timeout, rate limit, outage: the search order is still a good answer
        print(f"  [labour search] rerank failed ({type(e).__name__}): search order kept")
        return articles
    text = "".join(b.text for b in resp.content if b.type == "text")
    if not POSITIONS.fullmatch(text):           # any other text: its numbers may be parts of "L3141-3", not positions
        print("  [labour search] rerank answer is not a list of positions: search order kept")
        return articles
    order = []
    for n in text.split(","):                   # ignore numbers out of range and repeats
        i = int(n)
        if 1 <= i <= len(articles) and i not in order:
            order.append(i)
    order += [i for i in range(1, len(articles) + 1) if i not in order]   # anything it forgot keeps search order
    return [articles[i - 1] for i in order]


def reranked_search(conn, question, k=20):
    # multi-query finds the right article in its top 20 for all 50 test questions; the reranker puts it first
    return rerank(question, multi_query_search(conn, question, k))


def agent_questions(user_question, agent_query):
    # the agent rewrites the user's question before calling the tool, and its wording can lose the meaning:
    # "paid holiday days per year entitlement" -> L3141-3 not in the top 20; the user's own question -> rank 2.
    # So search with BOTH, merge the candidates, and rerank against both (the agent's query keeps the
    # context of follow-ups like "And during which months?").
    queries = [q for q in dict.fromkeys([user_question, agent_query]) if q]
    return queries, queries[0] if len(queries) == 1 else f"{user_question}\n(search query: {agent_query})"


def search_for_agent(conn, user_question, agent_query, k=20):
    # raises EmbeddingFailed when the embedding service is down: the nightly checks and the recall measurements fail then
    # instead of lowering their recall; the assistant catches it and uses keyword_search_for_agent (agent.py)
    queries, question = agent_questions(user_question, agent_query)
    return rerank(question, fuse([multi_query_search(conn, q, k) for q in queries], k))


def keyword_search_for_agent(conn, user_question, agent_query, failure, k=20):
    # the assistant's search while the embedding service is down: the French keyword search on each rewrite, reranked.
    # It misses articles the full search finds (63/84 recall questions at top 5, none of the paid-leave ones), so the agent
    # is told the search is limited and a line under its answer says so (agent.py, law_status.limited_line)
    print(f"  [labour search] embedding failed ({type(failure.__cause__).__name__}): French keyword search only")
    queries, question = agent_questions(user_question, agent_query)
    return rerank(question, fuse([keyword_search(conn, rewrite_query(q) or q, k) for q in queries], k))
