# labour_code/load_api.py: load the Code du travail from Légifrance (legifrance.py) into a SIDE table, chunks_api, and compare
# it with chunks, the table the assistant searches today. Nothing the assistant uses changes: switching over is a later PR.
#   python labour_code/load_api.py              -> fetch (PISTE_ENV, default sandbox), clean, compare, chunk, embed, store
#   python labour_code/load_api.py --no-store   -> fetch, clean and compare only: no embedding cost, no database write
# Recall on the new data afterwards:
#   cd labour_code && LABOUR_TABLE=chunks_api python eval_retrieval.py only=user+agent top=5
import hashlib
import json
import sys
from datetime import date

import psycopg
from psycopg.rows import tuple_row

import chunk
import legifrance
from store import BATCH, DB_URL, MODEL, client, schema

SIDE = "chunks_api"
COST_PER_M = 0.02          # text-embedding-3-small, dollars per million tokens
SPOT_CHECKS = legifrance.SPOT_CHECKS
same_words = legifrance.same_words


def text_hash(search_text):
    return hashlib.sha256(search_text.encode("utf-8")).hexdigest()


def current_articles(conn, table="chunks"):
    # what the assistant searches today: each article's text put back together from its chunks
    with conn.cursor(row_factory=tuple_row) as cur:
        return dict(cur.execute(f"""SELECT num, string_agg(text, ' ' ORDER BY split_part(chunk_id, '#', 2)::int)
                                    FROM {table} GROUP BY num""").fetchall())


def compare(articles, current):
    by_num = {a["num"]: a for a in articles}
    both = by_num.keys() & current.keys()
    changed = sorted(n for n in both if not same_words(by_num[n]["texte"], current[n]))
    return {"same": len(both) - len(changed), "changed": changed,
            "new": sorted(by_num.keys() - current.keys()), "gone": sorted(current.keys() - by_num.keys())}


def store(conn, chunks, articles, table=SIDE, reuse_from=(SIDE, "chunks")):
    # embed only chunks whose words are new; then replace the table's rows in one transaction (sync.py builds chunks_next with it)
    ids = {a["num"]: (a["id"], a["cid"]) for a in articles}
    for c in chunks:
        c["text_hash"] = text_hash(c["search_text"])
    conn.execute(schema(table))
    conn.commit()
    hashes = list({c["text_hash"] for c in chunks})
    known = {}
    for source in dict.fromkeys((table, *reuse_from)):  # an unchanged chunk keeps its vector, from the last load or from the live table
        with conn.cursor(row_factory=tuple_row) as cur:
            if cur.execute("SELECT to_regclass(%s)", (source,)).fetchone()[0] is None:
                continue                         # e.g. no side table yet
            known.update(cur.execute(
                f"""SELECT h, embedding::text FROM (SELECT encode(sha256(convert_to(search_text, 'UTF8')), 'hex') AS h, embedding
                    FROM {source}) x WHERE h = ANY(%s)""", (hashes,)).fetchall())
    todo = [c for c in chunks if c["text_hash"] not in known]
    tokens = 0
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        resp = client.embeddings.create(model=MODEL, input=[c["search_text"] for c in batch])
        tokens += resp.usage.total_tokens
        for c, e in zip(batch, resp.data):
            known[c["text_hash"]] = str(e.embedding)
        print(f"  embedded {min(start + BATCH, len(todo))}/{len(todo)}")
    rows = [(c["chunk_id"], c["num"], c["part"], c["path"], c["etat"],
             None if c["date_fin"] in (None, "None") else c["date_fin"],
             c["text"], c["search_text"], known[c["text_hash"]], *ids[c["num"]], c["text_hash"]) for c in chunks]
    conn.execute(f"TRUNCATE {table}")            # nothing is written until every vector is ready; a failure rolls back
    with conn.cursor() as cur:
        cur.executemany(f"""INSERT INTO {table} (chunk_id, num, part, path, etat, date_fin, text, search_text, embedding,
                                                article_id, cid, text_hash)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s)""", rows)
    conn.commit()
    return len(chunks) - len(todo), len(todo), tokens


def main():
    today = date.today()
    code = legifrance.fetch_code(today)
    articles, report, boilerplate, annexes = legifrance.articles_from(code, today)
    with open("labour_code/api_articles.jsonl", "w", encoding="utf-8") as f:     # git-ignored, for a look by hand
        for a in articles:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")

    print(f"Légifrance, Code du travail as in force on {today}:")
    for name, n in report.items():
        print(f"  {name}: {n}")
    print(f"  annexes: {', '.join(annexes)}")
    print(f"  texts repeated in {legifrance.BOILERPLATE_MIN}+ articles (kept, each is a real article):")
    for text, n in boilerplate:
        print(f"    {n}x  {text[:100]}")
    for num, words in SPOT_CHECKS.items():
        found = next((a for a in articles if a["num"] == num), None)
        print(f"  spot check {num}: {'OK' if found and words in found['texte'] else 'MISSING OR CHANGED'}")

    chunks = [c for a in articles for c in chunk.chunks_for(a)]
    print(f"\nChunks: {len(chunks)} from {len(articles)} articles, "
          f"{len({c['num'] for c in chunks if not c['chunk_id'].endswith('#1')})} articles split, "
          f"longest {max(len(c['text']) for c in chunks)} characters")

    with psycopg.connect(DB_URL) as conn:
        diff = compare(articles, current_articles(conn))
        print("\nCompared with the table the assistant searches today (chunks):")
        print(f"  same wording: {diff['same']}")
        print(f"  wording changed: {len(diff['changed'])}  e.g. {', '.join(diff['changed'][:8])}")
        print(f"  only on Légifrance (missing from our copy): {len(diff['new'])}  e.g. {', '.join(diff['new'][:8])}")
        print(f"  only in our copy (not in force on Légifrance today): {len(diff['gone'])}  e.g. {', '.join(diff['gone'][:8])}")
        if "--no-store" in sys.argv:
            return
        reused, new, tokens = store(conn, chunks, articles)
        print(f"\nStored in {SIDE}: {len(chunks)} chunks. Vectors kept: {reused}, new: {new} "
              f"({tokens:,} tokens, about ${tokens / 1_000_000 * COST_PER_M:.2f})")


if __name__ == "__main__":
    main()
