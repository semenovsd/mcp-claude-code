"""Pytest fixtures for E2E tests.

Provides:
- mcp_client: Connected Real MCP client (HTTP/SSE)
- test_workspace: Temporary directory for test files with unique ID
- cleanup: Removes test files and permissions after each test
- verify_file_created: Helper for strict file verification with polling

NO MOCKS! All tests use REAL Claude Code CLI.
"""

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import httpx

from .mcp_client_sse import RealMCPClientSSE

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

async def verify_file_created(
    workspace: Path,
    filename: str | None = None,
    pattern: str | None = None,
    content_contains: str | None = None,
    timeout_seconds: float = 10.0,
    poll_interval: float = 0.5,
) -> Path:
    """Verify file exists in workspace with optional content check.

    Waits for file to appear (handles async file creation by Claude).
    Raises AssertionError with detailed info on failure.

    Args:
        workspace: Directory to search in
        filename: Exact filename to find (e.g., "test.txt")
        pattern: Glob pattern to match (e.g., "*.md")
        content_contains: String that must be present in file content
        timeout_seconds: Maximum time to wait for file (default: 10s)
        poll_interval: Time between checks (default: 0.5s)

    Returns:
        Path to the found file

    Raises:
        AssertionError: If file not found or content doesn't match
    """
    if not filename and not pattern:
        raise ValueError("Must specify either filename or pattern")

    start_time = time.time()
    last_error = None

    while time.time() - start_time < timeout_seconds:
        try:
            # Find file
            if filename:
                target_file = workspace / filename
                if target_file.exists():
                    found_file = target_file
                else:
                    found_file = None
            else:
                matches = list(workspace.glob(pattern))
                found_file = matches[0] if matches else None

            if found_file and found_file.exists():
                # File found - check content if required
                if content_contains:
                    content = found_file.read_text()
                    if content_contains in content:
                        logger.info(f"✅ File verified: {found_file}")
                        return found_file
                    else:
                        last_error = f"Content mismatch: expected '{content_contains}' in '{content[:200]}...'"
                else:
                    logger.info(f"✅ File verified: {found_file}")
                    return found_file

        except Exception as e:
            last_error = str(e)

        await asyncio.sleep(poll_interval)

    # Timeout - collect diagnostic info
    workspace_contents = list(workspace.rglob("*")) if workspace.exists() else []

    raise AssertionError(
        f"FILE NOT FOUND!\n"
        f"  Looking for: {filename or pattern}\n"
        f"  In workspace: {workspace}\n"
        f"  Workspace exists: {workspace.exists()}\n"
        f"  Workspace contents: {workspace_contents}\n"
        f"  Waited: {timeout_seconds}s\n"
        f"  Last error: {last_error}"
    )


def verify_file_created_sync(
    workspace: Path,
    filename: str | None = None,
    pattern: str | None = None,
    content_contains: str | None = None,
) -> Path:
    """Synchronous version of verify_file_created (no polling).

    Use this when you're sure the file should already exist.
    """
    if not filename and not pattern:
        raise ValueError("Must specify either filename or pattern")

    # Find file
    if filename:
        target_file = workspace / filename
        if not target_file.exists():
            workspace_contents = list(workspace.rglob("*")) if workspace.exists() else []
            raise AssertionError(
                f"FILE NOT FOUND: {filename}\n"
                f"  Workspace: {workspace}\n"
                f"  Contents: {workspace_contents}"
            )
        found_file = target_file
    else:
        matches = list(workspace.glob(pattern))
        if not matches:
            workspace_contents = list(workspace.rglob("*")) if workspace.exists() else []
            raise AssertionError(
                f"NO FILES MATCHING: {pattern}\n"
                f"  Workspace: {workspace}\n"
                f"  Contents: {workspace_contents}"
            )
        found_file = matches[0]

    # Check content if required
    if content_contains:
        content = found_file.read_text()
        if content_contains not in content:
            raise AssertionError(
                f"CONTENT MISMATCH in {found_file}:\n"
                f"  Expected to contain: '{content_contains}'\n"
                f"  Actual content: '{content[:500]}...'"
            )

    logger.info(f"✅ File verified: {found_file}")
    return found_file


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
async def mcp_server():
    """Start REAL MCP server via HTTP/SSE.

    Starts FastMCP server on localhost:8765.
    Returns server URL.
    """
    server_script = Path(__file__).parent / "run_server.py"

    # Create log file for server output
    log_file = Path("/tmp/mcp_server_e2e.log")
    log_handle = open(log_file, "w")

    logger.info(f"Starting MCP server, logs will be written to {log_file}")

    server_process = subprocess.Popen(
        [sys.executable, str(server_script)],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )

    # Wait for server to start
    server_url = "http://127.0.0.1:8765"
    max_retries = 50
    for i in range(max_retries):
        try:
            async with httpx.AsyncClient() as http_client:
                async with http_client.stream("GET", f"{server_url}/sse", timeout=2.0) as response:
                    if response.status_code in (200, 426):
                        logger.info(f"Server ready at {server_url}")
                        break
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
            if i == max_retries - 1:
                server_process.kill()
                raise RuntimeError(f"Server failed to start after {max_retries} attempts")
            await asyncio.sleep(0.2)

    yield server_url

    # Cleanup
    server_process.terminate()
    try:
        server_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server_process.kill()

    log_handle.close()
    logger.info(f"Server logs saved to {log_file}")


@pytest.fixture
async def mcp_client(mcp_server) -> AsyncGenerator[RealMCPClientSSE, None]:
    """Provide connected Real MCP client via HTTP/SSE.

    Connects to REAL MCP server via SSE transport.
    Supports Elicitation!
    NO MOCKS!
    """
    client = RealMCPClientSSE()
    sse_url = f"{mcp_server}/sse"

    async with client.connect(sse_url):
        yield client


@pytest.fixture
def test_workspace(tmp_path: Path) -> Generator[Path, None, None]:
    """Provide temporary workspace for test files.

    Creates a clean directory with unique ID for each test.
    Includes a marker file for verification.
    Automatically cleaned up after test.
    """
    # Create unique workspace
    unique_id = uuid.uuid4().hex[:8]
    workspace = tmp_path / f"test_workspace_{unique_id}"
    workspace.mkdir(parents=True, exist_ok=True)

    # Create marker file to verify we're in the right place
    marker = workspace / ".test_marker"
    marker.write_text(f"test_marker_{unique_id}\ncreated_at={time.time()}")

    logger.info(f"📁 Created test workspace: {workspace}")
    logger.info(f"📁 Workspace absolute path: {workspace.absolute()}")

    yield workspace

    # Log contents before cleanup
    if workspace.exists():
        contents = list(workspace.rglob("*"))
        logger.info(f"📁 Workspace contents after test: {contents}")

    # Cleanup
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)


@pytest.fixture(autouse=True)
def cleanup_permissions():
    """Clean up permission storage before and after each test.

    Cleans:
    - ~/.mcp-claude-code/permissions.json
    - Any .claude/ directories in common locations

    This ensures each test starts with clean state.
    """
    # Paths to clean
    permission_file = Path.home() / ".mcp-claude-code" / "permissions.json"
    mcp_claude_dir = Path.home() / ".mcp-claude-code"

    # Clean before test
    if permission_file.exists():
        logger.info(f"🧹 Cleaning permissions file: {permission_file}")
        permission_file.unlink()

    yield

    # Clean after test
    if permission_file.exists():
        logger.info(f"🧹 Cleaning permissions file after test: {permission_file}")
        # Log what permissions were stored (for debugging)
        try:
            with open(permission_file) as f:
                perms = json.load(f)
                logger.debug(f"Permissions stored during test: {perms}")
        except Exception:
            pass
        permission_file.unlink()


@pytest.fixture(autouse=True)
def log_test_info(request, test_workspace: Path):
    """Log diagnostic information before and after each test.

    Provides visibility into:
    - Test name
    - Workspace path
    - Files created during test
    """
    test_name = request.node.name
    logger.info("=" * 80)
    logger.info(f"🧪 TEST START: {test_name}")
    logger.info(f"📁 Workspace: {test_workspace}")
    logger.info(f"📁 Workspace exists: {test_workspace.exists()}")
    logger.info("=" * 80)

    yield

    # After test
    logger.info("-" * 80)
    logger.info(f"🧪 TEST END: {test_name}")
    if test_workspace.exists():
        files = [f for f in test_workspace.rglob("*") if f.is_file()]
        dirs = [d for d in test_workspace.rglob("*") if d.is_dir()]
        logger.info(f"📄 Files created: {len(files)}")
        for f in files:
            try:
                size = f.stat().st_size
                logger.info(f"   - {f.relative_to(test_workspace)} ({size} bytes)")
            except Exception:
                logger.info(f"   - {f.relative_to(test_workspace)}")
        logger.info(f"📁 Directories: {len(dirs)}")
    logger.info("-" * 80)


@pytest.fixture(scope="session")
def event_loop():
    """Provide event loop for async tests.

    Required for pytest-asyncio to work properly.
    """
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# PYTEST CONFIGURATION
# =============================================================================

def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "permissions: tests that involve permission handling"
    )
    config.addinivalue_line(
        "markers", "file_creation: tests that create files"
    )
