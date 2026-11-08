# judge.py: LLM-as-judge. Checks that every claim in a reply is supported by THIS turn's tool results
# and attributed to the right source (company policy vs law vs what the system did).
# Keyword tests catch wrong facts; this catches invented reasons and wrong attributions.
import anthropic
from dotenv import load_dotenv

from judge_parse import verdict

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

One kind of claim is supported without being written word for word: the other side of a threshold the rule states.
When the text says something applies above a limit ("sick leave of more than 2 consecutive days requires a medical
certificate"), then "for 1 or 2 days no certificate is required" (also worded "not required by the policy") is
SUPPORTED. That does not make a new condition, exception, deadline, number or person who decides ("but tell your
manager first", "unless you were off sick before", "HR may still ask"), or an invented reason for what the system
did ("this sometimes happens if..."), supported: those stay UNSUPPORTED, even next to a supported statement.

Do NOT flag: greetings, questions to the user, offers to help (e.g. offering to raise an HR ticket),
advice to contact HR or a manager, or simple restatements of what the user asked.
Translations and paraphrases of the sources are fine if the meaning is the same.
A statement that the company policy does not cover a topic, or does not say something, is SUPPORTED when none of
the policy sections in the tool results answers the question: a section about another topic, or only loosely
related, does not answer it, and neither does a result with "found": false. The policy search ranks the whole
policy and returns the closest sections, so when those are unrelated the policy has nothing on the topic: do not
flag the statement because the search might have missed something. It is UNSUPPORTED when a returned section
does answer the question (then the policy does cover it). Anything else claimed next to it (an allowance, a
condition, a consequence) still needs support of its own.
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
    unsupported = verdict(text)          # tolerates text around the JSON, and a verdict the judge corrected
    if unsupported is None:
        return [{"claim": "(judge gave no usable verdict)", "why": text[-300:]}]   # fail loudly, never silently pass
    return unsupported
