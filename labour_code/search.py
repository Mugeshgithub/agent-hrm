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
        model="claude-haiku-4-5", max_tokens=60, system=REWRITE_PROMPT,
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
