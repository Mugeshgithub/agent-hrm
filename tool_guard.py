# tool_guard.py: the calls the code refuses. The model sometimes tries a tool it was not offered or is not allowed to use; the
# code refuses and says why. Such a call is NOT counted as "called": it did nothing, so the evals' must_call and must_not_call
# checks (and anything else that asks "what did the assistant do?") ignore it. No AI and no third-party package, so it is
# unit-tested in CI.
import roles

ROLE_REFUSAL = "That action isn't available for your role."


def refusal(role, name, called):
    """The error to give back for a call the code refuses, or None when it may go ahead.
    `called` is the tools already used in this answer (kept for rules that depend on it)."""
    if not roles.allowed(role, name):
        return {"error": ROLE_REFUSAL}
    return None
