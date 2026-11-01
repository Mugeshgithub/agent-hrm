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
MODEL = "text-embedding-3-small"
client = OpenAI()


def connect():
    return psycopg.connect(DB_URL, row_factory=dict_row)


@lru_cache(maxsize=1000)
def embed(question):
    return str(client.embeddings.create(model=MODEL, input=[question]).data[0].embedding)


def vector_search(conn, question, k=20):
    # meaning: closest embeddings first (<=> is cosine distance)
    v = embed(question)
    return conn.execute(
        """SELECT chunk_id, num, text FROM chunks
           ORDER BY embedding <=> %s::vector LIMIT %s""", (v, k)).fetchall()


def keyword_search(conn, question, k=20):
    # words: French stemming; any word may match (OR); text outweighs path; long chunks don't win by size
    words = re.findall(r"\w+", question.lower())
    if not words:
        return []
    query = " | ".join(words)
    return conn.execute(
        """SELECT chunk_id, num, text FROM chunks, to_tsquery('french', %s) q
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

claude = anthropic.Anthropic()

REWRITE_PROMPT = (
    "Rewrite the user's question as a short French search query for the French Code du travail. "
    "Use the specific legal terms the code itself uses (for example: congés payés, repos quotidien, "
    "repos hebdomadaire, période d'essai, harcèlement moral). "
    "Do NOT add generic filler such as 'Code du travail', 'durée légale', 'travail' or 'légal' "
    "unless the question is specifically about that. "
    "Output only the query, at most 10 words, no explanation."
)


@lru_cache(maxsize=1000)
def rewrite_query(question):
    # the model only prepares the SEARCH; it never answers here
    resp = claude.messages.create(
        model="claude-haiku-4-5", max_tokens=60, temperature=0, system=REWRITE_PROMPT,
        messages=[{"role": "user", "content": question}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def multi_query_search(conn, question, k=20):
    # keep the user's own words AND add the law's vocabulary; keyword search only on the French rewrite
    rewritten = rewrite_query(question)
    return fuse([
        vector_search(conn, question, k),
        vector_search(conn, rewritten, k),
        keyword_search(conn, rewritten, k),
    ], k)


def french_search(conn, question, k=20):
    # fix 1: search ONLY with the French rewrite; the English question pulled in look-alike articles
    rewritten = rewrite_query(question)
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


def rerank(question, rows, snippet=600):
    # second step: a model READS the question and each candidate together and reorders them.
    # the search only finds "same topic"; this judges "does it answer the question?"
    first = {}
    for r in rows:                              # one row per article, in search order
        first.setdefault(r["num"], r)
    articles = list(first.values())
    listing = "\n\n".join(f"[{i}] {r['num']}: {r['text'][:snippet]}" for i, r in enumerate(articles, start=1))
    resp = claude.messages.create(
        model="claude-haiku-4-5", max_tokens=150, temperature=0, system=RERANK_PROMPT,
        messages=[{"role": "user", "content": f"Question: {question}\n\nCandidates:\n\n{listing}"}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    order = []
    for n in re.findall(r"\d+", text):          # tolerate any formatting; ignore numbers out of range
        i = int(n)
        if 1 <= i <= len(articles) and i not in order:
            order.append(i)
    order += [i for i in range(1, len(articles) + 1) if i not in order]   # anything it forgot keeps search order
    return [articles[i - 1] for i in order]


def reranked_search(conn, question, k=20):
    # multi-query finds the right article in its top 20 for all 50 test questions; the reranker puts it first
    return rerank(question, multi_query_search(conn, question, k))
