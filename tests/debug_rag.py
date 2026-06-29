"""Debug the RAG 500 error under concurrency."""
import asyncio
import httpx

BASE = "http://localhost:8000"

async def main():
    async with httpx.AsyncClient(base_url=BASE) as client:
        # First try single request
        r = await client.post("/api/query", json={"question": "EC2 high CPU fix", "k": 4}, timeout=30)
        print(f"Single request: {r.status_code}")
        if r.status_code != 200:
            print(r.text[:500])
        else:
            print("OK:", list(r.json().keys()))

        # Now try 5 concurrent
        tasks = [
            client.post("/api/query", json={"question": f"question {i}", "k": 3}, timeout=30)
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                print(f"  [{i}] Exception: {r}")
            else:
                print(f"  [{i}] {r.status_code}: {r.text[:200] if r.status_code != 200 else 'OK'}")

asyncio.run(main())
