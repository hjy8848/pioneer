# Step 3：Verifier 与环境证据

我实现了 E1–E9 证据工具和 tool-use Verifier agent。公开流水线入口是 `pioneer.verifier.verify(task, meta, knowledge, llm=client)`。agent 必须查阅九项结果后提交诊断；最终 passed 由程序对检查结果取 AND，不能由模型覆盖。缺少结构化信息或必要证据会拒绝入库。

## 检查内容

| 检查 | 实现 |
|---|---|
| E1 | 全新环境逐步 execute；检查动作类型、显式 actions、reward_basis 和执行成功；参考序列只接受 WRITE 与 transfer |
| E2 | 写入前执行 world model 中的状态谓词；另检查目标是否属于已认证用户；policy_only 也必须满足 |
| E3 | 最终 DB hash、变更实体范围；写任务必须发生状态变化，只读任务必须能从 READ 输出得到 communicate_info |
| E4 | 已知邮箱或姓名+邮编唯一；重放 READ 工具链，逐参数验证绑定；禁止 item/payment ID 泄漏，拒绝多候选位置索引取巧 |
| E5 | 在新沙盒执行被拒绝请求，确认工具拒绝或违反状态规则；partial 可指定 after_actions 前缀；transfer 必须有结构化的范围外能力描述和参考 transfer |
| E6 | 对声明的初始筛选与澄清筛选执行查询，候选数必须从大于 1 变成 1；无歧义任务记 applicable=false |
| E7 | 构造参考 SimulationRun，经原生 ALL evaluator 评分为 1；不调用 NL_ASSERTION |
| E8 | 两次原生对话；有动作时 llm_agent_gt，actions=[] 时 llm_agent；user_simulator 共用配置模型；重新原生评分，至少一次为 1 |
| E9 | 实例签名与官方派生签名用于判重，结构签名仅统计；涉及 holdout 用户的目标、身份及初始化变更都会拒绝 |

如果 E1–E7 或 E9 已失败，E8 记为 blocked，不继续花费模型调用，任务仍不能通过。正式入口默认读取 Step 1 派生的 holdout 用户与签名，缺失时需要先运行 build_holdout。新任务池的 seen_instance 由流水线传入；seen_structure 只记统计。不会用调用者提供的字符串冒充已计算签名。

Step 3.5 修正：实例签名仅包含参考 actions 的名称与参数、initial_state、stressor 集合、expected_outcome、communicate_info，不含自然语言剧本或 action_id。同结构的不同实例允许入库；仅改写剧本不会绕过实例去重。这一修正覆盖了原 PLAN 中按结构去重的不合理限制。

`[OFFICIAL]` 环境执行、DB hash、runner、评分全部复用固定 τ²。`[BLOG]` Verifier agent 收集证据和诊断，环境决定是否入库。检查协议、JSON 格式、READ 绑定、签名和角色适配为 `[INDEPENDENT]`。

## TaskMeta 的证据协议

新增 read_steps、argument_bindings、communicate_bindings、refusal_requests、ambiguity 和 requested_capability。read_steps 的参数以及参考动作的每个参数，都必须使用下面两种表达式之一：

```json
{"source": "text", "value": "no longer needed"}
{"source": "read", "step": 2, "path": "order_id"}
```

text 值必须出现在给定用户信息/请求中；认证参数只能来自 known_info。read 引用真实 READ 输出，step 为从 0 开始的编号，path 是字段路径。可以附带 where、field、many 对 READ 返回的候选筛选；单值选择必须唯一。argument_bindings 使用 `动作序号.参数名` 为键，例如 `0.order_id`。communicate_info 每条答案都要有 READ 来源，并必须参与 COMMUNICATE 评分。

E4 是保守的可导出性检查，不能证明任意自然语言与结构化意图完全等价。E6 验证的是候选元数据声明的筛选条件；E5 的范围外能力也由候选的结构化描述给出。后续 Initializer 必须提供这些字段，不能用模型自由文字替代。认证/确认等对话层 policy 没有被宣称为仅靠 DB 就已完全验证。

## 运行

输入 JSON 包含 task 与 meta：

```bash
python scripts/build_holdout.py
# 在调用进程中注入 LITELLM_API_BASE 和 LITELLM_API_KEY。
python scripts/verify_candidate.py --input candidate.json --out reports/candidate_verification.json
pytest -q
```

模型参数来自 configs/retail.yaml。原生 task agent 和 user simulator 的生成函数在受控作用域内适配到 src/pioneer/llm 的缓存客户端，没有修改 τ² 源码。这个适配用于顺序验证，不支持在同一个进程内并发运行多个原生对话。

## 真实验证与花费

详见 reports/step3_validation.json，其中保留手工输入、E1–E9 结果、真实 SimulationRun 和来源用量。

| 样例 | E8 两次 reward | 最终结果 | 仅 E8 估算费用 | 含 Verifier agent 的估算费用 |
|---|---|---|---:|---:|
| 初始取消样例 | 0 / 0 | 拒绝，UNREACHABLE_E2E | $0.221003 | $0.346210 |
| 修正结束条件后的取消样例 | 1 / 1 | 通过 | $0.228189 | $0.351841 |
| 只读订单状态样例 | 1 / 1 | 通过 | $0.274423 | $0.445615 |

初始取消样例的 user simulator 在说 yes 时就发出 STOP，实际取消工具尚未调用，Verifier 因此正确拒绝。读完两条完整轨迹后，我只修正手工输入，要求等到 agent 报告取消完成再结束，重新验证成功。原失败输入和结果都保留，没有放宽评分标准，也没有改写失败为成功。

三次真实验证合计估算 **$1.143666**，均使用 qwen3.7-max；这是客户端估算，不是网关账单。只读样例使用原生 llm_agent，写样例使用 llm_agent_gt，每个样例 k=2；实际回复统一经过磁盘缓存客户端。

## 反例测试

最终全量离线测试：**60 passed, 1 warning**，仅有 τ² 上游 audioop 弃用警告。包含最新 review 要求的 ast.Import / ast.ImportFrom 生产隔离检查。保存的三组真实轨迹又用最终程序重新评分，初始失败仍拒绝、写与只读两个有效样例仍通过；该复核没有新增模型调用，结果保存在 final_program_recheck 中。

12 种 FailureCode 均有独立手工构造的拒绝样例，来自本项目自己构造的场景，不读取官方任务。

| FailureCode | 反例 |
|---|---|
| TOOL_ERROR | 不存在的工具 |
| POLICY_VIOLATION | 对 delivered 订单执行取消；另测工具放行而 policy_only 禁止的修改 |
| NO_STATE_CHANGE | 把用户地址改成原地址 |
| UNEXPECTED_DIFF | 执行中注入不相关用户的 DB 变更 |
| ARG_NOT_DERIVABLE | 缺少参数绑定；另测身份不唯一 |
| ID_LEAK | 用户说明直接含真实 item ID |
| REFUSAL_NOT_FORBIDDEN | 声称拒绝一个实际合法的取消请求 |
| AMBIGUITY_INVALID | 初始筛选只有一个候选 |
| REWARD_MISMATCH | 原生评分边界返回 0 |
| UNREACHABLE_E2E | 注入未执行写操作的轨迹，伪造 reward_info=1 仍被重评分拒绝 |
| DUPLICATE | 与已有结构签名或官方派生签名重叠 |
| HOLDOUT_LEAK | 任务指向被排除用户 |

另测 Verifier agent 提交 passed=true 也不能覆盖失败证据、原生生成适配作用域会恢复、partial 在已执行前缀后检验拒绝、跨用户操作被拒绝。pytest 禁止联网，均使用 MockLLM 或注入的离线轨迹。

## 修改文件

- src/pioneer/verifier/{__init__,agent,evidence,grounding}.py。
- src/pioneer/llm/{client,native_bridge}.py、src/pioneer/schemas.py。
- scripts/verify_candidate.py、tests/{test_verifier,test_step25}.py、tests/helpers/verifier_cases.py。
- README.md、docs/{PLAN,FIDELITY,VERIFIER}.md、reports/step3_validation.json。
