# judge.py: LLM-as-judge. Checks that every claim in a reply is supported by THIS turn's tool results
# and attributed to the right source (company policy vs law vs what the system did).
# Keyword tests catch wrong facts; this catches invented reasons and wrong attributions.
import json
import re

import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()
# a judge should be stronger than the model it judges: Haiku misjudged a different good reply on each run
# (too literal: "due on" vs "available on"), so the judge uses Sonnet
JUDGE_MODEL = "claude-sonnet-4-5"

JUDGE_PROMPT = """You check an HR assistant's reply against the tool results it received in this turn.

List every factual claim in the reply about rules, numbers, dates, durations, law articles, company policy,
or what the system did (e.g. whether a ticket was created, and why). For each claim, check:
1. Is it supported by the tool results? A claim that adds a rule, a number, a reason or a condition that the
   tool results do not contain is UNSUPPORTED, even if it sounds plausible.
2. Is it attributed to the right source? Saying "the company policy says X" when X only appears in a law
   article (or the reverse) is UNSUPPORTED. Anything written inside a company policy section counts as
   company policy, even if that section also mentions a law article.

Do NOT flag: greetings, questions to the user, offers to help (e.g. offering to raise an HR ticket),
advice to contact HR or a manager, or simple restatements of what the user asked.
Translations and paraphrases of the sources are fine if the meaning is the same.
The tool results are the ground truth about what happened in this turn. Before any change (leave request,
ticket, cancellation) the system shows the user a confirmation box OUTSIDE the chat; the tool result reports
their answer (e.g. "The user chose not to raise a ticket"). So a tool result can override what the user typed.

Reply with JSON only, no other text:
{"unsupported": [{"claim": "<the claim, quoted or paraphrased>", "why": "<what the sources say instead>"}]}
Use an empty list if every claim is supported."""


def judge(question, reply, sources):
    """Returns a list of unsupported claims (empty = the reply is grounded)."""
    content = (f"User question:\n{question}\n\n"
               f"Tool results in this turn:\n{chr(10).join(sources) if sources else '(no tool was called)'}\n\n"
               f"Assistant reply:\n{reply}")
    resp = client.messages.create(model=JUDGE_MODEL, max_tokens=600, system=JUDGE_PROMPT,
                                  messages=[{"role": "user", "content": content}])
    text = "".join(b.text for b in resp.content if b.type == "text")
    match = re.search(r"\{.*\}", text, re.S)          # tolerate text around the JSON
    if not match:
        return [{"claim": "(judge gave no JSON)", "why": text[:200]}]   # fail loudly, never silently pass
    try:
        return json.loads(match.group(0)).get("unsupported", [])
    except json.JSONDecodeError:
        return [{"claim": "(judge gave invalid JSON)", "why": text[:200]}]
