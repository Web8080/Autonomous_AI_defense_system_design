#!/usr/bin/env python3
"""L1 simulation regression: exercise must meet minimum precision/recall.

Author: Victor.I

Runs against a live simulation_service + inference stack (compose up).
Exit 0 on pass; non-zero on fail — suitable for CI nightly when Kafka is up.

Usage:
  python scripts/sim_regression.py --base http://localhost:8014 --min-precision 0.05 --frames 30
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _req(method: str, url: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8014")
    parser.add_argument("--layer", type=int, default=1)
    parser.add_argument("--scenario", default="railway-yard")
    parser.add_argument("--video", default="")
    parser.add_argument("--sequence", default="")
    parser.add_argument("--frames", type=int, default=40)
    parser.add_argument("--fps", type=float, default=5)
    parser.add_argument("--min-precision", type=float, default=0.0)
    parser.add_argument("--min-recall", type=float, default=0.0)
    parser.add_argument("--timeout-sec", type=float, default=180)
    args = parser.parse_args()

    body: dict = {"layer": args.layer, "fps": args.fps, "frames": args.frames}
    if args.layer == 1:
        if args.video:
            body["video"] = args.video
        if args.sequence:
            body["sequence"] = args.sequence
    else:
        body["scenario"] = args.scenario

    try:
        ex = _req("POST", f"{args.base}/exercises", body)
    except urllib.error.URLError as exc:
        print(f"FAIL: cannot reach simulation_service: {exc}", file=sys.stderr)
        sys.exit(2)

    eid = ex["id"]
    print(f"exercise {eid} started")
    deadline = time.time() + args.timeout_sec
    status = ex
    while time.time() < deadline:
        status = _req("GET", f"{args.base}/exercises/{eid}")
        if status.get("state") in {"completed", "stopped"}:
            break
        time.sleep(1.0)
    else:
        try:
            _req("POST", f"{args.base}/exercises/{eid}/stop", {})
        except Exception:
            pass
        print("FAIL: timeout waiting for exercise", file=sys.stderr)
        sys.exit(3)

    sb = status.get("scoreboard", {}).get("overall", {})
    p = float(sb.get("precision") or 0)
    r = float(sb.get("recall") or 0)
    gt = int(sb.get("gt") or 0)
    print(json.dumps({"id": eid, "precision": p, "recall": r, "gt": gt, "state": status.get("state")}, indent=2))

    if gt == 0 and args.layer == 1:
        print("WARN: zero GT scored — model may be stub or frames unmatched", file=sys.stderr)
    if p < args.min_precision or r < args.min_recall:
        print(
            f"FAIL: P={p:.3f} R={r:.3f} below floors "
            f"P>={args.min_precision} R>={args.min_recall}",
            file=sys.stderr,
        )
        sys.exit(1)
    print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
