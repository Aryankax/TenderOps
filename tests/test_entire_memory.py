"""Entire Continuous Memory tests (offline, no CLI required)."""

from fastapi.testclient import TestClient


def _isolate(monkeypatch, tmp_path):
    from app.core import config as config_mod

    monkeypatch.setattr(
        config_mod.settings, "ENTIRE_MEMORY_DIR", str(tmp_path / "entire_mem")
    )
    from helpers import isolate_auth

    isolate_auth(monkeypatch, tmp_path)


def _client():
    import app.main as m

    return TestClient(m.app, raise_server_exceptions=False)


def test_record_list_get_search(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from app.services import entire_memory as mem

    c = mem.record_checkpoint(
        tender_id="T-1", request="turnover eligibility check",
        decisions=["rejected comparing figures in P2, defer to P3"],
        changed_files=["app/agents/compliance_agent/rules.py"],
        validation={"pipeline_status": "COMPLETED"},
    )
    assert c["id"].startswith("ckpt_")
    assert "entire.io" in c["entire_url"]
    assert mem.get_checkpoint(c["id"])["tender_id"] == "T-1"
    assert mem.list_checkpoints("T-1")[0]["id"] == c["id"]
    hits = mem.search_checkpoints("turnover eligibility")
    assert hits and hits[0]["id"] == c["id"]


def test_brief_review_demo_scout(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from app.services import entire_memory as mem

    c = mem.record_checkpoint(
        tender_id="T-2", request="EMD exemption needs NSIC certificate",
        decisions=["avoided inferring exemption without certificate"],
        changed_files=["app/agents/verification_agent/rules.py"],
        validation={"passed": True},
    )
    brief = mem.build_briefing("EMD exemption certificate")
    assert "EMD" in brief["briefing_md"] or "exemption" in brief["briefing_md"]
    assert brief["past_attempts"]

    bad = mem.review_change("EMD exemption needs NSIC certificate",
                            ["app/main.py"], [])
    assert bad["verdict"] == "NEEDS_ATTENTION" and bad["flags"]

    good = mem.review_change("update verification rules",
                             ["app/agents/verification_agent/rules.py",
                              "tests/test_entire_memory.py"],
                             ["updated verification rules",
                              "covered by tests/test_entire_memory.py"])
    assert good["verdict"] == "PASS"

    demo = mem.checkpoint_demo(c["id"])
    assert c["id"] in demo["demo_md"] and "Evidence" in demo["demo_md"]

    scout = mem.scout_tests(c["id"])
    assert scout["suggestions"]


def test_graph_snapshot_diff_and_locate(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from app.services import entire_memory as mem

    snap = mem.graph_snapshot(".")
    assert snap["files"] > 10
    assert "app/orchestrator/graph.py" in snap["imports"]
    changed = dict(snap)
    changed = {"imports": dict(snap["imports"]),
               "hashes": dict(snap["hashes"])}
    changed["imports"]["app/orchestrator/graph.py"] = (
        changed["imports"]["app/orchestrator/graph.py"]
        + ["app/fake/new_dep.py"]
    )
    diff = mem.graph_diff(snap, changed)
    assert diff["new_dependencies"]
    assert diff["coupling_delta"] == 1

    hits = mem.EntireGraph.locate("orchestrator")
    assert hits and any("orchestrator" in (h.get("file", "") + h.get("hit", ""))
                        for h in hits)
    impact = mem.EntireGraph.impact("app/orchestrator/graph.py")
    assert isinstance(impact["dependents"], list)


def test_entire_api_end_to_end(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from helpers import login_headers

    c = _client()
    headers, _ = login_headers(c)
    assert c.get("/api/entire/status", headers=headers).status_code == 200

    r = c.post("/api/entire/checkpoints",
               json={"tender_id": "T-9", "request": "demo run",
                     "decisions": ["d1"],
                     "changed_files": ["app/main.py"],
                     "validation": {"ok": True}}, headers=headers)
    assert r.status_code == 200, r.text
    ckpt_id = r.json()["id"]

    assert c.get("/api/entire/checkpoints",
                 headers=headers).json()["checkpoints"]
    assert c.get(f"/api/entire/checkpoints/{ckpt_id}",
                 headers=headers).status_code == 200
    assert c.get("/api/entire/checkpoints/nope",
                 headers=headers).status_code == 404
    assert c.get(f"/api/entire/checkpoints/{ckpt_id}/demo",
                 headers=headers).status_code == 200

    r = c.get("/api/entire/search", params={"q": "demo run"}, headers=headers)
    assert r.status_code == 200 and r.json()["checkpoints"]

    r = c.post("/api/entire/brief", json={"query": "demo run"}, headers=headers)
    assert r.status_code == 200 and "briefing_md" in r.json()

    r = c.post("/api/entire/review",
               json={"request": "demo run", "changed_files": ["app/main.py"],
                     "decisions": ["demo run", "covered by tests"]},
               headers=headers)
    assert r.status_code == 200 and r.json()["verdict"] == "PASS"

    assert c.get("/api/entire/graph/search", params={"q": "orchestrator"},
                 headers=headers).status_code == 200
    assert c.get("/api/entire/graph/stats", headers=headers).status_code == 200
    assert c.get("/api/entire/graph/impact",
                 params={"file": "app/orchestrator/graph.py"},
                 headers=headers).status_code == 200
    assert c.get("/api/entire/scout", params={"checkpoint_id": ckpt_id},
                 headers=headers).status_code == 200
    assert c.get("/api/entire/brain/facts", headers=headers).status_code == 200
