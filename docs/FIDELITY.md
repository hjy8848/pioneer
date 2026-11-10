# 忠实度对照表

本表逐项列出各组件与博客 *data-synthesis-v0*（2026-09-15）的对应关系。源文件：`awslabs/agentcore-rl-toolkit:docs/site/src/content/docs/blog/data-synthesis-v0.mdx`。

**标注说明：**
- `[BLOG]`：博客有明确描述，照做。
- `[INDEPENDENT]`：博客没有公开，在博客框架内自行设计。
- `附加约束`：本项目的要求，不来自博客，也不与博客冲突。
- `无法复现`：博客没有提供足够信息，无法实现。

每一步完成后，Codex 都要更新本表。

## 合成流水线

| 组件 | 博客原文 / 依据 | 本复现的做法 | 标注 |
|---|---|---|---|
| 阶段如何驱动 | "Each is driven by an agent, but no LLM output is trusted until the environment confirms it." | 四个阶段都是 LLM agent，通过工具接触环境；程序只负责提供工具和做环境检查 | `[BLOG]` |
| LLM 骨干模型 | "a frontier model for all LLM roles (explorer, evolver, initializer, user simulator, verifier)" | 共享 `configs/retail.yaml`：网关 qwen3.7-max（LiteLLM 使用 openai/ 协议前缀）。Explorer 已实际运行；其他角色后续复用。初始 DeepSeek-V4-Flash 遇到限流，按用户要求切换 | `[BLOG]`；具体模型与切换为 `[INDEPENDENT]` |
| Explorer 的输入 | 工具代码、state schema、初始数据库、policy | agent 用 `read_source`、`get_metadata`、`query_data`、`sandbox_execute` 读取 | `[BLOG]` |
| Explorer 的输出 | 每个动作的 needs / changes / how it can fail | 已产出 retail_world_model.json：16 个动作，requires/failure_modes 指向已接受探针的真实谓词；changes 保存原生输出和 DB hash 是否变化，避免把无关的模型文字当成被证明的事实 | `[BLOG]`；JSON 和证据规范为 `[INDEPENDENT]` |
| Explorer 结果的环境确认 | "no LLM output is trusted until the environment confirms it" | 已跑 32 轮 tool-use agent，程序提供工具并验证 agent 提出的 case；10 confirmed_by_tool、2 policy_only；1 contradicted 未进入有效规则。模型不接收预制规则目录或固定探针，未确认的机制不得声称已验证 | 原则 `[BLOG]`，探针及提交协议 `[INDEPENDENT]` |
| 出题起点 | "We start from every atomic action" | 每个原子动作生成 n 个深度 0 的任务 | `[BLOG]` |
| stressor 类别 | 更多动作串联、更多实体、对 policy 讨价还价、条件请求、需求不明确、推理层难度 | `schemas.Stressor` 中的 6 类 | 类别 `[BLOG]`，细则 `[INDEPENDENT]` |
| 全局追踪器 | 追踪已覆盖的内容，引导下一代去补未覆盖的结果，"including … say no" | 覆盖维度和调度规则 | 原则 `[BLOG]`，维度 `[INDEPENDENT]` |
| 最大深度 | 图 3 画到深度 4 | max_depth = 4 | `[BLOG]`（取自图） |
| Initializer | user goal + 绑定到真实状态值的 reference outcome；`query_data` 用于把参考结果落到真实值上 | 已实现 agent 用 `query_data`、`sandbox_execute`、`submit_candidate` 构造参考结果，不提供动作模板。11 个任务通过全部 E1–E9，覆盖 6/13 类；用户提前结束本批，全类别验收未完成 | `[BLOG]`；工具协议和批次实现为 `[INDEPENDENT]` |
| 入库标准 | reference outcome 可达，并且是 policy 要求的结果；LLM 认为合理不够 | 证据 E1–E8 全部由程序在环境中检查；verifier agent 不能覆盖结果 | `[BLOG]`；各检查项是 `[INDEPENDENT]` |
| Verifier 角色 | 博客把 verifier 列为 LLM 角色 | 已实现 VerifierAgent，经工具检查 E1–E9 后提交诊断；passed 由程序计算，模型提交的 passed 不能覆盖失败证据 | `[BLOG]` 角色 / `[INDEPENDENT]` 分工 |
| 端到端可达（E8） | user simulator 是合成流水线的角色之一；`run()` 是六接口之一 | 已用共享 qwen3.7-max 跑通：写任务 llm_agent_gt、只读 llm_agent，各加 user simulator，k=2；都为 1/1。先前因提前结束而失败的 0/0 样例被拒绝，未放宽标准。所有生成均经过本仓库缓存客户端，原生 evaluator 重新评分 | `[INDEPENDENT]` |
| 失败处理 | "diagnosed and reworked until they pass"，不能只做过滤 | 已实现诊断回传、默认每批 5 轮，预算用完后进入 `rework_queue`，恢复后继续修；本批已完成候选无外层返工，未完成候选与缓存保留 | `[BLOG]`；预算机制是 `[INDEPENDENT]` |
| 六接口 | `get_metadata`、`init`、`run`、`state`、`query_data`、`reward` | 全部实现 | `[BLOG]`；签名 `[INDEPENDENT]` |
| `execute()` | 博客没有这个接口 | 单步执行，供重放和探针使用 | `[INDEPENDENT]` 扩展 |
| 运行方式 | 本地进程内，或部署在 AgentCore Runtime 上 | 本地进程内 | `[BLOG]`（博客允许这种方式） |
| 每个领域的规模 | 约 600 个已验证任务 | 先到 50 个作为检查点，再扩到约 600 个 | `[BLOG]` |
| 任务格式 | 博客没写 | τ² 的 Task 格式加上 TaskMeta | `[INDEPENDENT]` |
| prompt | 博客没写 | 自行编写，并纳入版本管理 | `[INDEPENDENT]` |

## 环境与评测

| 组件 | 博客原文 / 依据 | 本复现的做法 | 标注 |
|---|---|---|---|
| τ² 版本 | label-corrected release 的 `base` split | v1.0.1；base split 中 Retail 为 114 题 | `[BLOG]` |
| 评测领域 | Airline 50 题、Retail 114 题、Telecom 114 题；CAR-bench Base 100 / Disambiguation 56 / Hallucination 98 | 同左 | `[BLOG]` |
| Telecom 的 policy | 博客没写 | 待定（ART 示例使用 workflow policy） | `[INDEPENDENT]` |
| 评测指标 | 博客没写 | 平均成功率（pass^1） | `[INDEPENDENT]` |
| Frontier A / B | 博客没具名 | 改用我们自选的参照模型，并如实标注 | `无法复现` |
| 一致性分析（图 4） | Telecom 114 题每题跑 5 次，分为三个桶 | 同左 | `[BLOG]` |
| 难度定义（图 3） | 学生基座模型 zero-shot 跑 8 次的平均通过率 | 同左；因为博客没说是哪个学生模型，8B 和 30B 都跑 | `[BLOG]`；模型选择 `[INDEPENDENT]` |

## 训练

| 组件 | 博客原文 / 依据 | 本复现的做法 | 标注 |
|---|---|---|---|
| 学生模型 | Qwen3-8B、Qwen3-30B-A3B-Thinking-2507 | 同左 | `[BLOG]` |
| SFT / RL 划分 | "split between SFT trajectories and RL references by evolution depth" | 浅层用于 SFT、深层用于 RL，阈值可配置 | 原则 `[BLOG]`，阈值 `[INDEPENDENT]` |
| SFT 轨迹的来源 | 博客没写 | frontier 模型作为 task agent，只保留 reward=1 的轨迹 | `[INDEPENDENT]` |
| 单领域还是混合训练 | 博客没写 | 默认混合训练，保留按领域单独训练的开关 | `[INDEPENDENT]` |
| RL 设置 | GRPO with DAPO-style refinements；16 rollouts per task；full parameter；lr 1e-6；batch size 8 | 同左 | `[BLOG]` |
| 哪些模型做 RL | 表 1 只有 30B 做了 RL | 同左 | `[BLOG]` |
| 训练框架 | agentcore-rl-toolkit | ART 的 verl 或 sagemaker（DAPO）后端 | `[BLOG]` |

## 附加约束（来自项目要求，不来自博客）

Step 4.5 `[INDEPENDENT]`（博客没写，为让流水线跑通而补）：
- 阶段 prompt 不含 Retail 业务规则；领域名、规则语法、opaque ID 路径来自适配层。Explorer 原先在第 23/31 轮的催促消息里点名了两个已知缺口，已删除，Step 2 的 world model 是在有这条提示的情况下产出的。
- 用户剧本里禁止出现的 ID：取「嵌套在记录内部的 dict-of-record 字段的键」，由数据库结构推导（Retail 里即 item ID 和 payment ID）；顶层集合的键（用户、订单、商品 ID）允许出现。
- 实体多样性：候选池由已验证规则推出的实体约束加 seed 抽样得到；同一（原子动作，实体）不重复，每用户每批最多 2 题。
- 首轮通过率给两种口径（见 `stats/ledger.py`）。
- 写死实例 ID 的规则不得进入 world model；Step 2.5 的 5 条已被移出，覆盖率回到 15/60。
- 尚未做：Explorer / Initializer 代码（不只是 prompt）里仍有 Retail 专用逻辑（如 `explorer/rules.py` 的 ref 解析、`case()` 的补丁集合），换到 Airline / Telecom / CAR-bench 时再抽到适配层。

Step 3.5 `[INDEPENDENT]`：结构签名用于覆盖统计，不拒绝同结构的新实例；实例签名使用 actions 名称/参数、initial_state、stressor 集合、expected_outcome、communicate_info，排除剧本文字。Initializer 必须在 task_instructions 中包含等待操作完成后才结束的固定句，防止重现提前 STOP；没有采用额外 ART checklist。

Step 3 `[INDEPENDENT]`：参数证据用原文/READ 输出表达式表示，身份匹配必须唯一，E4 是保守的可导出性近似；E6 验证候选声明的初始/澄清筛选，不能声称解决任意自然语言语义验证。拒绝请求可声明 after_actions 前缀。E9 内容签名不依赖调用者提供的签名字符串，实例签名忽略 action_id 和剧本文字，包含 stressor 集合（Step 3.5 修正）；任务池已见签名由后续流水线传入。作用域内生成适配只支持单进程顺序的原生对话。

Step 2.5 `[INDEPENDENT]`：按原文段落与规则 policy_quote 做映射，未覆盖段落交给同一 Explorer agent 补探索。覆盖率 25%→33.3%，新增 5 条工具确认规则。分母包括标题和说明段，且一段可能有多个约束，因此该数字既不是“可执行规则召回率”，也不是 policy 完整性证明；未覆盖及未进一步确认的内容仍显式保留。生产包不再导出或引用模板编译器；网关地址由进程环境注入，轨迹与主 world model 分离。

- **评测隔离：** 官方评测题涉及的用户或实体不进入合成任务，生成代码不读取官方任务文件。
- **不使用 NL_ASSERTION：** 合成任务的 `reward_basis` 只能是 `[DB]` 或 `[DB, COMMUNICATE]`。这与博客「不由 LLM 评分」的要求一致。
- **先验证流水线，再进入训练：** 博客本身也是先合成任务、后训练，所以不冲突。
