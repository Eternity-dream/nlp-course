# 旅行规划 Agent

面向中文自由行用户，结合个人知识库与网络搜索，为目的地和日期已确定的旅行生成可编辑的按天行程。用户可选择中国大陆的目的地城市，每次规划一个城市的 3–7 天行程，覆盖景点、餐饮和当地交通。西安用于首轮开发验证与验收。

每份安装供单人使用，在个人电脑运行并通过本机浏览器访问，数据保存在安装设备。已选 Docker Compose 单应用容器方案，首个验收平台为 Windows（Intel／AMD）；使用者通过 `.env` 配置外部模型、搜索和地图服务。

模型由用户填写 `base_url + api_key + model`，首版支持 OpenAI 兼容聊天接口；开发验证使用 DeepSeek。网页搜索独立配置，按 Exa → Tavily → AnySearch 顺序回退，Exa 支持官方无 Key MCP 入口。

后端已实现配置检查、SQLite 持久化、偏好与资料管理、模型／搜索／地图适配，以及后台规划、取证、生成和核验。行程支持增删活动、替换地点、排序和指定跨日移动，编辑后仅调整相关日期并重新汇总预算；任务支持排队、去重、重试和结果版本保存。已提供后端 Docker／Compose 配置、运行状态接口和离线校验／备份／恢复命令。100 份生成中文资料容量及 Linux 容器验收通过，DeepSeek、高德与 Exa 的真实三天规划、编辑和恢复已跑通；真实事实覆盖和 Windows 安装继续验收。项目以处理和安装维护简单为优先，生成耗时与 API 调用费用不设目标。

基础技术栈为 React + TypeScript + Vite、FastAPI、Python 显式工作流、SQLite 与本地文件存储。知识库使用本地中文关键词检索，资料做基础文字提取，按说明手动备份数据卷。

## 启动后端

```sh
uv sync --locked
uv run uvicorn travel_planner.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

访问 `http://127.0.0.1:8000/api/docs` 操作接口。默认数据保存在 `./data`，可从 `.env.example` 创建配置；无外部 Key 时可使用资料管理、偏好与行程要求接口。运行 `uv run pytest -q` 执行测试。接口示例、导入限制和备份方式见[后端开发与验证](docs/backend.md)。

使用容器时执行 `docker compose build app` 与 `docker compose up -d --no-build --wait app`。停服维护可执行 `uv run travel-data --data-dir ./data verify`，支持 `backup` 与空目录 `restore`；Windows 安装、升级和空卷恢复命令见[后端部署说明](docs/deployment.md)。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [产品需求](docs/requirements.md) | 用户流程、已确认规则与规划状态。 |
| [推进计划](docs/plan.md) | 阶段交付物、数据契约概览与实际进度。 |
| [首版实施设计](docs/implementation.md) | 模块、接口、任务状态、数据格式及 W1–W6 工作包。 |
| [后端开发与验证](docs/backend.md) | uv 启动、已实现接口、资料导入、测试和数据备份。 |
| [后端部署说明](docs/deployment.md) | Docker／Compose、本机安装、停服校验、备份恢复与升级回滚。 |
| [后端验收记录](docs/validation/2026-11-15-backend.md) | 100 份中文资料容量、边界、中断恢复、在线服务与 Linux 容器验证结果。 |
| [规划检查记录](docs/planning-review.md) | 完整性结论、需求与验收映射、实际验证缺口。 |
| [技术栈选型](docs/tech-stack.md) | 已选组件、简化方案和实现依据。 |
| [外部服务接入](docs/service-integration.md) | 模型、联网搜索和地图的推荐组合及接入验证项。 |
| [搜索服务比较](docs/search-services.md) | 五家搜索服务的免费额度、付费方式、API Key 和接口。 |
| [旅行 API 方案](docs/travel-apis.md) | 地点、路线、天气与场馆规则的来源、配额和优先级。 |
| [验收设计](docs/acceptance.md) | 固定案例、目标用户试用与评测记录。 |
| [决策台账](docs/decisions.md) | 已确认的产品与技术结论及修订记录。 |
| [术语表](GLOSSARY.md) | 行程要求、偏好档案、事实依据等领域概念。 |
| [调研摘要](docs/research.md) | 已有开源项目的参考价值与后续核验点。 |
| [偏好更新决策](docs/adr/0001-confirm-preference-updates.md) | 本次行程与长期偏好分开管理的原因。 |
| [单人安装决策](docs/adr/0002-single-user-installation.md) | 以个人安装实例组织资料、偏好与行程。 |
| [存储决策](docs/adr/0003-sqlite-and-local-files.md) | SQLite 与本地资料目录的选择原因。 |

## 下一阶段

下一步改进真实来源的地点对应、营业时间与票价事实覆盖，再执行 Windows 安装验证。产品行为以[已确认需求](docs/requirements.md)为准，实际验收结果与待确认项见[后端验收记录](docs/validation/2026-11-15-backend.md)。
