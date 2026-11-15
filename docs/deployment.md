# 后端安装、升级与数据恢复

当前镜像提供后端 API 和 `/api/docs` 交互式接口文档，使用 Python 3.13、单个 Uvicorn 进程和一个持久化数据卷。镜像通过 uv 锁定依赖，以 UID／GID `10001:10001` 运行。安装与维护在项目根目录执行。

## Windows x64 安装

准备 Docker Desktop 的 Linux 容器环境，Docker Compose 需为 2.24 或更高版本。在 PowerShell 检查：

```powershell
docker version
docker compose version
Copy-Item .env.example .env
notepad .env
```

配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 与 `AMAP_API_KEY`，按需配置搜索凭据。缺 Key 时仍可保存行程要求、偏好和资料；`GET /api/config/status` 列出配置缺口。真实规划前可在 `/api/docs` 显式调用 `POST /api/config/check` 检查对应服务。

```powershell
docker compose build --pull app
docker compose up -d --no-build --wait app
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

浏览器打开 `http://127.0.0.1:8000/api/docs`。健康检查成功时返回 `status: ok` 和 `schema_version: 4`。健康检查读取本地数据库，外部模型与地图的连通性由显式检查接口判断。

Linux 使用相同的 Docker 命令，首次复制配置用 `cp .env.example .env`，HTTP 检查可用 `curl http://127.0.0.1:8000/api/health`。

## 配置与持久化

| 项目 | 默认值与行为 |
| --- | --- |
| 镜像 | 本地构建为 `travel-planner-backend:0.1.0`，平台为 `linux/amd64`。`APP_IMAGE` 可指定其他本地版本标签。 |
| 访问端口 | 仅发布 `127.0.0.1:8000`。通过 `.env` 的 `TRAVEL_PORT` 修改宿主机端口。 |
| 数据卷 | 命名卷 `travel-planner-data`，通过 `.env` 的 `TRAVEL_DATA_VOLUME` 修改。 |
| 容器数据目录 | 固定为 `/data`，覆盖 `.env` 中供本地 uv 使用的 `DATA_DIR`。 |
| 配置 | `.env` 在启动时注入，构建上下文仅包含 Python 源码、依赖声明和锁定文件。 |
| 停止 | `docker compose stop app` 等待进程退出，宽限期为 45 秒。 |
| 替换容器 | `docker compose up -d --no-build --wait app` 继续使用同一命名卷。 |

修改 `.env` 后执行 `docker compose up -d --no-build --force-recreate --wait app` 重新注入配置。`docker compose restart` 使用原容器配置。

同一数据卷只运行一个应用或维护进程。部署第二个独立实例时，分别设置 `TRAVEL_DATA_VOLUME`、`TRAVEL_PORT` 与 `COMPOSE_PROJECT_NAME`，避免共享个人数据和容器名称。

日常停止用 `docker compose stop app`；移除容器可用 `docker compose down`。`docker compose down -v` 会删除数据卷。

## 停服与备份

先通过 `GET /api/runtime` 查看任务、导入与文件清理计数。`idle: true` 表示没有排队／执行中的规划、待处理／处理中的导入或待清理文件；等待用户补充的任务可以保留。确认空闲后停止应用，再执行离线维护，维护命令会再次检查进程锁及数据状态。

PowerShell：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/runtime
docker compose stop app
New-Item -ItemType Directory -Force backups | Out-Null
docker compose run --rm --no-deps --user 0:0 --entrypoint travel-data app --data-dir /data verify
docker compose run --rm --no-deps --user 0:0 --entrypoint travel-data --volume "${PWD}/backups:/backup" app --data-dir /data backup --output /backup/before-upgrade.zip
Get-FileHash backups/before-upgrade.zip -Algorithm SHA256
docker compose up -d --no-build --wait app
```

Linux 先用 `mkdir -p backups` 创建目录，再执行相同的维护命令；摘要可用 `sha256sum backups/before-upgrade.zip`。绑定宿主目录的维护命令使用 root 读取和写入备份；Linux 导出后可用 `sudo chown "$(id -u):$(id -g)" backups/before-upgrade.zip` 将文件所有权交给当前用户。

命令成功输出一行 `{"ok":true,"result":...}`，退出码为 0。`result` 包含数据库版本、记录数、原资料数、未引用原资料数、空闲状态，以及备份文件的 SHA-256。备份文件名必须未被使用，每次备份使用新名称。

ZIP 保存 SQLite 单文件快照、全部原资料及含文件大小与 SHA-256 的清单。快照合并 WAL 中已提交的内容，保留偏好、请求版本、任务事件、草案历史、服务检查记录和全文索引。`.env` 需单独保管；备份路径放在数据目录之外。格式上限为展开后 8 GiB、10,000 个数据文件。

校验会检查 SQLite 与外键完整性、FTS 索引和片段对应关系、资料摘要、任务及草案的版本引用。备份使用系统临时目录，恢复在目标数据卷暂存；需要为展开数据与数据库校验副本预留空间。

## 恢复到新数据卷

停止应用，将 `.env` 中的 `TRAVEL_DATA_VOLUME` 改为一个新的卷名，例如 `travel-planner-restored-20261115`。保留旧卷，恢复命令只接受空目录或空卷。

```powershell
docker compose stop app
notepad .env
docker compose run --rm --no-deps --user 0:0 --entrypoint travel-data --volume "${PWD}/backups:/backup:ro" app --data-dir /data restore --archive /backup/before-upgrade.zip
docker compose run --rm --no-deps --user 0:0 --entrypoint chown app -R 10001:10001 /data
docker compose run --rm --no-deps --user 0:0 --entrypoint travel-data app --data-dir /data verify
docker compose up -d --no-build --wait app
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

恢复命令成功后，卷内文件所有权交回应用 UID／GID，再启动应用。通过 `/api/docs` 查看偏好、行程要求、当前及历史草案、任务和资料，执行中文短词检索；核对恢复报告与备份时的记录数。恢复支持当前应用认识的旧数据库版本，会先在临时数据中迁移，再发布到目标卷，报告包含 `restored_from_schema`。

损坏的 ZIP、文件摘要不一致、目录越界、符号链接、重复成员、清单不一致与超限数据会被拒绝。发布阶段失败会回滚已写入的文件。进程中断留下 `.restore-in-progress` 时，应用拒绝启动；保留该卷用于排查，再向另一个空卷恢复。

## 升级与回滚

1. 等待 `idle: true`，停止应用，用旧镜像执行校验和备份，记录旧的 `APP_IMAGE` 标签、备份摘要与记录数。
2. 更新源码，在 `.env` 将 `APP_IMAGE` 设置为新的版本标签，执行 `docker compose build --pull app`。
3. 执行 `docker compose up -d --no-build --wait app`。启动在事务中迁移数据库；检查健康接口、已有草案与资料检索。
4. 需要回滚时停止新版本，将 `APP_IMAGE` 改回保留的旧镜像标签，将 `TRAVEL_DATA_VOLUME` 改为新空卷名，按上节用旧镜像恢复升级前的备份，然后启动。

旧镜像会拒绝读取高于其支持版本的数据库；回滚使用升级前备份。首次升级前为当前镜像保留独立标签，例如 `docker image tag travel-planner-backend:0.1.0 travel-planner-backend:pre-upgrade-20261115`，并记录该标签。

## 使用 uv 维护本地安装

停止本地 Uvicorn 进程后，在项目根目录执行：

```sh
uv run travel-data --data-dir ./data verify
uv run travel-data --data-dir ./data backup --output ./backups/local-20261115.zip
uv run travel-data --data-dir ./restored-data restore --archive ./backups/local-20261115.zip
uv run travel-data --data-dir ./restored-data verify
```

确认恢复报告后，将 `.env` 的 `DATA_DIR` 指向 `./restored-data` 并启动后端。省略 `--data-dir` 时读取 `DATA_DIR`，默认 `./data`。

## 维护错误

| 错误代码 | 处理方式 |
| --- | --- |
| `data_in_use` | 停止应用或其他维护进程，等待退出后重试。 |
| `data_not_idle` | 重启应用，等待任务或导入结束；中断任务保留供用户重试，处理完文件清理后再停服。 |
| `archive_exists` | 使用新的备份文件名。 |
| `target_not_empty` | 为恢复选择新目录或新数据卷。 |
| `restore_incomplete` | 保留中断现场，向另一个空目录或空卷恢复。 |
| `source_file_missing`、`source_hash_mismatch`、`index_inconsistent`、`broken_references` | 检查报告及完整备份，核对原资料、索引与版本记录。 |
| `invalid_archive`、`archive_hash_mismatch`、`archive_limit` | 核对备份摘要、格式和展开大小，重新取得完整备份。 |
| `unsupported_schema` | 使用支持该数据库版本的应用镜像。 |
| `data_io_error` | 检查数据卷、临时目录与备份目录的权限及可用空间。 |

失败退出码为 1，标准错误输出 `{"ok":false,"error":{"code":"...","message":"..."}}`；参数用法错误由命令行解析器返回退出码 2。

## 验证记录（2026-11-15）

Linux、Python 3.13 下，275 项自动测试与 Ruff 检查通过。维护测试覆盖完整规划和编辑历史恢复、等待补充任务保留、恢复后继续规划与编辑、WAL 快照、旧版本迁移、损坏与越界备份拒绝、独占锁、失败回滚和中断启动保护。

已使用锁定依赖完成非开发、非 editable 安装，删除构建源码后启动实际 Uvicorn 服务，通过 HTTP 保存行程要求和偏好、后台导入及中文检索，并用已安装的 `travel-data` 完成备份恢复，再启动服务核对数据。`uv build` 成功生成 wheel 和 sdist；Compose 配置解析确认回环端口、独立卷与 `/data` 设置。

当前账户连接 `/var/run/docker.sock` 被拒绝，镜像构建、容器运行及 Windows Docker Desktop 操作尚待执行。该记录覆盖本地安装与配置验证。
