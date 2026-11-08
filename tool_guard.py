# tool_guard.py: the calls the code refuses. The model sometimes tries a tool it was not offered (or is not allowed to use
# for this answer); the code refuses and says why. Such a call is NOT counted as "called": it did nothing, so the evals'
# must_call and must_not_call checks (and anything else that asks "what did the assistant do?") ignore it. No AI and no
# third-party package, so it is unit-tested in CI.
import employee_files
import roles

ROLE_REFUSAL = "That action isn't available for your role."
DOCUMENT_REFUSAL = ("This answer comes from the person's own document, so it cannot also use the company policy or the law. "
                    "Say what the document says, or that it does not say it, and offer to look in the policy next.")


def refusal(role, name, called):
    """The error to give back for a call the code refuses, or None when it may go ahead.
    `called` is the tools already used in this answer."""
    if not roles.allowed(role, name):
        return {"error": ROLE_REFUSAL}
    if name in employee_files.DOCUMENT_ONLY_BLOCKS and "read_my_document" in called:
        return {"error": DOCUMENT_REFUSAL}
    return None
