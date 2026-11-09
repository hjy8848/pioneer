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

1. `modify_pending_order_address` 和 `modify_pending_order_payment` 用 `_is_pending_order`（判断条件是 `"pending" in status`）。所以订单在 `pending (item modified)` 状态下仍能修改，而 policy 规定修改商品之后不能再修改。`tests/test_retail_env.py::test_policy_only_gap_is_not_enforced_by_tool` 复现了这一点。
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
  2. 直接调用 `tau2.runner` 的 `run_single_task(TextRunConfig(...), task)`。这一条待核实，见 §5。
- 数据目录可以用 `TAU2_DATA_DIR` 指定。

## 5. 待核实（PLAN Step 0 / Step 1）

- v1.0.1 与 v2.0.0 在 `retail/` 目录（tools.py、data_model.py、db.json、policy.md）上的 diff。
- `tau2.runner.run_single_task` 和 `TextRunConfig` 的完整签名。
- `LLMGTAgent` 怎样把 expected actions 交给模型，以及它在 Retail 上是否可靠。
- 官方 114 题涉及的用户数。初步估计约 52 个，需由 `scripts/build_holdout.py` 精确计算。
