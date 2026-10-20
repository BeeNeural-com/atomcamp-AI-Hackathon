"""Redis delivery queue and shared rate limiter, using an in-memory Redis stand-in."""

from __future__ import annotations

from collections import defaultdict

from beeproof.api.app import RateLimiter
from beeproof.providers import queue as Q
from beeproof.worker import Worker


class FakeRedis:
    """The subset of redis-py used by BeeProof."""

    def __init__(self):
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.counters: dict[str, int] = defaultdict(int)
        self.down = False

    def _check(self):
        if self.down:
            raise ConnectionError("redis down")

    def lpush(self, key, value):
        self._check()
        self.lists[key].insert(0, value)

    def brpop(self, keys, timeout=0):
        self._check()
        for k in keys:
            if self.lists[k]:
                return k, self.lists[k].pop()
        return None

    def ping(self):
        self._check()
        return True

    def pipeline(self):
        parent, ops = self, []

        class Pipe:
            def incr(self, key):
                ops.append(("incr", key))

            def expire(self, key, seconds):
                ops.append(("expire", key))

            def execute(self):
                parent._check()
                out = []
                for op, key in ops:
                    if op == "incr":
                        parent.counters[key] += 1
                        out.append(parent.counters[key])
                    else:
                        out.append(True)
                return out

        return Pipe()


def test_redis_queue_fifo_and_failure_tolerance():
    r = FakeRedis()
    q = Q.RedisQueue("redis://unused", client=r)
    q.publish("a")
    q.publish("b")
    assert (q.wait(1), q.wait(1), q.wait(1)) == ("a", "b", None)
    r.down = True
    q.publish("c")  # swallowed: the DB sweep still delivers the job
    assert q.ping() is False


def test_api_publishes_and_worker_consumes(client, sample_pdf, settings, monkeypatch):
    r = FakeRedis()
    rq = Q.RedisQueue("redis://unused", client=r)
    monkeypatch.setattr(Q, "_queue", rq)
    doc = client.post("/api/v1/documents", files={"file": ("t.pdf", sample_pdf, "application/pdf")}).json()["data"]
    job_id = client.post(f"/api/v1/documents/{doc['id']}/jobs", json={}).json()["data"]["job_id"]
    assert r.lists[Q.JOBS_KEY] == [job_id]
    assert Worker(settings, queue=rq).run_once() is True
    assert client.get(f"/api/v1/jobs/{job_id}").json()["data"]["status"] == "review_required"
    health = client.get("/api/v1/health").json()["data"]
    assert health["queue"] == "redis" and health["queue_ok"] and health["database_ok"]


def test_redis_rate_limiter_is_shared_and_falls_back():
    r = FakeRedis()
    a, b = RateLimiter(3, r), RateLimiter(3, r)  # two API replicas
    assert [a.allow("ip"), b.allow("ip"), a.allow("ip"), b.allow("ip")] == [True, True, True, False]
    r.down = True
    assert a.allow("other") is True  # in-process fallback


def test_healthz_reports_unreachable_queue(client, monkeypatch):
    r = FakeRedis()
    r.down = True
    monkeypatch.setattr(Q, "_queue", Q.RedisQueue("redis://unused", client=r))
    resp = client.get("/healthz")
    assert resp.status_code == 503 and resp.json()["queue_ok"] is False
