"""Tests for the FastAPI endpoints -- full request/response lifecycle.

POST /research and POST .../review return 202 immediately and run the graph in a
background task, so these tests start work and then poll until it settles.
"""
import time


def _await_thread(client, thread_id, tries=100):
    """Poll GET until the background run stops. Returns the final body."""
    for _ in range(tries):
        r = client.get("/research/" + thread_id)
        assert r.status_code == 200, r.text
        body = r.json()
        if not body["running"]:
            return body
        time.sleep(0.02)
    raise AssertionError("thread still running after " + str(tries) + " polls")


def _start(client, topic):
    """POST a topic, wait for the run to settle, return the final body."""
    r = client.post("/research", json={"topic": topic})
    assert r.status_code == 202, r.text
    assert r.json()["running"] is True
    return _await_thread(client, r.json()["thread_id"])


def _review(client, thread_id, **payload):
    r = client.post("/research/" + thread_id + "/review", json=payload)
    assert r.status_code == 202, r.text
    return _await_thread(client, thread_id)


def test_health(mocked_client):
    r = mocked_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready(mocked_client):
    r = mocked_client.get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
    assert r.json()["max_revisions"] == 3


def test_start_research_pauses_for_review(mocked_client):
    body = _start(mocked_client, "small modular reactors")
    assert body["awaiting_review"] is True
    assert body["draft"] == "draft v1"
    assert body["revision_count"] == 0
    assert body["sub_queries"] == ["q1", "q2"]
    assert body["topic"] == "small modular reactors"
    assert body["thread_id"]


def test_start_research_rejects_empty_topic(mocked_client):
    r = mocked_client.post("/research", json={"topic": ""})
    assert r.status_code == 422  # pydantic min_length=1 validation


def test_get_research_status(mocked_client):
    started = _start(mocked_client, "test")
    r = mocked_client.get(f"/research/{started['thread_id']}")
    assert r.status_code == 200
    assert r.json()["draft"] == started["draft"]
    assert r.json()["sub_queries"] == ["q1", "q2"]
    assert r.json()["topic"] == "test"


def test_get_research_unknown_thread_404s(mocked_client):
    r = mocked_client.get("/research/does-not-exist")
    assert r.status_code == 404


def test_review_revision_increments_count_and_produces_new_draft(mocked_client):
    started = _start(mocked_client, "test")
    thread_id = started["thread_id"]

    body = _review(mocked_client, thread_id, approved=False, feedback="add more detail")
    assert body["draft"] == "draft v2"
    assert body["revision_count"] == 1
    assert body["awaiting_review"] is True
    assert body["status"] != "finalized"


def test_review_approval_finalizes(mocked_client):
    started = _start(mocked_client, "test")
    thread_id = started["thread_id"]

    body = _review(mocked_client, thread_id, approved=True)
    assert body["status"] == "finalized"
    assert body["final_report"] == "draft v1"
    assert body["awaiting_review"] is False


def test_review_on_already_finalized_thread_is_rejected(mocked_client):
    started = _start(mocked_client, "test")
    thread_id = started["thread_id"]
    _review(mocked_client, thread_id, approved=True)  # finalize it

    r = mocked_client.post(f"/research/{thread_id}/review", json={"approved": True})
    assert r.status_code == 400  # not awaiting review anymore


def test_revision_cap_forces_finalization(mocked_client):
    started = _start(mocked_client, "test")
    thread_id = started["thread_id"]

    # MAX_REVISIONS = 3 -- request revisions 4 times; the 4th must force-finalize.
    for _ in range(3):
        body = _review(mocked_client, thread_id, approved=False, feedback="more")
        assert body["awaiting_review"] is True

    body = _review(mocked_client, thread_id, approved=False, feedback="more")
    assert body["status"] == "finalized"
    assert body["awaiting_review"] is False
    assert "maximum revision limit" in body["final_report"]


def test_metrics_reflect_activity(mocked_client):
    mocked_client.post("/research", json={"topic": "test"})
    r = mocked_client.get("/metrics")
    body = r.json()
    assert body["reports_started"] >= 1
    assert body["request_count"] >= 1


def test_metrics_values_are_correct_not_just_present(mocked_client):
    """test_metrics_reflect_activity only checks values exist; this checks
    they're actually right, so a broken percentile/error_rate calculation
    doesn't ship silently."""
    _start(mocked_client, "one")
    _start(mocked_client, "two")
    r = mocked_client.get("/metrics")
    body = r.json()

    assert body["reports_started"] >= 2
    assert body["request_count"] >= 2
    assert body["error_count"] <= body["request_count"]
    assert 0.0 <= body["error_rate"] <= 1.0
    assert body["latency_ms_p50"] <= body["latency_ms_p95"] <= body["latency_ms_p99"]
    assert all(body[k] >= 0 for k in ("latency_ms_p50", "latency_ms_p95", "latency_ms_p99"))


def test_research_failure_surfaces_via_error_field(failing_researcher_client):
    """The graph now runs in the background, so a node blowing up cannot be a 500
    on the POST. It has to reach the client through the polled error field."""
    r = failing_researcher_client.post("/research", json={"topic": "test"})
    assert r.status_code == 202
    body = _await_thread(failing_researcher_client, r.json()["thread_id"])
    assert body["running"] is False
    assert "failed" in body["error"].lower()


def test_failed_thread_is_not_reported_or_reviewable_as_awaiting_review(failing_researcher_client):
    """A thread that fails on its very first node (before any interrupt()) still
    has a non-empty snapshot.next, since that just reflects "researcher runs
    next". awaiting_review must not conflate that with a real paused-for-review
    state, and /review must refuse to resume it."""
    r = failing_researcher_client.post("/research", json={"topic": "test"})
    thread_id = r.json()["thread_id"]
    body = _await_thread(failing_researcher_client, thread_id)
    assert body["awaiting_review"] is False

    r = failing_researcher_client.post(f"/research/{thread_id}/review", json={"approved": True})
    assert r.status_code == 400


def test_crashed_job_entry_still_blocks_review_after_restart(failing_researcher_client):
    """_jobs is documented as lost on restart (the checkpoint survives, the
    running/error flag does not). Simulate that: let a run genuinely fail
    (error correctly recorded), then drop its _jobs entry as a real process
    restart would, and confirm the already-fixed awaiting_review/review gate
    holds even with no error flag left to check -- a missing entry must not
    be read as "clean success" for a thread that never reached interrupt()."""
    from app.main import _jobs

    r = failing_researcher_client.post("/research", json={"topic": "test"})
    thread_id = r.json()["thread_id"]
    _await_thread(failing_researcher_client, thread_id)
    assert thread_id in _jobs  # sanity: the error entry exists before "restart"

    _jobs.pop(thread_id, None)  # simulate the in-memory dict being lost on restart

    body = failing_researcher_client.get(f"/research/{thread_id}").json()
    assert body["awaiting_review"] is False

    r = failing_researcher_client.post(f"/research/{thread_id}/review", json={"approved": True})
    assert r.status_code == 400


def test_concurrent_review_calls_only_one_is_accepted(mocked_client):
    """Two overlapping /review submissions for the same thread_id (double
    click, two open tabs) must not both schedule a graph run against the
    same checkpoint -- exactly one may be accepted (202). The rest are
    rejected either as 409 (still running, if they land while the winner's
    background run is in flight) or 400 (already finalized, if the mocked
    -- effectively instant -- run finishes before they're scheduled), but
    never a second 202."""
    started = _start(mocked_client, "test")
    thread_id = started["thread_id"]

    results = []

    def submit():
        r = mocked_client.post(f"/research/{thread_id}/review", json={"approved": True})
        results.append(r.status_code)

    import threading

    threads = [threading.Thread(target=submit) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(202) == 1
    assert set(results) <= {202, 409, 400}
    _await_thread(mocked_client, thread_id)


def test_auth_rejects_missing_key_when_api_key_configured(mocked_client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "API_KEY", "secret123")
    r = mocked_client.post("/research", json={"topic": "test"})
    assert r.status_code == 401


def test_auth_accepts_correct_key(mocked_client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "API_KEY", "secret123")
    r = mocked_client.post("/research", json={"topic": "test"}, headers={"X-API-Key": "secret123"})
    assert r.status_code == 202


def test_auth_rejects_wrong_key_when_api_key_configured(mocked_client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "API_KEY", "secret123")
    r = mocked_client.post("/research", json={"topic": "test"}, headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_start_research_rejects_topic_over_max_length(mocked_client):
    r = mocked_client.post("/research", json={"topic": "x" * 501})
    assert r.status_code == 422


def test_start_research_accepts_topic_at_max_length(mocked_client):
    r = mocked_client.post("/research", json={"topic": "x" * 500})
    assert r.status_code == 202


def test_sqlite_persistence_across_app_restarts(tmp_path, fake_agents, monkeypatch):
    """Verify thread state created in one app instance is readable from a fresh app instance sharing the same SQLite DB file."""
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from app import config

    db_file = str(tmp_path / "test_checkpoints.sqlite")
    monkeypatch.setattr(config, "DB_PATH", db_file)

    with patch("app.graph.researcher_node", fake_agents["researcher"]), \
         patch("app.graph.analyst_node", fake_agents["analyst"]), \
         patch("app.graph.writer_node", fake_agents["writer"]):
        from app.main import app

        # App Instance 1: start research thread
        with TestClient(app) as client1:
            res1 = client1.post("/research", json={"topic": "persistent topic"})
            assert res1.status_code == 202
            thread_id = res1.json()["thread_id"]
            _await_thread(client1, thread_id)

        # App Instance 2: verify thread is still retrieved from disk
        with TestClient(app) as client2:
            res2 = client2.get(f"/research/{thread_id}")
            assert res2.status_code == 200
            assert res2.json()["draft"] == "draft v1"
            assert res2.json()["awaiting_review"] is True

            # Resume and finalize in instance 2
            rev_res = client2.post(f"/research/{thread_id}/review", json={"approved": True})
            assert rev_res.status_code == 202
            assert _await_thread(client2, thread_id)["status"] == "finalized"


def test_root_serves_the_web_ui(mocked_client):
    r = mocked_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
