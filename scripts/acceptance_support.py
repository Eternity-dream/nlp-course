"""通过独立 HTTP 进程执行后端验收，保存数据与资源采样。"""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

PROJECT = Path(__file__).resolve().parents[1]


def save(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def poll(function, predicate, *, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = function()
        if predicate(value):
            return value
        time.sleep(0.1)
    raise TimeoutError(f"验收等待超时：{timeout} 秒")


class Server:
    def __init__(self, root: Path, *, configured=False, direct=False):
        self.root = root
        self.data = root / "data"
        self.environment = dict(os.environ, DATA_DIR=str(self.data), WEB_DIST_DIR=str(root / "web"))
        if direct:
            self.environment["NO_PROXY"] = "*"
            self.environment["no_proxy"] = "*"
        if not configured:
            self.environment.update(
                LLM_BASE_URL="",
                LLM_API_KEY="",
                LLM_MODEL="",
                AMAP_API_KEY="",
                EXA_API_KEY="",
                DEEPSEEK_API_KEY="",
                TAVILY_API_KEY="",
                ANYSEARCH_API_KEY="",
                SEARCH_PROVIDERS="exa,deepseek,tavily,anysearch",
                SEARCH_ALLOW_ANONYMOUS="false",
            )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{self.port}",
            timeout=httpx.Timeout(30, connect=2),
            trust_env=False,
        )
        self.process = None
        self.log = None
        self.peaks = {"service_rss_bytes": 0, "child_rss_bytes": 0, "group_rss_bytes": 0}
        self.stop_sampling = threading.Event()
        self.sampler = threading.Thread(target=self._sample, daemon=True)
        self.sampler.start()

    def start(self, data=None):
        if data is not None:
            self.data = data
        environment = {**self.environment, "DATA_DIR": str(self.data)}
        self.log = (self.root / "service.log").open("ab")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "travel_planner.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--no-access-log",
                "--log-level",
                "warning",
            ],
            cwd=PROJECT,
            env=environment,
            stdout=self.log,
            stderr=self.log,
            start_new_session=os.name != "nt",
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            require(self.process.poll() is None, "验收服务提前退出，见 service.log")
            try:
                if self.client.get("/api/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise TimeoutError("验收服务启动超时")

    def stop(self, *, kill=False):
        process = self.process
        if process is None:
            return
        try:
            if process.poll() is None:
                if kill and os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                elif kill:
                    process.kill()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=5)
                    raise
        finally:
            self.process = None
            self.log.close()

    def close(self):
        try:
            self.stop()
        finally:
            self.stop_sampling.set()
            self.sampler.join(timeout=2)
            self.client.close()

    def request(self, method, path, *, expected=200, **kwargs):
        response = self.client.request(method, path, **kwargs)
        require(
            response.status_code == expected,
            f"{method} {path}: {response.status_code}, {response.text[:1000]}",
        )
        return response

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs).json()

    def source(self, source_id, *, timeout=120):
        return poll(
            lambda: self.get("/api/knowledge/" + source_id),
            lambda item: item["status"] in {"ready", "failed"},
            timeout=timeout,
        )

    def upload(self, name, raw, title):
        return self.request(
            "POST",
            "/api/knowledge/files",
            expected=202,
            data={"title": title},
            files={"file": (name, raw)},
        ).json()

    def maintenance(self, command, *extra, data=None):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "travel_planner.maintenance",
                "--data-dir",
                str(data or self.data),
                command,
                *map(str, extra),
            ],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        require(result.returncode == 0, f"维护命令失败：{result.stderr[:1000]}")
        return json.loads(result.stdout)["result"]

    def _sample(self):
        while not self.stop_sampling.wait(0.05):
            process = self.process
            if process is None or sys.platform != "linux":
                continue
            pending, samples = [process.pid], {}
            while pending:
                pid = pending.pop()
                if pid in samples:
                    continue
                try:
                    fields = (Path("/proc") / str(pid) / "status").read_text().splitlines()
                    samples[pid] = next(
                        int(row.split()[1]) * 1024 for row in fields if row.startswith("VmRSS:")
                    )
                    for task in Path(f"/proc/{pid}/task").glob("*/children"):
                        try:
                            pending.extend(map(int, task.read_text().split()))
                        except OSError:
                            continue
                except (OSError, ValueError, StopIteration):
                    continue
            self.peaks["service_rss_bytes"] = max(
                self.peaks["service_rss_bytes"], samples.get(process.pid, 0)
            )
            self.peaks["child_rss_bytes"] = max(
                self.peaks["child_rss_bytes"],
                max((rss for pid, rss in samples.items() if pid != process.pid), default=0),
            )
            self.peaks["group_rss_bytes"] = max(
                self.peaks["group_rss_bytes"], sum(samples.values())
            )


def run_acceptance(args, scenario):
    args.output_dir = args.output_dir.absolute()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    report = {"started_at": datetime.now(UTC).isoformat(), "status": "running"}
    started = time.monotonic()
    try:
        scenario(args, report)
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["finished_at"] = datetime.now(UTC).isoformat()
    save(args.output_dir / "report.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "passed" else 1
