# M2 → M3：真实编辑在输出上限处中止，未进入元能力对照

从 `bfa608e` 开始。先通过原 242 项测试、8 项真实候选隔离回归和原生编辑离线验收，再提交运行前协议 `d964ae9`。实际只发起 **一次 M3 策略编辑任务**。固定后端第五次服务响应触发每请求 4,096 输出 Token 上限，CLI 以失败退出；补丁和候选文件均为空。

**本轮没有产生 M3 程序。资格检查未执行，两个新初始程序上的 M2/M3 对照也未执行。** 不能回答新机制是否生效，不能据此评价 M3 的算法改进能力。这个结果是原生编辑完成率失败，不是已测出的元策略能力失败；没有把失败任务换成手写 M3、重试提案或提高预算后继续。

## 1. 实际调用和失败位置

[原始 JSONL](results/creation/coding/events.jsonl) 记录后端成功读取 algorithm.py、TASK.json、CONTRACT.md，随后描述了区分历史诊断与完整 Probe 组的计划。接着直接出现：

```text
Incomplete response returned, reason: max_output_tokens
```

之后为 `turn.failed`，没有完成的 file_change 事件。`cli.json` 为 exit_code=1；[backend_result.json](results/creation/coding/backend_result.json) 为 status=failed、candidate_git=null、patch为空。[失败阶段检查](checks/failure-stage.json) 确认 candidate.py 和 candidate.diff 均为 0 字节。

| 服务请求 | 输入 Token | 输出 Token | 合计 | 响应状态 |
|---|---:|---:|---:|---|
| 1 | 8,833 | 65 | 8,898 | completed |
| 2 | 18,776 | 48 | 18,824 | completed |
| 3 | 23,620 | 35 | 23,655 | completed |
| 4 | 33,472 | 47 | 33,519 | completed |
| 5 | 34,523 | **4,096** | 38,619 | **incomplete** |
| 总计 | **119,224** | **4,291** | **123,515** | 编码任务未完成 |

HTTP 均为 200；不能把 HTTP 成功或 relay 的流读取 status=completed 当作模型响应完成。最后一个 `response_status=incomplete`，CLI 保留了正确失败状态。用量完整，无未知 Token；服务未提供金额，未推测货币成本。

这次不是总预算耗尽：5/6 服务请求，123,515/400,000 Token，输入 566,677/1,048,576 字节、单请求最大163,973/196,608字节，编码105.36/600秒。失败约束是**单次输出上限**，不是网络、认证、总 Token 或隔离不可用。最后响应的输出包含推理和未完成工具输出，不能据其Token数声称已有可执行策略。

## 2. 已实现的实验入口与未执行部分

[run_evidence_m3.py](../../scripts/run_evidence_m3.py) 复用原 run_codex、candidate、LayoutProcess、run_case、MeshWorld、distance、eligible，仅增加本轮共同数据组织、四种子统计、资格和编排。原预算控制器、评价规则、M0/M1/M2均未改动。

- 已预登记两个新初始程序：[coordinate_walk](initial_coordinate_walk.py) 为单坐标局部步进，[whole_restart](initial_whole_restart.py) 为整布局重启。它们静态审计通过，但**未在新任务上执行**，没有初始难度或性能成绩。
- 已实现 current/history/constraints 输入和可选 diagnosis 转发；冻结 M2 返回旧 schema，M3须自己产生诊断。Controller不补诊断、不替换/追加选例。
- 已实现四种子六配对统计与原始逐case隔离执行组合，原 `eligible` 原样复用。旧双种子状态复制构造的单元测试核对配对计数和距离，不能当作新的四种子场景实验。
- 已实现 planned 21 组反事实、最多42次策略执行的资格检查，和实际 TASK 交付集合核对；本次 **0组执行**。只在新增候选完整且静态审计后才进入。
- 已实现两个新初始程序×M2/M3各K=1的编排、selection先封闭再test、原始与回退版本分开报告；本次 **0/4 下游任务启用**。没有生成新dev/selection/test条件或参考解，没有场景分数可报告。

M2 父代本身实际运行了旧dev训练包的 choose/propose，输出连同输入保留在 [parent](results/parent)。它参与提出这次策略编辑任务，但后端未能产出新代码；不能将自然语言计划计为元级程序自修改。

## 3. 资格检查的语义边界

预登记测试将当前六类违反项分别与三种历史风险组合；当前事实必须保持不变，切换当前违反项必须改变结构化诊断及 probe/交付成员。历史风险不能成为 observed；propose必须选择完整四种子组且至少含实际所选Probe的一组，Controller原样交付。

在**未读取任何新候选代码**的独立只读审查中，发现最初校验仅匹配 baseline/candidate 路径前缀，可能误把 tasks 等无关字段当作位置证据。已将其收紧为诊断目标对应的指标路径，并增加负例单元测试。没有候选产生、没有执行过资格判定，因此不存在据资格结果修改标准或修补M3；[运行前版本](PROTOCOL.md)与Git差异保留。冻结评分器未改动。

当前代码的四种子统计在输出完整时沿用原定义。若输出缺失，可用配对子集会变化，须同时突出运行错误；原验收会拒绝有运行错误的候选，不能单独把其多样性均值解释成改善。

## 4. 实际验证和资源

| 项目 | 本轮结果 |
|---|---|
| 原基线测试 | 242/242，30.209秒 |
| 调用前完整测试 | 248/248，30.545秒 |
| 加入目标相关证据负例后 | **249/249，29.702秒** |
| 真实候选安全回归 | **8/8**：私有文件、网络、fork、Verifier写入、提限、内存、输出、无限循环 |
| 原生编辑离线验收 | 通过；本地假模型，无推理Token |
| 实际M3编辑完成 | **0/1**；5次真实服务请求 |
| 新M3静态/资格检查 | 未进入 |
| 新条件生成/场景查询/下游提案 | **0 / 0 / 0** |

完整日志见 [checks](checks)，原生补丁/空候选、模型事件、保留配额、实际父代输出、用量均见 [results](results)。编码105.36秒；编辑器进程及已等待helpers CPU约.727秒，单进程RSS峰值约137.70MiB，不是容器聚合内存或远程模型资源量。M2父代两次策略执行的资源另存 parent/*/execution.json。全程无GPU、Blender或训练。

预留上限为1次策略创建+4次后续编码，实际消费1次，未把未启用槽位算作调用。未发生新私有任务读取/泄露或参考解发布，见 [privacy](results/privacy.json)。

## 5. 运行与复核

本次实际运行目录为 `/srv/lab-disks/disk2/users/xymo/rsi/m3_evidence_run/live`。实际调用命令如下，目录已存在，重复执行会在开始处拒绝，不会隐式花费第二次提案：

```bash
PY=/srv/lab-disks/disk2/users/xymo/rsi/world_run/env/bin/python
BASE=/srv/lab-disks/disk2/users/xymo/rsi/m3_evidence_run
"$PY" -B scripts/run_evidence_m3.py --phase create --run "$BASE/live" \
  --geometry /srv/lab-disks/disk2/users/xymo/rsi/continuous_run/geometry \
  --guard "$BASE/editor_guard" --security "$BASE/security" \
  --editor-security "$BASE/editor_security"
```

不调用模型的复核：

```bash
"$PY" -B -m unittest discover -s tests -v
# 输出须为新目录；仅读取既有记录
"$PY" -B scripts/summarize_evidence_m3.py --run "$BASE/live" --out /tmp/m3-evidence-replay
```

控制脚本的 prepare/execute 阶段在未合格策略时拒绝运行，本次没有执行这些阶段。完整数据划分、四种子统计和固定门槛见 [PROTOCOL.md](PROTOCOL.md)，给真实模型的约束见 [META_CONTRACT.md](META_CONTRACT.md)。

下一轮若继续，应先验证原生编辑能在不变单请求/总预算下分块完成长策略补丁，再另行登记一次策略创建与公平对照。本轮未提高输出上限或重跑来取得成功结果。

**结论：M3的证据驱动机制是否生效、是否优于M2，都尚未检验。当前唯一支持的结论是这次固定预算原生编辑未能产出可评价代码。**
