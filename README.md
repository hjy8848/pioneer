# pioneer

这是 AWS 博客 [*Growing verifiable training tasks for customer-service agents from the environment alone*](https://awslabs.github.io/agentcore-rl-toolkit/blog/data-synthesis-v0/) 的**独立复现**。验证环境是原生 [τ²-bench](https://github.com/sierra-research/tau2-bench) Retail（固定为 `v1.0.1`）。

- **目标：** 不使用任何人工种子任务，只依靠环境本身（工具代码、状态 schema、初始数据库、policy），自动生成可验证的训练任务。任务要覆盖不同的操作组合和难度层级，并且能由环境状态自动判定是否合法。
- **为什么是独立实现：** AWS 的合成代码尚未开源（截至 2026-11-10，[agentcore-rl-toolkit](https://github.com/awslabs/agentcore-rl-toolkit) 中没有相关代码）。哪些部分复用官方实现、哪些需要自行实现，见 [docs/AUDIT.md](docs/AUDIT.md)。

## 当前状态

已完成 PLAN Step 0–1：

- 固定版本的 τ² 拉取脚本。
- 六接口环境层：`get_metadata`、`init`、`run`、`state`、`query_data`、`reward`，以及扩展接口 `execute`。
- 官方 base 114 题的隔离清单提取（53 个用户）和写动作签名。
- 各阶段共享的数据契约。
- 冒烟测试和隔离测试。

Explorer、Evolver、Initializer、Verifier 都还没有实现，见 [docs/PLAN.md](docs/PLAN.md)。

## 安装与测试

```bash
bash scripts/setup_tau2.sh            # Windows: powershell -ExecutionPolicy Bypass -File scripts/setup_tau2.ps1
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e third_party/tau2-bench
pip install -e ".[dev]"
pytest
python scripts/build_holdout.py      # 输出 data/holdout/{users,signatures,task_users}.json
```

`run(task, agent_cfg, user_cfg)` 直接接收自定义 τ² Task，不加载官方题库。
`agent_cfg` 使用原生字段 `agent`（默认 `llm_agent`，可选 `llm_agent_gt`）、
`llm_agent`、`llm_args_agent`、`seed`、`max_steps`、`max_errors`；
`user_cfg` 使用 `llm_user`、`llm_args_user`，user 固定为 `user_simulator`。
`reward()` 返回原生 `RewardInfo`；任务必须明确提供 `actions`（无写动作时为 `[]`），
`reward_basis` 只接受 `[DB]` 或 `[DB, COMMUNICATE]`。
`llm_agent_gt` 的原生实现不支持空动作任务，详见 AUDIT §5。

## 目录结构

```
src/pioneer/
  env/retail_env.py   六接口环境层（get_metadata / init / run / state / query_data / reward，以及扩展接口 execute）
  schemas.py          Scenario / Stressor / ExpectedOutcome / FailureCode / VerifierReport / TaskMeta
  explorer/           Stage 1  环境知识        (PLAN Step 2)
  verifier/           Stage 3b 环境验证        (PLAN Step 3)
  initializer/        Stage 3a 实例化与修复    (PLAN Step 4)
  evolver/            Stage 2  演化与覆盖率    (PLAN Step 5)
  llm/                LLM 客户端与缓存
  stats/              统计报告
configs/retail_v0.yaml
scripts/setup_tau2.{ps1,sh}
docs/{PLAN,AUDIT}.md
tests/
```

## 与评测集隔离

- 生成代码不读取 τ² 的官方任务文件（有静态测试检查）。
- 生成时排除官方 base 114 题涉及的用户。
- 合成任务不使用由 LLM 判定的 `NL_ASSERTION`。
