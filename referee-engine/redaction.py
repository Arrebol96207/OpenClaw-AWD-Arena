import re
from typing import Any, Dict


DEFAULT_REDACTED_VALUE = "********"

SENSITIVE_KEY_PARTS = (
    "apikey",
    "api_key",
    "authorization",
    "token",
    "secret",
    "password",
    "private_key",
    "cookie",
    "set_cookie",
)

SENSITIVE_EXACT_KEYS = {
    "flag",
    "current_flag",
    "flag_value",
    "submitted_flag",
    "session",
    "session_cookie",
    "session_token",
    "sessiontoken",
    "session_secret",
    "sessionsecret",
}

FLAG_VALUE_PATTERN = re.compile(r"FLAG\{[^}\r\n]{0,256}\}")
KEY_VALUE_SECRET_PATTERN = re.compile(
    r"(?i)\b(authorization|x-api-key|x-player-token|api[_-]?key|token|secret|password|cookie|set-cookie|session)\b"
    r"(\s*[:=]\s*)([^\r\n,;]+)"
)
BEARER_TOKEN_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
SK_TOKEN_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
HEX_TOKEN_PATTERN = re.compile(r"\b[a-f0-9]{32,}\b", re.IGNORECASE)


def is_sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
    normalized = normalized.replace("-", "_").replace(".", "_").lower()
    compact = normalized.replace("_", "")
    return normalized in SENSITIVE_EXACT_KEYS or any(part in compact or part in normalized for part in SENSITIVE_KEY_PARTS)


def redact_text(value: str, *, redacted: str = DEFAULT_REDACTED_VALUE) -> str:
    def replace_secret(match: re.Match[str]) -> str:
        secret_key = match.group(1)
        separator = match.group(2)
        secret_value = match.group(3).strip()
        if secret_key.lower() == "authorization" and secret_value.lower().startswith("bearer "):
            return f"{secret_key}{separator}Bearer {redacted}"
        if secret_value.startswith("sk-"):
            return f"{secret_key}{separator}sk-{redacted}"
        return f"{secret_key}{separator}{redacted}"

    sanitized = FLAG_VALUE_PATTERN.sub(f"FLAG{{{redacted}}}", value)
    sanitized = KEY_VALUE_SECRET_PATTERN.sub(replace_secret, sanitized)
    sanitized = BEARER_TOKEN_PATTERN.sub(f"Bearer {redacted}", sanitized)
    sanitized = SK_TOKEN_PATTERN.sub(f"sk-{redacted}", sanitized)
    return HEX_TOKEN_PATTERN.sub(redacted, sanitized)


def redact_value(value: Any, *, redacted: str = DEFAULT_REDACTED_VALUE) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[Any, Any] = {}
        for key, item in value.items():
            sanitized[key] = redacted if is_sensitive_key(key) and item else redact_value(item, redacted=redacted)
        return sanitized
    if isinstance(value, list):
        return [redact_value(item, redacted=redacted) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item, redacted=redacted) for item in value]
    if isinstance(value, str):
        return redact_text(value, redacted=redacted)
    return value
