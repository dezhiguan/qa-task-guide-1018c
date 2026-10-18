import json
from keel import Agent
from keel.protocol.errors import ErrorCode, KeelError
from role import execute

agent = Agent.from_manifest("agent.yaml")

@agent.entry
async def run(request, ctx):
    raw = request.input.get("text") or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"text": raw}
    if isinstance(data, list):
        raise KeelError(ErrorCode.SERVER_INVALID_PARAM, "输入必须是 JSON 对象")
    if not isinstance(data, dict):
        data = {"text": raw}
    ctx.step("role")
    result = await execute(data, ctx)
    if not isinstance(result, dict):
        raise KeelError(ErrorCode.SERVER_INVALID_PARAM, "岗位结果必须是 JSON 对象")
    return ctx.final(json.dumps(result, ensure_ascii=False))

app = agent.asgi()
