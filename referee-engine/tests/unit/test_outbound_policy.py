import pytest
from fastapi import HTTPException

from outbound_policy import outbound_private_urls_allowed, validate_outbound_url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://10.0.0.2/v1",
        "http://172.16.0.10/v1",
        "http://192.168.1.10/v1",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]:8000/v1",
        "https://metadata.google.internal/computeMetadata/v1",
        "https://user:pass@example.test/v1",
        "file:///tmp/model",
    ],
)
def test_validate_outbound_url_blocks_private_or_unsafe_targets_by_default(monkeypatch, url):
    monkeypatch.delenv("REFEREE_ALLOW_PRIVATE_OUTBOUND_URLS", raising=False)

    with pytest.raises(HTTPException):
        validate_outbound_url(url, field_name="baseUrl")


def test_validate_outbound_url_allows_public_https_target(monkeypatch):
    monkeypatch.delenv("REFEREE_ALLOW_PRIVATE_OUTBOUND_URLS", raising=False)

    assert (
        validate_outbound_url("https://api.findmini.top/gpt/", field_name="baseUrl")
        == "https://api.findmini.top/gpt"
    )


def test_validate_outbound_url_can_allow_private_targets_for_explicit_local_testing(monkeypatch):
    monkeypatch.setenv("REFEREE_ALLOW_PRIVATE_OUTBOUND_URLS", "yes")

    assert outbound_private_urls_allowed() is True
    assert (
        validate_outbound_url("http://127.0.0.1:8000/v1", field_name="baseUrl")
        == "http://127.0.0.1:8000/v1"
    )


def test_validate_outbound_url_rejects_missing_host_even_when_private_urls_allowed(monkeypatch):
    monkeypatch.setenv("REFEREE_ALLOW_PRIVATE_OUTBOUND_URLS", "1")

    with pytest.raises(HTTPException) as exc_info:
        validate_outbound_url("https:///v1", field_name="proxy")

    assert exc_info.value.status_code == 400
    assert "http(s) URL" in exc_info.value.detail
