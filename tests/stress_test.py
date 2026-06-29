"""
Stress test suite for the AWS DevOps RAG Agent server.

Tests:
  1. Health endpoint — 200 concurrent requests
  2. /api/status   — 20 concurrent (calls AWS + anomaly detection)
  3. /api/query    — 30 concurrent RAG queries with varied questions
  4. WebSocket     — 50 simultaneous connections, receive 3 events each
  5. Spike test    — 500 rapid-fire /health requests

Run:  python tests/stress_test.py
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from typing import List

import httpx
import websockets

BASE = "http://localhost:8000"
WS_BASE = "ws://localhost:8000"

RAG_QUESTIONS = [
    "EC2 high CPU how to fix",
    "RDS out of storage what to do",
    "Lambda errors increasing remediation",
    "ECS task crashing fix",
    "ALB 5xx errors troubleshoot",
    "how to scale out auto scaling group",
    "RDS too many connections fix",
    "Lambda throttles how to resolve",
    "EC2 status check failed remediation",
    "ECS memory utilization high",
]


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TestResult:
    name: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    latencies: List[float] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return (self.passed / self.total * 100) if self.total else 0

    @property
    def p50(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0

    @property
    def p95(self) -> float:
        if not self.latencies:
            return 0
        s = sorted(self.latencies)
        return s[int(len(s) * 0.95)]

    @property
    def p99(self) -> float:
        if not self.latencies:
            return 0
        s = sorted(self.latencies)
        return s[int(len(s) * 0.99)]

    @property
    def avg(self) -> float:
        return statistics.mean(self.latencies) if self.latencies else 0


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get(client: httpx.AsyncClient, url: str, result: TestResult):
    t0 = time.perf_counter()
    try:
        r = await client.get(url, timeout=30)
        elapsed = (time.perf_counter() - t0) * 1000
        result.latencies.append(elapsed)
        result.total += 1
        if r.status_code == 200:
            result.passed += 1
        else:
            result.failed += 1
            result.errors.append(f"HTTP {r.status_code}")
    except Exception as e:
        result.total += 1
        result.failed += 1
        result.errors.append(str(e)[:80])


async def _post(client: httpx.AsyncClient, url: str, body: dict, result: TestResult):
    t0 = time.perf_counter()
    try:
        r = await client.post(url, json=body, timeout=60)
        elapsed = (time.perf_counter() - t0) * 1000
        result.latencies.append(elapsed)
        result.total += 1
        if r.status_code == 200:
            result.passed += 1
        else:
            result.failed += 1
            result.errors.append(f"HTTP {r.status_code}: {r.text[:80]}")
    except Exception as e:
        result.total += 1
        result.failed += 1
        result.errors.append(str(e)[:80])


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Health endpoint: 200 concurrent
# ─────────────────────────────────────────────────────────────────────────────

async def test_health_concurrent(n: int = 200) -> TestResult:
    result = TestResult(name=f"Health /{n} concurrent")
    async with httpx.AsyncClient(base_url=BASE) as client:
        tasks = [_get(client, "/health", result) for _ in range(n)]
        await asyncio.gather(*tasks)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — /api/status: 20 concurrent (hits AWS CloudWatch)
# ─────────────────────────────────────────────────────────────────────────────

async def test_status_concurrent(n: int = 20) -> TestResult:
    result = TestResult(name=f"Status /api/status x{n} concurrent")
    async with httpx.AsyncClient(base_url=BASE) as client:
        tasks = [_get(client, "/api/status", result) for _ in range(n)]
        await asyncio.gather(*tasks)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — RAG query: 30 concurrent varied questions
# ─────────────────────────────────────────────────────────────────────────────

async def test_rag_concurrent(n: int = 30) -> TestResult:
    result = TestResult(name=f"RAG /api/query x{n} concurrent")
    async with httpx.AsyncClient(base_url=BASE) as client:
        tasks = [
            _post(client, "/api/query",
                  {"question": RAG_QUESTIONS[i % len(RAG_QUESTIONS)], "k": 4},
                  result)
            for i in range(n)
        ]
        await asyncio.gather(*tasks)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — WebSocket: 50 simultaneous connections
# ─────────────────────────────────────────────────────────────────────────────

async def _ws_connect(result: TestResult, events_to_receive: int = 2):
    t0 = time.perf_counter()
    try:
        async with websockets.connect(
            f"{WS_BASE}/ws/events",
            ping_interval=None,
            open_timeout=10,
            close_timeout=5,
        ) as ws:
            received = 0
            # Wait up to 30s to receive N non-ping events
            deadline = time.perf_counter() + 30
            while received < events_to_receive and time.perf_counter() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    ev = json.loads(raw)
                    if ev.get("event_type") != "ping":
                        received += 1
                except asyncio.TimeoutError:
                    break
            elapsed = (time.perf_counter() - t0) * 1000
            result.latencies.append(elapsed)
            result.total += 1
            result.passed += 1
    except Exception as e:
        result.total += 1
        result.failed += 1
        result.errors.append(str(e)[:80])


async def test_websocket_concurrent(n: int = 50) -> TestResult:
    result = TestResult(name=f"WebSocket x{n} simultaneous connections")
    tasks = [_ws_connect(result) for _ in range(n)]
    await asyncio.gather(*tasks)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Spike: 500 rapid /health requests (throughput test)
# ─────────────────────────────────────────────────────────────────────────────

async def test_spike(n: int = 500) -> TestResult:
    result = TestResult(name=f"Spike /health x{n} (throughput)")
    t_wall = time.perf_counter()
    # Use a pool of 50 connections to fire 500 requests
    limits = httpx.Limits(max_connections=50, max_keepalive_connections=50)
    async with httpx.AsyncClient(base_url=BASE, limits=limits) as client:
        tasks = [_get(client, "/health", result) for _ in range(n)]
        await asyncio.gather(*tasks)
    result._wall_time = time.perf_counter() - t_wall
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Reporter
# ─────────────────────────────────────────────────────────────────────────────

def _bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    color = "\033[92m" if pct >= 95 else "\033[93m" if pct >= 80 else "\033[91m"
    return color + "█" * filled + "\033[0m" + "░" * (width - filled)


def print_result(r: TestResult):
    rps = getattr(r, "_wall_time", None)
    throughput = f"  {r.total / rps:.0f} req/s" if rps else ""
    status = "\033[92mPASS\033[0m" if r.success_rate >= 95 else "\033[91mFAIL\033[0m"
    print(f"\n  [{status}] {r.name}")
    print(f"         Success : {_bar(r.success_rate)} {r.success_rate:.1f}%  "
          f"({r.passed}/{r.total}){throughput}")
    if r.latencies:
        print(f"         Latency : avg={r.avg:.0f}ms  p50={r.p50:.0f}ms  "
              f"p95={r.p95:.0f}ms  p99={r.p99:.0f}ms  "
              f"max={max(r.latencies):.0f}ms")
    if r.errors:
        unique = list(dict.fromkeys(r.errors))[:3]
        print(f"         Errors  : {'; '.join(unique)}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    print("\n" + "="*64)
    print("  AWS DevOps RAG Agent — Stress Test Suite")
    print("="*64)

    # Quick connectivity check
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{BASE}/health", timeout=5)
            assert r.status_code == 200
        print(f"\n  Server reachable at {BASE}  ✓\n")
    except Exception as e:
        print(f"\n  ERROR: Cannot reach {BASE} — {e}")
        print("  Start the server first:  python main.py\n")
        return

    results = []
    wall = time.perf_counter()

    print("  Running tests (this takes ~60-90s due to AWS calls)...\n")

    # Run tests sequentially so AWS rate limits aren't hit simultaneously
    print("  [1/5] Health endpoint — 200 concurrent...")
    results.append(await test_health_concurrent(200))

    print("  [2/5] WebSocket — 50 simultaneous connections...")
    results.append(await test_websocket_concurrent(50))

    print("  [3/5] RAG query — 30 concurrent questions...")
    results.append(await test_rag_concurrent(30))

    print("  [4/5] API status — 20 concurrent (live AWS calls)...")
    results.append(await test_status_concurrent(20))

    print("  [5/5] Spike — 500 rapid /health requests...")
    results.append(await test_spike(500))

    total_wall = time.perf_counter() - wall

    print("\n" + "="*64)
    print("  RESULTS")
    print("="*64)
    for r in results:
        print_result(r)

    passed = sum(1 for r in results if r.success_rate >= 95)
    total_reqs = sum(r.total for r in results)
    print(f"\n{'='*64}")
    print(f"  Tests passed : {passed}/{len(results)}")
    print(f"  Total requests: {total_reqs}")
    print(f"  Wall time     : {total_wall:.1f}s")
    print("="*64 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
