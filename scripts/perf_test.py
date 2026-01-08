import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DEFAULT_IMAGE_FILES = ["lv.jpg", "test.png"]


def _now_stamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")


def _percentile(values: Sequence[float], pct: float) -> Optional[float]:
    if not values:
        return None
    values_sorted = sorted(values)
    k = (len(values_sorted) - 1) * pct
    f = int(k)
    c = min(f + 1, len(values_sorted) - 1)
    if f == c:
        return values_sorted[f]
    return values_sorted[f] + (values_sorted[c] - values_sorted[f]) * (k - f)


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    return "application/octet-stream"


def load_images(paths: Sequence[Path]) -> List[Tuple[str, bytes, str]]:
    images: List[Tuple[str, bytes, str]] = []
    for path in paths:
        if not path.exists():
            continue
        images.append((path.name, path.read_bytes(), _guess_mime(path)))
    return images


def load_titles(path: Path, limit: int) -> List[Dict[str, str]]:
    titles: List[Dict[str, str]] = []
    if not path.exists():
        return titles
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = (row.get("title") or "").strip()
            language = (row.get("language") or "").strip() or "ja"
            if not title:
                continue
            titles.append({"title": title, "language": language})
            if limit > 0 and len(titles) >= limit:
                break
    return titles


def call_image(
    base_url: str,
    image: Tuple[str, bytes, str],
    timeout: int,
) -> Dict[str, Any]:
    filename, data, mime = image
    files = {"image": (filename, data, mime)}
    data_fields = {
        "language": "ja",
        "debug": "false",
        "category_count": "1",
        "price_strategy": "vision",
    }
    url = f"{base_url}/api/v1/mercari/image/analyze"
    return _post_request(url, timeout=timeout, files=files, data=data_fields)


def call_title(
    base_url: str,
    payload: Dict[str, str],
    timeout: int,
) -> Dict[str, Any]:
    url = f"{base_url}/api/v1/mercari/title/analyze"
    return _post_request(url, timeout=timeout, json_payload=payload)


def _post_request(
    url: str,
    timeout: int,
    files: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    json_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    start = time.perf_counter()
    status_code = 0
    error = ""
    response_size = 0
    try:
        with requests.Session() as session:
            resp = session.post(url, files=files, data=data, json=json_payload, timeout=timeout)
            status_code = resp.status_code
            response_size = len(resp.content or b"")
            if status_code >= 400:
                error = resp.text[:200]
    except Exception as exc:
        error = str(exc)
    duration_ms = (time.perf_counter() - start) * 1000
    return {
        "status_code": status_code,
        "latency_ms": round(duration_ms, 2),
        "ok": status_code != 0 and status_code < 400 and not error,
        "error": error,
        "response_bytes": response_size,
    }


def run_scenario(
    name: str,
    tasks: Sequence[Any],
    worker_fn,
    concurrency: int,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker_fn, item) for item in tasks]
        for future in as_completed(futures):
            results.append(future.result())
    duration = time.perf_counter() - start
    return {"name": name, "duration_sec": duration, "results": results}


def summarize(results: List[Dict[str, Any]], duration_sec: float) -> Dict[str, Any]:
    latencies = [item["latency_ms"] for item in results if item.get("latency_ms") is not None]
    ok_count = sum(1 for item in results if item.get("ok"))
    total = len(results)
    summary = {
        "total_requests": total,
        "success_requests": ok_count,
        "error_requests": total - ok_count,
        "duration_sec": round(duration_sec, 2),
        "qps": round(total / duration_sec, 3) if duration_sec > 0 else 0.0,
    }
    if latencies:
        summary.update(
            {
                "latency_ms_avg": round(mean(latencies), 2),
                "latency_ms_min": round(min(latencies), 2),
                "latency_ms_max": round(max(latencies), 2),
                "latency_ms_p50": round(_percentile(latencies, 0.5) or 0.0, 2),
                "latency_ms_p90": round(_percentile(latencies, 0.9) or 0.0, 2),
                "latency_ms_p99": round(_percentile(latencies, 0.99) or 0.0, 2),
            }
        )
    return summary


def wait_for_health(base_url: str, timeout_sec: int = 60) -> None:
    deadline = time.time() + timeout_sec
    last_error = ""
    while time.time() < deadline:
        try:
            resp = requests.get(f"{base_url}/health", timeout=5)
            if resp.status_code == 200:
                return
            last_error = f"health returned {resp.status_code}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"Server not ready: {last_error}")


def start_server(host: str, port: int, log_path: Path) -> Any:
    import subprocess

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    env = os.environ.copy()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=ROOT_DIR, stdout=log_file, stderr=log_file, env=env)
    return proc, log_file


def stop_server(proc: Any, log_file: Any) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=15)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        log_file.close()
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run API performance tests.")
    parser.add_argument("--base-url", default="", help="Base URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--start-server", action="store_true", help="Start uvicorn before testing.")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--image-requests", type=int, default=6)
    parser.add_argument("--title-requests", type=int, default=10)
    parser.add_argument("--title-limit", type=int, default=10)
    parser.add_argument("--output-dir", default="perf_results")
    args = parser.parse_args()

    base_url = args.base_url or f"http://{args.host}:{args.port}"
    run_dir = ROOT_DIR / args.output_dir / _now_stamp()
    run_dir.mkdir(parents=True, exist_ok=True)

    proc = None
    log_file = None
    if args.start_server:
        proc, log_file = start_server(args.host, args.port, run_dir / "uvicorn.log")
        wait_for_health(base_url)

    try:
        image_paths = [DATA_DIR / name for name in DEFAULT_IMAGE_FILES]
        images = load_images(image_paths)
        if not images:
            raise RuntimeError("No test images found under data/.")

        titles = load_titles(DATA_DIR / "title_test_cases.csv", args.title_limit)
        if not titles:
            raise RuntimeError("No title test cases found under data/title_test_cases.csv.")

        image_tasks = [images[i % len(images)] for i in range(max(0, args.image_requests))]
        title_tasks = [titles[i % len(titles)] for i in range(max(0, args.title_requests))]

        scenarios: List[Dict[str, Any]] = []

        if image_tasks:
            scenarios.append(
                run_scenario(
                    "image_analyze",
                    image_tasks,
                    lambda item: call_image(base_url, item, args.timeout),
                    args.concurrency,
                )
            )

        if title_tasks:
            scenarios.append(
                run_scenario(
                    "title_analyze",
                    title_tasks,
                    lambda item: call_title(base_url, item, args.timeout),
                    args.concurrency,
                )
            )

        summary = {
            "started_at_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "base_url": base_url,
            "concurrency": args.concurrency,
            "image_requests": len(image_tasks),
            "title_requests": len(title_tasks),
            "scenarios": [],
        }

        all_rows: List[Dict[str, Any]] = []
        for scenario in scenarios:
            name = scenario["name"]
            duration_sec = scenario["duration_sec"]
            results = scenario["results"]
            for item in results:
                row = dict(item)
                row["scenario"] = name
                all_rows.append(row)
            summary["scenarios"].append(
                {
                    "name": name,
                    **summarize(results, duration_sec),
                }
            )

        with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        with (run_dir / "requests.jsonl").open("w", encoding="utf-8") as f:
            for row in all_rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    finally:
        if proc and log_file:
            stop_server(proc, log_file)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
