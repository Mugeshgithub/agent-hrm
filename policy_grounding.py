# policy_grounding.py: a policy answer says only what the policy text found in this turn says. After search_policy (alone: not with the labour code or
# the collective agreement), a second model reads the reply next
# to this turn's tool results and lists any reason, condition, requirement, deadline or consequence they do not contain: "your manager will decide
# based on business needs" when the policy only says the manager decides (the December eval, Nov 2026). One retry that names those parts; if they are
# still there, the reply is the policy sections found in this turn, quoted word for word.
# The check never blocks an answer on its own failure: a timeout or an answer it cannot read lets the reply through, logged.
# The model call is passed in (`check_model(system, text) -> text`), so this module needs no package: it is unit-tested in CI with a scripted checker.
import json

from candidate_assistant import _french
from document_answers import HIDDEN
from judge_parse import verdict

CHECK_PROMPT = """You check one reply of a company's HR assistant before the employee sees it. You get the employee's question, the tool results the assistant received in this turn (company policy sections and other data), and the reply.

Report every REASON, CONDITION, EXCEPTION, REQUIREMENT, DEADLINE or CONSEQUENCE the reply gives about the company's rules, and every statement of WHO DECIDES or ON WHAT BASIS, that the tool results do not contain. For example:
- "your manager decides based on business needs" when the text only says the manager decides: the basis is invented;
- "you also need HR's approval", "unless you were off sick before", "it must be signed by a doctor registered in France": new conditions;
- "upload it by your third day back" when the text says "within 3 days of returning to work": a changed deadline;
- "the policy doesn't restrict paid leave by month", "there is no limit", "you can take it any time" when no returned section says so: silence turned
  into a rule (what the text does not mention is unknown, not allowed).

These are supported. Do NOT report them:
- what the tool results say, in other words or in another language, with the same meaning. "Yes, you can take paid leave in December" is the same
  meaning as a text that says it "can still be requested" and the manager decides, even when the reply leaves the manager out; it is reported only
  when the reply promises the result, makes it automatic, or says nobody has to approve it;
- the other side of a limit the text states: "more than 2 consecutive days requires a certificate" supports "for 1 or 2 days you don't need one", and
  "up to 5 unused days can be carried forward" supports "any more than 5 are lost" or "the rest does not carry over";
- a sentence about one thing is not a claim about the others: "only up to 5 casual days carry over" says nothing about paid leave;
- whether a date falls inside a period the text gives (July is inside 1 May to 31 October);
- "the policy does not cover / does not say / does not mention X" when no returned section answers the question. A section about another topic,
  or a result with "found": false, does not answer it: the search returns the closest sections of the whole policy, so when they are about something
  else, the policy has nothing on the topic. Do not report it because the search might have missed something;
- numbers of days, amounts, dates and law article numbers (other checks handle them);
- facts about the employee (name, role, category, team, balance, requests);
- greetings, questions, offers to help, advice to ask HR or the manager, and plain advice that follows from a stated rule ("so it's worth using them").

Reply with JSON only, no other text:
{"unsupported": [{"claim": "<the unsupported part, quoted from the reply>", "why": "<what the tool results say instead>"}]}
Use an empty list when everything the reply says about the rules is in the tool results."""

RETRY = HIDDEN + ("These parts of your reply are not in what the tools returned in this turn: {parts}. If the policy sections you found are about "
                  "something else, call search_policy once more with other words (the topic's own name, such as \"paid leave\" or \"sick leave\"). Then "
                  "answer the question again with only what the policy text and the other tool results say: leave out any reason, condition, deadline or "
                  "consequence they do not state, and where they still do not answer something, say the policy does not say. Start with the answer itself "
                  "and say nothing about this note.")
QUOTE = "Here is what the company policy says, word for word ({title}): \"{text}\""
QUOTE_FR = "Voici ce que dit la politique de l'entreprise, mot pour mot ({title}) : « {text} »"
NOT_CONFIRMED = "I can't confirm that from the company policy, so I won't guess. HR can tell you."
NOT_CONFIRMED_FR = "Je ne peux pas le confirmer à partir de la politique de l'entreprise, donc je préfère ne rien supposer. Les RH pourront vous répondre."


OTHER_SOURCES = ("search_labour_code", "search_collective_agreement")


def applies(tools_called):
    """A policy answer: search_policy ran in this turn, and neither the labour code nor the collective agreement did. With law articles in the results the
    checker read the law as if it were the policy: it flagged correct law answers, and the safe reply then quoted an unrelated policy section instead."""
    return "search_policy" in tools_called and not any(name in tools_called for name in OTHER_SOURCES)


def check(question, reply, sources, check_model):
    """The parts of the reply this turn's tool results do not support: a list (empty when grounded), or (None, why) when the check gave no answer."""
    text = (f"Employee's question:\n{question}\n\n"
            f"Tool results in this turn:\n{chr(10).join(sources) if sources else '(no tool was called)'}\n\n"
            f"Assistant's reply:\n{reply}")
    try:
        answer = check_model(CHECK_PROMPT, text)
    except Exception as e:                 # a timeout or an API error: the reply goes out unchecked, and the log says so
        return None, type(e).__name__
    found = verdict(answer)
    if found is None:
        return None, "no usable answer"
    # a question to the person states no rule: the checker once reported "Would you like me to raise an HR ticket to ask about this?", and the retry
    # lost the offer
    return [f for f in found if isinstance(f, dict) and not str(f.get("claim") or "").strip().strip("\"'“”").endswith("?")], None


def policy_quote(sources, french=False):
    """The sections search_policy found in this turn, word for word (each once, in the order found), or None. Taking the bad sentences out of the reply
    was tried first: it left pieces such as "If you'd like to book some, let me know the dates" that answered nothing."""
    quotes, seen = [], set()
    for source in sources:
        try:
            result = json.loads(source)
        except (TypeError, ValueError):
            continue
        if not (isinstance(result, dict) and result.get("found")):
            continue
        for section in result.get("sections") or []:
            if isinstance(section, dict) and section.get("title") not in seen:
                seen.add(section.get("title"))
                quotes.append((QUOTE_FR if french else QUOTE).format(title=section.get("title", ""), text=section.get("text", "")))
    return "\n\n".join(quotes) or None


def review(question, reply, sources, check_model, retried):
    """("ok" | "retry" | "replaced" | "unchecked", text, detail): the reply, the retry note, the safe reply, or the reply let through unchecked."""
    found, failure = check(question, reply, sources, check_model)
    if found is None:
        return "unchecked", reply, failure
    if not found:
        return "ok", reply, []
    if not retried:
        return "retry", RETRY.format(parts="; ".join(f'"{f.get("claim")}"' for f in found)), found
    french = _french(question)
    return "replaced", policy_quote(sources, french) or (NOT_CONFIRMED_FR if french else NOT_CONFIRMED), found
