"""Permission storage and management."""

import hashlib
import json
from pathlib import Path

from ..models.interactions import PermissionDecision, StoredPermission


class PermissionManager:
    """Manages permission storage and retrieval.

    Two storage layers:
    1. Session storage (in-memory dict) - cleared when server restarts
    2. Persistent storage (JSON file) - survives restarts

    Attributes:
        workspace_root: Root directory for workspace
        storage_path: Path to persistent permissions JSON file
        session_permissions: In-memory session permissions
        persistent_permissions: Persistent permissions loaded from JSON
    """

    def __init__(self, workspace_root: Path, storage_path: Path | None = None) -> None:
        """Initialize permission manager.

        Args:
            workspace_root: Root directory for workspace
            storage_path: Optional custom path to permissions JSON file
        """
        self.workspace_root = workspace_root
        self.storage_path = storage_path or (
            workspace_root / ".mcp-claude-code" / "permissions.json"
        )

        # Session storage (in-memory)
        self.session_permissions: dict[str, StoredPermission] = {}

        # Persistent storage (loaded from JSON)
        self.persistent_permissions: dict[str, StoredPermission] = {}

        # Load persistent on init
        self._load_persistent()

    def check_permission(self, action: str, target: str) -> StoredPermission | None:
        """Check if permission already granted.

        Priority:
        1. Session storage
        2. Persistent storage

        Args:
            action: Tool name (Read, Edit, Bash, etc.)
            target: File path or command

        Returns:
            StoredPermission if found, None otherwise

        Examples:
            >>> manager = PermissionManager(Path("/workspace"))
            >>> perm = manager.check_permission("Edit", "file.py")
            >>> perm is None
            True
        """
        perm_hash = self._generate_hash(action, target)

        # Check session first
        if perm_hash in self.session_permissions:
            return self.session_permissions[perm_hash]

        # Check persistent
        if perm_hash in self.persistent_permissions:
            return self.persistent_permissions[perm_hash]

        return None

    def store_permission(
        self,
        action: str,
        target: str,
        decision: PermissionDecision,
    ) -> None:
        """Store a permission decision.

        - ALLOW_SESSION: Store in session_permissions only
        - ALLOW_ALWAYS: Store in both session and persistent
        - ALLOW_ONCE/DENY: Don't store

        Args:
            action: Tool name
            target: File path or command
            decision: Permission decision type
        """
        if decision in (PermissionDecision.ALLOW_ONCE, PermissionDecision.DENY):
            return

        perm_hash = self._generate_hash(action, target)
        perm = StoredPermission(
            action=action,
            target=target,
            decision=decision,
            hash=perm_hash,
        )

        if decision == PermissionDecision.ALLOW_SESSION:
            self.session_permissions[perm_hash] = perm

        elif decision == PermissionDecision.ALLOW_ALWAYS:
            self.session_permissions[perm_hash] = perm
            self.persistent_permissions[perm_hash] = perm
            self._save_persistent()

    def _generate_hash(self, action: str, target: str) -> str:
        """Generate unique hash for permission.

        Args:
            action: Tool name
            target: File path or command

        Returns:
            16-character SHA256 hash
        """
        content = f"{action}:{target}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _load_persistent(self) -> None:
        """Load permissions from JSON file."""
        if not self.storage_path.exists():
            return

        try:
            with open(self.storage_path) as f:
                data = json.load(f)

            for entry in data.get("permissions", []):
                perm = StoredPermission(
                    action=entry["action"],
                    target=entry["target"],
                    decision=PermissionDecision(entry["decision"]),
                    hash=entry["hash"],
                )
                self.persistent_permissions[perm.hash] = perm

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"Error loading permissions: {e}")

    def _save_persistent(self) -> None:
        """Save permissions to JSON file."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "permissions": [
                {
                    "action": p.action,
                    "target": p.target,
                    "decision": p.decision.value,
                    "hash": p.hash,
                }
                for p in self.persistent_permissions.values()
            ]
        }

        with open(self.storage_path, "w") as f:
            json.dump(data, f, indent=2)
