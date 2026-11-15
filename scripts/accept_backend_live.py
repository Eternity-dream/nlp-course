"""使用 .env 中的实际服务，通过 HTTP 验收公开样例的规划、编辑及恢复。"""

import argparse
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import httpx
from acceptance_support import Server, poll, require, run_acceptance, save
from travel_planner.config import Settings


def wait_job(server, job_id, timeout):
    stage = None
    last = {"status": "queued", "stage": "conditions"}
    interruptions = 0

    def read():
        nonlocal stage, last, interruptions
        try:
            job = server.get("/api/jobs/" + job_id)
        except httpx.HTTPError:
            interruptions += 1
            print(f"job {job_id}: 轮询连接重试 {interruptions}", flush=True)
            return last
        last = job
        current = (job["status"], job["stage"])
        if stage != current:
            print(f"job {job_id}: {current[0]}/{current[1]}", flush=True)
            stage = current
        return job

    result = poll(
        read,
        lambda job: job["status"] in {"succeeded", "failed", "interrupted", "awaiting_input"},
        timeout=timeout,
    )
    if interruptions:
        save(server.root / f"poll-errors-{job_id}.json", {"transport_errors": interruptions})
    return result


def check_itinerary(result, request):
    dates = [
        (date.fromisoformat(request["start_date"]) + timedelta(days=i)).isoformat()
        for i in range(3)
    ]
    require([day["on_date"] for day in result["days"]] == dates, "行程日期不匹配")
    activities = [activity for day in result["days"] for activity in day["activities"]]
    require(len({item["id"] for item in activities}) == len(activities), "活动 ID 重复")
    places = {item["id"] for item in result["places"]}
    require(all(item["place_id"] in places for item in activities), "活动地点引用缺失")
    sources = {item["id"]: item for item in result["sources"]}
    require(
        all(item["queried_at"] and item["url"] for item in sources.values()), "来源缺少查询时间"
    )
    require(all(item["source_id"] in sources for item in result["evidence"]), "事实来源引用缺失")
    require(result["validation"]["status"] in {"passed", "needs_review", "blocked"}, "核验状态缺失")
    severities = {issue["severity"] for issue in result["validation"]["issues"]}
    status = result["validation"]["status"]
    require("conflict" not in severities or status == "blocked", "硬冲突未标记为 blocked")
    require(not severities or status != "passed", "存在冲突或未知项却判定为通过")
    return {
        "activities": len(activities),
        "places": len(places),
        "sources": len(sources),
        "evidence": dict(Counter(item["status"] for item in result["evidence"])),
        "routes": dict(Counter(item["route"]["status"] for item in result["transport"])),
        "validation": result["validation"],
        "search_attempts": dict(
            Counter(item["provider"] + ":" + item["outcome"] for item in result["search_attempts"])
        ),
    }


def scenario(args, report):
    server = Server(args.output_dir, configured=True, direct=args.direct)
    report["network_mode"] = "direct_tls" if args.direct else "environment_proxy"
    report["model"] = Settings().llm_model
    if args.provider_timeout is not None:
        server.environment["PROVIDER_TIMEOUT_SECONDS"] = str(args.provider_timeout)
        report["provider_timeout_seconds"] = args.provider_timeout
    try:
        server.start()
        config = server.get("/api/config/status")
        report["config"] = config
        require(config["ready_for_planning"], "模型、地图或搜索配置尚未齐全")
        report["checks"] = []
        for service in ("llm", "amap", "search"):
            for attempt in range(3):
                result = server.request(
                    "POST", "/api/config/check", json={"service": service, "destination": "西安"}
                ).json()
                report["checks"].append(result)
                if result["success"] or result["details"].get("error", {}).get("code") not in {
                    "provider_unavailable",
                    "provider_timeout",
                    "provider_rate_limited",
                }:
                    break
                if attempt < 2:
                    time.sleep(2**attempt)
            require(result["success"], f"{service} 连通性检查失败")
        preferences = server.request(
            "PUT",
            "/api/preferences",
            json={"expected_revision": 1, "preferences": {"节奏": "慢节奏", "兴趣": "历史文化"}},
        ).json()
        source = server.request(
            "POST",
            "/api/knowledge",
            expected=202,
            json={
                "title": "公开验收笔记",
                "text": "西安旅行背景资料：碑林博物馆、城墙和公园适合安排参观。"
                "本笔记不提供营业时间或票价，出行信息应查询公开来源。",
            },
        ).json()
        require(server.source(source["id"])["status"] == "ready", "本地资料导入失败")
        request = {
            "destination": "西安",
            "start_date": args.start_date.isoformat(),
            "end_date": (args.start_date + timedelta(days=2)).isoformat(),
            "travelers": 2,
            "budget_cents": 300000,
            "priorities": ["历史文化", "当地美食"],
            "known_conditions": [
                "三天均在西安市内，住宿已安排，每天可使用完整白天，无已订门票或预约。"
            ],
            "assumptions": ["两名成年人，无饮食禁忌，公共交通及步行为主，无必去地点。"],
        }
        save(args.output_dir / "request.json", request)
        trip = server.request("POST", "/api/trips", expected=201, json=request).json()
        started = time.monotonic()
        submitted = server.request(
            "POST",
            f"/api/trips/{trip['id']}/jobs",
            expected=202,
            json={"expected_revision": 1, "idempotency_key": "live-plan"},
        ).json()
        job = wait_job(server, submitted["id"], args.timeout)
        if job["status"] == "awaiting_input":
            save(args.output_dir / "clarification-job.json", job)
            report["clarification_questions"] = job["questions"]
            request["known_conditions"].extend(
                [
                    "仅安排西安中心城区，不去临潼的兵马俑、华清宫；以室内文化馆为主。",
                    "接受短时户外步行，单次不超过60分钟；愿意提前预约，当前未预约，需保留预约提示。",
                    "没有其他必去地点或特殊要求，普通偏好按上述假设继续。",
                ]
            )
            trip = server.request(
                "PATCH",
                f"/api/trips/{trip['id']}",
                json={
                    "expected_revision": trip["revision"],
                    "known_conditions": request["known_conditions"],
                },
            ).json()
            save(args.output_dir / "request-clarified.json", request)
            server.request(
                "POST",
                f"/api/jobs/{job['id']}/resume",
                expected=202,
                json={"expected_revision": trip["revision"]},
            )
            job = wait_job(server, job["id"], args.timeout)
        save(args.output_dir / "plan-job.json", job)
        report["plan_seconds"] = round(time.monotonic() - started, 3)
        require(job["status"] == "succeeded", f"真实规划未成功：{job['status']} {job['error']}")
        original = server.get(f"/api/trips/{trip['id']}/itinerary")
        save(args.output_dir / "itinerary-1.json", original)
        report["plan"] = check_itinerary(original, request)
        target = next(
            (item for item in original["days"][0]["activities"] if item["kind"] != "meal"),
            original["days"][0]["activities"][0],
        )
        started = time.monotonic()
        submitted = server.request(
            "PATCH",
            f"/api/trips/{trip['id']}/itinerary",
            expected=202,
            json={
                "expected_revision": 1,
                "expected_request_revision": trip["revision"],
                "idempotency_key": "live-edit",
                "operations": [{"op": "remove", "activity_id": target["id"]}],
            },
        ).json()
        job = wait_job(server, submitted["id"], args.timeout)
        save(args.output_dir / "edit-job.json", job)
        report["edit_seconds"] = round(time.monotonic() - started, 3)
        require(job["status"] == "succeeded", f"真实编辑未成功：{job['status']} {job['error']}")
        edited = server.get(f"/api/trips/{trip['id']}/itinerary")
        save(args.output_dir / "itinerary-2.json", edited)
        report["edit"] = check_itinerary(edited, request)
        require(edited["revision"] == 2, "编辑后草案未升版")
        require(edited["days"][1:] == original["days"][1:], "编辑改动了其他日期")
        require(
            [item for item in edited["evidence"] if item["on_date"] != request["start_date"]]
            == [item for item in original["evidence"] if item["on_date"] != request["start_date"]],
            "编辑改动了其他日期的事实",
        )
        ids = {item["id"] for day in original["days"][1:] for item in day["activities"]}
        require(
            [item for item in edited["transport"] if item["from_activity_id"] in ids]
            == [item for item in original["transport"] if item["from_activity_id"] in ids],
            "编辑改动了其他日期的路线",
        )
        require(
            server.get(f"/api/trips/{trip['id']}/itinerary?revision=1") == original, "历史草案变化"
        )
        require(server.get("/api/preferences") == preferences, "编辑意外修改偏好")
        server.stop()
        server.start()
        require(server.get(f"/api/trips/{trip['id']}/itinerary") == edited, "重启后草案变化")
        report["restart_verified"] = True
        server.stop()
        report["verify"] = server.maintenance("verify")
        archive = args.output_dir / "backup.zip"
        report["backup"] = server.maintenance("backup", "--output", archive)
        target_dir = args.output_dir / "restored"
        report["restore"] = server.maintenance("restore", "--archive", archive, data=target_dir)
        server.start(target_dir)
        require(server.get(f"/api/trips/{trip['id']}/itinerary") == edited, "恢复后草案变化")
        require(
            server.get(f"/api/trips/{trip['id']}/itinerary?revision=1") == original,
            "恢复后历史变化",
        )
        require(server.get("/api/preferences") == preferences, "恢复后偏好变化")
        require(server.get("/api/knowledge/search", params={"q": "碑林"}), "恢复后索引不可读")
        report["restored_http_verified"] = True
    finally:
        server.close()
        report["sampled_peak_resources"] = server.peaks


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="新的验收输出目录")
    parser.add_argument(
        "--start-date", type=date.fromisoformat, default=date.today() + timedelta(days=14)
    )
    parser.add_argument("--timeout", type=int, default=1800, help="每个任务最长等待秒数")
    parser.add_argument("--direct", action="store_true", help="验收进程直连外部服务，保留 TLS 校验")
    parser.add_argument(
        "--provider-timeout",
        type=int,
        choices=range(1, 301),
        metavar="1..300",
        help="仅为验收进程设置外部服务调用超时秒数",
    )
    raise SystemExit(run_acceptance(parser.parse_args(), scenario))
