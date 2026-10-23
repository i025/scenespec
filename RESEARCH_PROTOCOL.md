# Scene-RSI：下一阶段严格递归实验协议（设计草案，尚未实现）

## 研究假设

- **H1：** 自动代码改写能够改善未见过的 3D 场景生成任务。
- **H2：** 经过代码级修改的 Meta-Agent，相比**固定的** Meta-Agent，能在相同预算内更有效地改进新的场景生成器。
- **H3：** H2 能跨代、跨场景族和跨初始生成器稳定成立；仅仅出现 3 代提升并不证明开放式 RSI 或递归加速。

## 系统边界

- 任务级可修改：`scene_rsi/agent/`（布局、资产选择、空间规划代码）。
- 元级可修改（下一阶段新增）：`scene_rsi/meta/`（错误分析、候选代码搜索、试验排序、历史利用等逻辑）。
- 永远不允许 Meta-Agent 修改：`scene_rsi/evaluator/`、最终评分规则、资产尺寸、最终测试集、预算管理器、隔离环境、实验日志与版本完整性检查。
- 在真正的隔离运行中，外部验证器应在**另一只读工作树或独立容器中**运行，而不是依靠约定限制 Agent 的修改范围。

## 公平对照组

| 组别 | 可以改 Scene Agent 代码 | 可以改 Meta-Agent 代码 | 历史版本归档 |
|---|---:|---:|---:|
| A. 固定生成器 | 否 | 否 | 否 |
| B. 固定 Meta 改进器 | 是 | 否 | 是 |
| C. 可变 Meta 改进器 | 是 | 是 | 是 |
| D. 元级反馈消融 | 是 | 是 | 是（禁用结构化归因反馈） |

所有实验组使用相同基础模型、初始任务 Agent、数据访问条件、最多 K 个候选修改、token/GPU/时间预算和安全检查。对于实时模型接口，还应固定版本、提示和工具，并记录不可控随机因素。

## 验证协议

1. 按场景族、资产组合、空间关系组合分成 `dev`（改进时可见） / `val`（候选选择） / `test`（最终报告一次性使用）。不能只用同一个模板换几个随机种子。
2. 在 `dev` 上由每个 Meta-Agent 修改同一个初始生成器，共允许 K 个候选并在 `val` 上选择最佳候选；禁止访问 `test` 细节。
3. 保留新 Agent 代码、Git 补丁、父代版本、实验预算、提交时间、验证数据摘要及验收结果。
4. 冻结不同代 Meta-Agent：`M_0`、`M_1`、`M_2`。让它们分别从多个**此前没使用过的**初始生成器 `G_seed` 出发，在同样 K 和总计算预算下试验；最终在 `test` 上评估。
5. 多个独立初始生成器、场景族和随机试验，报告置信区间、方差、累计费用和失败率，明确负结果。

形式化指标：

\[
G_t^* = \arg\max_{G \in \mathcal{C}_K(M_t,G_{seed})} Q_{val}(G)
\]

\[
\mathrm{MetaImp@K}(M_t) = Q_{test}(G_t^*)-Q_{test}(G_{seed})
\]

只有当后代 Meta-Agent 在新的初始生成器上稳定改善 `MetaImp@K`，才能说观察到*有限范围的元级自我改进证据*。对多代之间的效果应避免只挑最好一次作为结论。

## 待实现的最小闭环

```text
trusted controller --> select parent version
                 --> launch parent Meta-Agent in sandbox
                 --> generate limited code patch (agent/ and meta/ only)
                 --> independent AST/import/static checks, smoke tests
                 --> execute candidate on dev/val under fixed budget
                 --> archive candidate and measurements
                 --> optionally promote candidate
                 --> independent MetaImp@K probe of frozen descendants
```

## 参考研究

- Darwin Gödel Machine: https://arxiv.org/abs/2505.22954
- Hyperagents: https://arxiv.org/abs/2603.19461
- AIDE²: https://arxiv.org/abs/2609.26457
- AgentGarten: https://github.com/MirroS-Lab/AgentGarten

上述论文可用来比较系统设计，但不能作为本项目尚未实现的功能的实验凭据。
