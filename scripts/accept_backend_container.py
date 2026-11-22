"""验收已构建后端镜像的 Compose 启动、容器替换和空卷恢复。"""

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
from acceptance_support import PROJECT, poll, require, run_acceptance, save


class Containers:
    def __init__(self, args):
        self.args = args
        self.project = "travel-accept-" + uuid4().hex[:12]
        self.volumes = [self.project + "-original", self.project + "-restored"]
        self.prefix = (["sudo", "-n"] if args.sudo else []) + ["docker"]
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.environment_file = args.output_dir / "compose.env"
        self.override = args.output_dir / "compose.override.json"
        save(
            self.override,
            {
                "services": {
                    "app": {
                        "environment": {
                            "LLM_BASE_URL": "",
                            "LLM_API_KEY": "",
                            "LLM_MODEL": "",
                            "AMAP_API_KEY": "",
                            "EXA_API_KEY": "",
                            "DEEPSEEK_API_KEY": "",
                            "TAVILY_API_KEY": "",
                            "ANYSEARCH_API_KEY": "",
                            "SEARCH_PROVIDERS": "exa,deepseek,tavily,anysearch",
                            "SEARCH_ALLOW_ANONYMOUS": "false",
                        }
                    }
                }
            },
        )
        self.configure(self.volumes[0])

    def configure(self, volume):
        self.environment_file.write_text(
            f"APP_IMAGE={self.args.image}\nTRAVEL_PORT={self.port}\nTRAVEL_DATA_VOLUME={volume}\n",
            encoding="utf-8",
        )

    def command(self, *arguments, check=True):
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"APP_IMAGE", "TRAVEL_PORT", "TRAVEL_DATA_VOLUME"}
        }
        result = subprocess.run(
            [*self.prefix, *arguments],
            cwd=PROJECT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        with (self.args.output_dir / "docker.log").open("a", encoding="utf-8") as log:
            log.write(json.dumps(list(arguments), ensure_ascii=False) + "\n")
            log.write(result.stdout + result.stderr + "\n")
        if check:
            require(result.returncode == 0, f"Docker 命令失败：{result.stderr[-1000:]}")
        return result.stdout.strip()

    def compose(self, *arguments, check=True):
        return self.command(
            "compose",
            "--project-name",
            self.project,
            "--env-file",
            str(self.environment_file),
            "-f",
            str(PROJECT / "compose.yaml"),
            "-f",
            str(self.override),
            *arguments,
            check=check,
        )

    def start(self, *, recreate=False):
        arguments = ["up", "-d", "--no-build", "--wait", "--wait-timeout", "60"]
        if recreate:
            arguments.append("--force-recreate")
        self.compose(*arguments, "app")
        return self.compose("ps", "-q", "app")

    def maintenance(self, command, *extra):
        mounted = str(self.args.output_dir) + ":/backup"
        output = self.compose(
            "run",
            "--rm",
            "--no-deps",
            "--user",
            "0:0",
            "--entrypoint",
            "travel-data",
            "--volume",
            mounted,
            "app",
            "--data-dir",
            "/data",
            command,
            *extra,
        )
        result = json.loads(output)
        require(result["ok"], "容器维护命令失败")
        return result["result"]

    def close(self):
        self.compose("down", "--volumes", "--remove-orphans", check=False)
        for name in self.volumes:
            self.command("volume", "rm", name, check=False)


def scenario(args, report):
    containers = Containers(args)
    try:
        original_container = containers.start()
        with httpx.Client(
            base_url=f"http://127.0.0.1:{containers.port}", timeout=10, trust_env=False
        ) as client:

            def get(path, **kwargs):
                response = client.get(path, **kwargs)
                require(response.status_code == 200, f"容器 HTTP 读取失败：{path}")
                return response.json()

            def post(path, payload, expected):
                response = client.post(path, json=payload)
                require(response.status_code == expected, f"容器 HTTP 写入失败：{path}")
                return response.json()

            report["health"] = get("/api/health")
            require(
                report["health"] == {"status": "ok", "schema_version": 6},
                "容器健康或数据库版本错误",
            )
            homepage = client.get("/")
            require(
                homepage.status_code == 200
                and "text/html" in homepage.headers.get("content-type", "")
                and 'id="root"' in homepage.text,
                "容器首页未提供网页",
            )
            assets = re.findall(r'(?:src|href)="(/assets/[^" ]+)"', homepage.text)
            require(bool(assets), "网页缺少构建资源")
            report["web_asset_sha256"] = {}
            for asset in assets:
                response = client.get(asset)
                expected = (PROJECT / "web/dist" / asset.lstrip("/")).read_bytes()
                require(
                    response.status_code == 200 and response.content == expected,
                    "容器静态资源与本地构建不一致",
                )
                report["web_asset_sha256"][asset] = hashlib.sha256(expected).hexdigest()
            report["web_assets_verified"] = len(assets)
            config = get("/api/config/status")
            require(not config["ready_for_planning"], "缺 Key 状态未保留")
            report["search_modes"] = [(p["provider"], p["auth_mode"]) for p in config["search"]]
            require(
                report["search_modes"]
                == [(name, "disabled") for name in ("exa", "deepseek", "tavily", "anysearch")],
                "缺配置实例的搜索模式不符",
            )
            ports = json.loads(
                containers.command(
                    "inspect", "--format", "{{json .HostConfig.PortBindings}}", original_container
                )
            )
            require(ports["8000/tcp"][0]["HostIp"] == "127.0.0.1", "宿主端口未绑定回环")
            environment = json.loads(
                containers.compose(
                    "exec",
                    "-T",
                    "app",
                    "python",
                    "-c",
                    "import importlib.util,json,os; from pathlib import Path; "
                    "print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),"
                    "'env_file':Path('/app/.env').exists(),'source_tree':Path('/app/backend').exists(),"
                    "'pytest':importlib.util.find_spec('pytest') is not None,"
                    "'data_dir':os.environ['DATA_DIR']}))",
                )
            )
            require(
                environment
                == {
                    "uid": 10001,
                    "gid": 10001,
                    "env_file": False,
                    "source_tree": False,
                    "pytest": False,
                    "data_dir": "/data",
                },
                "镜像隔离或用户错误",
            )
            report["image_environment"] = environment
            trip = post("/api/trips", {"destination": "西安"}, 201)
            response = client.put(
                "/api/preferences", json={"expected_revision": 1, "preferences": {"节奏": "慢"}}
            )
            require(response.status_code == 200, "容器偏好写入失败")
            preferences = response.json()
            text = "西安碑林博物馆：容器替换与恢复验收资料。"
            source = post("/api/knowledge", {"title": "容器验收", "text": text}, 202)
            ready = poll(
                lambda: get("/api/knowledge/" + source["id"]),
                lambda item: item["status"] in {"ready", "failed"},
                timeout=30,
            )
            require(ready["status"] == "ready", "容器后台导入失败")
            hits = get("/api/knowledge/search", params={"q": "碑林"})
            require(hits, "容器中文检索失败")
            replacement = containers.start(recreate=True)
            require(replacement != original_container, "容器未被替换")
            require(get("/api/trips/" + trip["id"]) == trip, "替换容器后行程丢失")
            require(get("/api/preferences") == preferences, "替换容器后偏好丢失")
            require(
                get("/api/knowledge/search", params={"q": "碑林"}) == hits, "替换容器后索引变化"
            )
            report["container_replaced_data_preserved"] = True
            require(get("/api/runtime")["idle"], "停服前服务未空闲")
            containers.compose("stop", "app")
            report["verify"] = containers.maintenance("verify")
            report["backup"] = containers.maintenance("backup", "--output", "/backup/snapshot.zip")
            if os.name != "nt":
                containers.compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "--user",
                    "0:0",
                    "--entrypoint",
                    "chown",
                    "--volume",
                    str(args.output_dir) + ":/backup",
                    "app",
                    f"{os.getuid()}:{os.getgid()}",
                    "/backup/snapshot.zip",
                )
            containers.configure(containers.volumes[1])
            report["restore"] = containers.maintenance(
                "restore", "--archive", "/backup/snapshot.zip"
            )
            require(containers.maintenance("verify") == report["verify"], "恢复前后校验结果不同")
            containers.compose(
                "run",
                "--rm",
                "--no-deps",
                "--user",
                "0:0",
                "--entrypoint",
                "chown",
                "app",
                "-R",
                "10001:10001",
                "/data",
            )
            containers.start()
            require(get("/api/trips/" + trip["id"]) == trip, "空卷恢复后行程变化")
            require(get("/api/preferences") == preferences, "空卷恢复后偏好变化")
            require(
                get("/api/knowledge/search", params={"q": "碑林"}) == hits, "空卷恢复后索引变化"
            )
            original = client.get(f"/api/knowledge/{source['id']}/original")
            require(
                original.status_code == 200 and original.content == text.encode(), "恢复原文不一致"
            )
            report["empty_volume_restore_verified"] = True
            report["loopback_port_verified"] = True
            report["image"] = args.image
            report["checked_at_unix"] = time.time()
    finally:
        containers.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="新的验收输出目录")
    parser.add_argument("--image", default="travel-planner-backend:0.1.0")
    parser.add_argument("--sudo", action="store_true", help="以 sudo -n 调用 Docker")
    raise SystemExit(run_acceptance(parser.parse_args(), scenario))
