# Step 2：Explorer agent

2026-11-10，我按最新 PLAN 的 Step 2 实现了通过工具探索环境的 LLM agent。默认产物是 `data/knowledge/retail_world_model.json`。τ² 继续固定为 `fc0055dc4e0a316c3f83133267fbd6faaa770992`，没有修改其源码。

## 运行

```bash
python scripts/build_holdout.py
# 在调用进程中设置 LITELLM_API_KEY，不把密钥写入命令或配置文件。
python scripts/explore.py --config configs/explorer.yaml
pytest -q
```

Windows 临时注入方式：

```powershell
$env:LITELLM_API_KEY = (Get-Clipboard -Raw).Trim()
try { .venv/Scripts/python.exe scripts/explore.py } finally { Remove-Item Env:LITELLM_API_KEY }
```

共享模型配置来自 `configs/retail.yaml`；Explorer 仅在 `configs/explorer.yaml` 指定共享配置和 turn 预算。当前选择网关返回的 `qwen3.7-max`，LiteLLM 用 `openai/` 前缀选择兼容协议，temperature=0、seed=0、max_tokens=16000。其他合成角色后续也使用这个共享模型。第一次使用的 DeepSeek-V4-Flash 在 agent 第 5 轮触发 60,000 token/min 限额；我按用户要求换成 qwen3.7-max，没有静默重试或切换。

## Agent 与环境证据

- `[BLOG]` agent 自己决定读什么、查询哪些实体、提出哪些前置条件与失败方式，并构造满足/违反条件的探针。
- `[INDEPENDENT]` `read_source` 只开放 Retail 的 tools.py、data_model.py、policy.md；另提供 get_metadata、query_data、sandbox_execute、probe_condition、submit_world_model。
- `[OFFICIAL]` 工具执行、DB hash、policy、schema、统计都来自固定 τ² 环境。AST 另保存签名、ToolType、显式 ValueError 路径与 helper 调用关系；并保留 data_model 的字段和 Literal。
- `[INDEPENDENT]` 每个 sandbox 都重新加载环境。agent 可提出仅作用于内存 DB 的 patches 和 setup 动作，不能改磁盘 DB 或实体身份。参数、身份查询、订单选择及 patch 目标均排除 holdout 用户。实体查阅也经过过滤。
- probe_condition 先检查结构化谓词在正例为真、反例为假，再分别执行。正例失败记 contradicted；反例被拒绝记 confirmed_by_tool；反例放行且有 policy 原文依据记 policy_only；放行但没有 policy 依据记 unconfirmed。后两种未接受状态与 contradicted 不进入有效 rules。
- JSON 检查使用 query_data 的集合、字段和算子；参数比较使用相同的确定性算子，支持 all/any、DB/参数引用和逐商品检查，没有执行模型生成的 Python。
- actions 中每条 requires / failure_modes 必须引用对应工具的已接受探针；changes 必须引用成功执行的 observation 或探针。模型不能自行指定通过标签。保留结构化 case、patch/setup、执行结果与前后 hash，供复验与诊断。
- 对话规则保留 policy 原文且 check=null，单独列出，不宣称通过了 DB 验证。

探针确认的是具体实例上的局部行为，不是对所有状态的证明。LLM 文字是对证据的说明，不是独立的环境判据。阶段内的工具协议、JSON 格式、预算和收尾提醒属于 `[INDEPENDENT]` 实现。

最初远端仍是旧版计划时写的有界规则编译器和成对 fixture 留作离线回归辅助；默认 CLI 的 agent 不接收它的规则目录或预制探针。旧版 retail_knowledge.json 留在本地工作产物中，不作为新版交付。

## 验证

最终离线测试：**31 passed, 1 warning**（上游 audioop 弃用警告）。覆盖 AST、缓存、规则分类与 policy gaps、原生 runner，以及 MockLLM 的完整 agent 工具循环、源码白名单、holdout 边界、无证据声明拒绝、预算结束不输出半成品。

本机 pytest 系统临时目录有权限问题，我为本次测试设置了工作目录内的 PYTEST_DEBUG_TEMPROOT，没有修改系统目录权限。pytest 使用 mock 模型并禁用 socket 连接；真实网关调用在测试之外运行。

## 用量口径

world model 的 llm_usage 记录最后一次运行新增调用、缓存命中、token 和估算花费；llm_requests 保留每个来源响应的 token/花费，即使重放缓存也能追溯原生成用量。缓存键包含模型、messages、tools、参数和 API base，不含凭证。凭证仅在进程中使用，不提交响应缓存。

开发中最早一次 JSON 格式错误的响应发生在用量持久化补齐前，其 token/花费未保留，因此无法给出完整开发账单。后续响应在 JSON 解析前缓存。429 请求没有成功响应，用量也不可据此推断。所有花费均为客户端估算，不能代替网关账单。

## 修改文件

- Explorer：src/pioneer/explorer/{__init__,agent,build,source,rules,probes}.py。
- LLM：src/pioneer/llm/{__init__,client}.py。
- 入口与配置：scripts/explore.py、configs/{explorer,retail}.yaml、pyproject.toml。
- 测试：tests/{test_explorer,test_explorer_agent,conftest}.py。
- 文档：README.md、docs/{PLAN,AUDIT,EXPLORER,FIDELITY}.md。
- 新版产物：data/knowledge/retail_world_model.json。

## 本次实际产出

qwen3.7-max 的正式运行在第 32 轮通过提交检查。16 个动作、12 条有效状态规则（10 confirmed_by_tool、2 policy_only）、7 条对话规则；另有 1 条 contradicted 放在丢弃记录中。41 条独立 sandbox 观察之外，每个记录成功的状态规则都有正反两次执行。构造错误的探针会返回错误给 agent，修正前不作为证据。

两条 policy_only 分别是：真实修改商品后仍能改地址，以及 exchange 接受相同 item。取消商品修改后的订单被工具拒绝。支付修改的 agent 探针正反例都因为 Payment method not found 失败，属于失效的正例控制，故丢弃；这不是证明 policy 错误，也不能据此声称支付工具缺口已经由该探针确认。独立回归 fixture 仍单独覆盖支付状态检查的子串缺口，见 AUDIT。

我在提交阶段把 requires/failure_modes 的有效内容归一为其引用的已验证谓词及分类，changes 则归一为实际原生输出和 hash 变化，避免“引用了状态探针，却额外声称库存条件也被证明”的情况。agent 原始提交保留在 trace 中供审计；没有证据的模型文字不作为有效知识。

正式产物的 32 份模型来源响应共使用 **1,874,781 输入 token、25,690 输出 token**，估算 **$3.456091**。最后一轮有 8 次新增调用、24 次缓存命中；随后对最终规范化做缓存重放，未另行调用模型。整个开发过程已保存 40 份成功响应，用量估算合计 **$3.562143**；另有前述最早一次格式错误未留存用量，以及一次 429 拒绝，不能把已知金额当作完整账单。

原子动作包含全部 7 个 WRITE 名称，以及 transfer_to_human_agents 和 5 类 inform（用户、订单、商品、item、商品类型信息）。这些都是 agent 读环境后提交的类型；模型提出但没有确认的其他机制没有进入有效规则。

## Step 2.5 review 跟进

生产包现在仅导出 ExplorerAgent；旧编译器和规则目录已移到 tests/helpers，静态测试禁止生产代码引用。配置不再保存网关地址，CLI 从 LITELLM_API_BASE 读取，world model 只保存环境变量占位符。历史提交不改写、不 force push。

我把 policy 分为 60 个非空段落，用 state/conversation 规则的 policy_quote 映射。补探索前 15 段有映射（25%），补探索后 20 段（33.3%）。新增 5 条 confirmed_by_tool，分别覆盖新旧支付方式不同、退款目的支付方式以及三种写操作的礼品卡余额。另一个正例失败的探针进入丢弃记录，不能当作 policy 反证。

仍未映射的 40 段逐条保存在 policy_coverage.paragraphs，包括标题、数据结构说明，也包括确认要求等尚未单独映射的行为段。模型给出的解释原样保留为原因，程序不会据此将它们计为覆盖。一段可包含多个约束，部分条款已映射不等于整段所有机制都已验证；同商品、有货等条件仍以原生工具执行作为入库底线。

补探索使用 qwen3.7-max，12 次真实调用，输入 442,236 token，输出 14,937 token，客户端估算 $1.103133。最终规则分布为 15 confirmed_by_tool、2 policy_only，另 2 条丢弃。agent_trace 和 llm_requests 已移入 retail_explorer_trace.json，保留上一阶段来源，不占用主 world model 的下游字段。全量离线验证为 35 passed，1 条上游弃用警告。
