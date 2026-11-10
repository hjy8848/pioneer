# 审查记录：官方实现 vs 需独立实现

审查日期：2026-11-10。下面每条结论都注明了来源，大多是读源码得到的。

## 1. 参考资料与版本

| 资料 | 版本 / 位置 |
|---|---|
| AWS 博客 *Growing verifiable training tasks for customer-service agents from the environment alone* | 2026-09-15；源文件 `awslabs/agentcore-rl-toolkit:docs/site/src/content/docs/blog/data-synthesis-v0.mdx` |
| ART（agentcore-rl-toolkit） | main 分支 `fe325f82e0e2`（2026-11-06），连同全部远程分支都检查过 |
| τ²-bench | 固定为 tag `v1.0.1`，commit `fc0055dc4e0a316c3f83133267fbd6faaa770992`（2026-07-16） |

**为什么固定 v1.0.1：**

- 博客评测用的是「label-corrected release（taubench.com/blog/tau3-task-fixes）的 `base` split，Retail 114 题」。
- v1.0.1 的 `data/tau2/domains/retail/split_tasks.json` 中，`base` 正好是 114 题（train 74 + test 40）。
- main 分支已经是 τ-bench 2.0，README 写明它与 1.x 不可比。

## 2. 开源情况

| 博客组件 | 是否开源 | 本项目的做法 |
|---|---|---|
| Explorer / Evolver / Initializer / Verifier | **未开源**。博客原文是 "We will release the task synthesis recipe … soon"；ART main 与所有分支都搜不到相关代码 | 全部 `[INDEPENDENT]` 实现 |
| 六接口 `get_metadata/init/run/state/query_data/reward` | 只公开了函数名和一句话说明，没有签名 | 签名为 `[INDEPENDENT]`，语义为 `[BLOG]` |
| 环境、工具、DB hash | 已开源（τ²） | `[OFFICIAL]` 复用，不改源码 |
| 评分（DB hash / COMMUNICATE） | 已开源（τ² evaluator） | `[OFFICIAL]` 复用 |
| user simulator 与 agent 的对话编排 | τ² runner 已开源；ART 的 `examples/strands_taubench_agent/rl_app.py` 是 Strands + Bedrock 版本（user sim 为 Claude Opus 4.6） | 本地使用 τ² 原生 runner；ART 版本只作参考 |
| ART 的 `TauBenchReward` | 已开源（`examples/strands_taubench_agent/reward.py`） | 作为 reward 实现的对照参考 |
| 拿到参考动作的求解 agent | τ² `LLMGTAgent`（注册名 `llm_agent_gt`） | 可用于端到端可达性检查（V9） |

## 3. 博客公开的方法细节（`[BLOG]`）

- **Stage 1：** explorer agent 读取工具代码、state schema、初始数据库和 policy，编译出每个动作「需要什么、改变什么、怎样失败」。
- **Stage 2：**
  - 从每个原子动作出发，逐代施加 *evolution stressors*。
  - stressor 包括：更多动作串联、更多实体、交互层难度（用户对 policy 讨价还价、条件请求、需求不明确）、推理层难度。
  - 全局追踪器引导每一代去补未覆盖的结果，**包括正确行为是拒绝的情况**。
- **Stage 3：**
  - initializer 生成「用户目标 + 绑定到具体状态值的参考结果」。
  - 只有当环境证明参考结果**可达**、并且**正是 policy 要求的结果**时，任务才入库。LLM 认为合理不算数。
  - 失败的任务要诊断并修复，不能只做过滤丢弃。
- **`run()`：** 编排 user simulator 与 task agent，返回多轮轨迹。
- **`query_data()`：** 返回满足谓词的实体，用于把参考结果落到真实值上。
- **规模与度量：**
  - 每个领域约 600 个已验证任务。
  - 难度 = 学生基座模型 zero-shot 跑 8 次的平均通过率。
  - 按写动作数：只读 98%，13 个写动作约 14%。
  - 按演化深度：深度 0 为 97%，深度 4 为 42%。
- **LLM 角色：** explorer、evolver、initializer、user simulator、verifier 全部使用同一个未具名的 frontier 模型。

**博客没有公开的内容（因此都是 `[INDEPENDENT]`）：** prompt、所用模型、stressor 的精确定义与调度、覆盖率定义、验证与修复的具体流程、任务格式、如何判定「policy 要求的结果」。

## 4. τ² v1.0.1 Retail 的关键事实（读源码得到）

### 工具（`src/tau2/domains/retail/tools.py`）

- **WRITE（7 个）：** `cancel_pending_order`、`exchange_delivered_order_items`、`modify_pending_order_address`、`modify_pending_order_items`、`modify_pending_order_payment`、`modify_user_address`、`return_delivered_order_items`
- **READ（7 个）：** `find_user_id_by_email`、`find_user_id_by_name_zip`、`get_order_details`、`get_product_details`、`get_item_details`、`get_user_details`、`list_all_product_types`
- **GENERIC（2 个）：** `calculate`、`transfer_to_human_agents`

### 工具放行、但 policy 禁止的情况（Verifier 必须用程序检查）

1. `modify_pending_order_address` 和 `modify_pending_order_payment` 用 `_is_pending_order`（判断条件是 `"pending" in status`），因此其状态检查会放行 `pending (item modified)`，而 policy 禁止商品修改后的进一步修改。地址修改可在真实 modify_items 后复现，见 `tests/test_retail_env.py::test_policy_only_gap_is_not_enforced_by_tool`。Step 2 补充：modify_items 会新增支付记录，因此随后 modify_payment 可能被“仅一条支付记录”检查拒绝；支付工具的状态缺口通过保留单条支付记录的沙盒探针单独复现，不能推断实际链一定放行。
2. `exchange_delivered_order_items` 不检查新旧 item 是否相同（`modify_pending_order_items` 会检查），而 policy 要求换成不同的选项。
3. `modify_user_address` 没有任何校验。
4. 对话层规则：先认证、写操作前取得明确确认、一次对话只服务一个用户、只在超出能力范围时才转人工。这些都无法通过 DB 状态验证。

### 工具会强制执行的规则

- cancel、modify_items、exchange、return 要求严格的状态匹配。
- 取消原因只能是两个枚举值之一。
- 礼品卡余额不足会被拒绝。
- return 的退款方式只能是原支付方式或礼品卡。
- modify_payment 要求只有一条 payment 记录，且新支付方式与原来不同。

### 两个 bug，以及 evaluator 的两个坑

- **`modify_pending_order_items` 的价格 bug：** 所有被修改的 item 都写入循环中最后一个 variant 的价格。参考结果一律以真实环境重放的结果为准，不能自己计算。
- **参考动作抛异常只打 warning（`evaluator_env.py`）：** 异常会被吞掉，参考状态就退化为初始状态。Verifier 必须自己确认每一步 `ok=True`。
- **`actions=None` 直接判为 reward 1：** 写在 `evaluator_env.py` 的 "No expected actions" 分支里。只读、拒绝、转人工类任务必须写成 `actions=[]`，「DB 不应有变化」才会被真正检查。
- **官方 112/114 题的 `reward_basis = [DB, NL_ASSERTION]`，NL_ASSERTION 由 LLM 判定。** 合成任务只允许 `[DB]` 或 `[DB, COMMUNICATE]`。

### DB 统计（`db.json`）

- 50 个商品，591 个 variant，其中 379 个有货。
- 500 个用户，其中 62 个没有订单。
- 1000 个订单：pending 423、delivered 373、processed 102、cancelled 102。
- 支付方式：credit_card 239、gift_card 234、paypal 222。

### 加载自定义任务

- CLI 只有 `--task-set-name`（只能选已注册的任务集）、`--task-split-name` 和 `--task-ids`，**没有加载任意任务文件的参数**。
- 两种做法：
  1. 在自己的入口里调用 `tau2.registry.registry.register_tasks(loader, "retail_synth")`，然后走 runner。
  2. 直接调用 `tau2.runner` 的 `run_single_task(TextRunConfig(...), task)`。已核实并通过离线集成测试，见 §5。
- 数据目录可以用 `TAU2_DATA_DIR` 指定。

## 5. Step 1 核实结果（2026-11-10）

### Retail 版本差异 `[OFFICIAL]`

对比固定 v1.0.1 与 v2.0.0（`989d3285ae669e49503324bdb9981bf14fccedeb`）：

```bash
git -C third_party/tau2-bench fetch --depth 1 origin tag v2.0.0
git -C third_party/tau2-bench diff v1.0.1 v2.0.0 -- src/tau2/domains/retail data/tau2/domains/retail/db.json data/tau2/domains/retail/policy.md
```

- `tools.py`：增加 regional_settings 和区域化工具描述；身份查询改用 `fold_for_match`，并在多匹配时抛出 `User lookup is ambiguous`。
- `environment.py`：增加 policy 和 regional_settings 参数；传入 DB 时先深拷贝。
- `data_model.py`、`db.json`、`policy.md` 无差异。整个 Retail 源码目录只有上述两个文件变化（60 行新增、9 行删除），不能据此推断两个版本整体评测可比。
- 本项目继续使用 v1.0.1，没有修改 τ² 受版本控制的源码。

### runner 和评分 `[OFFICIAL]`

`run_single_task` 定义于 `src/tau2/runner/batch.py:341`，从 `tau2.runner` 导出；`TextRunConfig` 定义于 `src/tau2/data_model/simulation.py:514`。完整运行时签名附后。

`run_single_task(config, task, ...)` 接受 Task 对象，经 `build_orchestrator` 构造 agent、user 和新环境，然后运行并附加 `reward_info`；无需注册合成任务集，也不调用官方任务加载函数。

`[INDEPENDENT]` 包装层固定 domain=retail、user=user_simulator，开放模型、LLM 参数、seed、步数和错误次数配置；不接受任务集选择参数。`run()` 返回轨迹，`state()` 仍表示显式 `init()/execute()` 的环境；原生 runner 使用它自己创建的环境。

`reward()` 委托 `evaluate_simulation(simulation, task, EvaluationType.ALL, solo_mode=False, domain="retail")`，返回 RewardInfo。包装层在运行和评分前拒绝缺失 actions、`actions=None` 和不允许的 reward_basis，避免跳过 DB 检查或调用 NL evaluator。

### Ground-truth agent 的能力与限制 `[OFFICIAL]`

`src/tau2/agent/llm_agent.py:166` 的 LLMGTAgent 将参考动作逐条写为 `[Step n]` resolution steps，默认 `provide_function_args=True`，包括工具名和参数，并放入 system prompt；这只是提示模型执行，不保证正确。

`check_valid_task` 要求至少一条参考动作。`actions=[]` 的只读或拒绝任务会在构造时触发断言，不能用该 agent 做全部 V9 检查。未来 Step 3 的 V9 需要显式处理这个限制，不能以空动作直接宣称已验证。

本次两个原生 agent 都通过 mock LLM 完成一条取消订单对话，并获真实环境 reward=1。只验证接线与重放，不代表真实模型在 Retail 上的成功率；尚未做付费 LLM 可靠性实验。

### holdout 用户与任务签名 `[INDEPENDENT]`

执行 `python scripts/build_holdout.py`：`base_tasks=114 holdout_users=53 signatures=114`。

- 从 actions 参数里的 user_id、email、姓名+邮编，以及真实 order_id 反查身份；同时检查 known_info 的 user_id、邮箱和姓名+邮编。只读取 base split，所有 114 题均解析成功。
- 官方 46/47 等任务含故意输错的订单号，不能将不存在的订单当作身份；无其他可解析身份时脚本会报错。57 题没有动作，但 known_info 直接给出了 user_id，已覆盖。
- `data/holdout/users.json` 为 53 个用户 ID 的排序列表；`signatures.json` 为逐题 `{task_id, user_id, write_actions}`，其中 write_actions 按原顺序保留重复 WRITE 工具，READ 和 GENERIC 不纳入写动作序列。
- `task_users.json` 保留逐题解析结果供追溯。这些派生产物按现有 .gitignore 留在本地，可用同一命令重建。

### 验证记录 `[INDEPENDENT]`

Python 3.12.7；`python -m pytest -q`：**15 passed, 1 warning**（上游 audioop 弃用警告）。
包含：相同写动作 reward=1、省略写动作 reward=0、空动作保持原状态 reward=1、空动作任务出现额外写操作 reward=0；两个 agent 的原生 runner 离线对话、身份提取分支和评测隔离测试。

安装脚本补取 `/data/tau2/user_simulator/`，解决原生 user simulator 缺少 simulation_guidelines.md 的问题。pytest 使用本地 LiteLLM 价格表，模型生成函数全部 mock，不调用模型 API。

### 固定版本完整运行时签名 `[OFFICIAL]`

```python
run_single_task(config: Union[tau2.data_model.simulation.TextRunConfig, tau2.data_model.simulation.VoiceRunConfig], task: tau2.data_model.tasks.Task, *, seed: Optional[int] = None, evaluation_type: tau2.evaluator.evaluator.EvaluationType = <EvaluationType.ALL: 'all'>, save_dir: Optional[pathlib.Path] = None, user_voice_settings: Optional[tau2.data_model.voice.VoiceSettings] = None, user_persona_config: Optional[tau2.data_model.persona.PersonaConfig] = None, verbose_logs: bool = False, audio_debug: bool = False, audio_taps: bool = False, auto_review: bool = False, review_mode: str = 'full', review_model: Optional[str] = None, hallucination_feedback: Optional[str] = None) -> tau2.data_model.simulation.SimulationRun

TextRunConfig(*, domain: str = 'airline', task_set_name: Optional[str] = None, task_split_name: Optional[str] = 'base', task_ids: Optional[list[str]] = None, num_tasks: Optional[int] = None, llm_user: str = 'gpt-4.1-2025-04-14', llm_args_user: dict = <factory>, num_trials: int = 1, max_errors: int = 10, timeout: Optional[float] = None, save_to: Optional[str] = None, max_concurrency: int = 3, seed: Optional[int] = 300, log_level: Optional[str] = 'ERROR', verbose_logs: bool = False, max_retries: int = 3, retry_delay: float = 1.0, auto_resume: bool = False, auto_review: bool = False, review_mode: Literal['full', 'user'] = 'full', review_model: str = 'claude-opus-4-5', hallucination_retries: int = 3, is_remote: bool = False, retrieval_config: Optional[str] = None, retrieval_config_kwargs: Optional[dict] = None, agent: str = 'llm_agent', llm_agent: str = 'gpt-4.1-2025-04-14', llm_args_agent: dict = <factory>, user: str = 'user_simulator', max_steps: int = 200, enforce_communication_protocol: bool = False, text_streaming_config: Optional[dict] = None) -> None
```
