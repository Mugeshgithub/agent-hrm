# judge_parse.py: reads the judge's answer. Pure text handling (no API), so it is unit-tested in CI.
import json


def verdict(text):
    """The judge's list of unsupported claims, or None when the text holds no usable verdict.

    The judge sometimes thinks aloud: it gives a verdict, writes "Wait, let me reconsider", and gives a new
    one. The LAST verdict is its answer. A verdict is a JSON object with an "unsupported" list; the claim
    objects inside it do not count, so an answer cut off half-way gives None (the caller fails loudly),
    never an empty list (a silent pass).
    """
    decoder = json.JSONDecoder()
    found = None
    i = text.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict) and isinstance(obj.get("unsupported"), list):
            found = obj["unsupported"]
        i = text.find("{", end)
    return found
