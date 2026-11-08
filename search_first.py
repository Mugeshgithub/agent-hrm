# search_first.py: a question about the company's rules is searched before it is answered, asked back or handed to HR.
# The prompt already says "call search_policy FIRST, before asking any clarifying question", and the model still skipped it now
# and then (measured: "Do I need a doctor's note?" 2 times in 10, "Can I get a company car?" 1 time in 10): it asked "which
# situation?" or said the topic was outside its tools and offered a ticket. So code checks the final reply: a rules question
# answered with no tool at all is sent back once, with a note to search. No AI and no third-party package, so it is unit-tested in CI.
import re

import roles
from document_answers import HIDDEN

# the person asked something: a question mark, or a sentence that opens like a question
_QUESTION = re.compile(r"\?|^\s*(?:do|does|did|can|could|may|might|must|should|shall|is|are|am|will|would|how|what|when|which|where|who|why)\b", re.I)
# and it is about what the rules are or what is allowed (not "show my balance", not a greeting)
_RULES = re.compile(
    r"\b(?:need|needs|needed|require|requires|required|allowed|allow|permit|permitted|entitled|entitlement|eligible|eligibility|"
    r"policy|policies|rule|rules|have to|has to|must|obliged|mandatory|"
    r"(?:can|could|may|am|are|is|do|does) (?:i|we|they|an employee|employees|the company|hr)|"
    r"how (?:many|much|long|often|early|late|soon)|what happens|is there|are there)\b", re.I)

SEARCH_FIRST = HIDDEN + ("The person asked a question and you answered it without searching. If it is about what the company's rules are or "
                         "what is allowed, call search_policy now with their words (and search_labour_code too if it is a legal question) "
                         "before you ask them anything or offer a ticket, then answer from what the search returns. If the policy does "
                         "not say, say so. If the question is not about work, HR or the company's rules, ignore this note and answer as "
                         "you would have.")


def asks_about_rules(text):
    text = str(text or "")
    return bool(_QUESTION.search(text) and _RULES.search(text))


def nudge(role, user_text, called):
    """What to tell the assistant once, before its reply is shown, or None. `called` is the tools it used in this answer:
    any tool at all (a balance, a document, a search) means it looked something up, so only an answer with no tool is sent back."""
    if called or not roles.allowed(role, "search_policy"):
        return None
    return SEARCH_FIRST if asks_about_rules(user_text) else None
