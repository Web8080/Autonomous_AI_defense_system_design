"""
AI safeguardrails: constrain autonomous and human-triggered actions.
No lethal or irreversible actions; geofence and confidence thresholds enforced elsewhere.
"""
from __future__ import annotations

import re
from typing import Any

# Intents the System AI is allowed to issue without human approval. All others require operator.
AI_ALLOWED_INTENTS = frozenset({"path_plan", "investigate", "patrol", "retreat", "mission_abort"})

# Intents that are never allowed from API (e.g. hypothetical lethal or destructive).
FORBIDDEN_INTENTS = frozenset({"lethal", "weapon", "destroy", "disable_permanent"})

# Max depth and key count for command payload to avoid DoS and injection via nested structures.
MAX_PAYLOAD_KEYS = 32
MAX_PAYLOAD_DEPTH = 5
MAX_PAYLOAD_BYTES = 8192

# UUID v4 pattern; asset_id must match this or be literal "all".
ASSET_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def is_allowed_intent(intent: str) -> bool:
    if not intent or not isinstance(intent, str):
        return False
    normalized = intent.strip().lower()
    if normalized in FORBIDDEN_INTENTS:
        return False
    return normalized in {
        "emergency_stop", "override", "path_plan", "mission_abort", "take_control",
        "investigate", "patrol", "retreat",
    }


def is_ai_allowed_intent(intent: str) -> bool:
    return intent.strip().lower() in AI_ALLOWED_INTENTS


def validate_asset_id(asset_id: Any) -> bool:
    if asset_id is None:
        return False
    if isinstance(asset_id, str) and asset_id.strip().lower() == "all":
        return True
    if isinstance(asset_id, str) and ASSET_ID_PATTERN.match(asset_id.strip()):
        return True
    return False


def _payload_size_ok(payload: Any, depth: int, key_count: list) -> bool:
    if depth > MAX_PAYLOAD_DEPTH or key_count[0] > MAX_PAYLOAD_KEYS:
        return False
    if isinstance(payload, dict):
        for k, v in payload.items():
            key_count[0] += 1
            if key_count[0] > MAX_PAYLOAD_KEYS:
                return False
            if not _payload_size_ok(v, depth + 1, key_count):
                return False
    elif isinstance(payload, list):
        for item in payload:
            if not _payload_size_ok(item, depth + 1, key_count):
                return False
    return True


def validate_command_payload(payload: Any) -> tuple[bool, str]:
    if payload is None:
        return True, ""
    if not isinstance(payload, dict):
        return False, "payload must be an object"
    key_count = [0]
    if not _payload_size_ok(payload, 0, key_count):
        return False, "payload too large or too deep"
    try:
        size = len(str(payload).encode("utf-8"))
        if size > MAX_PAYLOAD_BYTES:
            return False, "payload byte size exceeded"
    except Exception:
        return False, "payload serialization failed"
    return True, ""
