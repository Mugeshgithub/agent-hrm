# labour_code/store.py: load chunks + embeddings into Postgres (pgvector) for hybrid search
import json
import os

import psycopg
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
DB_URL = os.environ["LABOUR_DB_URL"]
MODEL = "text-embedding-3-small"   # 1,536 numbers per text; works across French and English
BATCH = 100
client = OpenAI()

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    text PRIMARY KEY,
    num         text NOT NULL,
    part        text,
    path        text,
    etat        text,
    date_fin    date,
    text        text NOT NULL,
    search_text text NOT NULL,
    embedding   vector(1536) NOT NULL,
    -- keyword search: French stemming, so "congé" also matches "congés"
        -- keyword search: article text counts fully (A), the path only a little (D)
    tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('french', coalesce(text, '')), 'A') ||
        setweight(to_tsvector('french', coalesce(path, '')), 'D')
    ) STORED
);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_num_idx ON chunks (num);
"""


def load_chunks():
    with open("labour_code/chunks.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def main():
    chunks = load_chunks()
    total_tokens = 0
    with psycopg.connect(DB_URL) as conn:
        conn.execute(SCHEMA)
        conn.execute("TRUNCATE chunks")   # full reload every time, so the table always matches chunks.jsonl
        for start in range(0, len(chunks), BATCH):
            batch = chunks[start:start + BATCH]
            resp = client.embeddings.create(model=MODEL, input=[c["search_text"] for c in batch])
            total_tokens += resp.usage.total_tokens
            rows = [
                (c["chunk_id"], c["num"], c["part"], c["path"], c["etat"],
                 None if c["date_fin"] in (None, "None") else c["date_fin"],
                 c["text"], c["search_text"], str(e.embedding))
                for c, e in zip(batch, resp.data)
            ]
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO chunks (chunk_id, num, part, path, etat, date_fin,
                                           text, search_text, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)""",
                    rows)
            conn.commit()
            print(f"  {min(start + BATCH, len(chunks))}/{len(chunks)} chunks stored")

        count = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]

    print(f"\nRows in database: {count}")
    print(f"Embedding tokens: {total_tokens:,}  (about ${total_tokens / 1_000_000 * 0.02:.2f})")


if __name__ == "__main__":
    main()