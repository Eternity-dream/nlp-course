"""通过独立 HTTP 服务验收西安五日规划、局部编辑和备份恢复。"""

import argparse
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from accept_backend_live import wait_job
from acceptance_support import Server, require, run_acceptance, save


def trip_request(start, reference_id):
    end = start + timedelta(days=4)
    return {
        "destination": "西安",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "travelers": 2,
        "budget_cents": 350000,
        "known_conditions": [
            "两名成年人，住宿自行安排在南稍门或钟楼附近。首日10:00抵达西安北站，末日18:00从西安北站离开。",
            "愿意提前预约，目前未预约。全程预算3500元用于两人门票、餐饮和当地交通。",
        ],
        "priorities": [
            "喜欢历史文化、博物馆与老街区，想去兵马俑、陕西历史博物馆、西安城墙、大雁塔或碑林，可按开放情况安排。",
            "节奏舒适，地铁和步行为主。",
        ],
        "requirements": ["餐饮只安排午餐、晚餐大致时段，不指定店家。"],
        "time_constraints": [
            {
                "kind": "arrival",
                "on_date": start.isoformat(),
                "minute": 600,
                "place_name": "西安北站",
            },
            {
                "kind": "departure",
                "on_date": end.isoformat(),
                "minute": 1080,
                "place_name": "西安北站",
            },
        ],
        "reference_ids": [reference_id],
    }


def summarize(result):
    places = {p["id"]: p["name"] for p in result["places"]}
    visits, days, meals = defaultdict(list), [], []
    for day in result["days"]:
        activities = []
        for a in day["activities"]:
            activities.append({**a, "place": places[a["place_id"]]})
            if a["kind"] == "meal":
                meals.append({"date": day["on_date"], **activities[-1]})
            else:
                visits[a["place_id"]].append(day["on_date"])
        days.append({"date": day["on_date"], "activities": activities})
    facts = Counter(f["status"] for f in result["evidence"])
    activities = [a for day in days for a in day["activities"]]
    return {
        "days": days,
        "activity_count": len(activities),
        "introductions": sum(bool(a["description"].strip()) for a in activities),
        "meals": meals,
        "flexible_meals": sum(a["flexible_meal"] for a in meals),
        "repeated_non_meals": [
            {"id": k, "place": places[k], "dates": dates}
            for k, dates in visits.items()
            if len(dates) > 1
        ],
        "facts": {k: facts[k] for k in ("supported", "estimated", "unknown", "conflict")},
        "search_attempts": dict(
            Counter(a["provider"] + "/" + a["outcome"] for a in result["search_attempts"])
        ),
        "validation": result["validation"],
        "steps": result["planning_context"]["steps"],
        "tool_calls": result["planning_context"]["tool_calls"],
    }


def check_budget(result, travelers):
    supported = {f["id"] for f in result["evidence"] if f["status"] == "supported"}
    known = estimated = unknown = 0
    for day in result["days"]:
        for a in day["activities"]:
            category = "meal" if a["kind"] == "meal" else "ticket"
            unknown += not any(c["category"] == category for c in a["costs"])
            for c in a["costs"]:
                if c["amount_cents"] is None:
                    unknown += 1
                    continue
                amount = c["amount_cents"] * (travelers if c["basis"] == "per_person" else 1)
                if c["evidence_id"] in supported:
                    known += amount
                else:
                    estimated += amount
    for leg in result["transport"]:
        route = leg["route"]
        if route["mode"] != "walking":
            if route["fare_cents"] is None:
                unknown += 1
            else:
                estimated += route["fare_cents"] * (
                    travelers if route["fare_basis"] == "per_person" else 1
                )
    totals = {
        "known_cost_cents": known,
        "estimated_cost_cents": estimated,
        "unknown_cost_count": unknown,
    }
    require(all(result["validation"][k] == v for k, v in totals.items()), "全程费用逐项复算不符")
    return totals


def live_server(root):
    server = Server(root, configured=True)
    server.environment["PROVIDER_TIMEOUT_SECONDS"] = "120"
    server.environment["NO_PROXY"] = server.environment["no_proxy"] = "127.0.0.1,localhost,::1"
    return server


def scenario(args, report):
    server = live_server(args.output_dir)
    try:
        server.start()
        report["config"] = server.get("/api/config/status")
        save(args.output_dir / "config-status.json", report["config"])
        require(report["config"]["ready_for_planning"], "规划所需服务配置不全")
        require(
            [(p["provider"], p["auth_mode"], p["protocol"]) for p in report["config"]["search"]]
            == [
                ("exa", "anonymous", "mcp"),
                ("deepseek", "key", "rest"),
                ("tavily", "disabled", "rest"),
                ("anysearch", "anonymous", "rest"),
            ],
            "搜索服务顺序或鉴权模式与本次验收条件不符",
        )
        report["checks"] = []
        for service in ("llm", "amap", "search"):
            check = server.request(
                "POST",
                "/api/config/check",
                timeout=600,
                json={"service": service, "destination": "西安"},
            ).json()
            report["checks"].append(check)
            save(args.output_dir / "service-checks.json", report["checks"])
        source = server.request(
            "POST",
            "/api/knowledge",
            expected=202,
            json={
                "title": "西安五日参观笔记",
                "text": "西安历史文化游参考：从南稍门或钟楼出发，"
                "可分区看明城墙、碑林及书院门的老街巷。"
                "临潼兵马俑往返留出半日以上。大雁塔和陕西历史博物馆可按预约情况组合。"
                "赏碑时想看石刻笔画和拓片工艺。开放、票价与预约以馆方适用日期的公告为准。",
            },
        ).json()
        reference = server.source(source["id"])
        require(reference["status"] == "ready", "参考资料导入失败")
        save(args.output_dir / "reference.json", reference)
        request = trip_request(args.start_date, source["id"])
        save(args.output_dir / "request.json", request)
        trip = server.request("POST", "/api/trips", expected=201, json=request).json()
        base = f"/api/trips/{trip['id']}"
        path = base + "/itinerary"
        started = time.monotonic()
        job = server.request(
            "POST",
            base + "/jobs",
            expected=202,
            json={"expected_revision": trip["revision"], "idempotency_key": "five-days"},
        ).json()
        save(args.output_dir / "job-id.json", job)
        done = wait_job(server, job["id"], args.timeout)
        save(args.output_dir / "plan-job.json", done)
        report["plan_seconds"] = round(time.monotonic() - started, 2)
        require(done["status"] == "succeeded", f"规划任务 {done['status']} {done['error']}")
        original = server.get(path)
        save(args.output_dir / "itinerary-1.json", original)
        report["plan"] = summarize(original)
        require(
            [day["on_date"] for day in original["days"]]
            == [(args.start_date + timedelta(days=i)).isoformat() for i in range(5)],
            "日期与五日请求不符",
        )
        report["reference_chunks_used"] = sorted(
            set(original["knowledge_chunk_ids"]) & {c["id"] for c in reference["chunks"]}
        )
        require(report["reference_chunks_used"], "指定参考资料没有片段进入模型上下文")
        report["budget_before"] = check_budget(original, 2)
        day = original["days"][1]
        activity = next(a for a in day["activities"] if a["end_minute"] - a["start_minute"] > 15)
        spec = {k: v for k, v in activity.items() if k != "id"}
        spec["end_minute"] -= 15
        body = {
            "expected_revision": original["revision"],
            "expected_request_revision": trip["revision"],
            "idempotency_key": "shorten-visit",
            "operations": [{"op": "replace", "activity_id": activity["id"], "activity": spec}],
        }
        save(args.output_dir / "edit-request.json", body)
        started = time.monotonic()
        job = server.request("PATCH", path, expected=202, json=body).json()
        done = wait_job(server, job["id"], args.timeout)
        save(args.output_dir / "edit-job.json", done)
        report["edit_seconds"] = round(time.monotonic() - started, 2)
        require(done["status"] == "succeeded", f"编辑任务 {done['status']} {done['error']}")
        edited = server.get(path)
        save(args.output_dir / "itinerary-2.json", edited)
        require(edited["revision"] == 2, "局部编辑未升至版本2")
        updated = next(a for a in edited["days"][1]["activities"] if a["id"] == activity["id"])
        require(
            updated["end_minute"] - updated["start_minute"]
            == spec["end_minute"] - spec["start_minute"],
            "时长修改未生效",
        )
        other_days = [d for d in original["days"] if d["on_date"] != day["on_date"]]
        ids = {a["id"] for d in other_days for a in d["activities"]}
        report["edit_checks"] = {
            "days_equal": other_days
            == [d for d in edited["days"] if d["on_date"] != day["on_date"]],
            "routes_equal": [a for a in original["transport"] if a["from_activity_id"] in ids]
            == [a for a in edited["transport"] if a["from_activity_id"] in ids],
            "evidence_equal": [f for f in original["evidence"] if f["on_date"] != day["on_date"]]
            == [f for f in edited["evidence"] if f["on_date"] != day["on_date"]],
            "history_equal": server.get(path + "?revision=1") == original,
        }
        require(all(report["edit_checks"].values()), "编辑范围或历史版本核验失败")
        report["budget_after"] = check_budget(edited, 2)
        report["edit_validation"] = edited["validation"]
        server.stop()
        report["verify"] = server.maintenance("verify")
        archive = args.output_dir / "backup.zip"
        report["backup"] = server.maintenance("backup", "--output", archive)
        restored = args.output_dir / "restored"
        report["restore"] = server.maintenance("restore", "--archive", archive, data=restored)
        report["verify_restored"] = server.maintenance("verify", data=restored)
        server.start(restored)
        require(
            server.get(path) == edited and server.get(path + "?revision=1") == original,
            "恢复后版本内容变化",
        )
        report["restored_versions_equal"] = True
    finally:
        server.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 12, 5))
    parser.add_argument("--timeout", type=int, default=3600)
    raise SystemExit(run_acceptance(parser.parse_args(), scenario))
