"""
Run this AFTER starting ngrok to generate openapi-orchestrate.json
with the correct public URL baked in.

Usage:
    python export_orchestrate_spec.py https://xxxx.ngrok-free.app
"""
import sys
import json
import httpx

def export(base_url: str):
    base_url = base_url.rstrip("/")
    url = f"{base_url}/openapi-orchestrate.json"
    print(f"Fetching: {url}")
    r = httpx.get(url, timeout=10, headers={"ngrok-skip-browser-warning": "1"})
    spec = r.json()
    # Ensure server URL is the public ngrok address
    spec["servers"] = [{"url": base_url, "description": "AWS DevOps RAG Agent"}]
    out = "openapi-orchestrate.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2)
    print(f"Saved: {out}")
    print(f"Server URL in spec: {base_url}")
    print(f"\nUpload '{out}' to WatsonX Orchestrate → Skills catalog → Add skill → From file")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python export_orchestrate_spec.py https://xxxx.ngrok-free.app")
        sys.exit(1)
    export(sys.argv[1])
