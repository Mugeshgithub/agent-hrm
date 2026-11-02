# labour_code/agent_queries.py: what query does the REAL agent send to search_labour_code for each test question?
# The tool receives the agent's rewording, not the user's words (that rewording once hid L3141-3), so retrieval
# must be measured on it. Saved to agent_queries.json so the eval is repeatable and we only pay once.
# Run from the project folder:  python labour_code/agent_queries.py
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)                      # to import agent.py from the project folder
sys.path.insert(0, os.path.join(ROOT, "labour_code"))

import agent                                  # same prompt, same tools, same model as production
from retrieval_cases import CASES, HOLDOUT, SITUATIONS

OUT = os.path.join(ROOT, "labour_code", "agent_queries.json")


def agent_query(question):
    # force the labour tool, so we see exactly what the agent would put in its "question" field
    resp = agent.client.messages.create(
        model=agent.MODEL, max_tokens=200,
        system=agent.system_prompt_for("E001"),
        tools=agent.tools_for("employee", agent.TOOLS),
        tool_choice={"type": "tool", "name": "search_labour_code"},
        messages=[{"role": "user", "content": question}],
    )
    return next(b.input["question"] for b in resp.content if b.type == "tool_use")


if __name__ == "__main__":
    saved = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    for question, lang, expected in CASES + HOLDOUT + SITUATIONS:
        if question not in saved:             # only pay for new questions
            saved[question] = agent_query(question)
            print(f"[{lang}] {question}\n      -> {saved[question]}")
    json.dump(saved, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n{len(saved)} agent queries saved to {OUT}")
