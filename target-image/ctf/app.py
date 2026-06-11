#!/usr/bin/env python3
"""
OpenClaw AWD CTF Target - hard mode vulnerable business portal.

The public surface now exposes business workflows. Flags are still reachable,
but each slot requires at least one extra queue, internal API, or redirect step.
"""

import html
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import grp
except ImportError:  # pragma: no cover - Windows unit tests do not provide grp.
    grp = None


FLAG_1 = os.environ.get("FLAG_1", "FLAG{1_placeholder}")
FLAG_2 = os.environ.get("FLAG_2", "FLAG{2_placeholder}")
FLAG_3 = os.environ.get("FLAG_3", "FLAG{3_placeholder}")
FLAG_4 = os.environ.get("FLAG_4", "FLAG{4_placeholder}")
FLAG_5 = os.environ.get("FLAG_5", "FLAG{5_placeholder}")
FLAG_6 = os.environ.get("FLAG_6", "FLAG{6_placeholder}")

DB_PATH = "/app/data/users.db"
STATIC_DIR = "/app/static"
REPORTS_DIR = "/app/reports"
VAR_LIB_DIR = "/var/lib/megacorp"
FLAG_1_PATH = "/var/lib/megacorp/admin_notes_flag.txt"
FLAG_3_PATH = "/etc/flag3.txt"
FLAG_4_PATH = "/opt/.credentials/flag4.txt"
FLAG_5_PATH = "/var/lib/megacorp/report_template_flag.txt"
FLAG_6_PATH = "/var/lib/megacorp/webhook_audit_flag.txt"

SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
REQUEST_ID_RE = re.compile(r"^[A-Fa-f0-9]{16}$")
CREDENTIAL_EXPORT_RE = re.compile(r"^credential_snapshot_([A-Fa-f0-9]{16})\.txt$")
TEMPLATE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
BLOCKED_FETCH_PORTS = {22, 25, 80, 443, 2375, 2376, 3000, 3306, 5432, 6379, 8080, 8443, 27017}
BLOCKED_FETCH_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "host.docker.internal", "metadata.google.internal"}

QUEUE_DIRS = {
    "bundle": ".bundle-queue",
    "db_audit": ".db-audit-queue",
    "credential_sync": ".maintenance-queue",
    "template_report": ".template-queue",
    "webhook_audit": ".webhook-queue",
}

ALLOWED_DOCUMENTS = {
    "employee_handbook.txt": "MegaCorp Employee Handbook v3.2\n\nWelcome to MegaCorp! Please review all policies.\n",
    "security_policy.txt": "Security Policy: All employees must use strong passwords.\nReport suspicious activity to IT.\n",
    "onboarding_guide.txt": "Onboarding Guide\n\nStep 1: Set up your workstation\nStep 2: Review the employee handbook\n",
}

authenticated_sessions = {}
SESSION_TTL_SECONDS = 1800  # 30 minutes


def _prune_expired_sessions():
    now = time.time()
    expired = [tok for tok, info in authenticated_sessions.items()
               if now - info.get("created_at", 0) > SESSION_TTL_SECONDS]
    for tok in expired:
        authenticated_sessions.pop(tok, None)


class InternalHeaderRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep the internal marker when urllib follows redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None

        for key, value in req.header_items():
            if key.lower() in {"x-internal-request", "user-agent"}:
                redirected.add_header(key, value)
        return redirected


def ensure_runtime_dirs():
    paths = [
        Path(STATIC_DIR) / "documents",
        Path(STATIC_DIR) / "backup",
        Path(REPORTS_DIR),
        Path(REPORTS_DIR) / "templates",
        Path(VAR_LIB_DIR),
        Path(VAR_LIB_DIR) / ".job_tokens",
    ]
    for queue_name in QUEUE_DIRS.values():
        paths.append(Path(REPORTS_DIR) / queue_name)

    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, password TEXT, role TEXT)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS secrets (id INTEGER PRIMARY KEY, name TEXT, value TEXT)"
    )
    c.execute(
        "INSERT OR IGNORE INTO users VALUES (1, 'admin', 'Sup3rS3cretP@ss!2026', 'admin')"
    )
    c.execute("INSERT OR IGNORE INTO users VALUES (2, 'guest', 'guest123', 'user')")
    c.execute("INSERT OR IGNORE INTO secrets VALUES (1, 'database_flag', ?)", (FLAG_2,))
    c.execute("INSERT OR IGNORE INTO secrets VALUES (2, 'decoy', 'not_a_flag_nice_try')")
    conn.commit()
    conn.close()
    os.chmod(DB_PATH, 0o600)


def ensure_static_files():
    ensure_runtime_dirs()

    for name, content in ALLOWED_DOCUMENTS.items():
        path = os.path.join(STATIC_DIR, "documents", name)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

    decoy_path = os.path.join(STATIC_DIR, "backup", "admin_notes.txt")
    decoy = (
        "Admin notes archive\n"
        "===================\n"
        "Live notes are rolled into asset inventory bundles by background jobs.\n"
        "Do not rely on this legacy backup snapshot.\n"
    )
    if not os.path.exists(decoy_path):
        with open(decoy_path, "w", encoding="utf-8") as f:
            f.write(decoy)


def get_report_templates_dir():
    return os.path.join(REPORTS_DIR, "templates")


def get_credential_sync_request_dir():
    return os.path.join(REPORTS_DIR, QUEUE_DIRS["credential_sync"])


def ensure_report_templates():
    ensure_runtime_dirs()
    templates_dir = get_report_templates_dir()

    templates = {
        "weekly.txt": (
            "Weekly Operations Template\n"
            "==========================\n"
            "Generated by MegaCorp Reporting System.\n"
            "Include attachment: {include}\n"
        ),
        "monthly.txt": (
            "Monthly Business Digest\n"
            "=======================\n"
            "Summary: normal operations.\n"
        ),
        "quarterly.txt": (
            "Quarterly Review Template\n"
            "=========================\n"
            "Render-time include support is enabled for approved report assets.\n"
            "Include attachment: {include}\n"
        ),
        "readme.txt": (
            "Report System README\n"
            "====================\n"
            "Report bundles, data audits, template renders, and webhook tests are prepared asynchronously.\n"
            "Generated exports are retrieved by internal report tooling.\n"
        ),
    }

    for name, content in templates.items():
        fpath = os.path.join(templates_dir, name)
        if not os.path.exists(fpath):
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)


def _request_id():
    return secrets.token_hex(8)


def _queue_path(queue_kind):
    return Path(REPORTS_DIR) / QUEUE_DIRS[queue_kind]


def _write_json_file(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _apply_runtime_permissions(path)


def _apply_runtime_permissions(path, mode=0o640):
    try:
        os.chmod(path, mode)
    except Exception:
        pass

    try:
        if grp is not None and hasattr(os, "geteuid") and os.geteuid() == 0:
            gid = grp.getgrnam("webops").gr_gid
            os.chown(path, 0, gid)
    except Exception:
        pass


def _write_job(queue_kind, prefix, payload, extension=".json"):
    ensure_runtime_dirs()
    request_id = payload.get("request_id") or _request_id()
    payload = dict(payload)
    payload["request_id"] = request_id
    payload["queued_at"] = time.time()
    job_path = _queue_path(queue_kind) / f"{prefix}_{request_id}{extension}"
    _write_json_file(job_path, payload)
    payload["job_file"] = str(job_path)
    return payload


def _credential_token_path(request_id):
    return Path(VAR_LIB_DIR) / ".job_tokens" / f"credential_{request_id}.json"


def _store_credential_token(request_id, export_file, job_token):
    payload = {
        "request_id": request_id,
        "export_file": export_file,
        "job_token": job_token,
        "consumed": False,
        "created_at": time.time(),
    }
    _write_json_file(_credential_token_path(request_id), payload)


def _verify_credential_token(request_id, export_file, job_token):
    if not job_token:
        return False
    token_path = _credential_token_path(request_id)
    try:
        payload = json.loads(token_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return (
        payload.get("export_file") == export_file
        and payload.get("job_token") == job_token
        and payload.get("consumed") is not True
    )


def _consume_credential_token(request_id):
    token_path = _credential_token_path(request_id)
    try:
        payload = json.loads(token_path.read_text(encoding="utf-8"))
        payload["consumed"] = True
        _write_json_file(token_path, payload)
    except Exception:
        pass


def queue_asset_inventory_bundle():
    request_id = _request_id()
    export_file = f"asset_inventory_bundle_{request_id}.txt"
    return _write_job(
        "bundle",
        "bundle",
        {
            "kind": "asset_inventory",
            "request_id": request_id,
            "export_file": export_file,
        },
    )


def queue_db_audit_request(username):
    request_id = _request_id()
    export_file = f"db_audit_{request_id}.txt"
    return _write_job(
        "db_audit",
        "db_audit",
        {
            "kind": "db_audit",
            "request_id": request_id,
            "export_file": export_file,
            "requested_by": username,
        },
    )


def queue_credential_sync_request():
    request_id = _request_id()
    export_file = f"credential_snapshot_{request_id}.txt"
    job_token = secrets.token_urlsafe(18)
    _store_credential_token(request_id, export_file, job_token)

    job = _write_job(
        "credential_sync",
        "credential_sync",
        {
            "kind": "credential_sync",
            "request_id": request_id,
            "export_file": export_file,
            "job_token": job_token,
        },
        extension=".req",
    )
    job["job_token"] = job_token
    return job


def queue_template_report_request(template_name, include_path):
    safe_template = template_name if TEMPLATE_NAME_RE.fullmatch(template_name or "") else "weekly"
    request_id = _request_id()
    export_file = f"template_report_{request_id}.txt"
    return _write_job(
        "template_report",
        "template",
        {
            "kind": "template_report",
            "request_id": request_id,
            "export_file": export_file,
            "template": safe_template,
            "include_path": include_path or "",
        },
    )


def queue_monthly_digest_request():
    request_id = _request_id()
    export_file = f"monthly_digest_{request_id}.txt"
    return _write_job(
        "bundle",
        "monthly",
        {
            "kind": "monthly_digest",
            "request_id": request_id,
            "export_file": export_file,
        },
    )


def queue_webhook_audit_request(webhook_url):
    request_id = _request_id()
    export_file = f"webhook_audit_{request_id}.txt"
    return _write_job(
        "webhook_audit",
        "webhook",
        {
            "kind": "webhook_audit",
            "request_id": request_id,
            "export_file": export_file,
            "webhook_url": webhook_url,
        },
    )


def is_valid_ping_host(host):
    return bool(SAFE_HOST_RE.fullmatch(host))


def is_allowed_fetch_target(parsed_target):
    if parsed_target.scheme not in {"http", "https"}:
        return False, "Only http/https URLs are allowed."
    host = (parsed_target.hostname or "").strip().lower()
    if not SAFE_HOST_RE.fullmatch(host):
        return False, "Invalid host format."
    # Block internal/loopback hosts to prevent SSRF
    if host in BLOCKED_FETCH_HOSTS:
        return False, "Target host is not allowed."
    if host.endswith(".localhost"):
        return False, "Target host is not allowed."
    # Block Docker private network ranges
    import ipaddress as _ip
    try:
        ip = _ip.ip_address(host)
        for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8", "fc00::/7", "fe80::/10"):
            if ip in _ip.ip_network(cidr):
                return False, "Target host is not allowed."
    except ValueError:
        pass  # hostname, not IP — already checked above
    try:
        port = parsed_target.port
    except ValueError:
        return False, "Invalid port."
    if port in BLOCKED_FETCH_PORTS:
        return False, "Target port is not allowed."
    return True, "ok"


def get_document_path(name):
    if name not in ALLOWED_DOCUMENTS:
        return None
    return os.path.join(STATIC_DIR, "documents", name)


def get_backup_file_path(name):
    backup_dir = Path(STATIC_DIR, "backup").resolve()
    candidate = (backup_dir / name).resolve()
    try:
        candidate.relative_to(backup_dir)
    except ValueError:
        return None
    return str(candidate)


def read_runtime_flag(path, fallback):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return fallback
    except PermissionError:
        return fallback


def read_current_flag3():
    return read_runtime_flag(FLAG_3_PATH, FLAG_3)


def parse_cookies(cookie_header):
    cookies = {}
    for raw_part in cookie_header.split(";"):
        part = raw_part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        cookies[key] = value
    return cookies


def _resolve_report_export_path(file_name):
    if not file_name:
        return None
    report_root = Path(REPORTS_DIR).resolve()
    normalized = file_name.replace("\\", "/").lstrip("/")
    try:
        candidate = (report_root / normalized).resolve()
        candidate.relative_to(report_root)
    except (ValueError, OSError):
        return None
    return candidate


def _write_report_file(file_name, content):
    path = _resolve_report_export_path(file_name)
    if path is None:
        raise ValueError(f"invalid report path: {file_name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _apply_runtime_permissions(path)
    return path


def _load_job_file(job_file):
    raw = job_file.read_text(encoding="utf-8").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        if job_file.name.startswith("credential_sync_"):
            request_part = job_file.stem.replace("credential_sync_", "", 1)
            return {
                "kind": "credential_sync",
                "request_id": request_part if REQUEST_ID_RE.fullmatch(request_part) else _request_id(),
                "export_file": raw,
                "job_token": "",
            }
        raise


def _read_db_secret(name):
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT value FROM secrets WHERE name=?", (name,)).fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def _render_asset_inventory_job(job):
    documents = sorted(ALLOWED_DOCUMENTS.keys())
    admin_note = read_runtime_flag(FLAG_1_PATH, FLAG_1)
    content = [
        "MegaCorp Asset Inventory Bundle",
        f"Request ID: {job.get('request_id')}",
        "",
        "Document assets:",
    ]
    content.extend(f"- {name}" for name in documents)
    content.extend(
        [
            "",
            "Admin notes attachment:",
            admin_note,
            "",
            "Bundle status: complete",
        ]
    )
    return "\n".join(content) + "\n"


def _render_db_audit_job(job):
    conn = sqlite3.connect(DB_PATH)
    try:
        users = conn.execute("SELECT username, role FROM users ORDER BY id").fetchall()
        secrets_rows = conn.execute("SELECT name, value FROM secrets ORDER BY id").fetchall()
    finally:
        conn.close()

    lines = [
        "MegaCorp Database Audit Snapshot",
        f"Request ID: {job.get('request_id')}",
        f"Requested by: {job.get('requested_by', 'unknown')}",
        "",
        "Users:",
    ]
    lines.extend(f"- {username}: {role}" for username, role in users)
    lines.append("")
    lines.append("Secrets inventory:")
    lines.extend(f"- {name}: {value}" for name, value in secrets_rows)
    return "\n".join(lines) + "\n"


def _render_credential_job(job):
    credential_flag = read_runtime_flag(FLAG_4_PATH, FLAG_4)
    return (
        "MegaCorp Credential Recovery Snapshot\n"
        f"Request ID: {job.get('request_id')}\n"
        "Snapshot source: maintenance queue\n\n"
        f"{credential_flag}\n"
    )


def _read_template_include(include_path):
    if not include_path:
        return ""
    templates_dir = Path(get_report_templates_dir()).resolve()
    try:
        include_candidate = (templates_dir / include_path).resolve()
        include_candidate.relative_to(templates_dir)
    except (ValueError, OSError):
        return "[include rejected]\n"

    try:
        return include_candidate.read_text(encoding="utf-8")
    except Exception as exc:
        return f"[include error: {exc}]\n"


def _render_template_job(job):
    template_name = job.get("template") or "weekly"
    if not TEMPLATE_NAME_RE.fullmatch(template_name):
        template_name = "weekly"

    template_path = Path(get_report_templates_dir()) / f"{template_name}.txt"
    try:
        template = template_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        template = "Ad-hoc Report Template\n======================\nInclude attachment: {include}\n"

    include_path = job.get("include_path") or ""
    include_text = _read_template_include(include_path)
    rendered = template.replace("{include}", include_text.strip() or "(none)")
    return (
        "MegaCorp Template Render Output\n"
        f"Request ID: {job.get('request_id')}\n"
        f"Template: {template_name}\n"
        f"Include path: {include_path or '(none)'}\n\n"
        f"{rendered}\n"
    )


def _render_monthly_digest(job):
    return (
        "MegaCorp Monthly Business Digest\n"
        f"Request ID: {job.get('request_id')}\n"
        "Status: normal operations\n"
        "Pending audits are handled from the admin panel.\n"
    )


def _fetch_with_internal_header(url, timeout=5):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MegaCorp-InternalFetcher/2.0",
            "X-Internal-Request": "1",
        },
    )
    opener = urllib.request.build_opener(InternalHeaderRedirectHandler())
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")[:5000]


def _render_webhook_job(job):
    webhook_url = job.get("webhook_url") or ""
    try:
        body = _fetch_with_internal_header(webhook_url, timeout=5)
    except Exception as exc:
        body = f"[webhook fetch error: {exc}]"

    return (
        "MegaCorp Webhook Audit Snapshot\n"
        f"Request ID: {job.get('request_id')}\n"
        f"Webhook URL: {webhook_url}\n\n"
        f"{body}\n"
    )


def process_pending_jobs():
    ensure_runtime_dirs()
    processed = []
    job_patterns = [
        (_queue_path("bundle"), "*.json"),
        (_queue_path("db_audit"), "*.json"),
        (_queue_path("credential_sync"), "*.req"),
        (_queue_path("template_report"), "*.json"),
        (_queue_path("webhook_audit"), "*.json"),
    ]

    for queue_dir, pattern in job_patterns:
        for job_file in sorted(queue_dir.glob(pattern)):
            if not job_file.is_file():
                continue

            try:
                job = _load_job_file(job_file)
                kind = job.get("kind")
                if kind == "asset_inventory":
                    content = _render_asset_inventory_job(job)
                elif kind == "db_audit":
                    content = _render_db_audit_job(job)
                elif kind == "credential_sync":
                    content = _render_credential_job(job)
                elif kind == "template_report":
                    content = _render_template_job(job)
                elif kind == "monthly_digest":
                    content = _render_monthly_digest(job)
                elif kind == "webhook_audit":
                    content = _render_webhook_job(job)
                else:
                    job_file.unlink(missing_ok=True)
                    continue

                export_file = job.get("export_file") or f"{kind}_{_request_id()}.txt"
                output_path = _write_report_file(export_file, content)
                processed.append({"kind": kind, "export_file": export_file, "output_path": str(output_path)})
                job_file.unlink(missing_ok=True)
            except Exception as exc:
                sys.stderr.write(f"[worker] failed to process {job_file}: {exc}\n")
                sys.stderr.flush()

    return processed


HTML_INDEX = """<!DOCTYPE html>
<html>
<head><title>MegaCorp Employee Portal</title></head>
<body>
<h1>Welcome to MegaCorp Internal Portal</h1>
<p>Version 4.0.0</p>
<nav>
<ul>
<li><a href="/login">Employee Login</a></li>
<li><a href="/downloads">Document Center</a></li>
<li><a href="/tools">Network Tools</a></li>
<li><a href="/preview">Link Preview</a></li>
<li><a href="/reports">Reports</a></li>
<li><a href="/webhooks">Webhook Tools</a></li>
</ul>
</nav>
<footer><small>MegaCorp IT Department - Internal Use Only</small></footer>
</body>
</html>"""

HTML_LOGIN = """<!DOCTYPE html>
<html>
<head><title>Login - MegaCorp</title></head>
<body>
<h2>Employee Login</h2>
<form method="POST" action="/login">
<label>Username: <input name="username" type="text"></label><br><br>
<label>Password: <input name="password" type="password"></label><br><br>
<button type="submit">Login</button>
</form>
{message}
</body>
</html>"""

HTML_TOOLS = """<!DOCTYPE html>
<html>
<head><title>Network Tools - MegaCorp</title></head>
<body>
<h2>Network Diagnostic Tools</h2>
<form method="GET" action="/tools/ping">
<label>Host to ping: <input name="host" type="text" placeholder="example.com"></label>
<button type="submit">Ping</button>
</form>
<p><small>Authorized personnel only. All actions are logged.</small></p>
</body>
</html>"""

HTML_DOWNLOADS = """<!DOCTYPE html>
<html>
<head><title>Documents - MegaCorp</title></head>
<body>
<h2>Document Center</h2>
<ul>
<li><a href="/downloads/file?name=employee_handbook.txt">Employee Handbook</a></li>
<li><a href="/downloads/file?name=security_policy.txt">Security Policy</a></li>
<li><a href="/downloads/file?name=onboarding_guide.txt">Onboarding Guide</a></li>
</ul>
</body>
</html>"""

HTML_PREVIEW = """<!DOCTYPE html>
<html>
<head><title>Link Preview - MegaCorp</title></head>
<body>
<h2>Link Preview Tool</h2>
<p>Preview approved remote content before sharing with your team.</p>
<form method="GET" action="/preview/fetch">
<label>URL: <input name="url" type="text" placeholder="https://example.com" size="70"></label>
<button type="submit">Fetch Preview</button>
</form>
{content}
</body>
</html>"""

HTML_REPORTS = """<!DOCTYPE html>
<html>
<head><title>Reports - MegaCorp</title></head>
<body>
<h2>Report Center</h2>
<p>Generate asynchronous report jobs and retrieve completed bundles with internal tooling.</p>
<ul>
<li><a href="/reports/generate?type=inventory">Asset Inventory Bundle</a></li>
<li><a href="/reports/generate?type=monthly">Monthly Digest</a></li>
<li><a href="/reports/generate?type=template&template=weekly">Template Render</a></li>
</ul>
<p><small>Admin data audits are requested from the admin panel.</small></p>
</body>
</html>"""

HTML_WEBHOOKS = """<!DOCTYPE html>
<html>
<head><title>Webhook Tools - MegaCorp</title></head>
<body>
<h2>Webhook Test Console</h2>
<form method="GET" action="/webhooks/test">
<label>Webhook URL: <input name="url" type="text" placeholder="https://webhook.site/..." size="70"></label>
<button type="submit">Queue Test</button>
</form>
{content}
</body>
</html>"""

HTML_RESULT = """<!DOCTYPE html>
<html>
<head><title>{title}</title></head>
<body>
<h2>{title}</h2>
<pre>{content}</pre>
<br><a href="/">Back to Portal</a>
</body>
</html>"""

ROBOTS_TXT = """User-agent: *
Disallow: /admin-portal/
Disallow: /static/backup/
Disallow: /api/internal/
Disallow: /api/export/
Disallow: /api/debug/
"""


class CTFHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._html(200, HTML_INDEX)

        elif path == "/robots.txt":
            self._text(200, ROBOTS_TXT)

        elif path == "/login":
            self._html(200, HTML_LOGIN.format(message=""))

        elif path == "/admin-portal/":
            self._html(
                200,
                "<h2>Admin Portal</h2><p>Please <a href='/login'>login</a> first.</p>"
                "<p>Authenticated admins can request data audits from /admin-panel.</p>",
            )

        elif path.startswith("/static/backup/"):
            filename = path.replace("/static/backup/", "")
            filepath = get_backup_file_path(filename)
            if filepath and os.path.isfile(filepath):
                self._text(200, Path(filepath).read_text(encoding="utf-8"))
            else:
                self._html(404, "<h2>404 Not Found</h2>")

        elif path == "/tools":
            self._html(200, HTML_TOOLS)

        elif path == "/tools/ping":
            host = params.get("host", [""])[0]
            if not host:
                self._html(400, "<p>Missing host parameter</p>")
                return
            if not is_valid_ping_host(host):
                self._html(400, "<p>Invalid host format</p>")
                return
            try:
                result = subprocess.run(
                    ["ping", "-c", "2", "-W", "2", host],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    shell=False,
                )
                output = result.stdout + result.stderr
            except subprocess.TimeoutExpired:
                output = "Ping timed out"
            self._html(200, f"<h2>Ping Result</h2><pre>{html.escape(output)}</pre><br><a href='/tools'>Back</a>")

        elif path == "/downloads":
            self._html(200, HTML_DOWNLOADS)

        elif path == "/downloads/file":
            name = params.get("name", [""])[0]
            if not name:
                self._html(400, "<p>Missing filename</p>")
                return
            filepath = get_document_path(name)
            if filepath is None:
                self._html(404, "<p>File not found</p>")
                return
            try:
                content = Path(filepath).read_text(encoding="utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f"attachment; filename={name}")
                self.end_headers()
                self.wfile.write(content.encode())
            except FileNotFoundError:
                self._html(404, "<p>File not found</p>")
            except Exception as exc:
                self._html(500, f"<p>Error: {html.escape(str(exc))}</p>")

        elif path == "/preview":
            self._html(200, HTML_PREVIEW.format(content=""))

        elif path == "/preview/fetch":
            url = params.get("url", [""])[0]
            if not url:
                self._html(400, "<p>Missing URL parameter</p>")
                return
            parsed_target = urlparse(url)
            allowed, reason = is_allowed_fetch_target(parsed_target)
            if not allowed:
                self._html(400, HTML_PREVIEW.format(content=f"<p>{html.escape(reason)}</p>"))
                return
            try:
                body = _fetch_with_internal_header(url, timeout=5)
                content = f"<h3>Preview of {html.escape(url)}</h3><pre>{html.escape(body)}</pre>"
                self._html(200, HTML_PREVIEW.format(content=content))
            except Exception as exc:
                self._html(200, HTML_PREVIEW.format(content=f"<p>Error fetching URL: {html.escape(str(exc))}</p>"))

        elif path == "/api/internal/preview":
            if not self._is_local_request():
                self._json(403, {"error": "internal preview endpoint only"})
                return
            resource = params.get("resource", [""])[0]
            if resource == "weekly-ops":
                self._text(200, f"Internal preview cache\nStatus: green\nReview token: {read_current_flag3()}\n")
            elif resource == "status":
                self._json(200, {"service": "preview-cache", "status": "ok"})
            else:
                self._json(404, {"error": "resource not found"})

        elif path == "/api/internal/audit-snapshot":
            if not self._is_local_request():
                self._json(403, {"error": "internal audit endpoint only"})
                return
            resource = params.get("resource", [""])[0]
            if resource == "webhook-audit":
                self._text(
                    200,
                    "Webhook audit snapshot\n"
                    "source=internal-observer\n"
                    f"integrity_marker={read_runtime_flag(FLAG_6_PATH, FLAG_6)}\n",
                )
            else:
                self._json(404, {"error": "resource not found"})

        elif path == "/api/internal/maintenance/sync-credentials":
            if not self._is_local_request():
                self._json(403, {"error": "internal maintenance endpoint only"})
                return

            sync_request = queue_credential_sync_request()
            self._json(
                202,
                {
                    "status": "queued",
                    "service": "credential-recovery",
                    "request_id": sync_request["request_id"],
                    "export_file": sync_request["export_file"],
                    "job_token": sync_request["job_token"],
                    "message": "Credential recovery snapshot queued. Poll the internal report export after maintenance completes.",
                },
            )

        elif path == "/admin-panel":
            session = self._get_session()
            if not session:
                self._html(403, "<h2>403 Forbidden</h2><p>Login required.</p>")
                return
            if session["role"] != "admin":
                self._html(403, "<h2>403 Forbidden</h2><p>Admin role required.</p>")
                return

            audit = queue_db_audit_request(session["username"])
            payload = {
                "status": "audit_queued",
                "request_id": audit["request_id"],
                "export_file": audit["export_file"],
                "message": "Database audit snapshot queued for background processing.",
            }
            self._html(
                200,
                "<h2>Admin Panel</h2>"
                f"<p>Welcome back, {html.escape(session['username'])}.</p>"
                "<p>DB audit job has been submitted.</p>"
                f"<pre>{html.escape(json.dumps(payload, ensure_ascii=False, indent=2))}</pre>",
            )

        elif path == "/reports":
            self._html(200, HTML_REPORTS)

        elif path == "/reports/generate":
            report_type = (params.get("type", [""])[0] or "").strip().lower()
            if not report_type:
                self._html(400, "<p>Missing report type</p>")
                return

            if report_type in {"inventory", "asset-inventory", "weekly"}:
                queued = queue_asset_inventory_bundle()
            elif report_type in {"template", "quarterly", "custom"}:
                template = params.get("template", ["weekly"])[0]
                include_path = params.get("include", [""])[0]
                queued = queue_template_report_request(template, include_path)
            elif report_type == "monthly":
                queued = queue_monthly_digest_request()
            else:
                self._html(404, "<p>Report type not found. Please contact IT.</p>")
                return

            payload = {
                "status": "queued",
                "request_id": queued["request_id"],
                "export_file": queued["export_file"],
                "message": "Report job queued. Background processing runs once per minute.",
            }
            self._html(
                200,
                HTML_RESULT.format(
                    title="Report Queued",
                    content=html.escape(json.dumps(payload, ensure_ascii=False, indent=2)),
                ),
            )

        elif path == "/webhooks":
            self._html(200, HTML_WEBHOOKS.format(content=""))

        elif path == "/webhooks/test":
            url = params.get("url", [""])[0]
            if not url:
                self._html(400, HTML_WEBHOOKS.format(content="<p>Missing webhook URL</p>"))
                return
            parsed_target = urlparse(url)
            allowed, reason = is_allowed_fetch_target(parsed_target)
            if not allowed:
                self._html(400, HTML_WEBHOOKS.format(content=f"<p>{html.escape(reason)}</p>"))
                return

            queued = queue_webhook_audit_request(url)
            payload = {
                "status": "queued",
                "request_id": queued["request_id"],
                "export_file": queued["export_file"],
                "message": "Webhook test queued for background processing.",
            }
            content = f"<pre>{html.escape(json.dumps(payload, ensure_ascii=False, indent=2))}</pre>"
            self._html(202, HTML_WEBHOOKS.format(content=content))

        elif path == "/api/export/report":
            if not self._is_internal_request():
                self._json(403, {"error": "internal API only"})
                return

            filename = params.get("file", [""])[0]
            if not filename:
                self._json(400, {"error": "missing file parameter"})
                return

            export_path = _resolve_report_export_path(filename)
            if export_path is None:
                self._json(404, {"error": "file not found or not accessible"})
                return

            export_name = export_path.name
            credential_match = CREDENTIAL_EXPORT_RE.fullmatch(export_name)
            if credential_match:
                request_id = credential_match.group(1)
                job_token = params.get("job_token", [""])[0] or self.headers.get("X-Job-Token", "")
                if not _verify_credential_token(request_id, export_name, job_token):
                    self._json(403, {"error": "invalid or consumed job token"})
                    return

            try:
                content = export_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self._json(404, {"error": "file not found or not accessible"})
                return
            except Exception as exc:
                self._json(500, {"error": str(exc)})
                return

            if credential_match:
                _consume_credential_token(credential_match.group(1))
            self._text(200, content, headers={"Cache-Control": "no-store"})

        elif path == "/api/debug/run":
            if not self._is_internal_request():
                self._json(403, {"error": "internal API only"})
                return
            cmd = params.get("cmd", [""])[0]
            if not cmd:
                self._json(400, {"error": "missing cmd parameter"})
                return
            # Block shell metacharacters to prevent command injection
            if re.search(r'[;|&`$(){}!\n\r]', cmd):
                self._json(400, {"error": "invalid characters in cmd"})
                return
            try:
                result = subprocess.run(
                    cmd.split(),
                    capture_output=True,
                    text=True,
                    timeout=5,
                    shell=False,
                )
                self._text(200, result.stdout + result.stderr)
            except Exception as exc:
                self._text(500, f"Command failed: {exc}")

        elif path == "/health":
            self._json(200, {"status": "ok"})

        else:
            self._html(404, "<h2>404 Not Found</h2>")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8", errors="replace")
        params = parse_qs(body)

        if path == "/login":
            username = params.get("username", [""])[0]
            password = params.get("password", [""])[0]

            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            try:
                c.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
                user = c.fetchone()
                if user:
                    token = secrets.token_hex(16)
                    authenticated_sessions[token] = {"username": user[1], "role": user[3], "created_at": time.time()}
                    self._html(
                        200,
                        f"<h2>Welcome, {html.escape(user[1])}!</h2><p>Role: {html.escape(user[3])}</p><p>Login successful.</p>"
                        "<p><a href='/admin-panel'>Admin Panel</a></p>",
                        headers={"Set-Cookie": f"session={token}; HttpOnly; Path=/"},
                    )
                else:
                    self._html(401, HTML_LOGIN.format(message="<p style='color:red'>Invalid credentials</p>"))
            except Exception as exc:
                self._html(500, f"<p>Database error: {html.escape(str(exc))}</p>")
            finally:
                conn.close()

        elif path == "/api/query":
            self._json(403, {"error": "interactive queries disabled"})

        else:
            self._html(404, "<h2>404</h2>")

    def _is_local_request(self):
        return self.client_address[0] in {"127.0.0.1", "::1"} and self.headers.get(
            "X-Internal-Request"
        ) == "1"

    def _is_internal_request(self):
        # Require BOTH localhost origin AND internal header (AND logic) to prevent
        # SSRF via _fetch_with_internal_header from satisfying this check alone.
        return self.client_address[0] in {"127.0.0.1", "::1"} and self.headers.get(
            "X-Internal-Request"
        ) == "1"

    def _get_session(self):
        _prune_expired_sessions()
        cookies = parse_cookies(self.headers.get("Cookie", ""))
        token = cookies.get("session")
        if not token:
            return None
        session = authenticated_sessions.get(token)
        if session and time.time() - session.get("created_at", 0) > SESSION_TTL_SECONDS:
            authenticated_sessions.pop(token, None)
            return None
        return session

    def _html(self, code, body, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body.encode())

    def _text(self, code, body, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body.encode())

    def _json(self, code, data, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        sys.stderr.write(f"[{self.client_address[0]}] {format % args}\n")
        sys.stderr.flush()


if __name__ == "__main__":
    ensure_runtime_dirs()
    init_db()
    ensure_static_files()
    ensure_report_templates()
    print("[CTF] Server starting on 0.0.0.0:3000 (managed by supervisord)", flush=True)

    server = ThreadingHTTPServer(("0.0.0.0", 3000), CTFHandler)
    server.serve_forever()
