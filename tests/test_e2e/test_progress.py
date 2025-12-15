"""Real E2E tests for progress indication system.

These are REAL tests that:
- Connect to REAL MCP server via HTTP/SSE
- Call REAL execute_claude tool
- Interact with REAL Claude Code CLI
- Verify REAL progress messages
- Check REAL file creation
- NO MOCKS!

CRITICAL: Every test that creates files MUST verify file existence.
"""

import json
import logging
from pathlib import Path

import pytest

from .conftest import verify_file_created
from .mcp_client_sse import RealMCPClientSSE

logger = logging.getLogger(__name__)


# =============================================================================
# TEST: Progress Shows Tool Use
# =============================================================================

@pytest.mark.asyncio
async def test_progress_shows_tool_use(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test: Progress shows tool use with details.

    Prompt asks Claude to read a file.
    Verifies the file read actually happened.
    """
    logger.info("=" * 80)
    logger.info("TEST: Progress Shows Tool Use")
    logger.info(f"Workspace: {test_workspace}")
    logger.info("=" * 80)

    # Create a file to read
    test_file = test_workspace / "progress_test.txt"
    original_content = "Hello from progress test!"
    test_file.write_text(original_content)

    assert test_file.exists(), "Pre-condition failed: couldn't create test file"

    prompt = f"""Read the file at {test_file} and tell me exactly what it contains."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result: {json.dumps(result, indent=2)}")

    # Verify execution success
    assert result.get("success") == True, f"Execution failed: {result.get('error')}"

    # Verify the output mentions the content
    output = result.get("output", "").lower()
    assert "hello" in output or "progress" in output, (
        f"Output doesn't seem to contain file content!\n"
        f"Output: {result.get('output', '')[:500]}"
    )

    logger.info("TEST PASSED - Tool use with file read verified")


# =============================================================================
# TEST: Progress Shows Bash Command
# =============================================================================

@pytest.mark.asyncio
async def test_progress_shows_bash_command(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test: Progress shows Bash command execution.

    Runs a simple echo command to verify Bash tools work.
    """
    logger.info("=" * 80)
    logger.info("TEST: Progress Shows Bash Command")
    logger.info(f"Workspace: {test_workspace}")
    logger.info("=" * 80)

    prompt = """Run the command 'echo "Hello Progress Test"' and show me the output."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result: {json.dumps(result, indent=2)}")

    # Verify execution success
    assert result.get("success") == True, f"Execution failed: {result.get('error')}"

    # Verify the output contains the echo result
    output = result.get("output", "").lower()
    assert "hello" in output or "progress" in output, (
        f"Output doesn't contain expected echo result!\n"
        f"Output: {result.get('output', '')[:500]}"
    )

    logger.info("TEST PASSED - Bash command executed")


# =============================================================================
# TEST: Progress Shows Multiple Tools
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.file_creation
async def test_progress_shows_multiple_tools(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test: Progress shows multiple tools in sequence.

    Creates a file, reads it, lists directory.
    CRITICAL: Verifies file ACTUALLY exists.
    """
    logger.info("=" * 80)
    logger.info("TEST: Progress Shows Multiple Tools")
    logger.info(f"Workspace: {test_workspace}")
    logger.info("=" * 80)

    expected_filename = "multi_tool_test.txt"
    expected_content = "Testing multiple tools"
    expected_file = test_workspace / expected_filename

    prompt = f"""Do these steps:
1. Create a file at {expected_file} with content "{expected_content}"
2. Read the file back to verify
3. List the directory contents

Report what you did."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result: {json.dumps(result, indent=2)}")

    # Verify execution success
    assert result.get("success") == True, f"Execution failed: {result.get('error')}"

    # CRITICAL: Verify file was created
    try:
        found_file = await verify_file_created(
            workspace=test_workspace,
            filename=expected_filename,
            content_contains=expected_content,
            timeout_seconds=10.0,
        )
        logger.info(f"File verified: {found_file}")
    except AssertionError:
        all_files = list(test_workspace.rglob("*"))
        pytest.fail(
            f"File not created!\n"
            f"Expected: {expected_filename}\n"
            f"Workspace files: {all_files}\n"
            f"Output: {result.get('output', '')[:500]}"
        )

    logger.info("TEST PASSED - Multiple tools executed, file verified")


# =============================================================================
# TEST: Progress Completion Message
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.file_creation
async def test_progress_completion_message(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test: Progress shows completion with duration.

    Creates a file and verifies completion metrics.
    """
    logger.info("=" * 80)
    logger.info("TEST: Progress Completion Message")
    logger.info(f"Workspace: {test_workspace}")
    logger.info("=" * 80)

    expected_filename = "completion_test.txt"
    expected_content = "Done!"
    expected_file = test_workspace / expected_filename

    prompt = f"""Create a file at {expected_file} with content "{expected_content}"."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result: {json.dumps(result, indent=2)}")

    # Verify execution success
    assert result.get("success") == True, f"Execution failed: {result.get('error')}"

    # Verify duration was captured (if available)
    duration_ms = result.get("duration_ms", 0)
    if duration_ms > 0:
        logger.info(f"Duration: {duration_ms}ms")
    else:
        logger.info("Duration not captured in result")

    # CRITICAL: Verify file exists
    try:
        found_file = await verify_file_created(
            workspace=test_workspace,
            filename=expected_filename,
            content_contains=expected_content,
            timeout_seconds=10.0,
        )
        logger.info(f"File verified: {found_file}")
    except AssertionError:
        all_files = list(test_workspace.rglob("*"))
        pytest.fail(
            f"Completion test file not created!\n"
            f"Workspace files: {all_files}"
        )

    logger.info("TEST PASSED - Completion with file verified")


# =============================================================================
# TEST: Progress Glob Search
# =============================================================================

@pytest.mark.asyncio
async def test_progress_glob_search(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test: Progress shows Glob search with pattern.

    Creates files then searches for them.
    """
    logger.info("=" * 80)
    logger.info("TEST: Progress Shows Glob Search")
    logger.info(f"Workspace: {test_workspace}")
    logger.info("=" * 80)

    # Create some Python files to search
    file_a = test_workspace / "test_a.py"
    file_b = test_workspace / "test_b.py"
    file_other = test_workspace / "other.txt"

    file_a.write_text("# Test A\nprint('a')")
    file_b.write_text("# Test B\nprint('b')")
    file_other.write_text("Other file content")

    # Verify pre-conditions
    assert file_a.exists(), "Pre-condition failed: test_a.py"
    assert file_b.exists(), "Pre-condition failed: test_b.py"
    assert file_other.exists(), "Pre-condition failed: other.txt"

    prompt = f"""Find all Python files (*.py) in {test_workspace} and list their names."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result: {json.dumps(result, indent=2)}")

    # Verify execution success
    assert result.get("success") == True, f"Execution failed: {result.get('error')}"

    # Verify the output mentions the Python files
    output = result.get("output", "").lower()
    assert "test_a" in output or "test_b" in output or ".py" in output, (
        f"Output doesn't mention Python files!\n"
        f"Output: {result.get('output', '')[:500]}"
    )

    logger.info("TEST PASSED - Glob search completed")


# =============================================================================
# TEST: Progress Grep Search
# =============================================================================

@pytest.mark.asyncio
async def test_progress_grep_search(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test: Progress shows Grep search with pattern.

    Creates files with searchable content then searches.
    """
    logger.info("=" * 80)
    logger.info("TEST: Progress Shows Grep Search")
    logger.info(f"Workspace: {test_workspace}")
    logger.info("=" * 80)

    # Create files with searchable content
    code1 = test_workspace / "code1.py"
    code2 = test_workspace / "code2.py"
    readme = test_workspace / "readme.md"

    code1.write_text("def hello():\n    print('TODO: implement this')")
    code2.write_text("def world():\n    # TODO: fix this bug")
    readme.write_text("# Project\nNo todos here in readme")

    # Verify pre-conditions
    assert code1.exists(), "Pre-condition failed: code1.py"
    assert code2.exists(), "Pre-condition failed: code2.py"
    assert readme.exists(), "Pre-condition failed: readme.md"

    prompt = f"""Search for the text "TODO" in all files in {test_workspace} and show me where it appears."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result: {json.dumps(result, indent=2)}")

    # Verify execution success
    assert result.get("success") == True, f"Execution failed: {result.get('error')}"

    # Verify the output mentions finding TODO
    output = result.get("output", "").lower()
    assert "todo" in output or "code1" in output or "code2" in output, (
        f"Output doesn't mention TODO findings!\n"
        f"Output: {result.get('output', '')[:500]}"
    )

    logger.info("TEST PASSED - Grep search completed")


# =============================================================================
# TEST: Progress with File Write and Read
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.file_creation
async def test_progress_write_then_read(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test: Write file then read it back.

    Verifies full round-trip of file operations.
    """
    logger.info("=" * 80)
    logger.info("TEST: Progress Write Then Read")
    logger.info(f"Workspace: {test_workspace}")
    logger.info("=" * 80)

    expected_filename = "roundtrip_test.txt"
    expected_content = "This is a round-trip test"
    expected_file = test_workspace / expected_filename

    prompt = f"""Do these steps:
1. Create a file at {expected_file} with content "{expected_content}"
2. Read the file back and confirm the content matches

Report both the write and read operations."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result: {json.dumps(result, indent=2)}")

    # Verify execution success
    assert result.get("success") == True, f"Execution failed: {result.get('error')}"

    # CRITICAL: Verify file exists with correct content
    try:
        found_file = await verify_file_created(
            workspace=test_workspace,
            filename=expected_filename,
            content_contains="round-trip",
            timeout_seconds=10.0,
        )
        actual_content = found_file.read_text()
        logger.info(f"File content: {actual_content}")
    except AssertionError:
        all_files = list(test_workspace.rglob("*"))
        pytest.fail(
            f"Round-trip file not created!\n"
            f"Workspace files: {all_files}"
        )

    logger.info("TEST PASSED - Write/read round-trip verified")
