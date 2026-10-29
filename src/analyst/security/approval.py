"""Signed human approvals.

The graph pauses for a human before risky SQL. When the human approves, the API signs
(source_id, sql) with a key the LLM never sees. The MCP server re-checks the signature
before running anything that needs approval, so an MCP client can't just claim
"approved=true", and the approval only covers that exact SQL.
"""

import hashlib
import hmac

from analyst.config import get_settings


def _message(source_id: str, sql: str) -> bytes:
    return f"{source_id}\n{hashlib.sha256(sql.encode()).hexdigest()}".encode()


def sign_approval(source_id: str, sql: str) -> str:
    key = get_settings().require("approval_signing_key").encode()
    return hmac.new(key, _message(source_id, sql), hashlib.sha256).hexdigest()


def verify_approval(source_id: str, sql: str, token: str | None) -> bool:
    if not token:
        return False
    return hmac.compare_digest(sign_approval(source_id, sql), token)
