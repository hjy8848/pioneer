# Step 4：Initializer agent、深度 0 与返工

Initializer 通过 query_data 选择真实实体，通过 sandbox_execute 试执行，再自己提交 Task 和 TaskMeta。程序不提供参考动作模板；提交前检查目标原子动作、字段契约、等待完成句和 E1–E7/E9 预检查。预检查通过不表示已入库，E8 仍标 pending，随后由 Verifier agent 执行全部 E1–E9。

## 运行

```bash
# 通过进程环境注入 LITELLM_API_BASE 和 LITELLM_API_KEY。
python scripts/initialize.py --out data/runs/depth0 --atomic cancel_pending_order --n 1
python scripts/initialize_batch.py --out data/runs/depth0 --workers 4 --n 3
```

前一条用于完整预演。后一条按 world model 的所有原子动作运行，每种默认 3 个。worker 是相互隔离的 Python 进程，避免同一进程的原生生成适配相互影响；每个进程内部顺序运行。所有角色使用 configs/retail.yaml 的同一模型。

## 必含与禁止项

- user_scenario.instructions 为原生结构化用户指令；具体身份来自非 holdout 实体。
- task_instructions 必须包含 requirements.WAIT_FOR_COMPLETION 的原句；不能在 yes 确认时就结束。
- actions 明确为列表；只读任务为 []。深度 0 只包含指定 WRITE 或 transfer 原子动作。
- reward_basis 只接受 [DB] 或 [DB, COMMUNICATE]；只读答案有真实 READ 来源。
- 参数需要 READ/text 来源证据，身份唯一；item_id/payment id 不得进入用户指令。
- 不修改 τ² 源码、磁盘初始数据库或官方任务文件；原子生成使用 seeded DB，不允许初始状态 patch。

## 返工与产物

每个候选默认每批最多 5 轮。Verifier 的结构化失败原因回传给同一个 Initializer 角色，agent 可重新查实体或修改场景；未修好的任务连同旧候选和原因进入 rework_queue，预算耗尽不等于丢弃。重启后从历史候选和队列恢复；已入库的候选不重新生成。

每个 shard 保存 candidates.jsonl（包括失败）、depth0.jsonl、rework_queue.json 和 summary.json。完整批次汇总后，根目录 depth0.jsonl 仅保留全局实例去重后的任务，结构重复不拒绝。原始候选和完整环境轨迹保留在运行目录，缓存和凭证不提交。

费用按初次生成、初次验证、返工三部分汇总，三部分相加等于已记录花费；返工包含后续轮次的生成和验证。另报告每道已入库任务的平均成本及按当前速率扩到 600 题的估算。agent 在单次生成中修复预检查失败的花费计入生成部分。

`[BLOG]` agent 自行构造场景与参考结果，失败诊断后返工。`[OFFICIAL]` 原生 Task、工具、runner、环境评分全部复用 τ²。批次预算、工具协议、必含句、队列与成本拆分为 `[INDEPENDENT]`。未采用额外 ART checklist。

## 当前验证依据

用户要求停止扩充并推送现有结果。本批保留 **11 个通过 E1–E9 的任务，覆盖 6/13 类**；全部通过原生 Task 和 TaskMeta 加载。每个任务的 E8 已运行真实完整对话。取消订单 3 个、交换已送达商品 1 个、修改待处理订单地址 3 个、修改待处理商品 2 个、修改支付方式 1 个、修改用户地址 1 个。其余 7 类未完成，Step 4 全类别验收保持未完成。

可加载任务及验证证据见 [depth0_partial.jsonl](../data/runs/depth0_partial.jsonl)（Step 4.5 检查实体复用后剩 10 题，剔除的 1 题在同目录 `depth0_partial.dropped.jsonl`），各类数量和费用见 [initializer_partial.json](../reports/initializer_partial.json)。11 个已完成记录均首轮通过完整验证，外层返工为 0；生成内部的预检查修复计入生成费用。未结束候选及缓存保留在本地运行目录。

已完成记录费用：生成 $5.097342、验证 $4.440353、外层返工 $0，合计 $9.537695。包含被中断候选的已返回缓存响应共 $11.669280；中断时尚未返回的请求用量未知。按已知费用除以 11 个任务，平均成本下界约 $1.060844，600 题外推下界约 $636.51，仅供本批试跑参考。

完整离线测试 **65 passed**（另有上游 audioop 弃用提示）。覆盖工具循环、查询后才能提交、预检查不伪装为 E8 通过及预算耗尽失败路径。

本步修改：`initializer/agent.py` 与 `initializer/__init__.py` 实现生成/修复；`verifier/evidence.py` 提供不运行 E8 的预检查；`scripts/initialize.py` 和 `initialize_batch.py` 管理返工、恢复、并发和费用；`tests/test_initializer.py` 验证生成协议。README、PLAN、FIDELITY 与本文记录实现及实际验收范围。
