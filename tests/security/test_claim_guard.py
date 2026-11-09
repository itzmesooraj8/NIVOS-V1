"""Unit tests for Claim Guard verification."""

from nivos.core.types import ToolResult
from nivos.server.claim_guard import GUARD_REPLACEMENT, verify_and_guard_claims


def test_claim_guard_intercepts_unverified_file_creation():
    content = "I have created the file test-nivos.txt in your downloads directory with the word 'hello'."
    result = verify_and_guard_claims(content, [])
    assert result == GUARD_REPLACEMENT


def test_claim_guard_allows_verified_file_creation():
    content = "I have created the file test-nivos.txt in your downloads directory."
    tool_results = [
        ToolResult(tool_name="file_write", content="OK", success=True)
    ]
    result = verify_and_guard_claims(content, tool_results)
    assert result == content


def test_claim_guard_intercepts_when_tool_failed():
    content = "I have created the file test-nivos.txt."
    tool_results = [
        ToolResult(tool_name="file_write", content="Permission denied", success=False)
    ]
    result = verify_and_guard_claims(content, tool_results)
    assert result == GUARD_REPLACEMENT


def test_claim_guard_intercepts_unverified_install():
    content = "I have installed the package requests."
    assert verify_and_guard_claims(content, []) == GUARD_REPLACEMENT

    # Verified install
    tool_results = [
        ToolResult(tool_name="sandboxed_shell", content="Successfully installed requests", success=True)
    ]
    assert verify_and_guard_claims(content, tool_results) == content


def test_claim_guard_intercepts_unverified_schedule():
    content = "I have scheduled the reminder for 5 PM tomorrow."
    assert verify_and_guard_claims(content, []) == GUARD_REPLACEMENT

    # Verified schedule
    tool_results = [
        ToolResult(tool_name="schedule_task", content="Scheduled task #1", success=True)
    ]
    assert verify_and_guard_claims(content, tool_results) == content


def test_claim_guard_leaves_conversational_text_untouched():
    conversations = [
        "Hello! How can I assist you today?",
        "Photosynthesis is the process used by plants to convert light energy into chemical energy.",
        "Here is a Python function you can use:\n\ndef add(a, b):\n    return a + b\n",
        "You can create a file using the file_write tool if you specify the path.",
    ]
    for text in conversations:
        assert verify_and_guard_claims(text, []) == text
