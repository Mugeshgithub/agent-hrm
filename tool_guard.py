# tool_guard.py: the calls the code refuses. The model sometimes tries a tool it was not offered or is not allowed to use; the
# code refuses and says why. Such a call is NOT counted as "called": it did nothing, so the evals' must_call and must_not_call
# checks (and anything else that asks "what did the assistant do?") ignore it. No AI and no third-party package, so it is
# unit-tested in CI.
import roles

ROLE_REFUSAL = "That action isn't available for your role."
AGREEMENT_FIRST = ("The collective agreement comes before the company policy. Call search_collective_agreement first, then search_policy "
                   "only if the agreement does not answer.")


def refusal(role, name, called, agreement_on_file=False):
    """The error to give back for a call the code refuses, or None when it may go ahead.
    `called` is the tools already used in this answer. With a collective agreement on file, the company policy is searched
    after it, never before (the model's own habit is to search the policy first, whatever the prompt says)."""
    if not roles.allowed(role, name):
        return {"error": ROLE_REFUSAL}
    if agreement_on_file and name == "search_policy" and "search_collective_agreement" not in called:
        return {"error": AGREEMENT_FIRST}
    return None
