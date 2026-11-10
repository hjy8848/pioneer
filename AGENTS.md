# AGENTS.md（Codex 与其他编码 agent 的工作规则）

本仓库**完全复现** AWS 博客 *data-synthesis-v0*，范围包括：
- 三阶段合成流水线。
- τ² 的 Airline、Retail、Telecom 三个领域，以及 CAR-bench。
- Qwen3 的 SFT / RL 训练和评测。

相关文档：
- 分步计划：`docs/PLAN.md`。每次只做被指定的那一步。
- 事实依据：`docs/AUDIT.md`。
- 与博客的逐项对照：`docs/FIDELITY.md`。每一步完成后都要更新。

## 硬性规则

0. **忠于博客。**
   - 博客写了的，照做。
   - 博客没写的，在博客框架内自行设计，并登记到 FIDELITY。
   - Explorer、Evolver、Initializer、Verifier 都必须是 LLM agent，通过工具接触环境，不能退化成程序模板。
   - 所有 LLM 角色共用 `configs/` 中指定的同一个 frontier 模型。

1. **不修改 τ² 源码。**
   - τ² 固定为 `v1.0.1`，commit `fc0055dc4e0a316c3f83133267fbd6faaa770992`，由 `scripts/setup_tau2.*` 拉取到 `third_party/`（该目录不提交）。
2. **评测隔离。**
   - 生成流水线（`src/` 以及除 `scripts/build_holdout.py` 外的所有 `scripts/`）不得读取 `tasks.json`、`split_tasks.json` 或 `tasks_voice.json`，也不得调用 τ² 的 `get_tasks` 或 `get_tasks_split`。
   - `tests/test_isolation_static.py` 会检查这一点。
   - 采样实体时排除 `data/holdout/users.json` 中的用户。
3. **入库只看环境证据。**
   - 任务能否入库，只由程序在真实环境中检查得到的证据决定（PLAN Step 3 的 E1–E9）。
   - Verifier agent 负责收集证据和诊断，但不能覆盖证据的结论。
   - LLM 的判断永远不能作为入库依据。
4. **reward_basis 的限制。**
   - 合成任务只能用 `[DB]` 或 `[DB, COMMUNICATE]`，禁止使用 `NL_ASSERTION`。
   - 没有写动作的任务，`actions` 必须是 `[]`，不能是 `None`。原因是 τ² 遇到 `None` 会直接给 reward 1。
5. **标注来源。**
   - 在代码 docstring 和文档中，用 `[OFFICIAL]`、`[BLOG]`、`[INDEPENDENT]` 标明每个设计决策的来源。
6. **LLM 调用。**
   - 统一经过 `src/pioneer/llm/`，带磁盘缓存。
   - 模型、温度、seed 写在 `configs/`。
   - pytest 中不联网，一律使用 MockLLM。需要真实 LLM 的测试标记为 `@pytest.mark.llm`。
7. **如实报告。**
   - 没跑通、没达标就直接写出来。
   - 不放宽验证标准，不编造数字。
8. **训练前先预演。**
   - 启动任何 GPU / NPU 任务之前，都要先在小规模上完整预演一遍运行路径。
9. **每一步结束时：**
   - `pytest` 全绿。
   - 提交一次 commit。
   - 在 PR 或 commit 说明中写「回报内容」（PLAN 中每一步都列出了要回报什么）。

## 环境

```bash
# Linux / macOS
bash scripts/setup_tau2.sh
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e third_party/tau2-bench && pip install -e ".[dev]"
pytest
```

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts/setup_tau2.ps1
py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e third_party/tau2-bench; pip install -e ".[dev]"
pytest
```

LLM key 放在 `.env` 中（参照 `.env.example`），不要提交。
