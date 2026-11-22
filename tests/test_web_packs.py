"""Web API for Paper Packs, matrix planning and the item explorer."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from medeval_core.config import Settings
from medeval_web.app import create_app

TOKEN = "packs-fixture-token"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    local = tmp_path / "local"
    local.mkdir()
    app = create_app(
        token=TOKEN,
        allowed_hosts={"testserver"},
        settings=Settings(seed=7),
        local_data_dir=local,
        connections_path=local / "connections.json",
        literature_path=local / "literature.csv",
        matrix_root=tmp_path / "matrix",
        cache_root=tmp_path / "cache",
    )
    return TestClient(app, base_url="http://testserver", headers={"X-Medeval-Token": TOKEN})


def wait_status(client: TestClient, run_id: str, wanted: set[str], timeout_s: float = 30.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = client.get(f"/api/matrix/runs/{run_id}").json()
        if (last.get("run") or {}).get("status") in wanted:
            return last
        time.sleep(0.05)
    raise AssertionError(f"status never reached {wanted}: {last}")


def test_pack_registry_lists_starter_packs(client: TestClient) -> None:
    response = client.get("/api/packs?include_archived=true")
    assert response.status_code == 200
    body = response.json()
    ids = {pack["id"] for pack in body["packs"]}
    assert {"template-clinical-mcq", "template-rubric-panel", "template-structured-extraction"} <= ids
    assert body["archived"], "archived studies must be visible read-only"
    assert all(pack["kind"] == "paper_pack" for pack in body["packs"])


def test_pack_detail_and_validation(client: TestClient) -> None:
    detail = client.get("/api/packs/template-clinical-mcq").json()
    assert detail["task_type"] == "mcq"
    assert detail["dataset"]["count"] == 24
    assert detail["preview"]
    validation = client.post("/api/packs/template-clinical-mcq/validate").json()
    assert validation["ok"] is True
    assert validation["pack_hash"]
    missing = client.get("/api/packs/does-not-exist")
    assert missing.status_code == 404


def test_plan_is_dry_by_default_and_reports_caps(client: TestClient) -> None:
    response = client.post("/api/matrix/plan", json={"paper": "template-clinical-mcq", "dry_run": True})
    assert response.status_code == 200
    body = response.json()
    plan = body["plan"]
    assert body["pipeline_test_only"] is True
    assert plan["target_calls"] == plan["n_items"] * 2
    assert plan["total_calls"] == plan["target_calls"]
    assert plan["budget"]["max_calls"] > 0
    assert "PIPELINE TEST ONLY" in body["note"]


def test_live_plan_needs_a_target_connection(client: TestClient) -> None:
    response = client.post("/api/matrix/plan", json={"paper": "template-clinical-mcq", "dry_run": False})
    assert response.status_code == 400
    assert "target connection" in response.json()["detail"]


def test_dry_run_start_items_report_and_bundle(client: TestClient) -> None:
    started = client.post("/api/matrix/runs", json={
        "paper": "template-clinical-mcq", "dry_run": True, "n_items": 6,
        "mock_targets": 2, "max_calls": 50, "max_usd": 0,
    })
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    payload = wait_status(client, run_id, {"completed", "failed"})
    assert payload["run"]["status"] == "completed", payload.get("error")
    assert payload["metrics"]["targets"]
    assert payload["run"]["pipeline_test_only"] is True

    report = client.get(f"/api/matrix/runs/{run_id}/report")
    assert report.status_code == 200
    assert "not a medical device" in report.text.lower()
    assert "PIPELINE TEST ONLY" in report.text

    bundle = client.get(f"/api/matrix/runs/{run_id}/bundle")
    assert bundle.status_code == 200
    assert bundle.headers["content-type"] == "application/zip"

    items = client.get(f"/api/matrix/runs/{run_id}/items?filter=all").json()
    assert items["total_items"] == 6
    assert items["items"][0]["targets"][0]["text"]
    assert client.get(f"/api/matrix/runs/{run_id}/items?filter=bogus").status_code == 400


def test_matrix_json_never_contains_a_key(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/connections", json={
        "name": "pack target", "provider_preset": "custom", "base_url": "https://example.invalid/v1",
        "api_style": "chat", "model": "some-model", "family_label": "F1",
        "key": "not-a-real-key-value", "per_connection_call_cap": 10, "per_connection_usd_cap": 1.0,
    })
    assert created.status_code == 201, created.text
    started = client.post("/api/matrix/runs", json={
        "paper": "template-clinical-mcq", "dry_run": True, "targets": [created.json()["id"]],
        "n_items": 3, "max_calls": 20, "max_usd": 0,
    })
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    wait_status(client, run_id, {"completed", "failed"})
    text = (tmp_path / "matrix" / run_id / "run.json").read_text(encoding="utf-8")
    assert "not-a-real-key-value" not in text
    assert '"api_key"' not in text


def test_stop_endpoint_refuses_an_inactive_run(client: TestClient) -> None:
    response = client.post("/api/matrix/runs/not-a-run/stop", json={})
    assert response.status_code == 409


def test_regrade_is_offline_and_keeps_the_notice(client: TestClient) -> None:
    started = client.post("/api/matrix/runs", json={
        "paper": "template-clinical-mcq", "dry_run": True, "n_items": 4, "max_calls": 20, "max_usd": 0,
    })
    run_id = started.json()["run_id"]
    wait_status(client, run_id, {"completed", "failed"})
    regraded = client.post(f"/api/matrix/runs/{run_id}/regrade", json={"dry_run": True})
    assert regraded.status_code == 200, regraded.text
    body = regraded.json()
    assert body["status"] == "completed"
    assert body["metrics"]["targets"]
    assert body["paths"]["report"]
    # Target models were never re-queried.
    new_run = wait_status(client, body["run_id"], {"completed", "failed"})
    assert new_run["metrics"]["overall"]["calls_made"] == 0
