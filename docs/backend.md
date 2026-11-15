# 后端开发与验证

当前已实现 FastAPI 服务、SQLite 迁移、行程要求与版本控制、偏好与资料管理、外部服务适配、后台规划、行程编辑和核验，以及运行状态与离线数据维护。代码位于 `backend/src/travel_planner/`，依赖由根目录 `pyproject.toml` 与 `uv.lock` 管理。

固定响应已验证西安三天、杭州七天的后端完整规划与局部编辑。已交付后端容器配置、停服校验与备份恢复工具，并验证非开发模式安装。容器运行、Windows 安装与真实账号端到端继续按[实施设计](implementation.md#工作包与完成条件)验收，操作命令见[部署说明](deployment.md)。

## 使用 uv 启动

在仓库根目录执行：

```sh
uv sync --locked
uv run uvicorn travel_planner.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

uv 使用 `.python-version` 指定的 Python 3.13。浏览器访问 `http://127.0.0.1:8000/api/docs` 操作接口，OpenAPI 位于 `/api/openapi.json`。

配置可从 `.env.example` 复制为 `.env`，修改后重启。当前资料管理与行程要求保存可在无外部 Key 时使用；`GET /api/config/status` 返回模型、地图与搜索的配置缺口和鉴权模式，检查过程不发送外部请求。`ready_for_planning` 表示配置完整性，实际连通性通过显式检查接口验证。

默认数据目录为 `./data`，通过 `DATA_DIR` 修改；仅启动一个服务进程，同一目录的第二个进程会拒绝启动。`WEB_DIST_DIR` 默认为 `web/dist`，存在前端构建产物时提供静态文件与页面路由回退。

## 当前接口

| 接口 | 行为 |
| --- | --- |
| `GET /api/health` | 检查数据库可读，返回数据库版本。 |
| `GET /api/runtime` | 返回规划任务、资料导入及文件清理的状态计数，以及供停服维护判断的 `idle`。 |
| `GET /api/config/status` | 检查配置与鉴权模式，返回结果不包含密钥。 |
| `POST /api/config/check` | 显式调用指定的模型、搜索或高德服务，返回并保存脱敏检查记录。 |
| `GET /api/config/checks` | 查看最近 20 条连通性检查记录。 |
| `POST /api/trips` | 保存行程要求，返回 `201`。缺少日期或人数时通过 `missing_fields` 提示补充。 |
| `GET /api/trips`、`GET /api/trips/{id}` | 查看已保存要求及当前版本。 |
| `PATCH /api/trips/{id}` | 通过 `expected_revision` 修改要求；生成新版本并保留历史记录，过期版本返回 `409`。 |
| `POST /api/trips/{id}/jobs` | 携带要求版本与去重标识提交规划，持久化后返回 `202` 和任务对象。 |
| `GET /api/trips/{id}/jobs`、`GET /api/jobs/{id}` | 查看最近 50 个任务及单个任务的阶段、事件、待补充问题、失败或结果版本。 |
| `POST /api/jobs/{id}/resume` | 补充并保存新要求版本后继续等待输入的任务。 |
| `POST /api/jobs/{id}/retry` | 失败或中断任务创建新任务，关联 `retry_of`。 |
| `GET /api/trips/{id}/itinerary` | 查看当前草案；`revision` 参数可读取已保存的历史版本。 |
| `PATCH /api/trips/{id}/itinerary` | 提交活动编辑，返回 `202` 和重新核验任务；成功后另存草案版本。 |
| `GET /api/trips/{id}/places?q=展馆名称` | 在本次城市查询具体地点，返回地点 ID、地址与坐标供编辑选择。 |
| `GET /api/preferences`、`PUT /api/preferences` | 查看、主动保存或清空偏好；保存携带 `expected_revision`。 |
| `POST /api/knowledge` | JSON 导入文本、URL 或收藏，持久化后返回 `202`，后台串行处理。 |
| `POST /api/knowledge/files` | multipart 导入文件，字段为 `title` 和 `file`。支持 PDF、UTF-8 TXT 与 Markdown。 |
| `GET /api/knowledge`、`GET /api/knowledge/{id}` | 查看导入状态、错误与已完成的定位片段。 |
| `GET /api/knowledge/{id}/original` | 下载原始文件；网页以附件下载。 |
| `GET /api/knowledge/search?q=碑林` | 中文关键词检索，最多返回 8 个去重片段及来源、页码或段落、字符偏移。 |
| `DELETE /api/knowledge/{id}` | 删除资料、片段与全文索引，成功返回 `204`；文件清理失败会持久化记录并重试。 |

行程和资料列表接受 `limit`（默认 50，最大 100）与 `offset`（默认 0）。时间戳带 UTC 时区，行程时区固定为 `Asia/Shanghai`；日期格式为 `YYYY-MM-DD`，起止日期均计入 3–7 天。`budget_cents` 为全体同行者、全程门票餐饮与当地交通的人民币整数分，允许留空。

创建行程要求：

```json
{
  "destination": "西安",
  "start_date": "2026-12-01",
  "end_date": "2026-12-03",
  "travelers": 2,
  "budget_cents": 300000,
  "requirements": ["素食"],
  "priorities": ["慢节奏", "博物馆"]
}
```

可选 `time_constraints` 表达已知抵离或预约时间，日期按 `Asia/Shanghai` 解释，分钟从当天零点起算：

```json
{
  "kind": "reservation",
  "on_date": "2026-12-01",
  "minute": 600,
  "end_minute": 660,
  "place_name": "具体场馆名称"
}
```

将上述对象放入行程要求的 `time_constraints` 数组。`kind` 可选 `arrival`、`departure`、`reservation`；预约需填写地点，`end_minute` 可选，跨午夜结束可大于 1440。日期必须在行程范围内。文字形式的要求和已知条件也会交给模型，但程序仅对结构化时间条件执行确定性排程检查，其余文字条件保留具体待核对项或模型指出的冲突。

## 提交与读取规划

行程要求保存后，向 `POST /api/trips/{id}/jobs` 提交：

```json
{"expected_revision": 1, "idempotency_key": "plan-20261201-001"}
```

返回对象包含 `id`、`status`、`stage`、`request_revision`、`result_revision` 和事件时间。相同行程中重发同一标识与原始要求版本返回同一任务；将标识用于其他版本返回 `409 idempotency_conflict`。完整条件缺少必要服务配置时返回 `503 config_incomplete`，补齐 `.env` 并重启后可提交。

轮询 `GET /api/jobs/{id}`。一次运行一个任务，其余按提交顺序排队；关闭客户端后任务继续。状态为 `queued → running → succeeded / failed / awaiting_input`。缺少日期、人数或模型提出关键问题时保存 `questions` 并释放执行器；用户先 `PATCH /api/trips/{id}` 保存补充，再向 `POST /api/jobs/{id}/resume` 发送 `{"expected_revision": 2}` 继续同一任务。

重启将遗留的排队和运行中任务设为 `interrupted`，等待输入和已完成结果保留。向 `POST /api/jobs/{id}/retry` 发送当前要求版本与新的 `idempotency_key` 创建重试任务；新任务使用该版本的要求，重新检索公开事实。成功任务不接受重试。任务错误只保存可读代码与脱敏原因。

成功后通过 `GET /api/trips/{id}/itinerary` 读取草案。任务成功表示结果与核验记录已保存，草案另有 `validation.status`：

| 状态 | 含义 |
| --- | --- |
| `passed` | 本次自动检查的条件均通过。 |
| `needs_review` | 存在未知、估算费用、来源冲突或尚待核对的文字条件。 |
| `blocked` | 存在营业、交通、明确时间或预算等已知冲突。 |

草案返回每天活动、地图地点、相邻交通、公开来源、事实依据、假设和具体问题。内部活动时间以分钟表示，跨午夜结束加 1440；活动 ID 由应用生成并随保存版本稳定。预算结果分别返回 `known_cost_cents`、`estimated_cost_cents` 和 `unknown_cost_count`，按全体同行者汇总每人、整组或固定费用。地图公交费按人、出租车估算按组，缺失费用保留未知；未填写预算时跳过预算上限检查。

工作流阶段为条件检查、个人资料检索、候选地点查询、网络取证、生成、路线查询、核验和保存。每个草案最多追加两轮冲突修复；达到上限后保存 `blocked` 草案。模型输出的不存在引用和无效格式走最多两轮格式修复，仍失败则任务失败。失败不会覆盖已有版本；保存时检查要求版本，条件已变更则返回 `request_changed` 并保留原草案。

资料检索最多取 8 个片段并裁剪至 6400 字符；未提及本城而被分词器识别为包含其他地名的片段会被排除，通用资料仍可命中。每次实际模型调用前重新读取可用片段，包括格式修复、协议兼容重发和冲突修复。个人资料仅进入候选和草案模型上下文；公开事实提取使用网页与地图信息，私人片段不成为公开事实依据。

公开事实提取保留原文引用、来源 URL、查询／抓取时间和适用日期。应用检查引用是否出现在正文、地点名称和地址是否在引用中，以及时间或金额能否对应；日期未明确适用时保留估算或未知。同一地点与日期的不同取值保留全部来源并标记冲突。当前自动提取处理明确日期、简单营业区间和金额；复杂节假日、长期开放规则或优惠资格保留待核对。搜索得到标题和 URL 仍是未核实来源，核验结果单独关联具体事实。

## 编辑行程

读取当前草案后，向 `PATCH /api/trips/{id}/itinerary` 提交：

```json
{
  "expected_revision": 1,
  "expected_request_revision": 1,
  "idempotency_key": "edit-001",
  "operations": [
    {"op": "remove", "activity_id": "从草案读取的活动ID"}
  ]
}
```

`expected_revision` 为草案版本，`expected_request_revision` 为该草案引用的行程要求版本。两者都需与当前数据一致；要求已修改时先重新规划。提交前检查操作有效性，过期版本返回 `409`，无效活动、日期、锚点或费用引用返回 `422`。同一去重标识配合同一完整请求返回同一任务；规划与编辑共享标识空间。

返回的任务包含 `kind: "edit"` 与 `base_revision`，与规划共用串行执行器。通过 `GET /api/jobs/{id}` 轮询，旧草案在处理期间继续可读。任务再次检查基础版本，在核验及事务保存成功后发布下一版本；其他编辑或规划已更新草案、处理中改变了行程要求，或模型／路线／保存发生错误时，原草案保持可读。

| 操作 | 字段与行为 |
| --- | --- |
| `add` | `on_date`、`activity`，可选 `before_activity_id`。新增活动 ID 由应用生成。 |
| `remove` | `activity_id`，删除指定活动；删除全天活动后保留空日期与缺失安排提示。 |
| `replace` | `activity_id`、`activity`，整体替换活动内容，保留活动 ID。可用于替换地点或修改时间、交通方式和费用。 |
| `move` | `activity_id`、`target_date`，可选 `before_activity_id`。日期相同则排序，日期不同则明确跨日移动，保留活动 ID。 |

每次最多 50 个操作，按数组顺序执行，结果每天最多 15 项活动。插入锚点必须属于目标日期；未填写或为 `null` 时放在当天末尾。活动日期由操作决定，`activity` 中不接受 ID 或日期。

新增活动示例：

```json
{
  "op": "add",
  "on_date": "2026-12-01",
  "before_activity_id": "当天另一活动ID",
  "activity": {
    "place_id": "当前草案或地点查询返回的ID",
    "kind": "leisure",
    "period": "noon",
    "start_minute": 840,
    "end_minute": 900,
    "transport_mode": "walking",
    "costs": [{"category": "ticket", "amount_cents": null, "basis": "per_person"}]
  }
}
```

`kind` 可选 `sightseeing / meal / leisure`，`period` 可选 `morning / noon / evening`，交通方式为到达该活动的 `walking / transit / driving`。费用可用 `ticket / meal`，金额为整数分；无依据 ID 的金额作为估算。已知费用依据只能引用同地点、同日期、同类别和口径的已有记录；换地点时清空旧依据和旧金额，按新地点填写估算或 `null`。

新增地点先调用 `GET /api/trips/{id}/places?q=地点名称`，按 ID 和地址选择具体场馆或门店。在 `activity` 中同时填写返回的 `place_id` 和相同的 `place_query`，后台会再次在目的地内查询并确认该 ID。当前草案已有的地点可直接使用 ID。同名地点不会按名称自动选取，编辑接口使用服务器取得的地址与坐标。

跨日移动示例：

```json
{
  "op": "move",
  "activity_id": "待移动的活动ID",
  "target_date": "2026-12-02",
  "before_activity_id": "目标日期中的活动ID"
}
```

编辑流程重新取得涉及日期的公开事实与相邻路线，再按操作形成的顺序重排。自动调整保留停留时长，从原计划开始时间起顺延，计入前项结束、交通、抵达时间与可对应的营业区间，更新早中晚分组。跨日移动同时核验两天；其他日期的活动、交通和事实依据保持原值，全程费用重新汇总。

无法在当天安排的活动保留冲突，自动重排不把它移至其他日期。删除或替换造成预约缺失、跨午夜与次日冲突、闭馆或超预算时，新草案保存为 `blocked` 并展示原因。未取得新事实时旧金额降为估算，跨日费用不沿用原日期的核实标记。文字条件针对编辑后的完整安排重新交给模型检查；程序检查明确时间与金额，未检测出文字冲突的条件继续保留待核对状态。编辑不自动修改偏好档案。

失败或中断的编辑可通过 `POST /api/jobs/{id}/retry` 重试，`expected_revision` 此处仍为行程要求版本。操作与原基础草案版本持久化保存；基础草案已更新时拒绝重试，需加载最新版本后重新提交操作。

## 偏好与资料

偏好采用键值集合，`PUT` 整体替换；传入空集合清空偏好。本次行程要求单独保存。

```json
{
  "expected_revision": 1,
  "preferences": {"节奏": "慢节奏", "饮食": "素食"}
}
```

导入文本：

```json
{
  "kind": "text",
  "title": "西安笔记",
  "text": "西安碑林博物馆可以安排半天参观。"
}
```

导入链接时使用 `{"kind":"url","title":"攻略","url":"https://example.com/guide"}`，收藏将 `kind` 改为 `bookmark`。二者都会提取公开正文。请求返回后轮询资料详情，状态依次为 `pending → processing → ready / failed`。服务重启后继续处理 `pending`，将旧 `processing` 标为导入中断；失败资料可以删除后重新导入。

资料上限为 100 份，每份 20 MiB，PDF 最多 200 页；失败资料保留记录并占用资料数量。相同输入返回 `409 duplicate_source` 和已有资料 ID。URL 导入成功后 `sha256` 对应取得的原始网页内容，其余资料对应上传内容；URL 尚未完成时对应输入 URL。

解析子进程限制为 30 秒，在 Linux 应用 512 MiB 地址空间与 25 秒 CPU 上限，提取文字最多 200 万字符。分段上限 800 字、重叠 100 字，保留页码或段落及段内偏移。正文与查询统一使用 jieba 搜索模式分词，再用 SQLite FTS5 检索；仅 `ready` 资料可检索。

网页导入逐跳检查 HTTP(S) 地址与 DNS 结果，连接固定到已校验公网 IP，最多 5 次重定向。扫描 PDF、密码保护 PDF、正文不可读或超限时保存具体错误，提示提供文字版本。整体 HTTP 请求正文上限为 21 MiB，供 20 MiB 文件及 multipart 元数据使用。

错误格式：

```json
{
  "error": {
    "code": "revision_conflict",
    "message": "行程要求已更新，请加载最新版本",
    "retryable": false,
    "object_id": "行程 ID"
  }
}
```

## 外部服务与连通性检查

在 `/api/docs` 调用 `POST /api/config/check`，例如：

```json
{"service": "search", "destination": "西安"}
```

`service` 可选 `llm`、`search`、`amap`。模型检查仅发送固定公开提示，高德检查按目的地查询博物馆；搜索检查执行已配置的回退链。每次显式检查会调用外部服务，结果通过 `success`、`details` 和 `checked_at` 表示，并存入 SQLite。供应商拒绝或不可用时也返回检查记录，具体原因保存在 `details`，不保存原始错误正文。

| 配置 | 行为 |
| --- | --- |
| `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` | 标准 Chat Completions，保留基址已有路径后追加 `/chat/completions`。 |
| `LLM_JSON_MODE` | 默认 `false`，通过提示词与 Pydantic 校验 JSON；设为 `true` 后发送通用 `response_format`，服务明确拒绝此选项时去掉并重发。 |
| `PROVIDER_TIMEOUT_SECONDS` | 默认 30 秒，限制每次外部 HTTP 请求及一次 MCP 会话的总等待时间。 |
| `SEARCH_PROVIDERS` | 默认 `exa,tavily,anysearch`，按配置顺序回退，成功后停止。 |
| `SEARCH_ALLOW_ANONYMOUS` | 默认 `true`，无 Exa Key 时走官方 MCP，无 AnySearch Key 时走匿名 REST；Tavily 需要 Key。 |
| `EXA_API_KEY`、`TAVILY_API_KEY`、`ANYSEARCH_API_KEY` | 各自独立；启动时固定模式，带 Key 失败后转下一家。 |
| `AMAP_API_KEY` | 地点搜索和步行、公交、驾车路线的服务端凭据。 |

`providers/llm.py` 接受 Pydantic 输出模型及校验上下文；空输出、截断、JSON 或业务字段校验失败时最多追加两次生成，失败返回具体错误。可选 JSON 模式的协议兼容重发独立于输出修复计数。来源 ID 是否存在等业务规则由调用方的输出模型校验。

`providers/search.py` 的 `SearchEngine.scope()` 为每个任务建立独立查询缓存和失败状态。目的地传入公开查询；同一任务复用等价查询和已取得正文。正文缺失时先复用本地公网下载与隔离提取，再按三家顺序抓取。来源保留实际提供方、稳定 ID、查询时间、正文取得时间和可用的发布时间，初始状态为 `unverified`；具体事实与适用日期由后续取证流程关联。

429 的冷却状态按提供方和鉴权模式共享到其他任务，优先使用 `Retry-After` 或重置头；没有可用重置时间时跳过当前任务剩余调用，下一个任务可再尝试。鉴权和额度失败仅在当前任务禁用该服务。400／422 参数错误停止回退；AnySearch 的 `422 extract_failed` 与单个网页正文失败仅影响该 URL。HTTPX 常规请求日志只保留方法和状态，避免高德查询参数中的 Key 出现在日志。

`providers/amap.py` 地点搜索传入 `region` 与 `city_limit=true`，保留 POI ID、地址、GCJ-02 坐标及城市编码。同名门店按 POI ID 区分；坐标或城市无法确认、路线缺失耗时的结果保留 `unknown`，有耗时的结果标为 `estimated`。营业时间和人均消费保留原始说明文字，交给后续核验处理。

数据库版本已升至 4；版本 2 新增连通性检查记录，版本 3 新增规划任务、事件及行程版本，版本 4 为任务增加类型、基础草案版本和编辑操作。启动时在事务内迁移，保留原有数据；旧任务默认为规划任务。

## 本地验证

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

2026-11-15 在 Linux、Python 3.13 环境完成 275 项测试，Ruff 代码与格式检查通过，覆盖：

- 3／7 天与错误日期、整数金额、待补充字段、条件版本保留及并发写入冲突。
- 偏好主动更新、清空、本次要求与偏好独立、无 Key 配置读取和密钥脱敏。
- 文本与 PDF 的实际子进程导入、页码与段落定位、扫描件、超页数、大小和数量限制、解析超时。
- “西安”“碑林”等中文短词检索、删除同步清理、删除与解析并发、文件清理重试。
- URL 协议、私网／混合 DNS 结果、重定向和下载大小检查，以及固定 HTML 的正文提取。
- 迁移失败回滚、未来数据库版本拒绝、进程互斥、重启恢复、安装隔离和停服目录复制恢复。
- 兼容模型基址和模型切换、JSON 模式兼容、输出有限修复、校验上下文中的来源 ID 检查。
- 三家搜索／正文字段映射、匿名与 Key 模式、成功停止、错误分类、共享限流、任务内缓存及全部失败保留已有来源。
- 通过真实 MCP SDK 验证初始化、工具发现、Schema 校验、JSON／SSE 响应与 HTTP 200 中的 JSON-RPC／工具错误。
- 高德目的地传递、同名门店消歧、三种交通模式、未知字段与估算标记、错误和日志脱敏。
- 显式连通性检查、记录持久化、配置读取不调用供应商，以及旧数据库升级后保留数据。
- 固定供应商响应下的西安三天与杭州七天完整规划、事实关联、稳定活动 ID、每人和整组预算及估算交通费。
- 任务串行排队、并发重复提交去重、补充后继续、失败与中断重试、自动修复上限、旧结果保留与事务保存失败回滚。
- 闭馆、停止入场、跨午夜营业、相邻交通、抵离与预约、费用缺失、来源冲突及无依据事实降级。
- 资料按目的地过滤、文字要求与偏好分别输入，以及在删除资料后生成与格式修复不重发已删正文。
- 活动增删替换、同日排序、指定跨日移动、稳定 ID 与新地点消歧；验证未编辑日期的活动、路线和事实依据保持原值。
- 编辑后刷新价格与全程预算、缺失依据降为估算、预约缺失及跨午夜冲突；文字条件在编辑后重新检查。
- 编辑并发去重、规划与编辑串行、基础版本冲突、核验／保存失败回滚、保存范围检查、编辑中断重试，以及版本 3 升级为 4。
- 离线维护与应用进程互斥、空闲检查、含 WAL 的数据库快照、备份摘要与原资料校验，以及无硬链接文件系统的备份写入。
- 完整规划／编辑历史、偏好、任务事件、等待补充任务和中文索引恢复后可读，并可继续规划和编辑；旧数据库先迁移再恢复。
- 备份不覆盖、空目标限制、清单／文件损坏、路径越界、重复项、符号链接和展开大小限制，以及发布失败回滚、恢复中断启动保护。

另通过实际 Uvicorn 进程的 HTTP 请求验证无 Key 启动、保存行程要求、后台导入文本与中文检索，并在终止、重新启动进程后核对行程及索引可读取。

规划与编辑进程测试使用实际 Uvicorn HTTP 服务，外部服务由固定响应提供：提交后断开客户端，重新连接可读完整结果；强制终止有运行中编辑和排队规划任务的进程后重启，确认中断状态、旧结果可读、编辑操作保留及用户重试生成新版本。此测试纳入上述 275 项。

## 真实服务验证（2026-11-15）

从当前 Linux 执行环境使用无 Key Exa 官方 MCP 完成初始化、工具发现和“西安 碑林博物馆 官方 开放时间”搜索，返回 3 个有效来源，回退链在 Exa 停止。随后使用实际发现的 `web_fetch_exa` 工具与 `urls` 参数取得 `https://beilin-museum.com/` 正文，保存工具 Schema、调用参数、时间和公开响应为 `tests/fixtures/providers/exa_mcp_live.json`，并以该录制响应回归正文解析。

模型、Exa 带 Key REST、Tavily、AnySearch、高德账号调用，以及真实三家回退仍待使用对应凭据验收；固定响应测试已覆盖其协议与错误分支。100 份真实资料容量样本和 Windows 安装继续按计划执行。对应案例的执行范围见[验收设计](acceptance.md#需求覆盖与执行状态)。

## 数据备份

先检查 `GET /api/runtime` 返回 `idle: true`，再停止服务并等待进程退出。维护工具复用实例锁，服务运行时拒绝读取或恢复数据。

```sh
uv run travel-data --data-dir ./data verify
uv run travel-data --data-dir ./data backup --output ./backups/snapshot-20261115.zip
uv run travel-data --data-dir ./restored-data restore --archive ./backups/snapshot-20261115.zip
```

`verify` 检查数据库、全文索引、原资料摘要与业务版本引用。`backup` 在空闲时生成含数据库快照、全部原资料及文件摘要清单的 ZIP；备份保存在数据目录外，已有文件不覆盖。`restore` 只接受空目录，先校验与迁移临时数据，再发布和复核；恢复中断标记会阻止应用启动。成功输出 JSON 与退出码 0，失败输出脱敏错误与退出码 1。配置 `.env` 单独保存。

非 editable 安装后已删除构建源码，实际 Uvicorn HTTP 请求验证行程要求、偏好、后台导入与中文检索；使用安装后的 CLI 备份恢复，再启动服务核对数据成功。`uv build` 成功生成 wheel 与 sdist。Compose 解析验证端口仅绑定回环地址，`.env` 的本地 `DATA_DIR` 不覆盖容器 `/data`，卷名和镜像标签可独立配置。

当前 Docker 守护进程套接字连接被拒绝，镜像构建、容器运行与 Windows 安装尚待执行。完整安装、空卷恢复、升级与回滚命令见[后端部署说明](deployment.md)。
