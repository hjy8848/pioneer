# 执行计划

每一步交给 Codex 执行一次。通用规则见仓库根目录的 `AGENTS.md`（Codex 会自动读取）。
事实依据见 `docs/AUDIT.md`。

**状态说明：** ✅ 已完成；⬜ 待做。

---

## Step 0：项目骨架、固定 τ² 版本、审查文档 ✅（随基础代码一起提交）

- `scripts/setup_tau2.{ps1,sh}`：用 blobless + sparse 的方式拉取 τ² v1.0.1，并校验 commit。
- `src/pioneer/env/retail_env.py`：
  - 已实现：`get_metadata`、`init`、`state`、`query_data`，以及扩展接口 `execute`。
  - 尚未实现：`run`、`reward`，留给 Step 1。
- `src/pioneer/schemas.py`：各阶段共享的数据契约（Scenario、Stressor、ExpectedOutcome、FailureCode、VerifierReport、TaskMeta）。
- 测试：
  - `tests/test_tau2_pin.py`：版本、commit 和 DB 规模。
  - `tests/test_retail_env.py`：环境层，包括复现一个「工具放行、policy 禁止」的缺口。
  - `tests/test_isolation_static.py`：评测隔离的静态检查。
- 遗留问题：`docs/AUDIT.md` §5 的待核实项，并入 Step 1 处理。

---

## Step 1：补全六接口 + 评测隔离 ✅

完成记录（2026-11-10）：AUDIT §5 已核实；114 题提取 53 个 holdout 用户、114 条任务签名；`pytest -q`：15 passed，1 条上游 audioop 弃用警告。原生 runner 的两个 agent 均通过 mock LLM 对话和真实环境评分测试。

1. **核实 AUDIT §5 的各项，结论写回 AUDIT.md。** 其中 v1.0.1 与 v2.0.0 的 retail diff 可以这样看：
   `git -C third_party/tau2-bench fetch --depth 1 origin tag v2.0.0`，再对 `src/tau2/domains/retail` 和 `data/tau2/domains/retail/{db.json,policy.md}` 做 diff。
2. **`RetailSynthEnv.run(task, agent_cfg, user_cfg)`：** 调用 τ² 原生 runner。
   - agent 默认 `llm_agent`，可选 `llm_agent_gt`；user 为 `user_simulator`。
   - 返回 `SimulationRun`。
   - 任务通过 `registry.register_tasks` 注册为 `retail_synth`，或直接把 Task 对象传给 runner。
3. **`RetailSynthEnv.reward(task, simulation)`：** 调用 `tau2.evaluator.evaluator.evaluate_simulation(..., EvaluationType.ALL, domain="retail")`。
4. **`scripts/build_holdout.py`：** 这是唯一允许读取官方任务的脚本。
   - 从 base 114 题中精确提取涉及的 `user_id`，来源包括：actions 参数、`order_id` 反查，以及 `known_info` 中的邮箱和「姓名 + 邮编」。
   - 输出 `data/holdout/users.json`。
   - 同时输出官方任务签名 `(user_id, 写动作名的有序序列)`。
5. **测试：**
   - 一个 `actions=[某写动作]` 的任务：重放相同动作得 1，不做动作得 0。
   - 一个 `actions=[]` 的任务：有任何写动作就得 0。
   - `run()` 用 mock 或 dummy 的 LLM 跑通一条最短对话（不联网）。

**回报内容：** AUDIT §5 的结论、holdout 用户数、pytest 输出。

---

## Step 2：Explorer（构建环境知识）⬜

产出 `data/knowledge/retail_knowledge.json`。

1. **`[INDEPENDENT]` 确定性部分：**
   - 对 τ² 的 `retail/tools.py` 做 AST 解析，提取签名、ToolType，以及每个 `raise ValueError` 对应的前置条件。
   - 从 `data_model.py` 提取 schema 和 Literal 枚举。
   - 用 `get_metadata()["db_stats"]` 做 DB 统计。
2. **`[BLOG]` LLM 部分：**
   - 对每个动作输出 `{requires, changes, failure_modes}`。
   - 把 policy 规则输出成 `{rule_id, tools, text, check, level: state|conversation}`。其中 `check` 必须是 `query_data` 可执行的结构化谓词。
3. **`[INDEPENDENT]` 环境探针校验：** 对每条 `level=state` 的规则，自动构造满足和违反的实例，在全新环境中用 `execute()` 执行，并标注结果：
   - `enforced_by_tool`：工具会抛异常。
   - `policy_only`：工具放行，但 policy 禁止。
   - `contradicted`：与环境行为矛盾，丢弃。

   回归测试至少覆盖 AUDIT §4 中列出的 policy_only 规则 1、2，以及「修改商品后再取消订单会被工具拒绝」。
4. **原子动作表：**
   - 7 个 WRITE 工具，各对应一个原子动作。
   - 只读的「可回答问题类型」，例如订单状态、商品选项、礼品卡余额；答案进入 `communicate_info`。
   - transfer（超出能力范围）。
5. **`src/pioneer/llm/`：** 基于 litellm 的客户端，带磁盘缓存；另提供 MockLLM 供测试使用。

**回报内容：** 规则数量及 enforced / policy_only / contradicted 的分布、原子动作清单、LLM 调用次数和花费。

---

## Step 3：Verifier（先于 Initializer 实现）⬜

输入候选任务（τ² Task + TaskMeta），输出 `VerifierReport`。

所有检查都是确定性的，不用 LLM 判定：

- **V1 可达** `[BLOG]`：在全新环境中用 `execute()` 逐步执行参考动作，每一步都必须 `ok=True`。这一项弥补了 evaluator 只打 warning 的缺陷。
- **V2 policy 一致** `[BLOG]`：每个写动作执行前，在当时的状态上检查所有 `enforced` 和 `policy_only` 规则，全部必须满足。
- **V3 状态效果** `[INDEPENDENT]`：
  - 写任务：最终 hash 不等于初始 hash，且 DB diff 只落在参考动作涉及的实体上。
  - 无写动作的任务：`actions` 必须是 `[]`，并且 `communicate_info` 可以由 `query_data` 算出。
- **V4 信息充分** `[INDEPENDENT]`：参考动作的每个参数，都要能从 `known_info` 出发，经 agent 可用的 READ 工具链唯一确定。同时检查指令里没有泄露 item_id 或 payment id。
- **V5 拒绝类结果** `[BLOG]`：
  - 被禁止的请求在沙盒副本中执行时，要么被工具拒绝，要么违反 V2。
  - 参考结果为 `[]`，或只执行合规部分。
  - 应转人工的，参考结果包含 `transfer_to_human_agents`。
- **V6 歧义** `[BLOG]`：仅凭初始描述得到的候选多于 1 个，加上澄清信息后正好 1 个。
- **V7 官方评分** `[OFFICIAL]`：用参考动作构造 SimulationRun，经 `reward()` 评分，结果必须为 1。
- **V8 去重与隔离：** 结构签名和实例签名都不重复；不涉及 holdout 用户；与官方任务签名无重叠。
- **V9（可选，较贵）端到端可达** `[INDEPENDENT]`：用 `run()` 跑 `llm_agent_gt` + user simulator k=2 次，至少一次 reward=1。判定依据是环境 reward，不是 LLM 的意见。

**diagnosis：** 由程序生成结构化原因（哪一步、哪条规则、哪个参数）。LLM 只能把它改写成可读文字，不能改变判定。

**测试：** `schemas.FailureCode` 中的每个失败码都至少有一个手工构造的反例。反例只能用自己构造的数据，不能用官方任务。

**回报内容：** 每个失败码的测试结果。

---

## Step 4：Initializer + 深度 0 原子任务 ⬜

1. **Initializer：**
   - `[BLOG]` 把 `entity_constraints` 编译成 `query_data` 查询，从非 holdout 用户中按 seed 采样实体。
   - `[INDEPENDENT]` 由程序生成参考 actions：只放写动作和 transfer，READ 动作不放入。
   - `[BLOG]` 由 LLM 写 `user_scenario.instructions`（`reason_for_call`、`known_info`、`unknown_info`、`task_instructions`）。prompt 中给出「必须出现的值」和「禁止出现的值」两个清单，生成后用程序检查。
   - 填写 `evaluation_criteria`：
     - `actions`：写动作列表，或 `[]`。
     - `communicate_info`。
     - `reward_basis`：只能是 `[DB]` 或 `[DB, COMMUNICATE]`。
2. **修复循环** `[BLOG]`：根据失败码选择修复策略（重新采样实体、补全 known_info、去掉泄露的 ID、调整参数、退回场景层重写），最多 3 轮。
   - 每一轮都写入 `repair_log`。
   - 3 轮仍失败才记为 dropped，并保留失败原因。
3. **生成量：** 每个原子动作生成 n 个深度 0 的任务（默认 n=3）。

**验收：**
- 每个原子动作至少有 1 个任务通过。
- `data/runs/<date>/depth0.jsonl` 能被 τ² 加载。
- 用 `run()` 跑通其中 1 个任务的完整对话。

**回报内容：** 每个原子动作的候选数、首轮通过数、修复后通过数，以及失败码分布。

---

## Step 5：Evolver + 全局覆盖追踪器 ⬜

1. **stressor：** 对应 `schemas.Stressor`，定义如下（细则都是 `[INDEPENDENT]`）：
   - `CHAIN`：叠加一个不同的原子动作。
   - `MULTI_ENTITY`：同一用户的多个订单或商品。
   - `POLICY_PUSHBACK`：用户坚持一个被禁止的请求，正确结果是拒绝或只执行合规部分。
   - `CONDITIONAL`：例如「有货就换，否则退」。参考结果取环境中实际成立的分支，同时验证另一分支确实不成立。
   - `UNDERSPECIFIED`：描述能对应多个对象，需要澄清。
   - `REASONING`：需要比较或计算，例如退掉最便宜的那件、判断礼品卡余额是否够用。
2. **每一代的流程：**
   - 覆盖缺口 → 选择父场景和 stressor → LLM 提出变异 → Initializer → Verifier → 修复 → 通过的任务进入任务池。
   - 深度 = 父深度 + 1，最大为 4。
3. **覆盖维度：**
   - 写工具、写工具对。
   - stressor。
   - `ExpectedOutcome`，必须包含 refuse 和 transfer。
   - 深度、写动作数。
   - policy_only 规则的命中情况。

   调度策略：优先补未覆盖的格子，其次选通过率高的父场景。这个策略要写进文档。
4. **`structure_signature`** = (写动作工具名的有序序列, stressor 多重集, expected_outcome, 条件分支类型)。

**测试：** 用 MockLLM 跑 2 代，断言深度、父子关系、覆盖率更新、去重和 holdout 过滤都正确。

**回报内容：** 真实 LLM 小规模运行（每代 10 个候选，共 2 代）的通过率和覆盖率变化。

---

## Step 6：正式生成 ≥50 个任务并统计 ⬜

**命令：** `python scripts/synthesize.py --config configs/retail_v0.yaml --out data/runs/<date>/`

**产出：**
- `tasks.jsonl`：τ² Task + TaskMeta。
- `candidates.jsonl`：包括失败的候选。
- `coverage.json`、`llm_usage.json`、`repair_log.jsonl`。

**目标：** 至少 50 个，建议 80–100 个。深度 0–4 都要有；refuse 和 transfer 类至少各 3 个。

**`scripts/stats.py` 生成 `reports/stats.md`，内容包括：**
- 验证通过率：首轮和修复后，分别按深度、stressor、expected_outcome 统计。
- 独特 `structure_signature` 的数量及分布。
- 深度分布、写动作数分布、覆盖率矩阵。
- 失败码分布和修复成功率。这一项对应博客「修复而不是丢弃」的主张。
- 隔离检查：holdout 用户出现 0 次，与官方任务签名的重叠为 0。
- LLM 调用次数、token 数和花费。

**复验：** 另起一个新进程，对 `tasks.jsonl` 重新跑 V1–V8，结果必须 100% 通过。

**人工抽查：** 随机抽 10 个任务写入 `reports/samples.md`。人工阅读不参与通过判定。

**回报内容：** `stats.md` 全文和复验结果。如果没达到 50 个，写明卡在哪个失败码，不要放宽标准凑数。

---

## Step 7（可选）：固定 solver 的难度曲线 ⬜

- 不训练任何模型。
- 用 `run()`（`llm_agent` + `user_simulator`），固定 agent 模型和 user 模型。
  - 博客用的是学生基座模型；有条件的话用 vLLM 部署 Qwen3-8B 最接近博客。
- 每个任务跑 k 次（博客 k=8，预算不足时用 4）。
- 报告：按深度 0–4 和按写动作数分别列出通过率，附 bootstrap 95% CI。只与博客图 3 比较趋势，不比较数值。
- 参照：用同一 solver 跑官方 Retail base 114 题，只报分数。
- 失败轨迹抽查：区分 solver 错误和任务缺陷。任务缺陷记入 Verifier 漏检清单，不事后修改任务。

---

## Step 8：文档收尾 ⬜

- **README：** 从安装到测试、explorer、synthesize、stats、（可选）solver 评测，每一步都给出可直接运行的命令。
- **`docs/REPRODUCTION.md`：** τ² commit、依赖版本、模型、seed、耗时和花费。
- **`docs/DIFFERENCES.md`：** 与 AWS 原方法的差异逐条列出，包括：
  - 所有 `[INDEPENDENT]` 决策。
  - 生成规模与博客约 600 个的差距。
  - 所用模型。
  - 新增的 `execute()` 接口。
  - 合成任务不使用 NL_ASSERTION。
  - V4 是近似检查。
  - 对话层规则无法用状态验证。
  - 只做了 Retail，没有做 SFT 或 RL。
- **结论：** 按成功标准逐条判定是否达成。
