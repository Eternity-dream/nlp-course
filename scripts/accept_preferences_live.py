"""用两个三日西安行程对照长期偏好、当次要求及偏好版本控制。"""

import argparse
import re
import time
from datetime import date, timedelta
from pathlib import Path

from accept_agent_live import live_server
from accept_backend_live import wait_job
from acceptance_support import require, run_acceptance, save

NOTES = "每天不早于09:30开始游览；偏爱博物馆与古建，不逛夜景和商业街；单段步行尽量不超过15分钟。"
OVERRIDE = "本次希望节奏紧凑一些，必须安排大唐不夜城夜游。"


def case(server, args, label):
    end = args.start_date + timedelta(days=2)
    request = {
        "destination": "西安",
        "start_date": args.start_date.isoformat(),
        "end_date": end.isoformat(),
        "travelers": 2,
        "budget_cents": 240000,
        "known_conditions": [
            "首日10:00抵达西安北站，末日18:00从西安北站离开。住宿自行安排在钟楼附近。"
        ],
    }
    if label == "B":
        request["known_conditions"].append(OVERRIDE)
    save(args.output_dir / f"{label}-request.json", request)
    trip = server.request("POST", "/api/trips", expected=201, json=request).json()
    path = "/api/trips/" + trip["id"]
    started = time.monotonic()
    job = server.request(
        "POST",
        path + "/jobs",
        expected=202,
        json={"expected_revision": trip["revision"], "idempotency_key": "preference-" + label},
    ).json()
    done = wait_job(server, job["id"], args.timeout)
    save(args.output_dir / f"{label}-job.json", done)
    stats = {"job_status": done["status"], "seconds": round(time.monotonic() - started, 2)}
    if done["status"] != "succeeded":
        stats["error"], stats["questions"] = done["error"], done["questions"]
        return stats
    result = server.get(path + "/itinerary")
    save(args.output_dir / f"{label}-itinerary.json", result)
    places = {p["id"]: p for p in result["places"]}
    rows = [
        {
            "date": d["on_date"],
            "name": places[a["place_id"]]["name"],
            "category": places[a["place_id"]]["category"],
            "description": a["description"],
            "kind": a["kind"],
            "start": a["start_minute"],
            "end": a["end_minute"],
        }
        for d in result["days"]
        for a in d["activities"]
    ]
    middle = [r for r in rows if r["date"] == result["days"][1]["on_date"]]
    walking = [
        leg["route"]["duration_seconds"]
        for leg in result["transport"]
        if leg["route"]["mode"] == "walking"
    ]
    stats.update(
        intent=result["planning_context"]["intent"],
        activities=rows,
        middle_first=middle[0],
        middle_first_visit=next((r for r in middle if r["kind"] != "meal"), None),
        night_visits=[
            r
            for r in rows
            if r["kind"] != "meal" and "大唐不夜城" in r["name"] and r["start"] >= 1020
        ],
        matching_rationales=[
            r
            for r in rows
            if re.search(r"(?:长期|个人|您的|你的)偏好|根据.{0,8}(?:习惯|偏好)", r["description"])
        ],
        empty_descriptions=sum(not r["description"].strip() for r in rows),
        walking_seconds=walking,
        validation=result["validation"]["status"],
        steps=result["planning_context"]["steps"],
        tool_calls=result["planning_context"]["tool_calls"],
    )
    return stats


def scenario(args, report):
    server = live_server(args.output_dir)
    try:
        server.start()
        initial = server.get("/api/preferences")
        prefs = server.request(
            "PUT",
            "/api/preferences",
            json={"expected_revision": initial["revision"], "notes": NOTES},
        ).json()
        require(prefs["notes"] == NOTES, "偏好保存后原文变化")
        report["saved"] = prefs
        for label in ("A", "B"):
            report[label] = case(server, args, label)
            report[label]["preferences_unchanged"] = server.get("/api/preferences") == prefs
            save(args.output_dir / f"{label}-observations.json", report[label])
            require(report[label]["preferences_unchanged"], "规划擅自修改长期偏好")
        notes = "今后希望早上十点左右开始，参观博物馆时留出较长休息时间。"
        old = server.request(
            "PUT",
            "/api/preferences",
            expected=409,
            json={"expected_revision": initial["revision"], "notes": notes},
        )
        require(server.get("/api/preferences") == prefs, "冲突请求改变了偏好")
        updated = server.request(
            "PUT", "/api/preferences", json={"expected_revision": prefs["revision"], "notes": notes}
        ).json()
        require(
            updated["revision"] == prefs["revision"] + 1 and updated["notes"] == notes,
            "主动更新未生效",
        )
        server.stop()
        report["verify"] = server.maintenance("verify")
        server.start()
        require(server.get("/api/preferences") == updated, "重启后偏好变化")
        report["C"] = {
            "conflict_http": old.status_code,
            "error": old.json()["error"]["code"],
            "updated": updated,
            "persisted": True,
        }
        require(
            all(report[label]["job_status"] == "succeeded" for label in ("A", "B")),
            "有对照行程未生成",
        )
        require(
            report["B"]["night_visits"] and not report["B"]["matching_rationales"],
            "夜游或介绍对照未达到预期",
        )
        require(
            report["A"]["middle_first_visit"] and report["A"]["middle_first_visit"]["start"] >= 570,
            "完整日首项游览早于09:30",
        )
    finally:
        server.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 12, 8))
    parser.add_argument("--timeout", type=int, default=3600)
    raise SystemExit(run_acceptance(parser.parse_args(), scenario))
