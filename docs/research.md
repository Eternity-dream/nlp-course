# 开源项目调研摘要

调研日期：2026-11-14。内容整理自原会话中通过 Exa 查询 GitHub 项目页与 README 后形成的报告；用于支持设计讨论，尚未形成代码复用选型。

## 项目与可借鉴的做法

| 项目 | 原调研记录的做法 | 对本项目的用途 |
| --- | --- | --- |
| [AI Travel Agent](https://github.com/nirbar1985/ai-travel-agent) | 保存对话状态，在邮件等外部操作前暂停确认。 | 参考多轮条件补充与状态延续。 |
| [TravelPlanner](https://github.com/osu-nlp-group/travelplanner) | 按环境、常识与硬约束等维度组织旅行规划评测。 | 参考约束分类和固定案例设计。 |
| [Rutugo](https://github.com/hardikverma22/travel-planner-ai) | 分步收集预算与偏好，支持修改条件和可行性检查。 | 参考输入流程和行程编辑体验。 |
| [Tripper](https://github.com/embabel/tripper) | 用领域模型组织流程，组合搜索与地图工具。 | 参考对象边界和工具职责。 |
| [LangGraph Trip Planner](https://github.com/Kyle-liu888/langgraph-trip-planner) | 使用真实地点数据、结构化行程、规则校验和失败修复。 | 参考地点核实、行程结构与核验流程。 |
| [FloatTrip](https://github.com/shouzhuoshouzhuo/FloatTrip) | 中文对话、旅行偏好记忆、本次要求覆盖长期偏好、地点服务和行程编辑。 | 优先核对偏好管理、地点数据与可编辑行程的衔接。 |
| [Traveler](https://github.com/Y-66/Traveler) | 组合个人偏好记忆与本地文档知识库。 | 参考资料导入、检索及偏好与资料的职责划分。 |

## 对当前计划的影响

推进方案采用“结构化要求 → 真实地点与资料检索 → 行程生成 → 规则核验 → 用户编辑”的流程建议。重点研究 FloatTrip 的偏好与编辑体验、LangGraph Trip Planner 的核验方式，以及 TravelPlanner 的评测分类。

技术验证应直接检查个人资料是否影响候选、时效事实能否关联依据，以及编辑后能否重新核验。这三项共同对应本项目已经确认的产品目标。

## 进入代码复用前的核验

选定候选项目后，针对具体版本检查实现入口、接口依赖、许可证、运行成本和维护情况，并运行与首发城市有关的样本。将可运行结果和复用决定另行记录；README 中的能力描述作为核验线索。
