# measure_prompt.py: how big is what we send on EVERY call?
from dotenv import load_dotenv
load_dotenv()
import agent


def count(system, tools=None):
    kwargs = {"tools": tools} if tools else {}
    return agent.client.messages.count_tokens(
        model=agent.MODEL, system=system,
        messages=[{"role": "user", "content": "hi"}], **kwargs,
    ).input_tokens


full = count(agent.system_prompt_for("E001"), agent.TOOLS)
no_tools = count(agent.system_prompt_for("E001"))
bare = count([{"type": "text", "text": "x"}])

print(f"Whole fixed part of each call: {full} tokens")
print(f"  tool definitions:            {full - no_tools}")
print(f"  system prompt:               {no_tools - bare}")
print(f"Rules in SYSTEM_PROMPT:        {len([l for l in agent.SYSTEM_PROMPT.splitlines() if l.strip()])}")
print(f"Tools:                         {len(agent.TOOLS)}")