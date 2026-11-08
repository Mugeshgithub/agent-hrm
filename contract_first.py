# contract_first.py: a question to HR about where a new hire's contract stands, or a request to send one, is answered from get_contract_status.
# The tool description already says "use it for any question about a contract, including a request to send one", and the model still skips it:
# measured on main (Nov 2026), "Send Lea's contract to her now." made no call 14 times in 40, answering "I can't send contracts" without looking
# (and so without saying the contract is waiting for approval). So code checks the final reply: a contract question answered without that tool
# is sent back once, with a note to call it. No AI and no third-party package, so it is unit-tested in CI.
import re

import roles
from document_answers import HIDDEN

TOOL = "get_contract_status"
_CONTRACT = re.compile(r"\bcontracts?\b|\bcontrats?\b", re.I)
# what they want to know or do with it: send it, where it stands, whether it is approved, signed, ready...
_ASKS = re.compile(
    r"\b(?:send|sent|sending|email|e-mail|mail|issue|give|hand|forward|download|sign|signed|signature|ready|status|where|stand|stands|"
    r"approve|approved|approval|draft|waiting|pending|go out|gone out|out yet|envoy\w*|sign[eé]\w*|pr[eê]t|statut)\b", re.I)
# their own contract is a document (read_my_document), not a new hire's
_OWN = re.compile(r"\b(?:my|mine|mon|ma|mes)\b[^.?!]{0,12}\bcontr", re.I)

CONTRACT_FIRST = HIDDEN + ("The person asked about a contract and you answered without calling get_contract_status. If it is about where a new hire's "
                           "contract stands, or a request to send one, call get_contract_status now with the person's name (a first name is enough), "
                           "then answer from its result: say where the contract stands (no draft, a draft, waiting for approval, approved, rejected or "
                           "signed). The app never sends a contract: if they asked you to send it, say that too and what has to happen first. If the "
                           "question is not about a new hire's contract, ignore this note and answer as you would have.")


def asks_about_a_contract(text):
    text = str(text or "")
    return bool(_CONTRACT.search(text) and _ASKS.search(text) and not _OWN.search(text))


def nudge(role, user_text, called):
    """What to tell the assistant once, before its reply is shown, or None. `called` is the tools it used in this answer."""
    if TOOL in called or not roles.allowed(role, TOOL):
        return None
    return CONTRACT_FIRST if asks_about_a_contract(user_text) else None
