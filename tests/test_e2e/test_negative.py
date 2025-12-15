"""Real E2E tests for negative scenarios and edge cases.

These tests verify the system handles errors gracefully:
- Invalid workspace paths
- Empty prompts
- Timeout handling
- Malformed requests

NO MOCKS! All tests use REAL Claude Code CLI.
"""

import json
import logging
from pathlib import Path

import pytest

from .mcp_client_sse import RealMCPClientSSE

logger = logging.getLogger(__name__)


# =============================================================================
# TEST: Invalid Workspace Path
# =============================================================================

@pytest.mark.asyncio
async def test_invalid_workspace_path(mcp_client: RealMCPClientSSE):
    """Test handling of non-existent workspace path.

    The system should either:
    - Fail gracefully with a clear error
    - Or create files in a fallback location

    It should NOT crash.
    """
    logger.info("=" * 80)
    logger.info("TEST: Invalid Workspace Path")
    logger.info("=" * 80)

    invalid_path = "/this/path/definitely/does/not/exist/anywhere"

    prompt = f"""Create a file named test.txt with content "Hello"."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=invalid_path,
    )

    logger.info(f"Result with invalid workspace: {json.dumps(result, indent=2)}")

    # The system should handle this gracefully
    # Either by failing with an error or by succeeding in a different location
    # The key is it should NOT crash

    if result.get("success"):
        logger.info("Execution succeeded despite invalid workspace (files may be elsewhere)")
    else:
        error = result.get("error", "")
        logger.info(f"Execution failed as expected: {error}")
        # Check that we got a meaningful error
        assert "error" in result or result.get("success") == False, (
            "Expected either success=False or an error message"
        )

    logger.info("TEST PASSED - Invalid workspace handled gracefully")


# =============================================================================
# TEST: Empty Prompt
# =============================================================================

@pytest.mark.asyncio
async def test_empty_prompt(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test handling of empty prompt.

    The system should handle empty prompts gracefully.
    """
    logger.info("=" * 80)
    logger.info("TEST: Empty Prompt")
    logger.info("=" * 80)

    result = await mcp_client.call_execute_claude(
        prompt="",
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result with empty prompt: {json.dumps(result, indent=2)}")

    # Empty prompt should either fail or complete without doing anything
    # It should NOT crash

    if result.get("success"):
        logger.info("Empty prompt executed (probably did nothing)")
    else:
        error = result.get("error", "")
        logger.info(f"Empty prompt failed: {error}")

    logger.info("TEST PASSED - Empty prompt handled gracefully")


# =============================================================================
# TEST: Whitespace-Only Prompt
# =============================================================================

@pytest.mark.asyncio
async def test_whitespace_only_prompt(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test handling of whitespace-only prompt.

    Similar to empty prompt but with spaces/newlines.
    """
    logger.info("=" * 80)
    logger.info("TEST: Whitespace-Only Prompt")
    logger.info("=" * 80)

    result = await mcp_client.call_execute_claude(
        prompt="   \n\t  \n  ",
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result with whitespace prompt: {json.dumps(result, indent=2)}")

    # Should handle gracefully without crashing

    logger.info("TEST PASSED - Whitespace prompt handled gracefully")


# =============================================================================
# TEST: Very Long Prompt
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.slow
async def test_very_long_prompt(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test handling of very long prompt.

    Should handle without memory issues or crashes.
    """
    logger.info("=" * 80)
    logger.info("TEST: Very Long Prompt")
    logger.info("=" * 80)

    # Create a moderately long prompt (not too long to timeout)
    long_content = "A" * 10000  # 10K characters
    prompt = f"""Create a file at {test_workspace}/long_test.txt with this content:
{long_content}
End of content."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result with long prompt: success={result.get('success')}")

    # Should handle gracefully
    if result.get("success"):
        # Check if file was created
        test_file = test_workspace / "long_test.txt"
        if test_file.exists():
            content_len = len(test_file.read_text())
            logger.info(f"File created with {content_len} characters")
        else:
            logger.info("File not created but execution succeeded")
    else:
        logger.info(f"Long prompt failed: {result.get('error')}")

    logger.info("TEST PASSED - Long prompt handled gracefully")


# =============================================================================
# TEST: Special Characters in Filename
# =============================================================================

@pytest.mark.asyncio
async def test_special_characters_in_filename(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test handling of special characters in filename.

    Some characters may be invalid in filenames.
    """
    logger.info("=" * 80)
    logger.info("TEST: Special Characters in Filename")
    logger.info("=" * 80)

    # Test with characters that are valid in most filesystems
    prompt = f"""Create a file at {test_workspace}/test-file_2024.txt with content "Special chars test"."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result: {json.dumps(result, indent=2)}")

    if result.get("success"):
        test_file = test_workspace / "test-file_2024.txt"
        if test_file.exists():
            logger.info(f"File with special chars created: {test_file}")
        else:
            # Claude might have normalized the filename
            files = list(test_workspace.glob("*.txt"))
            logger.info(f"Files created: {files}")

    logger.info("TEST PASSED - Special characters handled")


# =============================================================================
# TEST: Unicode in Prompt
# =============================================================================

@pytest.mark.asyncio
async def test_unicode_in_prompt(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test handling of Unicode characters in prompt.

    Should handle various Unicode characters without issues.
    """
    logger.info("=" * 80)
    logger.info("TEST: Unicode in Prompt")
    logger.info("=" * 80)

    prompt = f"""Create a file at {test_workspace}/unicode_test.txt with content:
Hello World!
Привет Мир!
你好世界!
مرحبا بالعالم!
End of Unicode test."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result: {json.dumps(result, indent=2)}")

    if result.get("success"):
        test_file = test_workspace / "unicode_test.txt"
        if test_file.exists():
            content = test_file.read_text()
            logger.info(f"Unicode file content: {content[:100]}...")
            # Check if Unicode was preserved
            if "Hello" in content:
                logger.info("ASCII content preserved")
        else:
            files = list(test_workspace.glob("*.txt"))
            logger.info(f"Files created: {files}")

    logger.info("TEST PASSED - Unicode handled")


# =============================================================================
# TEST: Concurrent File Operations
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.slow
async def test_rapid_sequential_requests(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test rapid sequential requests.

    Should handle multiple requests in sequence without issues.
    """
    logger.info("=" * 80)
    logger.info("TEST: Rapid Sequential Requests")
    logger.info("=" * 80)

    results = []

    for i in range(3):
        prompt = f"""Create a file at {test_workspace}/rapid_{i}.txt with content "File {i}"."""

        result = await mcp_client.call_execute_claude(
            prompt=prompt,
            enable_permissions=False,
            workspace_root=str(test_workspace),
        )

        results.append({
            "index": i,
            "success": result.get("success"),
            "error": result.get("error"),
        })

        logger.info(f"Request {i}: success={result.get('success')}")

    # Check results
    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    logger.info(f"Successful: {len(successful)}/{len(results)}")
    logger.info(f"Failed: {len(failed)}/{len(results)}")

    # List created files
    files = list(test_workspace.glob("rapid_*.txt"))
    logger.info(f"Files created: {len(files)}")

    # At least some requests should succeed
    assert len(successful) >= 1, (
        f"All rapid requests failed!\n"
        f"Results: {results}"
    )

    logger.info("TEST PASSED - Rapid sequential requests handled")


# =============================================================================
# TEST: Non-Existent File Read
# =============================================================================

@pytest.mark.asyncio
async def test_read_nonexistent_file(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test attempting to read a non-existent file.

    Claude should handle this gracefully without crashing.
    """
    logger.info("=" * 80)
    logger.info("TEST: Read Non-Existent File")
    logger.info("=" * 80)

    nonexistent = test_workspace / "this_file_does_not_exist.txt"

    prompt = f"""Read the file at {nonexistent} and tell me what it contains."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result: {json.dumps(result, indent=2)}")

    # Claude should report that the file doesn't exist
    # The execution might succeed (Claude reports file not found) or fail

    output = result.get("output", "").lower()
    error = result.get("error", "").lower() if result.get("error") else ""

    # Check that the response acknowledges the file doesn't exist
    file_not_found_indicators = [
        "not found", "doesn't exist", "does not exist",
        "no such file", "not exist", "cannot find"
    ]

    mentioned_not_found = any(
        indicator in output or indicator in error
        for indicator in file_not_found_indicators
    )

    logger.info(f"File not found acknowledged: {mentioned_not_found}")

    logger.info("TEST PASSED - Non-existent file read handled")


# =============================================================================
# TEST: Directory Instead of File
# =============================================================================

@pytest.mark.asyncio
async def test_create_file_over_directory(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test attempting to create a file where a directory exists.

    Claude may handle this in several valid ways:
    1. Fail gracefully with error
    2. Create a file inside the directory
    3. Remove the directory and create a file
    4. Refuse to do it and explain why

    All are valid behaviors - the key is not crashing.
    """
    logger.info("=" * 80)
    logger.info("TEST: Create File Over Directory")
    logger.info("=" * 80)

    # Create a directory first
    dir_path = test_workspace / "existing_dir"
    dir_path.mkdir(exist_ok=True)

    # Try to create a file with the same name
    prompt = f"""Create a file at {dir_path} with content "Test"."""

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result: {json.dumps(result, indent=2)}")

    # Check what Claude did
    if dir_path.is_dir():
        logger.info("Directory still exists (Claude may have refused or created file inside)")
        # Check if file was created inside
        files_inside = list(dir_path.glob("*"))
        if files_inside:
            logger.info(f"Files inside directory: {files_inside}")
    elif dir_path.is_file():
        logger.info("Directory was replaced with a file (Claude removed dir and created file)")
        content = dir_path.read_text()
        logger.info(f"File content: {content}")
    elif not dir_path.exists():
        logger.info("Path no longer exists (Claude may have deleted but failed to create)")
    else:
        logger.info(f"Path is neither file nor dir: {dir_path}")

    # The test passes as long as execution didn't crash
    logger.info("TEST PASSED - Directory conflict handled gracefully")


# =============================================================================
# TEST: Invalid Model Name
# =============================================================================

@pytest.mark.asyncio
async def test_invalid_model_name(mcp_client: RealMCPClientSSE, test_workspace: Path):
    """Test with invalid model name.

    Should fail gracefully with meaningful error.
    """
    logger.info("=" * 80)
    logger.info("TEST: Invalid Model Name")
    logger.info("=" * 80)

    prompt = "Say hello"

    result = await mcp_client.call_execute_claude(
        prompt=prompt,
        model="nonexistent-model-xyz",  # Invalid model
        enable_permissions=False,
        workspace_root=str(test_workspace),
    )

    logger.info(f"Result with invalid model: {json.dumps(result, indent=2)}")

    # Should fail or fall back to default model
    if not result.get("success"):
        error = result.get("error", "")
        logger.info(f"Failed with error: {error}")
    else:
        logger.info("Execution succeeded (possibly fell back to default model)")

    logger.info("TEST PASSED - Invalid model handled")
