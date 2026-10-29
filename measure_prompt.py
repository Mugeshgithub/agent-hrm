# measure_prompt.py: how big is the fixed part of every call, per role?
from dotenv import load_dotenv
load_dotenv()
import agent


def count(system, tools=None):
    kwargs = {"tools": tools} if tools else {}
    return agent.client.messages.count_tokens(
        model=agent.MODEL, system=system,
        messages=[{"role": "user", "content": "hi"}], **kwargs,
    ).input_tokens


bare = count([{"type": "text", "text": "x"}])
for user_id in ("E001", "M001", "H001"):
    role = agent.get_employee(user_id)["role"]
    system = agent.system_prompt_for(user_id)
    tools = agent.tools_for(role, agent.TOOLS)
    full = count(system, tools)
    no_tools = count(system)
    print(f"{role:9} total {full:5}   tools {full - no_tools:5} ({len(tools)} tools)   prompt {no_tools - bare:4}")