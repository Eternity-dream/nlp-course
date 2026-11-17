"""通过实际 HTTP 服务验收六天动态规划，输出动作记录与重复地点统计。"""

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from accept_backend_live import wait_job
from acceptance_support import Server, require, run_acceptance, save


def scenario(args, report):
    server = Server(args.output_dir, configured=True, direct=False)
    server.environment["PROVIDER_TIMEOUT_SECONDS"] = "120"
    # 外部请求继承 proxy，本机验收轮询直连。
    server.environment["NO_PROXY"] = server.environment["no_proxy"] = "127.0.0.1,localhost,::1"
    try:
        server.start()
        request = {
            "destination": "西安",
            "start_date": args.start_date.isoformat(),
            "end_date": (args.start_date + timedelta(days=5)).isoformat(),
            "travelers": 1,
            "budget_cents": None,
            "known_conditions": [
                "住南稍门附近，住宿自行安排。",
                f"{args.start_date.isoformat()}下午两点到，"
                f"{(args.start_date + timedelta(days=5)).isoformat()}下午三点走。",
                "可以安排兵马俑及周边，喜欢历史文化和当地美食，按常规消费安排。",
            ],
        }
        trip = server.request("POST", "/api/trips", expected=201, json=request).json()
        job = server.request(
            "POST",
            f"/api/trips/{trip['id']}/jobs",
            expected=202,
            json={"expected_revision": trip["revision"], "idempotency_key": "agent-six-days"},
        ).json()
        save(args.output_dir / "request.json", request)
        save(args.output_dir / "job-id.json", job)
        completed = wait_job(server, job["id"], args.timeout)
        save(args.output_dir / "job.json", completed)
        require(
            completed["status"] == "succeeded",
            f"动态规划未完成：{completed['status']} {completed['error']}",
        )
        result = server.get(f"/api/trips/{trip['id']}/itinerary")
        save(args.output_dir / "itinerary.json", result)
        places = {place["id"]: place for place in result["places"]}
        repeated = defaultdict(list)
        days = []
        for day in result["days"]:
            rows = []
            for activity in day["activities"]:
                place = places[activity["place_id"]]
                rows.append(
                    {
                        "name": place["name"],
                        "kind": activity["kind"],
                        "start": activity["start_minute"],
                        "end": activity["end_minute"],
                        "category": place.get("category"),
                    }
                )
                if activity["kind"] != "meal":
                    repeated[place["id"]].append(day["on_date"])
            days.append({"date": day["on_date"], "activities": rows})
        report["days"] = days
        report["repeated_visits"] = {
            places[key]["name"]: days for key, days in repeated.items() if len(days) > 1
        }
        report["validation"] = result["validation"]
        report["planning_context"] = result["planning_context"]
        report["stage_progress"] = [(e["stage"], e["status"]) for e in completed["events"]]
        print(
            json.dumps(
                {
                    "days": days,
                    "repeated_visits": report["repeated_visits"],
                    "status": result["validation"]["status"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        require(len(days) == 6, "行程天数不符")
        server.stop()
        report["verify"] = server.maintenance("verify")
        report["backup"] = server.maintenance("backup", "--output", args.output_dir / "backup.zip")
    finally:
        server.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 12, 5))
    parser.add_argument("--timeout", type=int, default=3600)
    raise SystemExit(run_acceptance(parser.parse_args(), scenario))
