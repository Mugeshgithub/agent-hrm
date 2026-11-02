# labour_code/eval_retrieval.py: recall@8 for each search method, in English and French
import sys

from retrieval_cases import CASES, HOLDOUT
from search import (connect, vector_search, keyword_search, hybrid_search,
                    multi_query_search, french_search, reranked_search, search_for_agent, rewrite_query)

METHODS = {"vector": vector_search, "keyword": keyword_search, "hybrid": hybrid_search,
           "multi-query": multi_query_search, "french-only": french_search,
           "multi+rerank": reranked_search}

# the agent's own query for each question (python labour_code/agent_queries.py): what the tool REALLY receives
import json, os
_AQ_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_queries.json")
if os.path.exists(_AQ_FILE):
    AQ = json.load(open(_AQ_FILE, encoding="utf-8"))
    METHODS["agent-query"] = lambda conn, q: reranked_search(conn, AQ[q])               # old path
    METHODS["user+agent"] = lambda conn, q: search_for_agent(conn, q, AQ[q])            # production path
TOP = 8   # the agent receives the top 8 articles, so measure exactly that
TOPS = [8]

# python eval_retrieval.py                          -> the 50 tuning questions, all methods, top 8
# python eval_retrieval.py holdout                  -> the 20 held-out questions the reranker never saw
# python eval_retrieval.py top=5 only=multi+rerank  -> any top-k, one method (cheaper)
args = sys.argv[1:]
if "holdout" in args:
    CASES = HOLDOUT
    print("HELD-OUT SET\n")
for a in args:
    if a.startswith("top="):
        TOPS = [int(k) for k in a.split("=")[1].split(",")]   # top=3,5,8 -> measured in one pass
        TOP = TOPS[-1]
    if a.startswith("only="):                                     # only=agent-query,user+agent
        keep = a.split("=")[1].split(",")
        METHODS = {k: v for k, v in METHODS.items() if k in keep}
print(f"recall@{', '.join(map(str, TOPS))}\n")


def top_rows(results, n):
    # several chunks can come from one article; keep the first chunk of each, like the agent's tool
    first = {}
    for r in results:
        first.setdefault(r["num"], r)
    return list(first.values())[:n]


def top_articles(results, n=None):
    return [r["num"] for r in top_rows(results, n or TOP)]

with connect() as conn:
    misses = {}
    print(f"{'method':15}  {'all':>7}  {'english':>7}  {'french':>7}")
    for name, search in METHODS.items():
        hits = {"en": 0, "fr": 0}
        chars = 0
        per_k = {k: {"hits": 0, "chars": 0} for k in TOPS}
        for question, lang, expected in CASES:
            results = search(conn, question)
            found = top_articles(results)
            chars += sum(len(r["text"]) for r in top_rows(results, TOP))   # what the agent would read
            for k in TOPS:
                if any(e in top_articles(results, k) for e in expected.split("|")):
                    per_k[k]["hits"] += 1
                per_k[k]["chars"] += sum(len(r["text"]) for r in top_rows(results, k))
            if any(e in found for e in expected.split("|")):   # "A|B" = either article is a correct answer
                hits[lang] += 1
            else:
                misses.setdefault(name, {})[question] = (expected, found)
        per_lang = len(CASES) // 2
        total = hits["en"] + hits["fr"]
        avg = chars // len(CASES)
        print(f"{name:15}  {total:>3}/{len(CASES)}   {hits['en']:>3}/{per_lang}    {hits['fr']:>3}/{per_lang}"
              f"    ~{avg:,} chars (~{avg // 4:,} tokens) sent per question")
        if len(TOPS) > 1:
            for k in TOPS:
                a = per_k[k]["chars"] // len(CASES)
                print(f"   top {k}:  {per_k[k]['hits']:>3}/{len(CASES)}    ~{a // 4:,} tokens per question")

    for name, missed in misses.items():
        print(f"\n{name} misses:")
        for q, (expected, found) in missed.items():
            print(f"  {q}\n    expected {expected}, got {found}")

print("\nRewritten queries:")
for question, lang, expected in CASES:
    print(f"  [{lang}] {question}\n       -> {rewrite_query(question)}")