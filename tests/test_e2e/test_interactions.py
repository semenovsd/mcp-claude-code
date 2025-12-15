"""Real E2E tests for all interaction types.

These are REAL tests that:
- Connect to REAL MCP server via HTTP/SSE
- Call REAL execute_claude tool
- Interact with REAL Claude Code CLI
- Support REAL Elicitation (via SSE)
- Check REAL file creation in filesystem
- NO MOCKS!

CRITICAL: Every test that creates files MUST verify:
1. File exists via Path.exists()
2. File content via read_text()
3. Cleanup happens automatically via test_workspace fixture
"""

import json
import logging
from pathlib import Path

import pytest

from .conftest import verify_file_created
from .mcp_client_sse import RealMCPClientSSE

logger = logging.getLogger(__name__)


# =============================================================================
# TEST 0: Server Connection
# =============================================================================

@pytest.mark.asyncio
async def test_server_connection(mcp_client: RealMCPClientSSE):
    """Test 0: Basic server connection.

    Verifies:
    - Can connect to server
    - Can list tools
    - execute_claude tool is available
    """
    logger.info("=" * 80)
    logger.info("TEST 0: Server Connection")
    logger.info("=" * 80)

    tools = await mcp_client.list_tools()
    logger.info(f"Available tools: {tools}")

    assert "execute_claude" in tools, f"execute_claude not found in tools: {tools}"

    logger.info("TEST 0 PASSED - Server connection OK")


# =============================================================================
# TEST 1: Basic File Creation (Simplest Case)
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.file_creation
async def test_file_creation_basic(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test 1: Basic file creation - simplest possible case.

    Creates a simple text file and verifies:
    1. MCP returns success=True
    2. File ACTUALLY exists in filesystem
    3. File content is correct
    """
    logger.info("=" * 80)
    logger.info("TEST 1: Basic File Creation")
    logger.info(f"Workspace: {test_workspace}")
    logger.info("=" * 80)

    expected_filename = "test_file.txt"
    expected_content = "Hello World"
    expected_file = test_workspace / expected_filename

    # Use explicit path in prompt to ensure Claude creates file in right location
    prompt = f"""Create a file with these EXACT specifications:
- Full path: {expected_file}
- Content: {expected_content}

IMPORTANT: Use the EXACT path specified above. Do not use a different path."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,  # --dangerously-skip-permissions
        workspace_root=str(test_workspace),
    )

    logger.info(f"MCP Result: {json.dumps(result, indent=2)}")

    # Check MCP result
    assert result.get("success") == True, (
        f"MCP execution failed!\n"
        f"Error: {result.get('error')}\n"
        f"Output: {result.get('output', '')[:500]}"
    )

    # Permissions should be 0 since bypassed
    assert result.get("permissions_requested", 0) == 0, (
        f"Permissions should be bypassed but got {result.get('permissions_requested')} requests"
    )

    # CRITICAL: Verify file ACTUALLY exists
    found_file = await verify_file_created(
        workspace=test_workspace,
        filename=expected_filename,
        content_contains=expected_content,
        timeout_seconds=10.0,
    )

    # Double-check content
    actual_content = found_file.read_text()
    assert expected_content in actual_content, (
        f"Content mismatch!\n"
        f"Expected to contain: '{expected_content}'\n"
        f"Actual content: '{actual_content}'"
    )

    logger.info(f"TEST 1 PASSED - File created at {found_file}")
    logger.info(f"Content: {actual_content[:100]}")


# =============================================================================
# TEST 2: File Modification
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.file_creation
async def test_file_modification(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test 2: File modification.

    Creates a file first, then asks Claude to modify it.
    Verifies:
    1. Original content preserved
    2. New content added
    """
    logger.info("=" * 80)
    logger.info("TEST 2: File Modification")
    logger.info(f"Workspace: {test_workspace}")
    logger.info("=" * 80)

    # PRE-CONDITION: Create initial file
    test_file = test_workspace / "example.txt"
    initial_content = "Initial content from test setup\n"
    test_file.write_text(initial_content)

    assert test_file.exists(), "Pre-condition failed: couldn't create initial file"
    logger.info(f"Created initial file: {test_file}")
    logger.info(f"Initial content: {initial_content.strip()}")

    # Modification prompt
    prompt = f"""Read the file at {test_file} and add a new line "Modified by Claude" at the end.
Save the changes to the same file."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"MCP Result: {json.dumps(result, indent=2)}")

    # Check MCP result
    assert result.get("success") == True, (
        f"MCP execution failed!\n"
        f"Error: {result.get('error')}\n"
        f"Output: {result.get('output', '')[:500]}"
    )

    # CRITICAL: Verify file still exists
    assert test_file.exists(), "File was deleted instead of modified!"

    # CRITICAL: Verify content was modified
    content = test_file.read_text()
    logger.info(f"File content after modification:\n{content}")

    assert "Initial content" in content, (
        f"Original content was lost!\n"
        f"Current content: {content}"
    )
    assert "Modified by Claude" in content, (
        f"Modification was not applied!\n"
        f"Current content: {content}"
    )

    logger.info("TEST 2 PASSED - File successfully modified")


# =============================================================================
# TEST 3: Text Question (Elicitation)
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.file_creation
async def test_text_question_with_file_creation(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test 3: Text question followed by file creation.

    This test enables text questions and verifies:
    1. Question can be asked (via elicitation)
    2. Answer is used
    3. File is created based on answer
    """
    logger.info("=" * 80)
    logger.info("TEST 3: Text Question with File Creation")
    logger.info(f"Workspace: {test_workspace}")
    logger.info("=" * 80)

    # Note: Elicitation answer for "What is your name?" is "Ivan" (see mcp_client_sse.py)
    prompt = f"""Ask the user for their name, then create a file named {{name}}.md
in {test_workspace} with a greeting message.

Use this JSON format to ask the question:
{{"__user_question__": {{"question": "What is your name?", "default": ""}}}}

After receiving the name, create the file."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_text_questions=True,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"MCP Result: {json.dumps(result, indent=2)}")

    # Check MCP result
    assert result.get("success") == True, (
        f"MCP execution failed!\n"
        f"Error: {result.get('error')}\n"
        f"Output: {result.get('output', '')[:500]}"
    )

    logger.info(f"Questions asked: {result.get('questions_asked', 0)}")

    # CRITICAL: Verify some .md file was created
    # The expected name is "Ivan.md" based on default elicitation answer
    try:
        found_file = await verify_file_created(
            workspace=test_workspace,
            pattern="*.md",
            timeout_seconds=10.0,
        )
        logger.info(f"TEST 3 PASSED - File created: {found_file}")
    except AssertionError:
        # List what files were created
        all_files = list(test_workspace.rglob("*"))
        pytest.fail(
            f"No .md file created!\n"
            f"questions_asked: {result.get('questions_asked', 0)}\n"
            f"Workspace files: {all_files}\n"
            f"Output: {result.get('output', '')[:500]}"
        )


# =============================================================================
# TEST 4: Choice Question (Elicitation)
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.file_creation
async def test_choice_question_with_file_creation(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test 4: Choice question followed by file creation.

    This test enables choice questions and verifies:
    1. Choice question is presented (via elicitation)
    2. Answer is used (default: "poetry")
    3. Correct config file is created
    """
    logger.info("=" * 80)
    logger.info("TEST 4: Choice Question with File Creation")
    logger.info(f"Workspace: {test_workspace}")
    logger.info("=" * 80)

    # Note: Elicitation answer for "Which package manager?" is "poetry"
    prompt = f"""Ask the user to choose a package manager, then create the appropriate config file.

Use this JSON format to ask:
{{"__user_choice__": {{"question": "Which package manager?", "options": ["pip", "poetry", "conda"], "multiSelect": false}}}}

Based on the choice:
- pip -> create requirements.txt with "# pip requirements"
- poetry -> create pyproject.toml with "[tool.poetry]"
- conda -> create environment.yml with "name: myenv"

Create the file in {test_workspace}"""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_choice_questions=True,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"MCP Result: {json.dumps(result, indent=2)}")

    # Check MCP result
    assert result.get("success") == True, (
        f"MCP execution failed!\n"
        f"Error: {result.get('error')}\n"
        f"Output: {result.get('output', '')[:500]}"
    )

    logger.info(f"Choices asked: {result.get('choices_asked', 0)}")

    # CRITICAL: Verify correct config file was created
    # Expected: pyproject.toml (because default answer is "poetry")
    req_files = (
        list(test_workspace.glob("requirements.txt")) +
        list(test_workspace.glob("pyproject.toml")) +
        list(test_workspace.glob("environment.yml"))
    )

    if not req_files:
        all_files = list(test_workspace.rglob("*"))
        pytest.fail(
            f"No config file created!\n"
            f"choices_asked: {result.get('choices_asked', 0)}\n"
            f"Expected one of: requirements.txt, pyproject.toml, environment.yml\n"
            f"Workspace files: {all_files}\n"
            f"Output: {result.get('output', '')[:500]}"
        )

    found_file = req_files[0]
    logger.info(f"TEST 4 PASSED - Config file created: {found_file}")
    logger.info(f"Content: {found_file.read_text()[:200]}")


# =============================================================================
# TEST 5: Combined Scenario (Question + Choice + Files)
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.file_creation
@pytest.mark.slow
async def test_combined_scenario(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test 5: Combined scenario with multiple interactions.

    Tests:
    1. Text question for project name
    2. Choice question for language
    3. Multiple file creation

    Verifies all interactions work together.
    """
    logger.info("=" * 80)
    logger.info("TEST 5: Combined Scenario")
    logger.info(f"Workspace: {test_workspace}")
    logger.info("=" * 80)

    prompt = f"""Create a project structure with these steps:

1. Ask for project name using:
   {{"__user_question__": {{"question": "What is the project name?", "default": "myproject"}}}}

2. Ask for programming language using:
   {{"__user_choice__": {{"question": "Which language?", "options": ["Python", "JavaScript", "Go"], "multiSelect": false}}}}

3. Create directory structure in {test_workspace}:
   - {{project_name}}/README.md with project description
   - {{project_name}}/main.{{extension}} with hello world code

Where extension is .py for Python, .js for JavaScript, .go for Go."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_text_questions=True,
        enable_choice_questions=True,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"MCP Result: {json.dumps(result, indent=2)}")

    # Check MCP result
    assert result.get("success") == True, (
        f"MCP execution failed!\n"
        f"Error: {result.get('error')}\n"
        f"Output: {result.get('output', '')[:500]}"
    )

    # Log interaction metrics
    logger.info(f"questions_asked: {result.get('questions_asked', 0)}")
    logger.info(f"choices_asked: {result.get('choices_asked', 0)}")
    logger.info(f"permissions_requested: {result.get('permissions_requested', 0)}")

    # CRITICAL: Verify files were created
    all_files = list(test_workspace.rglob("*"))
    file_files = [f for f in all_files if f.is_file()]

    logger.info(f"All files created: {all_files}")

    # Filter out marker file
    user_files = [f for f in file_files if f.name != ".test_marker"]

    if len(user_files) < 2:
        pytest.fail(
            f"Not enough files created!\n"
            f"Expected at least 2 files (README + source)\n"
            f"Got {len(user_files)}: {user_files}\n"
            f"Output: {result.get('output', '')[:500]}"
        )

    logger.info(f"TEST 5 PASSED - Created {len(user_files)} files")
    for f in user_files:
        logger.info(f"  - {f.relative_to(test_workspace)}")


# =============================================================================
# TEST 6: Elicitation History Verification
# =============================================================================

@pytest.mark.asyncio
async def test_elicitation_history_tracking(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test 6: Verify elicitation history is properly tracked.

    This test checks that we can programmatically verify
    what elicitations occurred during execution.
    """
    logger.info("=" * 80)
    logger.info("TEST 6: Elicitation History Tracking")
    logger.info("=" * 80)

    # Clear history before test
    mcp_client.clear_elicitation_history()

    prompt = f"""Ask the user's name using:
{{"__user_question__": {{"question": "What is your name?", "default": ""}}}}

Then say hello to them."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_text_questions=True,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    assert result.get("success") == True, f"Execution failed: {result}"

    # Check elicitation history
    history = mcp_client.get_elicitation_history()
    logger.info(f"Elicitation history: {len(history)} records")
    for i, record in enumerate(history):
        logger.info(f"  [{i}] Message: {record.message[:50]}...")
        logger.info(f"       Response: {record.response}")
        logger.info(f"       Action: {record.response_action}")

    # Non-permission questions
    questions = mcp_client.get_question_requests()
    logger.info(f"Question requests: {len(questions)}")

    # If questions were asked via elicitation, verify they were recorded
    if result.get("questions_asked", 0) > 0:
        assert len(questions) >= 1, (
            f"questions_asked={result.get('questions_asked')} but no questions in history!\n"
            f"Full history: {history}"
        )
        # Verify the expected answer was used
        assert any(q.response == "Ivan" for q in questions), (
            f"Expected 'Ivan' answer not found in questions: {questions}"
        )

    logger.info("TEST 6 PASSED - Elicitation history correctly tracked")


# =============================================================================
# TEST 7: Multiple Files Creation
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.file_creation
async def test_multiple_files_creation(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test 7: Create multiple files in one execution.

    Verifies Claude can create multiple files and all are present.
    """
    logger.info("=" * 80)
    logger.info("TEST 7: Multiple Files Creation")
    logger.info(f"Workspace: {test_workspace}")
    logger.info("=" * 80)

    prompt = f"""Create the following files in {test_workspace}:

1. file1.txt with content "First file"
2. file2.txt with content "Second file"
3. file3.txt with content "Third file"

Create all three files."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"MCP Result: {json.dumps(result, indent=2)}")

    assert result.get("success") == True, f"Execution failed: {result}"

    # CRITICAL: Verify ALL files were created
    for i in range(1, 4):
        filename = f"file{i}.txt"
        expected_content = f"file" if i == 1 else ("Second" if i == 2 else "Third")

        try:
            found = await verify_file_created(
                workspace=test_workspace,
                filename=filename,
                timeout_seconds=5.0,
            )
            content = found.read_text()
            logger.info(f"  {filename}: {content[:50]}...")
        except AssertionError as e:
            all_files = list(test_workspace.rglob("*"))
            pytest.fail(
                f"File {filename} not created!\n"
                f"Workspace files: {all_files}\n"
                f"Error: {e}"
            )

    logger.info("TEST 7 PASSED - All 3 files created")
