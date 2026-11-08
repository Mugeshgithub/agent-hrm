# policy.py: meaning-based search over the HR policy using embeddings
import json
import math
import os
import hashlib
from dotenv import load_dotenv
from openai import OpenAI

import policy_store

load_dotenv()
client = OpenAI()

CACHE_FILE = "policy_embeddings.json"   # so we only pay to embed each section once
EMBED_MODEL = "text-embedding-3-small"
MIN_SCORE = 0.30   # below this, a section is "not related"; tune it with the test below


def load_sections():
    # the current policy: what HR last saved in the Handbook, or hr_policy.md until then (policy_store.py)
    return policy_store.sections()


def embed(texts):
    response = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [item.embedding for item in response.data]


def cosine(a, b):
    # how similar two meanings are: 1 = same, 0 = unrelated
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def section_vectors():
    sections = load_sections()
    cache = json.load(open(CACHE_FILE)) if os.path.exists(CACHE_FILE) else {}
    keys = [hashlib.sha256((s["title"] + s["text"]).encode()).hexdigest() for s in sections]

    # only embed sections that are new or have changed
    missing = [i for i, k in enumerate(keys) if k not in cache]
    if missing:
        vectors = embed([sections[i]["title"] + "\n" + sections[i]["text"] for i in missing])
        for i, v in zip(missing, vectors):
            cache[keys[i]] = v
        tmp = f"{CACHE_FILE}.{os.getpid()}.tmp"                # written whole, then swapped in: two processes never leave a half-written file
        with open(tmp, "w") as f:
            json.dump(cache, f)
        os.replace(tmp, CACHE_FILE)

    return [(s, cache[k]) for s, k in zip(sections, keys)]


def search_policy(question, top_k=2, show_scores=False):
    q = embed([question])[0]
    scored = sorted(((cosine(q, v), s) for s, v in section_vectors()),
                    key=lambda pair: pair[0], reverse=True)
    if show_scores:
        for score, s in scored:
            print(f"      {score:.2f}  {s['title']}")
    return [s for score, s in scored[:top_k] if score >= MIN_SCORE]


if __name__ == "__main__":
    # quick manual test: python policy.py
    for question in ["Can I roll over my unused leave?",
                     "Do I need a doctor's note?",
                     "Can I do remote work?",
                     "What is the company car policy?"]:
        print(question)
        results = search_policy(question, show_scores=True)
        print("   ->", [s["title"] for s in results] or "nothing found")