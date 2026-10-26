# M1/M2 实际执行路径

审查起点 `46d87dc`；只读公开 dev 请求、响应、TASK 和原 JSONL，不执行新策略或模型。机器可核对的提取见 [path_evidence.json](path_evidence.json)，工具为 [audit_m2_paths.py](../../scripts/audit_m2_paths.py)。

实际 M1 文件是 [frozen_policy.py](../generalization_meta/results/meta/frozen_policy.py)，由 `scripts/run_continuous_meta.py:23` 指定；M2 为 [M2.py](../m2_failure/M2.py)。没有假设存在另一份 `M1.py`。

| 函数 | 源码变化 | 正式 pilot 中的作用 |
|---|---|---|
| `_number/_measurements/_rate/_brief` | AST 未变 | 摘要仍读旧任务字段，文本没有 preference/visibility/diversity 数值；这些数值实际保留在 TASK.feedback，未丢失全部信息 |
| `_objective_evidence` | 新增，M2:58 | 递归进入 summary.experience，将历史保持率与当前指标放入同一 risks；跳过查询重放，但未按历史/当前分域 |
| `_diagnose` | 新增，M2:130 | 当前正式 choose/propose 的 rotation 优先级和两个缺口与这条路径一致，不能说新增诊断未执行 |
| `_choose` | 修改，M2:149 | M1 正式输出 dense，M2 输出 barriers |
| `_features` | 修改，M2:200 | M2 能读 packet.spec.room；M1 只读根 width/depth，本轮 shape 为 (None,None)。本轮无旧 obstacles 密度字段 |
| `_select` | 修改，M2:231 | 有配对和目标风险加分，但正式例子最终集合相同，仅改变顺序 |
| `layout` | 修改，M2:301 | 实际 analysis/strategy 增加多目标指导；仍残留旧推箱子研究过程。两版都不读取传入 packet.source |

## 历史风险如何成为“当前瓶颈”

[M2 choose request](../m2_failure/results/pilot/c2/policy/choose/request.json) 的 `summary.multiobjective` 是当前全 dev：硬成功 1，位置 .6064695m，旋转 82.6670°，无基线/候选保持率对照。`summary.experience` 是旧四候选：位置保持 .7381756883、旋转保持 .5428528908。

实际 [choose response](../m2_failure/results/pilot/c2/policy/choose/response.json) 称：`Measured acceptance bottleneck: rotation. Deficits: position=0.1798,rotation=0.3214.` 两个数恰为 `(.9-.7381756883)/.9`、`(.8-.5428528908)/.8`。`_diagnose` 取较大缺口、`_choose` 把 rotation 映射到 barriers，propose 与最终 [TASK](../m2_failure/results/pilot/c2/coding/TASK.json) 延续这段指令。

因此历史失败确实影响了程序决策，问题在于没有标注为历史风险假说。当前绝对旋转多样性不能证明当前生成器已经发生旋转退化。代码虽然提醒不能无基线推断保持率，却没有在诊断输入上实施这一边界。

## Probe 与例子是否提供新信息

实际适配器 [request_for](../../scripts/run_failure_m2.py)（157–181 行）先提供相同全 dev multiobjective，再从已运行初始程序的反馈中选 probe 子集，不增加新查询。共同条件为 0–3、各两种子。

| | M1 | M2 |
|---|---|---|
| probe | dense，条件 5/6 两种子 | barriers，条件 5/7 两种子 |
| propose 子集偏好均值 | .32468761m | .30104849m |
| 返回 example_indices | [0,1,2,3,4,5,6,7] | [6,7,2,3,0,1,4,5] |
| 后端条件顺序 | [0,0,1,1,2,2,3,3] | [3,3,1,1,0,0,2,2] |

两组 probe 子集均值确实进入 TASK.feedback.metrics，不能说 probe 完全没有影响。但新 probe 场景输出都被示例选择丢弃，后端的逐项几何反馈覆盖没有扩展。选择顺序有变化，集合无变化。choose.analysis 不直接交给后端；task.analysis 来自 propose.analysis。

完整旧经验到 `request_for:180` 才加在 TASK.feedback.experience，发生在元策略决策之后。M2 程序实际仅看到四个历史聚合量；不能把后端阅读完整旧经验和算法源码的行为归为 M2 程序自身的源码诊断。

## 后端确实怎样响应

[M1 events](../m2_failure/results/pilot/c1/coding/events.jsonl) 第 5 行读完 algorithm/TASK/CONTRACT（输出 85,052 字符），第 6 行决定保留前 23 查询、最后做 5cm 精修并保持朝向。

[M2 events](../m2_failure/results/pilot/c2/coding/events.jsonl) 第 5 行成功读取同三类文件（86,853 字符），第 6 行指出初始朝向相同，决定随机初始 yaw 并交替关系精修/重启；第 11 行确认实现。旧 [initial.py](../m2_failure/initial.py) 只有第零查询的 heuristic yaw 为 0，后续重采样早已随机 yaw；从首查询相同推断最终旋转不足，证据不充分。

这支持“诊断进入指令且后端作出相应算法改变”，不构成“该指令必然导致最终失败”的反事实证明：未在同一模型抽样下替换指令重跑，本轮也不追加提案。

两版仍提到 boards/crate/deadlocks/reversible operations/shortest-path difficulty。M2 的连续多目标前缀和可信 CONTRACT 正确，后端日志也显示理解了连续任务。旧术语是目标噪声，未证明其造成退化。

资格检查中 8 个合成包唯一的非文本成员变化是一个 choose 从 barriers 到 dense；propose 示例成员集合都未改变。资格通过只证明接口可运行与局部决策变化，不能当成真实独立任务上的诊断有效性或元能力收益。
