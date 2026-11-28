"""Queue an ingestion through your API and watch the worker do it.

Usage:
    uv run python scripts/ingest.py --limit 20
    uv run python scripts/ingest.py --limit 20 --url https://your-app.onrender.com
    uv run python scripts/ingest.py --retry-failed
    uv run python scripts/ingest.py --limit 20 --watch
    uv run python scripts/ingest.py --watch JOB_ID

On a public URL the API requires the token: configure ADMIN_TOKEN in .env.
"""

import argparse
import sys
import time

import httpx

from licenselens.config import get_settings


def show(progress: dict) -> str:
    counts = progress.get("counts", {})
    parts = " ".join(
        f"{name}={counts.get(name, 0)}"
        for name in ("queued", "running", "done", "skipped", "failed")
    )
    pages = progress.get("pages", 0)
    seconds = progress.get("seconds", 0.0)
    status = progress.get("status", "unknown")
    return f"{status:<9} {parts} pages={pages} {seconds:.0f}s"


def watch(client: httpx.Client, job_id: str, every: float) -> int:
    last = ""
    while True:
        try:
            response = client.get(f"/v1/ingest/{job_id}")
        except httpx.TransportError as exc:
            print(f"Connection error while watching: {exc}")
            return 1

        if response.status_code != 200:
            try:
                detail = response.json().get("detail", response.text)
            except Exception:
                detail = response.text[:200]
            print(f"HTTP {response.status_code}: {detail}")
            return 1

        progress = response.json()
        line = show(progress)
        if line.rsplit(" ", 1)[0] != last:  # print only when progress changed
            print(line, flush=True)
            last = line.rsplit(" ", 1)[0]

        if progress.get("status") == "finished":
            break

        time.sleep(every)

    dead_letter = progress.get("dead_letter", [])
    for item in dead_letter:
        attempts = item.get("attempts", 0)
        tries = "1 try" if attempts == 1 else f"{attempts} tries"
        target = item.get("path") or item.get("url") or item.get("item")
        print(f"FAILED {target} ({tries}) {item.get('error', '')}")

    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", default="http://localhost:8000", help="API base URL")
    parser.add_argument(
        "--limit", type=int, default=20, help="maximum number of documents to ingest"
    )
    parser.add_argument("--source", default="", help="filter by source")
    parser.add_argument(
        "--retry-failed", action="store_true", help="retry previously failed documents"
    )
    parser.add_argument(
        "--watch",
        nargs="?",
        const=True,
        default=False,
        metavar="JOB_ID",
        help="monitor job progress until completion (or watch an existing JOB_ID)",
    )
    parser.add_argument(
        "--every", type=float, default=2.0, help="polling interval when watching progress"
    )
    args = parser.parse_args()

    settings = get_settings()
    headers = {"X-Admin-Token": settings.admin_token} if settings.admin_token else {}

    try:
        with httpx.Client(base_url=args.url.rstrip("/"), headers=headers, timeout=90.0) as client:
            if isinstance(args.watch, str):
                return watch(client, args.watch, args.every)

            body = {
                "limit": args.limit,
                "source": args.source,
                "retry_failed": args.retry_failed,
            }
            response = client.post("/v1/ingest", json=body)
            try:
                answer = response.json()
            except Exception:
                print(f"HTTP {response.status_code}: {response.text[:200]}")
                return 1

            if response.status_code not in (200, 202):
                detail = answer.get("detail", answer) if isinstance(answer, dict) else answer
                print(f"HTTP {response.status_code}: {detail}")
                return 1

            job_id = answer.get("job_id")
            queued = answer.get("queued", 0)

            if not job_id:
                detail = answer.get("detail", "Nothing to do: all of it is ingested.")
                print(f"HTTP {response.status_code}: {detail}")
                return 0

            print(f"HTTP {response.status_code}: job {job_id}, {queued} queued")

            if args.watch:
                return watch(client, job_id, args.every)

            return 0

    except httpx.ConnectError:
        print(f"Could not connect. Is the LicenseLens API running at {args.url}?")
        return 1
    except httpx.TimeoutException as exc:
        print(f"Request timed out connecting to {args.url}: {exc}")
        return 1
    except httpx.HTTPError as exc:
        print(f"HTTP error connecting to {args.url}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
