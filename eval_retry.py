# eval_retry.py: run a failed eval once more, in the open. Evals call a live model and a judge, and each has a
# small chance of failing for no reason in the code, so in CI a failed test gets one retry. Nothing is hidden:
# the log says "PASS (retry)", a list of retried tests ends the run, and the run fails anyway when more
# than MAX_RETRIED tests needed one (many retries mean something is wrong, not unlucky).
# Pure logic (no API), so it is unit-tested in CI.
MAX_RETRIED = 2


def run_with_retry(run_once, retries):
    """Call run_once() -> (problems, info) until it has no problems or the retries are used up.
    Returns (problems, info, failed_before): the last attempt's result, and the (problems, info) of
    every earlier attempt that failed."""
    failed_before = []
    for attempt in range(retries + 1):
        problems, info = run_once()
        if not problems or attempt == retries:
            return problems, info, failed_before
        failed_before.append((problems, info))


def retry_report(retried, limit=MAX_RETRIED):
    """retried: [(test name, passed on the retry)]. Returns (lines to print at the end, too many?)."""
    if not retried:
        return [], False
    lines = [f"Retried tests ({len(retried)}):"]
    lines += [f"  {name}: {'passed on retry' if passed else 'failed again'}" for name, passed in retried]
    too_many = len(retried) > limit
    if too_many:
        lines.append(f"FAIL: {len(retried)} tests needed a retry, more than the {limit} allowed.")
    return lines, too_many
