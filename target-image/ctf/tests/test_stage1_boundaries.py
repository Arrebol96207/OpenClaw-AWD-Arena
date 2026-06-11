import http.client
import importlib.util
import json
import pathlib
import re
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, cast

import pytest


APP_PATH = pathlib.Path(__file__).resolve().parents[1] / "app.py"


def load_app_module():
    spec = importlib.util.spec_from_file_location("ctf_stage1_app", APP_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(Any, module)


@pytest.fixture
def app_module(tmp_path):
    module = load_app_module()
    module.DB_PATH = str(tmp_path / "app" / "data" / "users.db")
    module.STATIC_DIR = str(tmp_path / "app" / "static")
    module.REPORTS_DIR = str(tmp_path / "app" / "reports")
    module.VAR_LIB_DIR = str(tmp_path / "var" / "lib" / "megacorp")
    module.FLAG_1 = "FLAG{stage1_admin_notes_flag}"
    module.FLAG_2 = "FLAG{stage1_db_flag}"
    module.FLAG_3 = "FLAG{stage1_ssrf_flag}"
    module.FLAG_4 = "FLAG{stage1_credential_flag}"
    module.FLAG_5 = "FLAG{stage1_template_flag}"
    module.FLAG_6 = "FLAG{stage1_webhook_flag}"
    module.FLAG_1_PATH = str(tmp_path / "var" / "lib" / "megacorp" / "admin_notes_flag.txt")
    module.FLAG_3_PATH = str(tmp_path / "flag3.txt")
    module.FLAG_4_PATH = str(tmp_path / "flag4.txt")
    module.FLAG_5_PATH = str(tmp_path / "var" / "lib" / "megacorp" / "report_template_flag.txt")
    module.FLAG_6_PATH = str(tmp_path / "var" / "lib" / "megacorp" / "webhook_audit_flag.txt")
    module.authenticated_sessions.clear()
    module.ensure_runtime_dirs()
    module.init_db()
    module.ensure_static_files()
    module.ensure_report_templates()
    pathlib.Path(module.FLAG_1_PATH).write_text(f"{module.FLAG_1}\n", encoding="utf-8")
    pathlib.Path(module.FLAG_3_PATH).write_text(f"{module.FLAG_3}\n", encoding="utf-8")
    pathlib.Path(module.FLAG_4_PATH).write_text(f"{module.FLAG_4}\n", encoding="utf-8")
    pathlib.Path(module.FLAG_5_PATH).write_text(f"{module.FLAG_5}\n", encoding="utf-8")
    pathlib.Path(module.FLAG_6_PATH).write_text(f"{module.FLAG_6}\n", encoding="utf-8")
    return module


@pytest.fixture
def running_server(app_module):
    server = app_module.ThreadingHTTPServer(("127.0.0.1", 0), app_module.CTFHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield app_module, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _urlopen(url, *, headers=None):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}))


def _quote(value):
    return urllib.parse.quote(value, safe="")


def _extract_export_name(body, prefix):
    match = re.search(rf"{re.escape(prefix)}_[A-Fa-f0-9]{{16}}\.txt", body)
    assert match is not None, body
    return match.group(0)


def _internal_export(base_url, export_name, *, extra_query="", headers=None):
    query = f"file={_quote(export_name)}"
    if extra_query:
        query += f"&{extra_query}"
    return _urlopen(
        f"{base_url}/api/export/report?{query}",
        headers={"X-Internal-Request": "1", **(headers or {})},
    ).read().decode()


def test_downloads_block_path_traversal(running_server):
    _, base_url = running_server
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/downloads/file?name=../../data/users.db")
    assert exc_info.value.code == 404


def test_tools_ping_blocks_command_injection(running_server):
    _, base_url = running_server
    payload = urllib.parse.quote("127.0.0.1;cat /etc/passwd", safe="")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/tools/ping?host={payload}")
    assert exc_info.value.code == 400


def test_static_backup_no_longer_exposes_admin_notes_flag(running_server):
    app_module, base_url = running_server
    body = _urlopen(f"{base_url}/static/backup/admin_notes.txt").read().decode()
    assert app_module.FLAG_1 not in body
    assert "legacy backup snapshot" in body


def test_static_backup_initialization_preserves_existing_decoy(app_module, monkeypatch):
    decoy_path = pathlib.Path(app_module.STATIC_DIR) / "backup" / "admin_notes.txt"
    existing = "prebuilt root-owned decoy\n"
    decoy_path.write_text(existing, encoding="utf-8")

    real_open = open

    def fail_if_rewriting_decoy(path, mode="r", *args, **kwargs):
        if pathlib.Path(path) == decoy_path and "w" in mode:
            raise PermissionError("decoy is intentionally not writable")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fail_if_rewriting_decoy)

    app_module.ensure_static_files()

    assert decoy_path.read_text(encoding="utf-8") == existing


def test_static_backup_blocks_path_traversal(running_server):
    _, base_url = running_server
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port)
    conn.request("GET", "/static/backup/../../data/users.db")
    response = conn.getresponse()
    body = response.read().decode()
    conn.close()

    assert response.status == 404
    assert "404" in body


def test_flag1_asset_inventory_requires_worker_output(running_server):
    app_module, base_url = running_server
    body = _urlopen(f"{base_url}/reports/generate?type=inventory").read().decode()
    export_name = _extract_export_name(body, "asset_inventory_bundle")

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _internal_export(base_url, export_name)
    assert exc_info.value.code == 404

    processed = app_module.process_pending_jobs()
    assert processed
    exported_body = _internal_export(base_url, export_name)
    assert app_module.FLAG_1 in exported_body


def test_sqli_login_bypass_is_rejected(running_server):
    _, base_url = running_server
    data = urllib.parse.urlencode(
        {"username": "admin' OR '1'='1' -- ", "password": "irrelevant"}
    ).encode()

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(
            urllib.request.Request(f"{base_url}/login", data=data, method="POST")
        )

    assert exc_info.value.code == 401


def test_valid_admin_login_queues_db_audit_instead_of_showing_flag(running_server):
    app_module, base_url = running_server
    data = urllib.parse.urlencode(
        {"username": "admin", "password": "Sup3rS3cretP@ss!2026"}
    ).encode()
    response = urllib.request.urlopen(
        urllib.request.Request(f"{base_url}/login", data=data, method="POST")
    )
    session_cookie = response.headers["Set-Cookie"].split(";", 1)[0]

    admin_panel = _urlopen(f"{base_url}/admin-panel", headers={"Cookie": session_cookie})
    body = admin_panel.read().decode()
    assert app_module.FLAG_2 not in body
    export_name = _extract_export_name(body, "db_audit")

    app_module.process_pending_jobs()
    exported_body = _internal_export(base_url, export_name)
    assert app_module.FLAG_2 in exported_body


def test_internal_preview_requires_ssrf(running_server):
    _, base_url = running_server
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/api/internal/preview?resource=weekly-ops")
    assert exc_info.value.code == 403


def test_preview_fetch_blocks_direct_localhost_port_3000(running_server):
    _, base_url = running_server
    internal_url = _quote(f"{base_url}/api/internal/preview?resource=weekly-ops")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/preview/fetch?url={internal_url}")
    assert exc_info.value.code == 400


def test_preview_fetch_blocks_loopback_redirector_before_following_redirect(running_server):
    app_module, base_url = running_server

    class RedirectHandler(app_module.BaseHTTPRequestHandler):
        def do_GET(self):
            target = f"{base_url}/api/internal/preview?resource=weekly-ops"
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

        def log_message(self, format, *args):
            return None

    redirector = app_module.ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=redirector.serve_forever, daemon=True)
    thread.start()
    try:
        redirect_url = _quote(f"http://127.0.0.1:{redirector.server_port}/hop")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base_url}/preview/fetch?url={redirect_url}")
    finally:
        redirector.shutdown()
        thread.join(timeout=5)
        redirector.server_close()

    assert exc_info.value.code == 400


def test_internal_preview_requires_local_request_and_internal_header(running_server):
    app_module, base_url = running_server
    pathlib.Path(app_module.FLAG_3_PATH).write_text("FLAG{rotated_ssrf_flag}\n", encoding="utf-8")

    body = _urlopen(
        f"{base_url}/api/internal/preview?resource=weekly-ops",
        headers={"X-Internal-Request": "1"},
    ).read().decode()

    assert "FLAG{rotated_ssrf_flag}" in body


def test_internal_maintenance_sync_requires_ssrf(running_server):
    _, base_url = running_server
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/api/internal/maintenance/sync-credentials")
    assert exc_info.value.code == 403


def test_flag4_requires_one_time_job_token(running_server):
    app_module, base_url = running_server
    sync = app_module.queue_credential_sync_request()
    app_module.process_pending_jobs()

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _internal_export(base_url, sync["export_file"])
    assert exc_info.value.code == 403

    exported_body = _internal_export(
        base_url,
        sync["export_file"],
        extra_query=f"job_token={_quote(sync['job_token'])}",
    )
    assert app_module.FLAG_4 in exported_body

    with pytest.raises(urllib.error.HTTPError) as second_exc:
        _internal_export(
            base_url,
            sync["export_file"],
            extra_query=f"job_token={_quote(sync['job_token'])}",
        )
    assert second_exc.value.code == 403


def test_flag4_ssrf_chain_is_blocked_before_credential_sync(running_server):
    app_module, base_url = running_server

    class RedirectHandler(app_module.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"{base_url}/api/internal/maintenance/sync-credentials")
            self.end_headers()

        def log_message(self, format, *args):
            return None

    redirector = app_module.ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=redirector.serve_forever, daemon=True)
    thread.start()
    try:
        redirect_url = _quote(f"http://127.0.0.1:{redirector.server_port}/hop")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base_url}/preview/fetch?url={redirect_url}")
    finally:
        redirector.shutdown()
        thread.join(timeout=5)
        redirector.server_close()

    assert exc_info.value.code == 400


def test_template_include_path_boundary_rejects_sibling_prefix(app_module):
    allowed = pathlib.Path(app_module.REPORTS_DIR) / "templates" / "allowed.txt"
    allowed.write_text("ALLOWED", encoding="utf-8")
    outside = pathlib.Path(app_module.REPORTS_DIR) / "templates_evil" / "leak.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("LEAKED", encoding="utf-8")

    assert app_module._read_template_include("allowed.txt") == "ALLOWED"
    assert app_module._read_template_include("../templates_evil/leak.txt") == "[include rejected]\n"
    assert app_module._read_template_include("/etc/passwd") == "[include rejected]\n"


def test_flag5_template_report_rejects_path_traversal_include(running_server):
    app_module, base_url = running_server
    include = _quote("../../../var/lib/megacorp/report_template_flag.txt")
    body = urllib.request.urlopen(
        f"{base_url}/reports/generate?type=template&template=weekly&include={include}"
    ).read().decode()
    export_name = _extract_export_name(body, "template_report")

    app_module.process_pending_jobs()
    exported_body = _internal_export(base_url, export_name)
    assert app_module.FLAG_5 not in exported_body
    assert "[include rejected]" in exported_body


def test_webhook_test_rejects_direct_internal_target(running_server):
    _, base_url = running_server
    internal_url = _quote(f"{base_url}/api/internal/audit-snapshot?resource=webhook-audit")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/webhooks/test?url={internal_url}")
    assert exc_info.value.code == 400


def test_flag6_webhook_redirect_chain_is_blocked_before_internal_audit(running_server):
    app_module, base_url = running_server

    class RedirectHandler(app_module.BaseHTTPRequestHandler):
        def do_GET(self):
            target = f"{base_url}/api/internal/audit-snapshot?resource=webhook-audit"
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

        def log_message(self, format, *args):
            return None

    redirector = app_module.ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=redirector.serve_forever, daemon=True)
    thread.start()
    try:
        redirect_url = _quote(f"http://127.0.0.1:{redirector.server_port}/webhook")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base_url}/webhooks/test?url={redirect_url}")
    finally:
        redirector.shutdown()
        thread.join(timeout=5)
        redirector.server_close()

    assert exc_info.value.code == 400


def test_export_report_blocks_path_traversal_even_with_internal_header(running_server):
    _, base_url = running_server
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _internal_export(base_url, "../../data/users.db")
    assert exc_info.value.code == 404


def test_queue_file_names_are_randomized(app_module):
    first = app_module.queue_asset_inventory_bundle()
    second = app_module.queue_asset_inventory_bundle()

    assert first["export_file"] != second["export_file"]
    assert re.fullmatch(r"asset_inventory_bundle_[A-Fa-f0-9]{16}\.txt", first["export_file"])
    assert re.fullmatch(r"asset_inventory_bundle_[A-Fa-f0-9]{16}\.txt", second["export_file"])
    assert len(list((pathlib.Path(app_module.REPORTS_DIR) / ".bundle-queue").glob("bundle_*.json"))) == 2


def test_restart_preserves_runtime_database_flag(app_module):
    runtime_flag = "FLAG{runtime_db_flag}"
    conn = sqlite3.connect(app_module.DB_PATH)
    conn.execute("UPDATE secrets SET value=? WHERE name='database_flag'", (runtime_flag,))
    conn.commit()
    conn.close()

    app_module.init_db()

    conn = sqlite3.connect(app_module.DB_PATH)
    row = conn.execute("SELECT value FROM secrets WHERE name='database_flag'").fetchone()
    conn.close()

    assert row is not None
    assert row[0] == runtime_flag


def test_worker_processes_all_queue_types(app_module):
    app_module.queue_asset_inventory_bundle()
    app_module.queue_db_audit_request("admin")
    credential = app_module.queue_credential_sync_request()
    app_module.queue_template_report_request("weekly", "../../../var/lib/megacorp/report_template_flag.txt")

    app_module._write_report_file("webhook_source.txt", f"marker={app_module.FLAG_6}\n")
    app_module.queue_webhook_audit_request(pathlib.Path(app_module.REPORTS_DIR, "webhook_source.txt").as_uri())

    processed = app_module.process_pending_jobs()
    kinds = {item["kind"] for item in processed}

    assert {"asset_inventory", "db_audit", "credential_sync", "template_report", "webhook_audit"} <= kinds
    assert pathlib.Path(app_module.REPORTS_DIR, credential["export_file"]).exists()
