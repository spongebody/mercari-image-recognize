#!/usr/bin/env python3
"""
Batch runner for Mercari title→category API tests.

Usage:
  python scripts/run_title_tests.py \
    --base-url http://localhost:8000 \
    --input data/title_test_cases.csv \
    --output logs/title_test_results.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests


def load_cases(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cases = []
        for row in reader:
            title = (row.get("title") or "").strip()
            if not title:
                continue
            cases.append(
                {
                    "id": row.get("id") or "",
                    "title": title,
                    "language": (row.get("language") or "ja").strip() or "ja",
                    "image_url": (row.get("image_url") or "").strip(),
                }
            )
    return cases


def run_case(
    session: requests.Session,
    base_url: str,
    case: Dict[str, Any],
    timeout: int,
) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/v1/mercari/title/analyze"
    payload: Dict[str, Any] = {
        "title": case["title"],
        "language": case.get("language") or "ja",
    }
    if case.get("image_url"):
        payload["image_url"] = case["image_url"]

    started = time.time()
    result: Dict[str, Any] = {
        "id": case.get("id"),
        "title": case["title"],
        "language": case.get("language"),
        "image_url": case.get("image_url"),
    }

    try:
        resp = session.post(url, json=payload, timeout=timeout)
        elapsed_ms = int((time.time() - started) * 1000)
        result["elapsed_ms"] = elapsed_ms
    except requests.RequestException as exc:
        result.update({"status": "request_error", "error": str(exc)})
        return result

    text = resp.text
    try:
        data = resp.json()
    except ValueError:
        data = {"_parse_error": text}

    result["response"] = data

    if not resp.ok:
        result.update(
            {
                "status": "http_error",
                "http_status": resp.status_code,
                "error": data.get("detail") if isinstance(data, dict) else text,
            }
        )
        return result

    best_path = data.get("best_target_path")
    best_id = data.get("best_category_id")
    if isinstance(best_path, str) and best_path.strip():
        result["status"] = "ok"
    else:
        result["status"] = "validation_failed"
        result["error"] = "Missing best_target_path"

    if best_id:
        result["best_category_id"] = best_id

    # Attach alternatives summary (if any)
    alts: Iterable[Any] = data.get("alternatives") or []
    alt_summary: List[Dict[str, Any]] = []
    if isinstance(alts, list):
        for alt in alts:
            if isinstance(alt, dict):
                alt_summary.append(
                    {
                        "target_path": alt.get("target_path"),
                        "category_id": alt.get("category_id"),
                    }
                )
            else:
                alt_summary.append({"target_path": alt})
    if alt_summary:
        result["alternatives"] = alt_summary

    return result


def write_results(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run batch title->category tests.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL.")
    parser.add_argument("--input", default="data/title_test_cases.csv", help="CSV file with test cases.")
    parser.add_argument("--output", default="logs/title_test_results.jsonl", help="Where to write results.")
    parser.add_argument("--timeout", type=int, default=30, help="Per-request timeout in seconds.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of cases (0 = all).")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[error] input file not found: {input_path}", file=sys.stderr)
        return 1

    cases = load_cases(input_path)
    if args.limit > 0:
        cases = cases[: args.limit]

    session = requests.Session()
    results: List[Dict[str, Any]] = []
    ok = 0
    failed = 0
    for idx, case in enumerate(cases, start=1):
        res = run_case(session, args.base_url, case, args.timeout)
        results.append(res)
        status = res.get("status")
        if status == "ok":
            ok += 1
        else:
            failed += 1
        print(f"[{idx}/{len(cases)}] {status}: {case['title']}")

    write_results(Path(args.output), results)
    print(f"\nDone. OK={ok} Failed={failed} -> results saved to {args.output}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
