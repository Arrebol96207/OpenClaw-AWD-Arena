from redaction import is_sensitive_key, redact_text, redact_value


def test_redact_text_covers_flags_tokens_cookies_and_hex_values():
    text = (
        "Authorization: Bearer live-secret-token\n"
        "X-Player-Token: player-secret-token\n"
        "set-cookie=sessionid=super-secret-cookie; Path=/\n"
        "api_key=sk-live-secret\n"
        "flag=FLAG{super-secret-flag}\n"
        "trace=0123456789abcdef0123456789abcdef"
    )

    redacted = redact_text(text, redacted="[X]")

    assert "live-secret-token" not in redacted
    assert "player-secret-token" not in redacted
    assert "super-secret-cookie" not in redacted
    assert "sk-live-secret" not in redacted
    assert "FLAG{super-secret-flag}" not in redacted
    assert "0123456789abcdef0123456789abcdef" not in redacted
    assert "Bearer [X]" in redacted
    assert "X-Player-Token: [X]" in redacted
    assert "set-cookie=[X]" in redacted
    assert "sk-[X]" in redacted
    assert "FLAG{[X]}" in redacted


def test_redact_value_redacts_nested_sensitive_keys_with_custom_placeholder():
    payload = {
        "safe": "hello",
        "session_id": "ses-123",
        "session_token": "session-secret",
        "headers": {
            "Set-Cookie": "sid=secret",
            "X-API-Key": "key-secret",
        },
        "items": [
            {"private_key": "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"},
            "token=inline-secret",
        ],
    }

    redacted = redact_value(payload, redacted="[X]")

    assert redacted["safe"] == "hello"
    assert redacted["session_id"] == "ses-123"
    assert redacted["session_token"] == "[X]"
    assert redacted["headers"]["Set-Cookie"] == "[X]"
    assert redacted["headers"]["X-API-Key"] == "[X]"
    assert redacted["items"][0]["private_key"] == "[X]"
    assert redacted["items"][1] == "token=[X]"


def test_is_sensitive_key_normalizes_common_spellings():
    assert is_sensitive_key("apiKey") is True
    assert is_sensitive_key("set-cookie") is True
    assert is_sensitive_key("session_token") is True
    assert is_sensitive_key("session_id") is False
    assert is_sensitive_key("display_name") is False
