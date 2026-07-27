"""Agent loop: tool decision -> module call -> final answer, plus the mock brain."""

import json

from vahub.agent.llm.base import LLMResult, ToolSpec
from vahub.agent.llm.mock import MockLLM
from vahub.agent.loop import AgentLoop
from vahub.agent.session import SessionStore
from vahub.core.config import BudgetConfig


class _FakeRegistry:
    def agent_catalog(self):
        return [{
            "name": "fake.echo", "module": "fake", "tool": "echo",
            "description": "echo", "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
        }]

    def unavailable_modules(self):
        return []


class _FakeModuleAPI:
    def __init__(self):
        self.calls = []

    async def call(self, module, tool, args, timeout_s=10.0, principal="agent"):
        self.calls.append((module, tool, args, principal))
        return {"ok": True, "result": args.get("text", "")}


class _ScriptedLLM:
    """First turn: call the echo tool. After the tool result: answer with text."""

    async def complete(self, messages, tools):
        if messages and messages[-1].get("role") == "tool":
            payload = json.loads(messages[-1]["content"])
            return LLMResult(text="done: " + str(payload["result"]))
        return LLMResult(tool_calls=[{"id": "c1", "name": "fake__echo", "arguments": {"text": "hi"}}])

    async def aclose(self):
        return None


async def test_agent_calls_tool_then_answers():
    api = _FakeModuleAPI()
    agent = AgentLoop(_FakeRegistry(), api, _ScriptedLLM(), BudgetConfig())
    session = SessionStore().get_or_create(None)

    result = await agent.run_turn(session, "please echo hi")

    # the tool was routed back to (module=fake, tool=echo) via the sanitized name
    assert api.calls == [("fake", "echo", {"text": "hi"}, "agent")]
    assert result["reply"] == "done: hi"
    assert result["steps"][0]["tool"] == "fake__echo"
    # the final assistant message is persisted in the session
    assert session.messages[-1] == {"role": "assistant", "content": "done: hi"}


async def test_agent_iteration_limit_is_enforced():
    class _LoopingLLM:
        async def complete(self, messages, tools):
            return LLMResult(tool_calls=[{"id": "c", "name": "fake__echo", "arguments": {"text": "x"}}])

        async def aclose(self):
            return None

    budgets = BudgetConfig(iterations_per_turn=3)
    agent = AgentLoop(_FakeRegistry(), _FakeModuleAPI(), _LoopingLLM(), budgets)
    session = SessionStore().get_or_create(None)
    result = await agent.run_turn(session, "loop forever")
    assert result.get("stopped") == "iteration_limit"


async def test_mock_llm_time_intent_and_answer():
    tools = [
        ToolSpec(name="time__get_current_time", module="time", tool="get_current_time", description="", parameters={}),
        ToolSpec(name="time__speak_current_time", module="time", tool="speak_current_time", description="", parameters={}),
    ]
    mock = MockLLM()

    decide = await mock.complete([{"role": "user", "content": "what time is it in Tokyo?"}], tools)
    assert decide.tool_calls and decide.tool_calls[0]["name"] == "time__get_current_time"
    assert decide.tool_calls[0]["arguments"].get("tz") == "Asia/Tokyo"

    answer = await mock.complete(
        [{"role": "tool", "content": json.dumps({"ok": True, "result": "12:00"})}], tools
    )
    assert answer.tool_calls == [] and "12:00" in (answer.text or "")
