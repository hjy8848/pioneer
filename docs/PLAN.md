# 执行计划：完全复现 AWS 博客

目标是**完全复现**博客 *Growing verifiable training tasks for customer-service agents from the environment alone*：
- 三阶段合成流水线，每个阶段都由 agent 驱动、由环境确认。
- 四个评测环境：τ² Airline、Retail、Telecom，以及 CAR-bench。
- Qwen3-8B 和 Qwen3-30B-A3B-Thinking-2507 的 SFT / RL 训练。
- 博客中的评测表格和图。

每一步交给 Codex 执行一次。通用规则见 `AGENTS.md`；事实依据见 `docs/AUDIT.md`；每个组件与博客的对照见 `docs/FIDELITY.md`。

**状态说明：** ✅ 已完成；⬜ 待做。

## 忠实度原则（所有步骤适用）

1. **博客写了的，照做。** 引用博客原文时标 `[BLOG]`，必要时附上原文短句。
2. **博客没写的，在博客框架内选最合理的做法，** 标 `[INDEPENDENT]`，并在 `docs/FIDELITY.md` 中登记。
3. **每个阶段都由 agent 驱动。**
   - 博客原文："Each is driven by an agent, but no LLM output is trusted until the environment confirms it."
   - Explorer、Evolver、Initializer、Verifier 都是 LLM agent，通过工具接触环境，不能退化成程序模板。
   - 程序只负责两件事：①为 agent 提供工具；②执行环境检查，作为入库的证据。
4. **入库只看环境证据。**
   - 博客原文："an LLM's judgment that a task looks reasonable is never sufficient"。
   - 任务能否入库，取决于程序在真实环境中得到的检查结果。Verifier agent 不能绕过这些结果。
5. **所有 LLM 角色共用同一个 frontier 模型，** 包括 explorer、evolver、initializer、user simulator 和 verifier。
   - 博客："a frontier model for all LLM roles"，但没有说是哪一个。
   - 我们的选择写在 `configs/` 中，并在 FIDELITY 中登记。
6. **本复现的附加约束**（来自项目要求，不是博客）：
   - 评测隔离（holdout）。
   - 合成任务不使用 NL_ASSERTION。
   - 先验证流水线，再进入训练。

   这三条都不与博客冲突，但要在 FIDELITY 中单独标明。

---

## 阶段 A：合成流水线（τ² Retail）

### Step 0：项目骨架、固定 τ² 版本、审查文档 ✅

- `scripts/setup_tau2.{ps1,sh}`：用 blobless + sparse 的方式拉取 τ² v1.0.1，并校验 commit。
- `src/pioneer/env/retail_env.py`：`get_metadata`、`init`、`state`、`query_data`，以及扩展接口 `execute`。
- `src/pioneer/schemas.py`：各阶段共享的数据契约。
- 测试：版本固定、环境层、评测隔离的静态检查。

### Step 1：补全六接口 + 评测隔离 ✅

完成记录（2026-11-10，`ca76f7b`）：
- AUDIT §5 已核实。
- `run()` 接入 τ² 原生 runner；`reward()` 接入原生 evaluator。
- 114 题提取出 53 个 holdout 用户和 114 条任务签名。
- 15 个测试全部通过。

---

### Step 2：Explorer agent（构建 world model）✅

完成记录（2026-11-10）：真实 qwen3.7-max agent 共 32 轮，经工具读取源码、查询环境、自行构造探针并提交 `data/knowledge/retail_world_model.json`。16 个动作、13 条状态探针规则（10 confirmed_by_tool / 2 policy_only / 1 contradicted 已丢弃）、7 条对话规则；41 条独立 sandbox 观察。有效来源响应 32 份，输入 1,874,781 token、输出 25,690 token，客户端估算 $3.456091。最终离线测试 31 项通过；详细运行和费用口径见 `docs/EXPLORER.md`。

**博客原文：** "An explorer agent reads the tool code, the state schema, the seeded database, and the policy document, and compiles them into a structured description of what each action needs, what it changes, and how it can fail."

1. **`src/pioneer/llm/`：** 基于 litellm 的客户端。
   - 支持 tool-use，带磁盘缓存。
   - 记录 token 用量和花费。
   - 提供 MockLLM 供测试使用。
2. **Explorer agent 可用的工具：**
   - `read_source(path)`：只允许读取 τ² Retail 的 `tools.py`、`data_model.py` 和 `policy.md`。
   - `get_metadata()`。
   - `query_data(...)`：浏览初始数据库。
   - `sandbox_execute(tool, args)`：在一次性的全新环境中执行。
   - 官方任务文件不在可读范围内。
3. **输出 `data/knowledge/retail_world_model.json`：**
   - 每个动作的 `{requires, changes, failure_modes}`。
   - policy 规则 `{rule_id, tools, text, check, level: state|conversation}`。其中 `check` 必须是 `query_data` 可执行的结构化谓词。
   - 原子动作清单：7 个 WRITE 工具、可回答的只读问题类型、转人工。
4. **由环境确认** `[BLOG 原则 / INDEPENDENT 做法]`：
   - agent 声称的每条前置条件和每种失败方式，都要提出一个探针（一个满足条件的实例、一个违反条件的实例），由程序在全新环境中执行。
   - 按实际结果标注：
     - `confirmed_by_tool`：工具会拒绝。
     - `policy_only`：工具放行，但 policy 禁止。
     - `contradicted`：与环境行为矛盾，丢弃。
   - 未经确认的条目不得进入 world model。
   - 回归测试至少覆盖 AUDIT §4 中的 policy_only 规则 1、2，以及「修改商品后再取消订单会被工具拒绝」。
5. **测试：** 用 MockLLM 跑通 agent 循环和探针确认。

**回报内容：** 动作和规则的数量；confirmed / policy_only / contradicted 的分布；LLM 调用次数和花费；所用模型。

---

### Step 2.5：Step 2 review 跟进项 ✅

完成记录：公开 API 仅导出 ExplorerAgent；模板及预制探针移到 tests/helpers。35 项测试通过；api_base 从 LITELLM_API_BASE 读取，不改写 Git 历史。补探索 12 次模型调用，估算 $1.103133；新增 5 confirmed_by_tool / 0 policy_only，另 1 条正例失效的探针丢弃。段落映射覆盖 15/60（25%）→20/60（33.3%）；余下 40 段的原因保留在主文件 policy_coverage，不能将其视为所有细粒度约束都已确认。轨迹和来源请求移到 retail_explorer_trace.json。详情见 docs/EXPLORER.md。

以下四项来自对 `344c1f1` 的 review。

1. **包的默认入口不能是模板编译器（违反 AGENTS 规则 0）。**
   - 现状：`pioneer.explorer.build_knowledge` 走的是 `rules.py` 里手写的 `rule_catalog()` 加预制探针，而且它是包对外导出的函数。
   - 改法：
     - `pioneer.explorer` 改为导出 `ExplorerAgent`，包的公开 API 只保留 agent 路径。
     - `build_knowledge` 和 `rule_catalog` 移到 `tests/helpers/`，或者重命名为 `_legacy_build_knowledge`、`_legacy_rule_catalog`，并在 docstring 里写明「仅用于离线回归，禁止在流水线中调用」。
     - 谓词检查函数 `check_rule` 是工具，可以继续留在 `src/`。
   - 增加一个静态测试：`src/` 中除测试辅助代码外，任何地方都不得引用 `rule_catalog` 或 `_legacy_*`。
2. **公开仓库中不能写内网地址。**
   - review 时的现状：`configs/retail.yaml` 里写死了内网 api_base（当前工作树不再保留该地址）。
   - 改法：
     - `api_base` 改为从环境变量读取（例如 `LITELLM_API_BASE`），和 key 的处理方式一样。
     - 配置文件里只保留模型名；`.env.example` 中补上这个变量名。
     - 缓存键可以继续包含 api_base，但记录到产物（world model 的 `llm_config` 等）中的值要脱敏，只保留主机名的哈希，或者写成 `"<env:LITELLM_API_BASE>"`。
   - 注意：旧提交的历史里仍然有这个地址。如果需要彻底清除，由用户决定是否改写历史；Codex 不要擅自 force push。
3. **world model 的 policy 覆盖检查** `[INDEPENDENT]`，要在 Step 5 之前完成。
   - 现状：12 条状态规则几乎都是订单状态检查。下面这些工具会拦、policy 也写明了的约束都没有收进来：
     - 礼品卡余额不足。
     - 退款只能退到原支付方式或礼品卡。
     - 改支付方式时只能有一条支付记录，且新方式必须与原来不同。
     - 换货或改商品必须是同一商品，且新 item 有货。
   - 这些违规在 E1 重放时会被发现，所以不影响入库判定；但 Evolver 的 `POLICY_PUSHBACK` 类题要依据 world model 来出，规则缺得越多，这类题就越少。
   - 改法：
     - 程序把 `policy.md` 按段落切开，检查每一段是否映射到至少一条状态规则或对话规则。
     - 把未覆盖的段落作为「缺口」交给 Explorer agent，让它继续探索一轮；新规则同样要经过探针确认才能入库。
     - 报告最终的覆盖率；仍未覆盖的段落要列出原因。
     - 手写的 `_legacy_rule_catalog` 只能用来离线对比召回率，不能作为 agent 的输入。
4. **world model 文件拆分。**
   - 把 `agent_trace` 和 `llm_requests` 移到单独的 `data/knowledge/retail_explorer_trace.json`。
   - 主文件只保留下游会用到的 actions、rules、atomic_actions、observations 等字段，方便 review 和 diff。

**回报内容：**
- 改动后包的公开 API。
- 静态测试的结果。
- api_base 的读取方式。
- policy 段落覆盖率：补探索前和补探索后各多少。
- 新增规则的 confirmed / policy_only 分布。
- 新增的 LLM 花费。

---

### Step 3：Verifier（环境证据 + verifier agent）✅

完成记录：已实现 E1–E9、工具驱动 VerifierAgent、READ 绑定数据契约及 CLI。12 个失败码均有手工反例；全量离线测试 60 passed，1 条上游弃用警告。真实 qwen3.7-max 写/只读样例各 k=2，修正结束条件后的写样例和只读样例均 reward=1/1。初始写样例因 user simulator 提前 STOP 得 0/0，正确拒绝并保留失败轨迹。仅 E8 单题估算：写 $0.228189、只读 $0.274423；含 agent 诊断和原始失败尝试的本步骤总估算 $1.143666。真实轨迹由最终程序再次评分，结果一致且未新增模型调用。详见 docs/VERIFIER.md 与 reports/step3_validation.json。

**博客原文：** "A task joins the dataset only once we can establish that its reference outcome is actually reachable and that it is the outcome the policy calls for." Verifier 是博客列出的 LLM 角色之一。

**3a. 环境证据工具集**（程序实现，结果确定；任务入库的依据只有这些）：

- **E1 可达性（重放）：** 在全新环境中用 `execute()` 逐步执行参考动作，每一步都必须 `ok=True`。这一项弥补了 evaluator 只打 warning 的缺陷。
- **E2 policy 一致：** 每个写动作执行前，在当时的状态上检查 world model 中已确认的规则（`confirmed` 和 `policy_only`），全部必须满足。
- **E3 状态效果：**
  - 写任务：最终 hash 不等于初始 hash，且 DB diff 只落在参考动作涉及的实体上。
  - 无写动作的任务：`actions` 必须是 `[]`，并且 `communicate_info` 可以由 `query_data` 算出。
- **E4 信息充分：** 参考动作的每个参数，都要能从 `known_info` 出发，经 agent 可用的 READ 工具链唯一确定。同时检查没有泄露 item_id 或 payment id。
  - **身份必须唯一（Step 1 review）：** v1.0.1 的 `find_user_id_by_name_zip` 遇到同名同邮编时，会静默返回第一个匹配。如果用「姓名 + 邮编」认证，必须在 DB 中只匹配到一个用户；邮箱同样要唯一。
- **E5 拒绝类：** 被禁止的请求在沙盒中要么被工具拒绝，要么违反 E2。参考结果为 `[]` 或只执行合规部分；应转人工的，参考结果包含 transfer。
- **E6 歧义：** 仅凭初始描述得到的候选多于 1 个，加上澄清信息后正好 1 个。
- **E7 官方评分：** 用参考动作构造 SimulationRun，经 `reward()` 评分，结果必须为 1。
- **E8 端到端可达（必做）：** 博客把 user simulator 列为合成流水线的 LLM 角色之一，`run()` 是六接口之一。
  - 有写动作或 transfer 的任务：用 `llm_agent_gt` + user simulator 跑 k 次（默认 k=2），至少一次 reward=1。
  - `actions=[]` 的任务：`llm_agent_gt` 无法运行（见 AUDIT §5），改用 `llm_agent` + user simulator 跑 k 次，至少一次 reward=1。
  - 这里的 reward 是环境 reward。具体做法为 `[INDEPENDENT]`。
- **E9 去重与隔离**（附加约束）：实例签名不重复；不涉及 holdout 用户；与官方任务签名无重叠。结构签名只用于统计，不作为拒绝条件，原因见 Step 3.5 第 1 项（这句原本写成「结构签名和实例签名都不重复」，是 PLAN 的错误）。

**3b. Verifier agent** `[BLOG 角色 / INDEPENDENT 做法]`：
- 输入：候选任务和 world model。
- 职责：调用 3a 的工具收集证据，对失败做诊断，输出 `VerifierReport`。
- 诊断必须引用具体证据：哪一步、哪条规则、哪个参数。
- `passed` 由程序计算：所有适用的 E 项都通过才为 true。agent 不能覆盖这个结果。

**测试：**
- `FailureCode` 中的每个失败码都至少有一个手工构造的反例，只能用自己构造的数据。
- 验证 agent 无法让未通过证据检查的任务入库。
- **补强静态隔离检查**（Step 2.5 review）：现有检查只匹配 `rule_catalog` 和 `_legacy_*` 这两种名字，但 helpers 模块名是 `legacy_build`、`legacy_rules`，而且 `import tests.helpers...` 这种写法完全检查不到。新增一条：`src/` 下任何文件都不得 `import tests` 或 `from tests... import`，`ast.Import` 和 `ast.ImportFrom` 都要检查。

**回报内容：** 每个失败码的测试结果；E8 的单题花费。

---

### Step 3.5：Step 3 review 跟进项 ✅

完成记录：E9 不再按结构签名拒绝，seen_structure 仅写入统计；实例签名由 actions 名称/参数、initial_state、stressor 集合、expected_outcome、communicate_info 计算，不含剧本文字。已加入同结构不同实例可通过、同结果改写措辞仍判重的测试。Initializer 必含清单已有固定的等待完成句；不采用额外 ART checklist。本步未调用模型，新增花费 $0。

以下几项来自对 `0053e67` 的 review。

1. **E9 不能因为结构签名重复而拒绝任务。必须修改。**
   - 原因：结构签名只看「写动作序列 + stressor + 结果类型」，同一种结构本来就会有很多道不同的题。例如深度 0 每个原子动作要出 3 题，这 3 题的结构签名完全相同。如果按结构去重，每个原子动作只能入库 1 题，整个题库的上限会卡在「不同结构的数量」（大约几十个），不可能达到约 600 个。
   - 这条规则的错误来自 PLAN 原文，不是 Codex 的实现问题。
   - 改法：
     - 在 `EvidenceEngine` 中，`duplicate` 只由「实例签名重复」或「与官方任务签名重叠」决定。
     - `seen_structure` 改为只用于统计，或者直接删除这个参数。
     - 修改或补充测试：两道结构相同、实例不同的任务都应该能通过 E9。
2. **实例签名不能包含用户剧本的原文。**
   - 原因：现在的实例签名里有 `user_scenario` 全文，同一道题换个说法就会算作新实例，近似重复的题能反复入库。
   - 改法：实例签名改为由「参考 actions（名称、参数）+ initial_state + stressor 集合 + expected_outcome + communicate_info」计算，不包含自然语言措辞。
   - 补一个测试：同一组参考结果配两种不同措辞，第二道题应该被判为 DUPLICATE。
3. **防止用户模拟器过早结束对话**（写给 Step 4 的 Initializer）。
   - 现象：Step 3 第一个真实样例中，用户模拟器在说 yes 时就发出了 STOP，写操作还没执行，两次对话都得 0 分。Verifier 的拒绝是正确的，但以后这种情况会反复出现。
   - 改法：
     - Initializer 写的 `task_instructions` 必须明确要求用户：等 agent 告知操作已完成后再结束对话。把这一点加入 Initializer 的「必须包含」检查清单。
     - 可选：ART 官方示例（`examples/strands_taubench_agent/constants.py`）在 user system prompt 后面附加了一份 "Verification Checklist"，用来稳定用户模拟器的行为。如果采用，标为 `[OFFICIAL]`（ART），并登记到 FIDELITY；用户模拟器的 prompt 要通过 bridge 注入，不能修改 τ² 源码。
4. **花费跟踪**（不需要改代码，但每一步都要回报）。
   - Step 3 实测：每道入库任务的验证约需 $0.35（E8 约 $0.23–0.27，Verifier agent 约 $0.12）。按被拒绝和返工候选为 2–3 倍估算，每个领域 600 题约需 $400–600，四个领域约 $2,000，还不包括生成本身的花费。
   - 从 Step 4 起，每次运行都要在回报中写出：每道入库任务的平均花费，包括生成、验证和返工的拆分；以及按当前速率推算到 600 题的总花费。

**回报内容：** E9 修改后的测试结果；新实例签名的定义；Initializer 的「必须包含」清单中是否已加入「等待完成后再结束对话」；是否采用了 ART 的 checklist。

---

### Step 4：Initializer agent + 深度 0 + rework ⬜

执行记录：Initializer 工具循环、返工队列、批次恢复和费用统计已实现，65 项测试通过。按用户要求提前结束本批并推送：11 个任务通过完整 E1–E9（含真实 E8），覆盖 6/13 类；其余 7 类未完成，故本步全类别验收仍未完成。逐类数量、费用和可加载任务见 [INITIALIZER.md](INITIALIZER.md) 与 [运行报告](../reports/initializer_partial.json)。

**博客原文：**
- "A task initializer turns each scenario into a concrete task: a user goal, plus a reference outcome tied to specific values in the environment state."
- `query_data()` 的用途是 "Used to ground references in real values."
- "Tasks that fail are not thrown away. They are diagnosed and reworked until they pass."

1. **Initializer agent 可用的工具：**
   - `query_data`：寻找满足场景要求的真实实体，排除 holdout 用户。
   - `sandbox_execute`：在一次性环境中试执行。
   - world model。
2. **agent 的输出：**
   - `user_scenario.instructions`：`reason_for_call`、`known_info`、`unknown_info`、`task_instructions`。
   - `evaluation_criteria`：
     - `actions`：写动作列表，或 `[]`。
     - `communicate_info`。
     - `reward_basis`：只能是 `[DB]` 或 `[DB, COMMUNICATE]`，且必须按这个顺序输出（Step 1 review）。
3. **参考结果由 agent 构造，正确性由 Verifier 的环境证据确认。**
   - 不使用程序模板生成参考动作。程序模板会把题型限制在模板覆盖的范围内，这违背「agent 驱动」。
4. **rework 循环：**
   - Verifier 的诊断交回 Initializer agent 修改，必要时退回 Evolver 重写场景。
   - 博客写的是 "until they pass"，没有轮数上限。我们设一个预算（默认 5 轮），超出预算的任务进入 `rework_queue`，在后续批次中继续修，而不是丢弃。
   - 最终仍未修好的，如实计数并报告。
5. **深度 0：** 从**每个**原子动作出发（博客："We start from every atomic action"），每个原子动作生成 n 个任务（默认 n=3）。

**验收：**
- 每个原子动作至少有 1 个任务通过。
- 任务能被 τ² 加载。
- 用 `run()` 跑通其中 1 个任务的完整对话。

**回报内容：** 每个原子动作的候选数、首轮通过数、rework 后通过数；失败码分布；rework_queue 的长度。

---

### Step 4.5：Step 4 review 跟进项，并补完 Step 4 ⬜

以下几项来自对 `f85a82e` 的 review。按顺序执行：先完成第 1、2、3 项，再补齐剩余的 7 类原子动作（第 4 项）。如果先补题，补出来的题仍然依赖手写的领域规则，还得重做。

1. **Initializer 的 prompt 不能写死领域规则（违反博客原文和 AGENTS 规则 0）。**
   - 博客原文："The recipe never needs to know what your domain is. It talks to the environment through six functions"。
   - 现状：`initializer/agent.py` 的 `SYSTEM` 中写死了 Retail 的业务知识，例如：
     - 「换货或改商品时选同一商品的有货 variant」。
     - 「礼品卡余额必须够付差价」。
     - 「退货必须退到原支付方式或礼品卡」。
     - 「订单 ID 和商品 ID 可以出现在用户剧本里」。
     - 「用卡号后四位区分支付方式」。

     这些属于人手写的领域知识，绕过了 world model。到 Step 8、9 换领域时，还得为每个领域重写一遍。
   - 改法：
     - `SYSTEM` 只保留与领域无关的内容：agent 的角色、工具协议、Task / TaskMeta 的格式、证据表达式语法、「先查环境再提交」「必须包含等待完成句」等通用要求。
     - 业务约束一律由 payload 提供，来源只能是 `get_metadata()`（policy、工具 schema）和 world model（已确认的 rules、actions 的 requires / failure_modes）。
     - 「哪些 ID 不能出现在用户剧本里」不能手写清单，要从 schema 和工具签名推导。例如：凡是 agent 必须通过 READ 工具查到的内部 ID 参数，都视为禁止泄露。在 FIDELITY 中登记推导规则。
     - 同样检查 Explorer：`explorer/agent.py` 中 "Retail Explorer agent" 的提法，以及写死的 `collections` 枚举（users / orders / products / variants），都要改为从环境的 metadata 获取。Explorer 已经产出的 world model 不需要重跑。
     - 增加一个静态测试：`src/pioneer/{explorer,initializer,evolver,verifier}` 中，prompt 字符串不得包含领域名词，例如 `retail`、`gift card`、`order`、`variant`、`paypal`。领域名词只能出现在 `env/` 适配层和 world model 数据中。如果确实需要例外，在测试中列出白名单并说明理由。
2. **记录预检失败，避免首轮通过率虚高。**
   - 现状：Initializer 在 `submit_candidate` 时先跑 E1–E7、E9 预检，不通过就在同一次生成里修改，所以报告显示 11/11 首轮通过、返工 0 次。真实的修改过程没有体现在统计里。
   - 改法：
     - 记录每次 `submit_candidate` 的结果：预检是否通过，以及失败码。
     - 报告中增加：每道入库题平均经历的预检失败次数；预检失败码的分布；「一次提交就通过预检」的比例。
     - Step 6 统计「首轮 / 返工后通过率」时，要同时给出两种口径：只算 Verifier 的外层返工，以及把预检修改也算作返工。
     - 预检保留，它能节省 E8 的花费。在 FIDELITY 中登记：Initializer 可以调用确定性证据工具做自检，属于 `[INDEPENDENT]`。
3. **提高实体多样性。**
   - 现状：11 道题里，同一用户的同一订单被多道题重复使用（例如 #W2974929 同时用于「取消」和「改地址」；#W2631563 出了两道几乎相同的改地址题）。原因是 agent 习惯直接用查询返回的前几个结果。
   - 改法：
     - 程序根据场景的实体约束，用 `query_data` 查出全部符合条件的实体，按 seed 随机抽取一个候选池（例如 20 个）交给 agent。agent 仍然可以自己查询，但必须优先从候选池中选择。
     - 全局记录每个用户和订单已被使用的次数。同一个 (原子动作, 订单) 组合不得出现在两道题里；同一个用户在同一批中最多出现 k 次（默认 k=2）。
     - 这个约束交给 Step 5 的追踪器继续沿用。在 FIDELITY 中登记为 `[INDEPENDENT]`。
4. **补齐剩余 7 类原子动作。**
   - 退货、转人工，以及 5 类查询（用户、订单、商品、item、商品类型）目前候选数为 0。它们还没有被执行过，不能据此判断这几类题难不难出。
   - 第 1–3 项完成后，按每类 3 题补齐。查询类和转人工类会首次在真实模型上走 COMMUNICATE 和 E5 的路径，要特别留意这两类的失败码。
   - 已入库的 11 题保留，不需要重做；但要用第 3 项的规则检查一遍。如果有违反（例如同一 (原子动作, 订单) 出现两次），按规则只保留其中一题，并在报告中写明。
5. **小问题：** 把 `data/knowledge/depth0_partial.jsonl` 移到 `data/runs/` 下。`data/knowledge/` 只放 world model。

**回报内容：**
- 改动后 Initializer 和 Explorer 的 `SYSTEM` 全文（应当不含领域名词）。
- 领域名词静态测试的结果。
- 13 类原子动作各自的候选数和通过数。
- 预检失败的统计。
- 实体重复使用的统计。
- 每道入库题的平均花费，拆成生成、验证、返工三部分；以及推算到 600 题的总花费。

---

### Step 5：Evolver agent + 全局覆盖追踪器 ⬜

**博客原文：** "Each generation takes existing scenarios and applies evolution stressors to produce harder descendants: more actions chained together, more entities involved, and more of the interaction-level and reasoning-level difficulty a real user brings, such as pushing back on policy, asking for something conditional, or under-specifying a request. A global tracker watches what has already been covered and steers each generation toward what hasn't, including outcomes where the correct behavior is to say no."

1. **stressor**（对应 `schemas.Stressor`）：
   - `[BLOG]` 的类别：
     - `CHAIN`：更多动作串联。
     - `MULTI_ENTITY`：更多实体。
     - 交互层：`POLICY_PUSHBACK`（对 policy 讨价还价）、`CONDITIONAL`（条件请求）、`UNDERSPECIFIED`（需求不明确）。
     - `REASONING`：推理层难度。
   - 各类别的细则是 `[INDEPENDENT]`。
2. **Evolver agent：**
   - 输入：已通过验证的父场景、追踪器给出的覆盖缺口、world model。
   - 输出：后代场景。
   - 后代场景交给 Initializer，再经 Verifier 验证，最后入库或进入 rework。
   - 深度 = 父深度 + 1。博客图 3 显示到深度 4，因此最大深度设为 4。
3. **全局追踪器**（程序实现，`[INDEPENDENT]` 维度）：
   - 写工具、写工具对。
   - stressor。
   - 结果类型（`ExpectedOutcome`），必须包含 refuse 和 transfer。
   - 深度、写动作数。
   - policy 规则的命中情况。

   追踪器把缺口写进 Evolver 的输入，引导下一代出题方向。
   - **段落内子条款的缺口**（Step 2.5 review）：policy 覆盖是按段落统计的，一段里可能有好几个约束。目前已知两个子条款没有对应规则：「换货或改商品必须是同一商品类型」（例如不能把衬衫换成鞋子）和「新 item 必须有货」。这两条都由工具执行，所以入库判定不受影响（E1 和 E5 接受工具拒绝作为证据）。但 Evolver 是照着 world model 出题的，没有这两条，就容易漏掉对应的 `POLICY_PUSHBACK` 类题。
   - 做法：让追踪器把「policy 中有、但 world model 中没有对应规则的子条款」也列为缺口。如果第一次小规模运行后，这两类题的数量为 0，就先让 Explorer agent 针对这两条做一次补探索：自己构造正反例探针，确认通过后才加入 world model。不要手写规则。
4. **`structure_signature`：** 用于统计和去重。

**测试：** 用 MockLLM 跑 2 代。

**回报内容：** 真实 LLM 小规模运行（每代 10 个候选，共 2 代）的通过率和覆盖率变化。

---

### Step 6：Retail 正式合成（先到 50 个，再扩到约 600 个）⬜

1. **里程碑：先到 50 个。**
   - 跑到 ≥50 个已验证任务，暂停，输出 `reports/retail_stats.md`，内容包括：
     - 首轮和 rework 后的通过率。
     - 独特 `structure_signature` 的数量。
     - 深度分布、写动作数分布。
     - 失败原因分布。
     - 隔离检查结果。
     - 花费。
   - 另起一个新进程，对已入库任务重跑 E1–E7、E9，结果必须 100% 通过。
   - 这个检查点由人工确认后，再继续扩大规模。
2. **扩到约 600 个已验证任务**（博客："roughly 600 verified tasks per domain"）：
   - 深度 0–4 都要有。
   - 写动作数要拉开分布。博客图 3 的横轴从 0 到 13 个写动作。
3. **命令：** `python scripts/synthesize.py --config configs/retail.yaml --out data/runs/retail_<date>/`

**回报内容：** 两个检查点的统计；最终达到的任务数。没达到就如实写明。

---

### Step 7：难度曲线（复现图 3）⬜

**博客原文：** "Difficulty is the student base model's mean zero-shot pass rate over eight parallel runs"。分别按写动作数和按演化深度统计。

- 用 vLLM 部署学生基座模型作为 task agent，user simulator 用 frontier 模型；每个任务跑 8 次，计算平均通过率。
- 博客没说图 3 用的是哪个学生模型，所以 Qwen3-8B 和 Qwen3-30B-A3B-Thinking-2507 两个都跑，在 FIDELITY 中登记。
- 输出两张图：通过率对写动作数、通过率对深度，并与博客的数值对照（只读 98%，13 个写动作约 14%；深度 0 为 97%，深度 4 为 42%）。

---

## 阶段 B：其余评测环境

### Step 8：τ² Airline 和 Telecom ⬜

- 为两个 domain 分别实现六接口。
  - Telecom 是双控环境：有 user tools 和 user DB，reward 要比较两个 DB 的 hash。
  - ART 示例中 Telecom 使用的是 `get_environment_workflow_policy`。博客没说用哪个版本的 policy，在 FIDELITY 中登记。
- 为每个 domain 构建 holdout：
  - Airline 使用 v1.0.1 的 base split（博客写的是 50 题）。
  - Telecom 的 base split 博客写的是 114 题。
  - 先核实这两个数字与 split 文件一致。
- 复用阶段 A 的流水线，只更换环境和 world model；每个 domain 合成约 600 个任务。
- 对每个 domain 都重复 Step 6 的检查点：先到 50 个，人工确认后再扩大规模。

### Step 9：CAR-bench ⬜

- 固定 `CAR-bench/car-bench` 的 tag `v1.0.0`（commit `60e84ec`），实现六接口。
  - 环境数据托管在 HuggingFace，首次运行时下载。
- 评测集为全部三类：Base 100 题、Disambiguation 56 题、Hallucination 98 题。这三类正好是官方 train + test 的总和，因此所有官方任务都是 holdout，生成代码不得读取。
- Hallucination 类任务需要「工具或权限被刻意移除」的场景。在环境层支持按场景屏蔽工具，具体做法为 `[INDEPENDENT]`。
- 注意：CAR-bench 官方评分中有一部分由 LLM 判定（policy evaluator）。这一部分只在 Step 13 评测时按官方方式使用；合成任务的入库仍然只看环境证据。
- 合成约 600 个任务，检查点同 Step 6。

---

## 阶段 C：训练与评测

**启动任何 GPU / NPU 任务之前，都必须先在小规模上完整预演一遍运行路径。**

### Step 10：SFT 数据与 RL 参考划分 ⬜

- **博客原文：** "split between SFT trajectories and RL references by evolution depth"。
- 博客没有给出具体的划分规则。默认按深度划分：较浅的深度用于 SFT，较深的深度留给 RL；阈值可配置，记为 `[INDEPENDENT]`。
- SFT 轨迹：用 frontier 模型作为 task agent，加上 user simulator，在 SFT 部分的任务上运行，只保留环境 reward=1 的轨迹。
- 输出训练格式的数据，并统计每个 domain 的条数。

### Step 11：SFT ⬜

- 学生模型：`Qwen3-8B` 和 `Qwen3-30B-A3B-Thinking-2507`。
- 博客没说是每个领域单独训练一个模型，还是所有领域混合训练。先在 FIDELITY 中登记这个问题，默认混合训练，并保留按领域单独训练的开关。
- 产出 checkpoint，然后在 Step 13 的评测集上跑一次 zero-shot 和 SFT 的对比。

### Step 12：RL ⬜

博客原文给出的设置：
- 算法：GRPO with DAPO-style refinements。
- 每个任务在真实环境中做 16 次 rollout。
- full parameter。
- lr 1e-6，batch size 8。
- 只对 30B 模型做 RL（博客表 1）。

实现方式：
- 用 ART 的训练后端（verl backend，或 `backends/sagemaker/train_dapo.py`）。
- rollout 应用参考 `examples/strands_taubench_agent`。
- reward 取环境 reward（`TauBenchReward` 或本仓库的 `reward()`）。
- 起点是 Step 11 的 SFT checkpoint；训练任务是 Step 10 划给 RL 的那部分。

### Step 13：评测（复现表 1、表 2、图 4）⬜

- **τ² 评测集：** v1.0.1 的 base split，Airline 50 题、Retail 114 题、Telecom 114 题。
- **CAR-bench 评测集：** Base、Disambiguation、Hallucination 三类。
- **要评测的模型：**
  - Qwen3-8B：zero-shot、SFT。
  - Qwen3-30B-A3B：zero-shot、SFT、RL。
  - 参照模型：博客里的 Frontier A / B 没有具名，无法复现；改用我们自选的 frontier 模型，并如实标注。
- **评测指标：** 博客没写。默认采用平均成功率，即 τ² 的 pass^1，记为 `[INDEPENDENT]`。
- **图 4：** Telecom 114 题每题跑 5 次，按「全部成功 / 时成时败 / 全部失败」分桶，分别统计 zero-shot、SFT、RL 三个阶段。

### Step 14：收尾 ⬜

- **`docs/REPRODUCTION.md`：** 所有 commit、模型、seed、数据量、耗时和花费。
- **`docs/FIDELITY.md` 定稿：** 每个组件标注 `[BLOG]`、`[INDEPENDENT]`、附加约束或「无法复现」，并写明理由。
- **结果对照表：** 我们的数字与博客表 1、表 2、图 3、图 4 逐项对照，写明差异和可能的原因。
