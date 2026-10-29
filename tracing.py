# tracing.py: Langfuse tracing that switches itself off when there are no keys (e.g. in CI)
import os
from contextlib import nullcontext

ENABLED = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))

if ENABLED:
    from langfuse import get_client, observe, propagate_attributes
    from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor

    AnthropicInstrumentor().instrument()   # records every Claude call: prompt, reply, tokens, cost
    langfuse = get_client()
else:
    def observe(*args, **kwargs):          # does nothing, so the code runs the same without Langfuse
        def wrap(fn):
            return fn
        return wrap

    def propagate_attributes(**kwargs):    # accepts any arguments (trace_name, user_id, tags...) and ignores them
        return nullcontext()


def current_trace_id():
    # the ID of the trace we're inside right now, or None when tracing is off
    return langfuse.get_current_trace_id() if ENABLED else None


def score_trace(trace_id, passed, comment=""):
    # attach "passed" = 1 or 0 to a trace, so failed tests can be filtered in Langfuse
    if ENABLED and trace_id:
        langfuse.create_score(trace_id=trace_id, name="passed",
                              value=1 if passed else 0, comment=comment)


def flush():
    # send any traces and scores still waiting, before the program exits
    if ENABLED:
        langfuse.flush()