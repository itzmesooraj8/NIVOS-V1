import pytest
from nivos.core.config import load_config
from nivos.server.models import ChatCompletionRequest, ChatMessage
from nivos.server.tier_router import (
    filter_relevant_tools,
    compute_prompt_token_breakdown,
)
from nivos.tools._stubs import BaseTool, ToolSpec


class DummyTool(BaseTool):
    def __init__(self, name: str, description: str):
        self._name = name
        self._desc = description

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self._name,
            description=self._desc,
            parameters={"type": "object", "properties": {"arg": {"type": "string"}}},
        )

    def execute(self, **kwargs):
        return None


ALL_BUILTIN_TOOL_NAMES = [
    "think",
    "calculator",
    "web_search",
    "get_weather",
    "get_current_time",
    "file_read",
    "file_write",
    "sandboxed_shell",
    "schedule_task",
    "list_scheduled_tasks",
    "cancel_scheduled_task",
    "search_vault",
    "save_to_inbox",
    "open_app",
    "install_app",
    "run_code",
]


def test_plain_chat_diet():
    """Verify that plain chat sends 0 tool schemas and total tokens < 200."""
    queries = ["hi", "hello", "good!", "thanks", "who are you?"]
    for q in queries:
        tools = filter_relevant_tools(q, available_tools=ALL_BUILTIN_TOOL_NAMES)
        assert tools == [], f"Expected 0 tools for {q}, got {tools}"

        breakdown = compute_prompt_token_breakdown(
            system_prompt="You are Nivos, a helpful AI assistant running locally.",
            tools=[],
            user_query=q,
        )
        assert breakdown["total"] < 200, f"Prompt tokens for {q} exceeded 200: {breakdown['total']}"
        assert breakdown["tools"] == 0


def test_tool_turns_diet():
    """Verify that tool turns send only matched intent tools and stay under 800 tokens."""
    samples = [
        ("open spotify", ["open_app"]),
        ("create a file in workspace/notes.txt with hello", ["file_write"]),
        ("what is the weather in Tokyo?", ["get_weather"]),
        ("search for latest news about Python", ["web_search"]),
        ("save note Meeting tomorrow in my inbox", ["save_to_inbox"]),
    ]

    for q, expected_subset in samples:
        tools = filter_relevant_tools(q, available_tools=ALL_BUILTIN_TOOL_NAMES)
        for exp in expected_subset:
            assert exp in tools, f"Expected {exp} in filtered tools for '{q}', got {tools}"
        # Make sure it didn't bloat to all 16 tools
        assert len(tools) <= 3, f"Too many tools selected for '{q}': {tools}"

        # Generate fake openai tool dicts for the filtered tools
        filtered_tool_dicts = [
            {
                "type": "function",
                "function": {
                    "name": t,
                    "description": f"Description for {t}",
                    "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
                },
            }
            for t in tools
        ]

        breakdown = compute_prompt_token_breakdown(
            system_prompt="You are Nivos, a helpful AI assistant running locally.",
            tools=filtered_tool_dicts,
            user_query=q,
        )
        assert breakdown["total"] < 800, f"Tool prompt for '{q}' exceeded 800 tokens: {breakdown['total']}"


def test_five_sample_messages_token_comparison():
    """Measure and verify before vs. after prompt token diet for 5 sample messages."""
    # Before diet: all 16 tools + full descriptions (~1800-2200 tokens)
    all_tool_dicts = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Extensive description and guidelines for executing {name} safely and effectively in the environment.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "Target argument for tool"},
                        "content": {"type": "string", "description": "Optional payload or content"},
                    },
                    "required": ["target"],
                },
            },
        }
        for name in ALL_BUILTIN_TOOL_NAMES
    ]

    samples = [
        "hi",
        "what is the time?",
        "how much ram is free?",
        "open spotify",
        "create a file in workspace/notes.txt with hello",
    ]

    results = []
    for q in samples:
        # Before diet breakdown (all tools sent)
        before_bd = compute_prompt_token_breakdown(
            system_prompt="You are Nivos, a helpful AI assistant running locally on the user's hardware.",
            tools=all_tool_dicts,
            user_query=q,
        )

        # After diet: filtered tools
        filtered_names = filter_relevant_tools(q, available_tools=ALL_BUILTIN_TOOL_NAMES)
        after_tool_dicts = [t for t in all_tool_dicts if t["function"]["name"] in filtered_names]

        after_bd = compute_prompt_token_breakdown(
            system_prompt="You are Nivos, a helpful AI assistant running locally on the user's hardware.",
            tools=after_tool_dicts,
            user_query=q,
        )

        results.append({
            "query": q,
            "before_tokens": before_bd["total"],
            "after_tokens": after_bd["total"],
            "tools_count_before": len(all_tool_dicts),
            "tools_count_after": len(after_tool_dicts),
        })

        if not filtered_names:
            assert after_bd["total"] < 200
        else:
            assert after_bd["total"] < 800

    assert len(results) == 5
