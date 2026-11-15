# 网页搜索服务比较

核对日期：2026-11-14 至 2026-11-15。额度和价格来自各服务的官方定价页、计费说明与 API 文档，货币为美元。本文完成了文档核对；中国大陆网络可达性和西安中文来源覆盖尚需在实际安装环境验证。

## 结论

用户已选 Exa → Tavily → AnySearch 的顺序回退方案。Exa 有 Key 时使用 REST，无 Key 时尝试官方匿名托管 MCP；Tavily 需要 Key，AnySearch 可按配置使用 Key 或匿名 REST。TinyFish 与 Keenable 保留为调研资料。

首版实现前三家的 `search` 与正文获取适配，按顺序串行尝试，成功后停止。模型的 `base_url + api_key + model` 与搜索服务配置独立，具体触发条件和冷却规则见[接入方案](service-integration.md#回退规则)。

## 免费与付费

| 服务 | 普通免费额度 | 付费形式与价格 | 影响接入的规则 |
| --- | --- | --- | --- |
| [Tavily](https://docs.tavily.com/documentation/api-credits) | 每月 1,000 credits，无需信用卡；每月 1 日重置。 | 按量 $0.008/credit；订阅从 $30/月、4,000 credits 起。Basic 搜索每次 1 credit，Advanced 每次 2。 | Basic 搜索约 $8/千次，Advanced $16/千次；额度也用于抽取等接口。开启 PAYG 后按用量计费，帮助页说明每累计 $40 用量扣款。 |
| [Exa](https://exa.ai/pricing) | 首个 team 每月 $10 credits；完成 onboarding 另送一次性 $10，不需付款方式。 | 预付余额，无订阅和最低消费。每千次搜索 Lite $4、Auto $7、Deep $12，含前 10 条结果及相应文本/高亮；超出结果另计。 | 月免费余额每月 1 日重置且不累计，一次性 onboarding 赠额不失效。独立 Contents 为 $1/千页/内容类型。 |
| [AnySearch](https://www.anysearch.com/pricing.md) | 每账户每日 1,000 calls，10 QPS；UTC 00:00，即北京时间 08:00 重置。 | Pro 一次性请求包，当前展示 $3/千 calls（页面原价 $5），可叠加、不失效，20 QPS。 | **先扣 Pro 付费额度，再用当日免费额度**。成功非空搜索或有效正文提取各算 1 call；批量搜索按成功子查询计。 |
| [TinyFish](https://www.tinyfish.ai/pricing) | TinySearch 免费，官网列 30 次/分钟、500 次/小时；文档另列 12,000 次/日。TinyFetch 免费 1,000 URL/日，150 URL/分钟。新账号另有一次性 $8 钱包赠金。 | 钱包预付，最低充值 $10。TinyAgent $0.016/step，TinyBrowser $0.002/分钟；Search/Fetch 的超免费额度单价未明确。 | Search/Fetch 与 Agent/Browser 分开计量。官网与文档对 Search/Fetch 超额是否扣钱包有差异，详见下文。 |
| [Keenable](https://keenable.ai/pricing) | 每组织每月 100,000 次请求，无需信用卡；按组织创建日重置。 | 预付 credits，普通按量 $4/千次；购买额度不失效，可选自动充值。 | **先用免费额度，再用购买余额**。Search 与 Fetch 目前各 1 credit/次；每次搜索返回 1–50 条仍算一次。 |

官方支持依据：Tavily [额度](https://docs.tavily.com/documentation/api-credits)、[PAYG 与重置](https://help.tavily.com/articles/3950309978-understanding-pay-as-you-go-and-billing-resets)；Exa [定价](https://exa.ai/docs/admin/pricing)、[余额规则](https://exa.ai/docs/admin/billing)；AnySearch [定价全文](https://www.anysearch.com/pricing.md)；TinyFish [定价](https://www.tinyfish.ai/pricing)、[Search 文档](https://docs.tinyfish.ai/search-api/reference)、[Fetch 文档](https://docs.tinyfish.ai/fetch-api/reference)；Keenable [定价](https://keenable.ai/pricing)。

## 计量细节

**Tavily。** Basic Extract 每 5 个成功 URL 消耗 1 credit，Advanced 每 5 个消耗 2；失败提取不收费。按量价下，1,000 次 Basic 搜索加 1,000 个成功 Basic 抽取的标价合计 $9.60，实际请求以响应计量为准。免费 1,000 credits 约可用于 1,000 次 Basic 搜索或 500 次 Advanced 搜索，其他接口会共享这份余额。

**Exa。** 月免费 $10 按 Auto $7/千次约可用于 1,428 次搜索，按 Lite $4/千次约 2,500 次，假设每次最多 10 条结果且没有额外计费项。独立 Contents 的 `text` 与 `highlights` 是不同内容类型，同时请求会分别计费；Search 已包含的正文不应再重复算独立 Contents 费用。购买额度在 team 下共享；账户余额用完或 key 预算耗尽返回 402。

**Exa 无 Key 入口。** [官方鉴权说明](https://exa.ai/auth.md)确认 `https://mcp.exa.ai/mcp` 可匿名搜索与抓取正文。[开源实现](https://github.com/exa-labs/exa-mcp-server/blob/main/api/mcp.ts)默认按 IP 限制 2 QPS、每天 50 次工具调用，部署值可以调整；搜索与正文共用工具调用计数。该匿名额度与上表带 Key 的月度账户赠额是两种入口，普通 REST 省略 Key 会进入鉴权或 x402 付费流程。应用通过 MCP 协议初始化和发现工具后调用，按实际限流响应切换下一家。

**AnySearch。** 本文指 `anysearch.com` 和 `api.anysearch.com`。所有 key 共享账户的每日免费额与 Pro 余额，增加 key 不增加额度。免费额度不结转，Pro 包不失效。学生/开发者认证可申请 2,000 次/日、20 QPS，具有资格条件；付费请求包的支付渠道和最小购买金额在所查公开页未明确。

[AnySearch 鉴权文档](https://www.anysearch.com/docs/auth.md)也支持省略 Authorization 的匿名请求，按 IP 限制；无效 Key 不会自动回到匿名。本文不把匿名 IP 限额等同于账户免费额度，适配器启动时确定模式。

**TinyFish。** 当前是 TinySearch、TinyFetch、TinyAgent、TinyBrowser 四类 API。官方定价页写 Search/Fetch 在任何钱包余额下免费且不扣钱包；API 文档写超过日免费量后需要正钱包余额，并区分不收费 beta 产品的 `FREE_ALLOWANCE_EXHAUSTED`。因此只能确认列出的免费限制，不能推定超额无限免费或用旧 credits 套餐换算新 Wallet 的价格。新账户 $8 是一次性赠金，未找到通用到期说明。原型只需 Search/Fetch；网页自动操作会增加执行步骤与费用。

**Keenable。** 免费组织额度耗尽后可购买请求包，原 key 继续按组织余额计费；购买额度不失效，key 可轮换且不自行到期。无 key 的公开接口另有每 IP、每端点 1,000 次/小时和 10 RPS 的共享限制，需要应用标识；不作为个人安装的固定保额。专用容量 $1/千次要求 100+ RPS，另有 x402 每次 $0.01 的钱包支付路径，均不属于普通 key 的 $4/千次报价。默认 Fetch 可能读取索引副本，时效事实应选择实时抓取并记录抓取时间。

## API Key 接入

免费与付费一般使用相同 API 协议，账单来自 key 所属账户、team 或组织。项目不需要为免费和付费写两套调用代码；由使用者在服务商控制台开通付费或充值，再继续使用对应 key。供应商的公开资料并未逐一承诺升级时 key 的所有细节，实际以账号设置为准。

| 服务 | 鉴权 | 搜索 | 正文提取 | Python 接入 |
| --- | --- | --- | --- | --- |
| Tavily | `Authorization: Bearer <key>` | `POST https://api.tavily.com/search` | `POST https://api.tavily.com/extract` | `tavily-python`，有同步与异步客户端。 |
| Exa | `x-api-key: <key>`，也支持 Bearer | `POST https://api.exa.ai/search` | `POST https://api.exa.ai/contents` | `exa-py`。 |
| AnySearch | `Authorization: Bearer <key>` | `POST https://api.anysearch.com/v1/search` | `POST https://api.anysearch.com/v1/extract` | 官方 REST 示例与 Python CLI；应用可直接调用 REST。 |
| TinyFish | `X-API-Key: <key>` | `GET https://api.search.tinyfish.ai` | `POST https://api.fetch.tinyfish.ai` | `tinyfish` SDK。 |
| Keenable | `X-API-Key: <key>`，也支持 Bearer | `POST https://api.keenable.ai/v1/search` | `GET https://api.keenable.ai/v1/fetch` | `keenable`，有异步客户端。 |

接口依据：[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[Exa Contents](https://exa.ai/docs/reference/get-contents)、[AnySearch 协议](https://www.anysearch.com/docs/api-endpoints/v1-search.md)、[AnySearch 鉴权](https://www.anysearch.com/docs/auth.md)、[TinyFish Search](https://docs.tinyfish.ai/search-api/reference)、[TinyFish Fetch](https://docs.tinyfish.ai/fetch-api/reference)、[Keenable API](https://docs.keenable.ai/api-reference)、[Keenable key](https://docs.keenable.ai/api-keys)。

搜索结果统一为标题、URL、摘要、提供方及查询时间；正文统一为 URL、抓取时间、正文和可用的页面更新时间。模型根据这些证据生成行程，查询时间与页面发布时间分别保存。服务商摘要用于找到来源，营业时间、票价和预约规则继续核对原文。

## 西安对比样本

以陕西历史博物馆当前预约规则、兵马俑指定季节检票时间、大雁塔地点与同名条目、碑林开放公告、目的地切换为固定查询。评价来源命中、正文是否可读取、日期是否适用及来源关联完整性；在实际 Windows 安装网络测试搜索与正文接口的连通性。

回退顺序已确定为 Exa → Tavily → AnySearch。无 Key Exa 的公开文档与默认限制已核对；2026-11-15 的直连探测被当前执行环境网络权限拦截，完整链路仍需实际安装环境验证。
