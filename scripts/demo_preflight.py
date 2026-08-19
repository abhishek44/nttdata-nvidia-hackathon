from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request(method: str, url: str, payload: dict | None = None, timeout: float = 20) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, method=method, headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="RecallZero hackathon demo preflight")
    parser.add_argument("--api", default=os.getenv("RECALLZERO_API_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--live", action="store_true", help="Also run one fresh NVIDIA trace")
    parser.add_argument("--odi", default=os.getenv("RECALLZERO_DEMO_ODI", "11466150"))
    parser.add_argument("--agent", action="store_true", help="Also run the optional NVIDIA Investigator Agent")
    args = parser.parse_args()
    base = args.api.rstrip("/")

    try:
        health = request("GET", f"{base}/health")
        print(f"[PASS] RecallZero API {health.get('version')} at {base}")
        readiness = request("GET", f"{base}/api/v1/demo/readiness")
        for name, value in readiness.get("checks", {}).items():
            print(f"[{'PASS' if value else 'FAIL'}] {name}")
        if not readiness.get("ready_for_live_trace"):
            print("[FAIL] Live NVIDIA trace is not ready")
            return 2

        actions = request("GET", f"{base}/api/v1/demo/action-readiness")
        alert = actions.get("alert", {})
        agent = actions.get("agent", {})
        print(f"[INFO] Engineering alert webhook: {'READY' if alert.get('configured') else 'optional/off'}")
        print(
            "[INFO] NVIDIA Investigator Agent: %s | execution %s"
            % (
                "AVAILABLE" if agent.get("available") else "not installed",
                "ENABLED" if agent.get("execution_enabled") else "safe-off",
            )
        )

        if args.live:
            print(f"[RUN ] Fresh NVIDIA trace for ODI {args.odi} ...")
            trace = request(
                "POST",
                f"{base}/api/v1/demo/nvidia-trace",
                {"odi_number": args.odi},
                timeout=300,
            )
            print(
                "[PASS] NIM %.2fs | embedding %.2fs | dimension %s | cache_write=%s"
                % (
                    float(trace["extraction"]["latency_ms"]) / 1000,
                    float(trace["embedding"]["latency_ms"]) / 1000,
                    trace["embedding"]["dimension"],
                    trace["cache_write"],
                )
            )
        if args.agent:
            print("[RUN ] NVIDIA Investigator Agent ...")
            result = request(
                "POST",
                f"{base}/api/v1/demo/agent-investigate",
                {
                    "prompt": (
                        "Investigate the 2021 and 2022 Ford Mustang Mach-E. Summarize the highest-priority "
                        "emerging signal and cite source ODI evidence."
                    )
                },
                timeout=210,
            )
            print(f"[PASS] Agent workflow {result.get('workflow')} completed with {len(result.get('tools', []))} tools available")
        return 0
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"[FAIL] HTTP {exc.code}: {detail}")
    except URLError as exc:
        print(f"[FAIL] Could not reach {base}: {exc.reason}")
    except Exception as exc:
        print(f"[FAIL] {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
