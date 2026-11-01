# citations.py: the agent may only cite labour-law articles that a tool returned in THIS turn.
# Pure functions, no AI, so they are unit-tested and the same code runs in the agent and the evals.
import re

CITATION = re.compile(r"\b([LRD])\.?\s?(\d{4}(?:-\d+)+)\b")

RETRY_MESSAGE = ("[System check] Your answer cites {articles}, but no tool returned that article in this turn, "
                 "so it may be quoted from memory. Call search_labour_code now and answer only from the articles "
                 "it returns. Do not cite any other article.")

SAFE_REPLY = ("I couldn't confirm that against the labour code, so I won't quote an article I haven't checked. "
              "Please ask again, or I can raise an HR ticket for you.")


def cited_articles(text):
    # "Article L3141-3", "L. 3141-3" -> {"L3141-3"}; dates like 2026-11-01 don't match (no L/R/D)
    return {a + b for a, b in CITATION.findall(text)}


def ungrounded_citations(reply, turn_sources):
    # articles the reply cites that no tool result in this turn contains
    return sorted(cited_articles(reply) - cited_articles(" ".join(turn_sources)))
