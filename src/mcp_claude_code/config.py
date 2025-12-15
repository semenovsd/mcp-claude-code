"""Configuration settings for MCP Claude Code server."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable support.

    All settings can be overridden via environment variables with MCP_CLAUDE_ prefix.

    Examples:
        >>> settings = Settings()
        >>> settings.claude_code_path
        'claude'
    """

    model_config = SettingsConfigDict(env_prefix="MCP_CLAUDE_")

    # Claude Code CLI
    claude_code_path: str = "claude"
    default_model: str = "sonnet"

    # Timeouts
    max_execution_seconds: int = 600
    inactivity_timeout_seconds: int = 120

    # Workspace
    workspace_root: str = ""  # Defaults to $WORKSPACE_ROOT

    # Storage
    permission_storage_path: str = "~/.mcp-claude-code/permissions.json"

    def get_permission_storage_path(self) -> Path:
        """Get expanded permission storage path.

        Returns:
            Absolute path to permissions JSON file
        """
        return Path(self.permission_storage_path).expanduser()
