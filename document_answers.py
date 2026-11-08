# document_answers.py: the shape of an answer about one of the person's own documents. When a document does not answer, the answer
# has two parts, in this order: first that the document does not specify it ("Your contract doesn't specify X"), then what the
# company policy or the law says, as a separate part clearly labelled with its source. The model followed the labelling by itself
# but skipped the first part, or stopped after it without looking at the policy, so code checks the final reply and asks for
# the missing part once. No AI and no third-party package, so it is unit-tested in CI.
import re

SILENT = re.compile(
    r"(?:(?:contract|document|amendment|certificate|agreement)[^.\n]{0,60}?(?:doesn't|does not|don't|do not|not|no|never)\s+"
    r"(?:say|mention|specify|state|include|cover|contain|address|provide|give|define|have|speak)"
    r"|(?:isn't|is not|not)\s+(?:mentioned|specified|stated|addressed|covered)[^.\n]{0,40}(?:contract|document)"
    r"|no mention|nothing (?:about|on)[^.\n]{0,40}(?:contract|document)|(?:contract|document)[^.\n]{0,40}nothing (?:about|on))", re.I)
SOURCES = ("search_collective_agreement", "search_policy", "search_labour_code")

# a nudge reaches the model as a user message, and it answers it like a person: "You're right, I apologize. Here's the corrected
# reply". The person has not seen the first reply, so they are told that.
HIDDEN = ("(A note from the app, not from the person. They have not seen your reply and must not hear about this note: do not "
          "apologise, do not say you corrected anything, answer their question directly.) ")
SEARCH_NOW = HIDDEN + ("Your reply says that the document does not specify it. Before you answer, call search_collective_agreement if you have it, "
              "then search_policy, and search_labour_code if it is a legal question. Then answer in two parts: first that the document does not "
              "specify it, then what the collective agreement, the company policy or the law says as a separate part, clearly labelled with its "
              "source (\"From the collective agreement: ...\", \"From the company policy, not your contract: ...\" or \"From the Code du travail, "
              "not your contract: ...\"). If none says it, say that too. Do not invent anything.")
SAY_SILENT_FIRST = HIDDEN + ("Your reply gives an answer from the collective agreement, the company policy or the law without first saying that the "
                    "document does not specify it. Rewrite it. If the document answers the question, answer from the document alone, name it and "
                    "quote it, and leave out the other sources. Otherwise start with \"Your <document> doesn't specify <what was asked>.\" and then "
                    "give the other source as a separate part, clearly labelled with its name (\"From the company policy, not your contract: ...\").")


# A retry note reaches the model as a user message, and it often opens its answer with "You're right. Let me correct that." even when told not to.
# The person never saw a first reply, so after a retry such an opening is taken off in code.
_OPENER = re.compile(
    r"^\s*(?:you(?:'|’)re right|you are right|i apologi[sz]e(?: for [^.!:\n]*)?|apologies(?: for [^.!:\n]*)?|"
    r"let me (?:correct|rewrite|redo|fix|give you|answer|clarify|try)[^.!:\n]*|"
    r"i (?:calculated|worked out|computed|added|did the (?:maths?|arithmetic)|shouldn(?:'|’)t have (?:calculated|added|worked))(?:\d\.\d|[^.!:\n])*|"
    r"here(?:'|’)s (?:the |a |my )?(?:corrected|correct|revised|updated|better|proper)[^.!:\n]*)[.,!:;]*[ \t]*\n*", re.I)


def strip_correction(reply):
    """The reply without a leading "You're right. Let me correct that." (only that kind of opening, and never the whole reply)."""
    text = str(reply or "")
    for _ in range(4):
        rest = _OPENER.sub("", text, count=1)
        if rest == text or not rest.strip():
            break
        text = rest
    return text.lstrip()


def says_document_silent(reply):
    return bool(SILENT.search(str(reply or "")))


def nudge(called, reply):
    """What to tell the assistant once, before its reply is shown, or None when the reply has the right shape.
    `called` is the tools it used in this answer."""
    if "read_my_document" not in called:
        return None                                     # not an answer about a document of theirs
    searched = any(t in called for t in SOURCES)
    silent = says_document_silent(reply)
    if silent and not searched:
        return SEARCH_NOW                               # "the document doesn't say it", and then it stopped
    if searched and not silent:
        return SAY_SILENT_FIRST                         # the policy or the law, with no word about the document being silent
    return None
