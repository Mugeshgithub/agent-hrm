# labour_code/explore.py: download the Code du travail and look at it before building anything
import json
from collections import Counter
from datasets import load_dataset

ds = load_dataset("louisbrulenaudet/code-travail", split="train")
print(f"Articles: {len(ds)}")
print(f"Columns: {ds.column_names}\n")

# what states are articles in? (in force, repealed, ...)
print("Status:", Counter(ds["etat"]).most_common(), "\n")

# how long are articles? this decides whether one article = one chunk works
lengths = sorted(len(t or "") for t in ds["texte"])
print(f"Text length: shortest {lengths[0]}, median {lengths[len(lengths)//2]}, "
      f"longest {lengths[-1]} characters\n")

# one real example
example = next(r for r in ds if r["num"] == "L3141-3")
print("Example:", example["num"])
print("Where:", example["fullSectionsTitre"])
print("Text:", (example["texte"] or "")[:400], "\n")

# save only what we need, as one JSON line per article
keep = ["id", "num", "texte", "fullSectionsTitre", "etat", "dateDebut", "dateFin"]
with open("labour_code/articles.jsonl", "w", encoding="utf-8") as f:
    for row in ds:
        f.write(json.dumps({k: row.get(k) for k in keep}, ensure_ascii=False) + "\n")
print("Saved labour_code/articles.jsonl")

# how many articles are too long to be one chunk?
for limit in (2000, 4000, 8000):
    n = sum(1 for t in ds["texte"] if len(t or "") > limit)
    print(f"Longer than {limit} characters: {n} articles")

# what do the shortest ones look like?
short = sorted(((len(r["texte"] or ""), r["num"], r["texte"]) for r in ds))[:5]
for length, num, text in short:
    print(f"{num} ({length}): {text}")