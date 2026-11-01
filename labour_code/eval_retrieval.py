# labour_code/eval_retrieval.py: recall@8 for each search method, in English and French
from retrieval_cases import CASES
from search import (connect, vector_search, keyword_search, hybrid_search,
                    multi_query_search, french_search, rewrite_query)

METHODS = {"vector": vector_search, "keyword": keyword_search, "hybrid": hybrid_search,
           "multi-query": multi_query_search, "french-only": french_search}
TOP = 8   # the agent receives the top 8 articles, so measure exactly that


def top_articles(results, n=TOP):
    # several chunks can come from one article; count each article once
    return list(dict.fromkeys(r["num"] for r in results))[:n]


with connect() as conn:
    misses = {}
    print(f"{'method':15}  {'all':>7}  {'english':>7}  {'french':>7}")
    for name, search in METHODS.items():
        hits = {"en": 0, "fr": 0}
        for question, lang, expected in CASES:
            found = top_articles(search(conn, question))
            if expected in found:
                hits[lang] += 1
            else:
                misses.setdefault(name, {})[question] = (expected, found)
        per_lang = len(CASES) // 2
        total = hits["en"] + hits["fr"]
        print(f"{name:15}  {total:>3}/{len(CASES)}   {hits['en']:>3}/{per_lang}    {hits['fr']:>3}/{per_lang}")

    for name, missed in misses.items():
        print(f"\n{name} misses:")
        for q, (expected, found) in missed.items():
            print(f"  {q}\n    expected {expected}, got {found}")

print("\nRewritten queries:")
for question, lang, expected in CASES:
    print(f"  [{lang}] {question}\n       -> {rewrite_query(question)}")