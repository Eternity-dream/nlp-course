# 外部服务接入方案

状态：兼容模型、三家搜索回退与高德适配器已实现；Exa 匿名搜索及正文取得真实成功响应。DeepSeek 等模型及其余供应商账号调用继续验收，详见[后端开发与验证](backend.md)。

## 服务职责

| 职责 | 方案 | 当前状态 |
| --- | --- | --- |
| 行程理解与生成 | 用户配置 `base_url + api_key + model`，应用调用 OpenAI 兼容 Chat Completions。 | 适配器和结构校验已实现，DeepSeek 等真实调用待验收。 |
| 网页搜索与正文 | Exa → Tavily → AnySearch 顺序回退，通过统一 search/fetch 适配接入。 | 适配器、共享冷却和回退测试通过；Exa 匿名 MCP 真实搜索与正文通过。 |
| 地点与当地交通 | 高德 Web 服务，按用户选择的城市查询地点与路线。 | 地点与三种路线适配及固定响应验证完成。 |
| 天气 | 高德短期天气或和风 7–10 天预报。 | 可选增强，详见旅行 API 方案。 |

完整对比见[搜索服务比较](search-services.md)和[旅行 API 方案](travel-apis.md)。原先百炼模型与内置搜索的组合建议由本方案替代。

## 模型接入契约

使用者只需配置服务基址、密钥和模型标识。应用在服务端调用标准 `POST /chat/completions`，以 `Authorization: Bearer` 鉴权；基址按提供方文档填写，客户端保留其已有路径前缀，不重复追加 `/v1`。

首版支持标准文本 `messages` 和 `choices[].message.content`。不同协议的服务需要相应适配器，仅更换 URL 不能改变请求协议。默认请求使用通用字段，提供方专有的推理参数、内置搜索或工具调用不作为必需能力。

行程输出由提示词约定 JSON 结构，应用解析并校验字段、日期、来源和约束。提供方支持 `response_format` 时可启用 JSON 模式；不支持时采用文本 JSON 与同一校验流程。空输出、截断或格式失败按有限重试处理，最终错误保留具体原因。模型参数差异在配置检查和公开样本测试中发现。

## DeepSeek 验证配置

按本次查阅的[官方快速开始](https://api-docs.deepseek.com/)，OpenAI 兼容基址为 `https://api.deepseek.com`，示例模型为 `deepseek-flash`；当前[模型页](https://api-docs.deepseek.com/quick_start/pricing)也列出 `deepseek-v4-pro`。以验证账号实际开放的模型 ID 为准，产品不固定到某个模型名称。

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=
LLM_MODEL=deepseek-flash
```

以上三个字段属于应用配置，示例值用于验证，安装者可以换成其他兼容提供方。DeepSeek [JSON 输出文档](https://api-docs.deepseek.com/guides/json_mode)要求 JSON 提示并支持 `response_format={"type":"json_object"}`；空白输出和截断仍需应用处理。

模型数据处理沿用 R08–R09：只发送当前任务必要片段，按实际提供方核对训练用途和保留规则。用户切换提供方时，相关条款与设置也随该提供方核对。公开样本可先用于验证协议、模型能力与 JSON 校验。

## 搜索与生成流程

1. 从本次要求提取目的地、地点、日期及需要核实的事实，构造公共查询词。
2. 按 Exa → Tavily → AnySearch 的顺序尝试可用服务，返回可用来源后停止切换，记录提供方与查询时间。
3. 对关键事实取得来源正文，保存抓取时间、可用的发布时间与适用日期。优先官方当前页面和公告。
4. 将证据、地点数据、本次要求和本地检索的相关片段交给已配置模型生成行程。
5. 模型输出 `source_ids` 引用应用已有证据；程序校验引用存在、事实对应、日期和费用等约束，再保存草案。

搜索适配器输出统一结构，具体供应商自行映射鉴权、字段与错误码。一次查询串行尝试，每个服务最多尝试一次；已有可用来源即返回，避免反复调用或无限回退。DeepSeek 验证不依赖其专有联网工具，其他模型可复用相同搜索结果。

## Exa 无 Key 模式

Exa 官方确认托管 MCP `https://mcp.exa.ai/mcp` 可匿名使用搜索与正文获取。应用连接后先完成初始化和 `tools/list`，按实际工具 Schema 调用 `web_search_exa`、`web_fetch_exa`，将结果映射到统一结构。该模式需要 MCP 客户端协议处理，不是向 REST `/search` 省略密钥。见[官方鉴权说明](https://exa.ai/auth.md)和[Exa MCP](https://exa.ai/docs/reference/exa-mcp)。

公开[托管实现源码](https://github.com/exa-labs/exa-mcp-server/blob/main/api/mcp.ts)的匿名默认限制为每 IP 2 QPS、每天 50 次 `tools/call`，搜索和正文工具共用该计数；初始化和列工具不计入。部署环境可以覆盖默认值，因此应用以实际 429、`Retry-After`、重置时间及 MCP 错误为准。普通 REST 无 Key 可能返回 x402 付款请求，不将它当成匿名免费入口。

启动时确定 Exa 模式：`EXA_API_KEY` 有值则使用带 Key 的 REST；为空且允许匿名时使用托管 MCP。有 Key 请求发生额度或鉴权错误时直接尝试下一家，不再切回同一服务的匿名入口。

## 回退规则

| 情况 | 行为 |
| --- | --- |
| 未配置 Tavily Key | 跳过 Tavily，记录未配置状态。 |
| Exa 或 AnySearch 未配置 Key | 若允许匿名，使用对应官方匿名入口；否则跳过。AnySearch 匿名请求不发送 Authorization 标头。 |
| 429 限流 | 记录冷却时间并立即尝试下一家；同一任务后续查询在冷却期跳过该服务，尊重 `Retry-After` 或重置时间。 |
| 402 额度耗尽、401/403 鉴权或权限错误 | 当前任务禁用该服务并记录原因，尝试下一家；由用户补充额度或修改配置。 |
| 连接失败、超时、5xx 或无法解析的提供方响应 | 记录本次错误并尝试下一家。 |
| 空结果或没有有效来源 URL | 尝试下一家；缺乏事实证据不视为已核实。 |
| 400/422 等固定请求参数错误 | 提示具体参数问题，停止该次请求，避免通过换服务掩盖调用错误。 |
| 三家均不可用或无结果 | 返回汇总原因，受影响事实保留待核实，已有有效证据继续保留。 |

MCP 的错误可能位于 JSON-RPC `error` 或工具结果 `isError=true`，即使 HTTP 为 200 也要识别并归类。来源记录保留实际成功的服务，错误记录不包含密钥。成功取得标题和 URL 只表示检索成功，营业时间与票价仍需正文支持。

鉴权失败与目标网页拒绝访问分开归类：搜索提供方确认 Key 无权限时才禁用该服务；单个网页返回 403、空正文或抓取失败只影响该 URL。若模型接口明确不支持可选 JSON 参数，可以去掉该参数使用文本 JSON 校验；这属于模型能力适配，不改变搜索请求参数错误的处理。

429 冷却状态在执行器内按提供方和鉴权模式共享，适用于所有排队任务。服务未返回可用重置信息时，该任务剩余步骤跳过它，并给出本地冷却起点；下一任务再按冷却状态决定是否尝试。具体记录格式和任务生命周期见[首版实施设计](implementation.md)。

正文优先复用已返回内容或现有本地网页提取；仍未取得正文时，对同一 URL 使用可用的 Exa → Tavily → AnySearch 正文能力，沿用冷却和错误状态。协议能力来自对应 REST 文档或 MCP `tools/list`。

## 配置示例

以下配置已提供于根目录 `.env.example`，后端支持读取、脱敏配置检查与显式连通性测试。三家服务独立填写 Key，匿名入口由一个开关控制，默认顺序可在配置中调整。

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=
LLM_MODEL=deepseek-flash
LLM_JSON_MODE=false
PROVIDER_TIMEOUT_SECONDS=30

SEARCH_PROVIDERS=exa,tavily,anysearch
SEARCH_ALLOW_ANONYMOUS=true
EXA_API_KEY=
TAVILY_API_KEY=
ANYSEARCH_API_KEY=
AMAP_API_KEY=
```

搜索服务的标准端点由对应适配器提供；Exa 无 Key 模式连接官方托管 MCP。配置列表选择协议适配器与回退顺序，模型密钥与各家搜索密钥分别使用。AnySearch 支持官方匿名 REST，但匿名 IP 配额与账户 Key 额度分别处理，不能混算。

若加入和风天气，再配置 `QWEATHER_API_HOST` 与 `QWEATHER_API_KEY`。API Key 从本机 `.env` 读取，页面配置检查返回各服务所用的鉴权模式、是否可用及可读错误；修改后重启生效。免费和付费额度由提供方管理，应用自动回退但不自动购买额度。

## 地点、路线与天气

高德 Web 服务 Key 由服务端使用。地点搜索 2.0 的 `/v5/place/text` 按本次城市设置 `region` 与 `city_limit=true`，保留地点 ID、地址、坐标与城市编码；路线按步行、公交或驾车接口取得估算耗时。见[地点搜索](https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch)和[路线规划](https://lbs.amap.com/api/webservice/guide/api/newroute)。

场馆票价、预约和特定日期的营业安排仍由原始来源核对。高德天气包含当天及未来三天；更长预报可选择和风，其当前每日预报接口默认 7 天、最多 10 天。超过预报日期范围时保留天气未知状态。配额、付费方式和来源样本见[旅行 API 方案](travel-apis.md)。

## 接入验收

- 使用 DeepSeek 和公开西安资料验证基址、密钥、模型名、内容输出及 JSON 校验。
- 更换兼容基址与模型，验证核心流程不依赖 DeepSeek 专有参数。
- 对搜索候选执行相同查询，比较官方来源、正文可读性、日期适用性与实际网络可达性。
- 检查额度耗尽、无效 key、限流和正文抓取失败时的明确反馈。
- 验证无 Exa Key 时通过匿名 MCP 搜索；遇限流按配置继续到 Tavily、AnySearch，正确处理 HTTP 200 内的 MCP 错误。
- 验证 Exa 成功后不调用后续服务、缺少 Tavily Key 时跳过，以及全部失败时汇总原因。
- 检查冷却期不重复访问被限流服务，带 Key 的服务失败后不改用匿名模式重试。
- 验证高德地点和路线参数随目的地变化，并完成西安之外城市的案例。
- 接入个人资料前核对所配置模型服务的数据处理要求。

2026-11-15 已从当前执行环境完成 Exa 匿名 MCP 初始化、工具发现、搜索和正文调用，并保存公开正文响应作为回归样本。此前的网络权限拦截属于早期研究记录。完整三家在线回退、模型及高德等实际账号调用与 Windows 安装验证继续纳入[推进计划 M1–M2](plan.md)。应用可通过 `POST /api/config/check` 主动调用所选服务，`GET /api/config/checks` 查看持久化的脱敏结果。
