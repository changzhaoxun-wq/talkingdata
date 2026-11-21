import io
import json
import os
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.files import workspace
from app.main import create_app


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "exam_test_private_key_do_not_publish_012345")
    monkeypatch.setenv("AGENT_MODEL", "test-model")
    agent = Path(__file__).parent / "fake_agent.py"
    agent.chmod(0o755)
    config = Config(data_dir=tmp_path, agent_bin=str(agent), concurrency=1, timeout=10)
    return config


@pytest.fixture
def client(configured):
    with TestClient(create_app(configured)) as client:
        yield client


def new(client, title="新会话"):
    response = client.post("/api/sessions", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def send(client, sid, content="Make a result", **kwargs):
    response = client.post(f"/api/sessions/{sid}/messages", json={"content": content, **kwargs})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def wait(client, sid, status):
    for _ in range(160):
        snapshot = client.get(f"/api/sessions/{sid}").json()
        if snapshot["runs"] and snapshot["runs"][-1]["status"] == status:
            if status != "completed" or any(m["role"] == "assistant" for m in snapshot["messages"]):
                return snapshot
        time.sleep(0.04)
    pytest.fail(f"Run did not reach {status}: {snapshot}")


def test_lifecycle_continuation_and_archive(client):
    sid = new(client)
    send(client, sid)
    result = wait(client, sid, "completed")
    assert result["cli_session_id"].startswith("ses_")
    first_id = result["cli_session_id"]
    assert len(result["messages"]) == 2
    assert any(e["kind"] == "tool" for e in result["events"])
    send(client, sid, "Continue the task")
    result = wait(client, sid, "completed")
    for _ in range(40):
        if len(result["messages"]) == 4:
            break
        time.sleep(0.03)
        result = client.get(f"/api/sessions/{sid}").json()
    assert len(result["messages"]) == 4
    assert result["cli_session_id"] == first_id
    download = client.get(f"/api/sessions/{sid}/files/download", params={"path": "result.md"})
    assert download.status_code == 200
    assert "Continue the task" in download.text
    archive = client.get(f"/api/sessions/{sid}/archive")
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        assert zipped.namelist() == ["result.md"]
    assert client.patch(f"/api/sessions/{sid}", json={"title": "Renamed"}).status_code == 200
    assert client.get("/api/sessions").json()[0]["title"] == "Renamed"


def test_material_upload_preview_duplicate_and_session_isolation(client):
    a, b = new(client), new(client)
    for expected in ["uploads/data.csv", "uploads/data-2.csv"]:
        r = client.post(f"/api/sessions/{a}/files", files={"files": ("data.csv", b"name,value\na,12\n", "text/csv")})
        assert r.status_code == 201
        assert r.json()["paths"] == [expected]
    r = client.get(f"/api/sessions/{a}/files/content", params={"path": "uploads/data.csv"})
    assert r.json()["rows"] == [["name", "value"], ["a", "12"]]
    assert client.get(f"/api/sessions/{b}/files").json()["files"] == []
    assert client.get(f"/api/sessions/{b}/files/download", params={"path": "uploads/data.csv"}).status_code == 404
    send(client, a, "Analyze", attachments=["uploads/data.csv"])
    wait(client, a, "completed")


@pytest.mark.parametrize("path", ["../settings.json", "/etc/passwd", ".env", "../other/workspace/a", "uploads/../../x", "x\\..\\x"])
def test_path_traversal_rejected(client, path):
    sid = new(client)
    assert client.get(f"/api/sessions/{sid}/files/download", params={"path": path}).status_code == 400


def test_symlink_download_and_listing_rejected(client, configured):
    sid = new(client)
    secret = configured.data_dir / "outside.txt"
    secret.write_text("private")
    (workspace(configured, sid) / "leak.txt").symlink_to(secret)
    assert client.get(f"/api/sessions/{sid}/files/content", params={"path": "leak.txt"}).status_code == 400
    assert client.get(f"/api/sessions/{sid}/files").json()["files"] == []


def test_atomic_upload_rejects_invalid_filename(client):
    sid = new(client)
    r = client.post(f"/api/sessions/{sid}/files", files=[("files", ("good.txt", b"ok")), ("files", ("../bad.txt", b"bad"))])
    assert r.status_code == 400
    assert client.get(f"/api/sessions/{sid}/files").json()["files"] == []


def test_html_preview_uses_separate_sandbox_policy(client):
    sid = new(client)
    client.post(f"/api/sessions/{sid}/files", files={"files": ("tool.html", b"<script>document.title='tool'</script>")})
    response = client.get(f"/api/sessions/{sid}/files/preview", params={"path": "uploads/tool.html"})
    assert response.status_code == 200
    assert "sandbox allow-scripts allow-downloads" in response.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in response.headers["content-security-policy"]
    assert "x-frame-options" not in response.headers
    assert "script-src 'self'" in client.get('/').headers['content-security-policy']


def test_queue_busy_rules_and_cancel_kills_process_tree(client, configured):
    a, b = new(client), new(client)
    ra = send(client, a, "WAIT")
    wait(client, a, "running")
    rb = send(client, b, "WAIT")
    assert client.post(f"/api/sessions/{a}/messages", json={"content": "second"}).status_code == 409
    assert client.delete(f"/api/sessions/{a}").status_code == 409
    assert client.put("/api/settings", json={"base_url": "http://localhost/v1"}).status_code == 409
    for _ in range(80):
        pidfile = workspace(configured, a) / "child.pid"
        if pidfile.exists():
            break
        time.sleep(0.04)
    assert pidfile.exists()
    child = int(pidfile.read_text())
    assert client.get(f"/api/sessions/{b}").json()["runs"][-1]["status"] == "queued"
    assert client.post(f"/api/sessions/{a}/runs/{ra}/cancel").json()["status"] == "cancelled"
    wait(client, b, "running")
    assert client.post(f"/api/sessions/{b}/runs/{rb}/cancel").json()["status"] == "cancelled"
    try:
        os.kill(child, 0)
    except ProcessLookupError:
        pass
    else:
        # An already-dead orphan can briefly remain a zombie until tini reaps it.
        assert Path(f"/proc/{child}/stat").read_text().split()[2] == "Z"


def test_failed_run_redacts_credentials_and_can_retry(client):
    sid = new(client)
    send(client, sid, "FAIL")
    failed = wait(client, sid, "failed")
    assert "[REDACTED]" in failed["runs"][-1]["error"]
    assert "exam_test_private" not in json.dumps(failed)
    send(client, sid, "Retry")
    wait(client, sid, "completed")


def test_budget_exhaustion_is_not_reported_as_success(client):
    sid = new(client)
    send(client, sid, "TRUNCATE")
    result = wait(client, sid, "failed")
    assert "length" in result["runs"][-1]["error"]


def test_timeout_stops_task_and_allows_continuation(configured):
    configured.timeout = 1
    with TestClient(create_app(configured)) as client:
        sid = new(client)
        send(client, sid, "WAIT")
        failed = wait(client, sid, "failed")
        assert "超过 1 秒" in failed["runs"][-1]["error"]
        send(client, sid, "Continue")
        wait(client, sid, "completed")


def test_queued_task_can_be_cancelled_without_execution(client, configured):
    a, b = new(client), new(client)
    ra = send(client, a, "WAIT")
    wait(client, a, "running")
    rb = send(client, b, "WAIT")
    assert client.post(f"/api/sessions/{b}/runs/{rb}/cancel").json()["status"] == "cancelled"
    assert not workspace(configured, b).joinpath("child.pid").exists()
    client.post(f"/api/sessions/{a}/runs/{ra}/cancel")


def test_restart_marks_stale_run_interrupted_and_preserves_files(configured):
    app = create_app(configured)
    with TestClient(app) as client:
        sid = new(client)
        workspace(configured, sid).joinpath("keep.txt").write_text("retained")
        app.state.db.execute("INSERT INTO runs(id,session_id,status,created_at,model) VALUES(?,?,?,?,?)", ("stale", sid, "running", time.time(), "test"))
    with TestClient(create_app(configured)) as client:
        snapshot = client.get(f"/api/sessions/{sid}").json()
        assert snapshot["runs"][0]["status"] == "interrupted"
        assert client.get(f"/api/sessions/{sid}/files/download", params={"path": "keep.txt"}).text == "retained"


def test_settings_mask_keys_and_csrf_protection(client):
    status = client.get("/api/settings").json()
    assert status["has_key"] and "api_key" not in status
    assert client.post("/api/sessions", json={"title": "x"}, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/sessions", json={"title": "x"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400
    r = client.put("/api/settings", json={"base_url": "https://example.com/v1", "model": "m", "api_key": "new-secret"})
    assert r.status_code == 200 and "new-secret" not in r.text
    assert client.put("/api/settings", json={"base_url": "file:///tmp", "model": "m"}).status_code == 400


def test_unconfigured_agent_rejects_run(configured, monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "")
    with TestClient(create_app(configured)) as client:
        sid = new(client)
        assert client.post(f"/api/sessions/{sid}/messages", json={"content": "do something"}).status_code == 503
