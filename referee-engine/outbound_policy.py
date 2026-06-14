"""Outbound URL validation for referee-managed external service calls.

安全说明:
- 此模块验证出站 URL，防止 SSRF 攻击
- 当 host 是域名时，仅在验证阶段检查，实际请求时 DNS 可能解析到不同 IP
- DNS Rebinding 攻击需要 REFEREE_ALLOW_PRIVATE_OUTBOUND_URLS=1 才可利用
- 未来改进: 可以在实际请求时再次验证解析后的 IP
"""

import os
import ipaddress
from urllib.parse import urlparse

from fastapi import HTTPException


BLOCKED_OUTBOUND_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "host.docker.internal",
}

BLOCKED_OUTBOUND_IP_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


def outbound_private_urls_allowed() -> bool:
    return os.environ.get("REFEREE_ALLOW_PRIVATE_OUTBOUND_URLS", "").strip().lower() in {"1", "true", "yes", "on"}


def validate_outbound_url(raw_url: str, *, field_name: str) -> str:
    url = str(raw_url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be an http(s) URL")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail=f"{field_name} must not include credentials")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise HTTPException(status_code=400, detail=f"{field_name} must include a host")
    if not outbound_private_urls_allowed():
        trimmed_host = host.rstrip(".")
        if trimmed_host in BLOCKED_OUTBOUND_HOSTS or trimmed_host.endswith(".localhost"):
            raise HTTPException(status_code=400, detail=f"{field_name} host is not allowed")
        try:
            ip = ipaddress.ip_address(trimmed_host)
        except ValueError:
            ip = None
        if ip is not None and any(ip in network for network in BLOCKED_OUTBOUND_IP_NETWORKS):
            raise HTTPException(status_code=400, detail=f"{field_name} host is not allowed")
    return url
