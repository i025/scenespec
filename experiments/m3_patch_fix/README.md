# M3 原生补丁执行恢复与固定 K=1 对照

**原生编辑链路已恢复。** 从本地 `8b40fa0` 出发，唯一一次新构建完成两次顺序原生补丁，M3通过原21组资格检查；随后原定四个K=1算法候选全部完成静态审计及隔离执行。没有人工修补M3、追加构建或M4。

工具恢复、机制资格与能力提升是三个不同结论。本轮M3在一个初始算法上保留了多目标收益；另一个未被选择。M2偏好误差降幅更大但出现终测硬约束退化，不能据此宣称M3普遍优于M2或已证明RSI能力提升。

## 旧故障：能确认与不能确认的部分

旧 `cli.stderr` 的确切错误为 `apply_patch verification failed: invalid patch: multiple operations target /workspace/algorithm.py`。目标为 **algorithm.py**，拒绝发生在Codex原生工具路由的补丁验证阶段，早于文件应用、Git Diff、外部静态审计和资格检查。

**旧档并没有完整模型工具响应。** 原运行目录和公开归档仅有14条CLI摘要、用量及错误；没有SSE、工具参数或失败补丁文本。因此不能判断原操作是多Update、Delete/Add、互不冲突的修改还是覆盖冲突，不能重放原4,893 Token补丁，也不能手工重构为M3。详见[离线诊断](offline_diagnosis.json)。

离线回归证明了原生协议的区别：同一patch的重复文件操作会在验证阶段拒绝，即便人工fixture的两处修改互不重叠；**一个Update File下多个@@，或多个独立顺序apply_patch调用**可以完成全部修改。实际上下文冲突仍被拒绝。本轮没有改写解析器、跳过操作或合并未知补丁。

旧本地门槛依次检查：路径必须`/responses`、原始Content-Length在`1..196608`、已登记上游请求数小于6；这三项原先共用一个错误。其他检查包括固定模型、重编码累计输入≤1MiB及400,000 Token预留限制。旧`request_bytes`是过滤后重编码大小，不是原始Content-Length；旧被拒请求的确切字节数无法恢复。

现在在拒绝位置保存实际请求字节数、限制类型、实际值、阈值及已登记次数；接受记录区分raw和encoded大小。离线实发 **196,609字节**、阈值 **196,608** 被拒且上游请求为0，见[数值记录](checks/quota_offline.json)。这不是旧拒绝请求的尺寸。此次17个真实请求没有触发拒绝。

## 最小修改与离线验收

- [coding.py](../../scene_rsi/meta/coding.py)：仅补充原生编辑格式及避免重复读取全文的说明；各研究task/预算不变。新增上述拒绝审计，并按既有2MiB上限保存每个完整服务响应SSE，不保存认证头或完整请求体。
- [check_native_editor.py](../../scripts/check_native_editor.py)：在真实CLI＋本地假服务中验证多hunk、依赖前次结果的顺序修改、真实冲突拒绝且内容不变、重复头拒绝且无部分写入、真实上下文的受保护文件编辑拒绝。
- [test_coding_backend.py](../../tests/test_coding_backend.py)：检查实际字节数/阈值、次数拒绝记录及认证内容不留存；验证响应SSE保存。
- [summarize_native_continuous.py](../../scripts/summarize_native_continuous.py)：随既有Diff/JSONL归档SSE。公共“研究任务→补丁”接口不变。

**252项单元测试通过（31.020秒），原8项真实隔离回归通过。** 另精确重放上轮留存的627字节正常假服务工具输入，所得文件与旧结果完全一致；它与无法重放的真实失败响应明确分开。原生多处编辑验收通过。首次回归的日志断言因嵌套JSON转义读取方式失败，修正测试解码后通过；当时原生编辑本身已成功，初次日志仍保留。

M2、Benchmark、Verifier、MeshWorld、初始算法、原生补丁解析器、文件允许范围、资格及最终选择规则均未改动。16,384响应上限和全部资源限制维持`8b40fa0`配置，无新增依赖或隔离框架。

## 一次构建与原资格检查

接口和协议先提交为`af88201`。在新独立工作区中，用冻结M2和相同开发反馈调用一次；调用前**整个研究task（含预算）逐字段相同**，证据见[task_equivalence](checks/task_equivalence.json)。只有后端工具使用说明改变。

真实响应`response_1.sse`、`response_2.sse`各包含一次原生apply_patch，每次一个Update File头；JSONL记录两次`file_change completed`及`turn.completed`。Diff为 **13,512字节**，仅修改algorithm.py，候选Git为`65ecfc313fe71279fead90af6154baf702bc552d`。完整[构建记录](results/creation/coding)保留原始SSE、CLI事件、实际Diff、源码、说明、预算和成本。

原21个反事实全部完成（42次受限策略调用），历史风险改变不影响当前已观测事实，当前违反项改变引起实质Probe/交付成员变化；证据来源与observed/hypothesis/unknown、完整四种子Probe交付均通过原检查。多目标固定阈值通过共同任务适配层原样交付。见[资格结果](results/qualification/gate.json)、[实际程序](../evidence_m3/M3.py)及[构建诊断](build_diagnosis.json)。

生成程序未经人工修补，先于新条件冻结在`3dbc431`。构建耗费 **77,723 Token、4次服务请求、131.648秒**；本地编辑器CPU约0.772秒、单进程RSS峰值139.94MiB，并非容器聚合内存或远程推理资源。资格通过证明这些检查中的行为生效，不证明未知任务上的元能力优势。

## 固定独立对照

复用原控制器，对coordinate_walk、whole_restart各进行M2/M3一次K=1比较；每初始程序新建dev6、selection6、私有test12条件，每条件四共享种子。48个合法参考场景先经可信MeshWorld验收，参考解不提供给Coding Agent。两个基线均通过原可行性门槛，没有因成绩调整或重采样。

两策略同用gpt-6.1-sol/medium、工具和预算：单编码任务600秒、最多6次服务请求、400,000 Token、单响应16,384；输入字节限制不变。每场景实际都执行24次查询，共 **576次场景生成、13,824次查询、0运行错误**。先关闭全部提案、固定选择集接受决定，再运行终测，没有据终测补选或回退。公开dev和汇总；36个私有选择/终测条件及参考解保留在服务器。隐私检查497文件，隐藏任务/种子/凭据匹配均为0，见[检查](results/privacy.json)。

固定门槛仍为：零运行错误、硬成功数不降、可见性损失≤0.01、全部与有效种子对的位置多样性保留≥90%、朝向保留≥80%、有效对数不降、偏好误差至少降低0.01m。以下均为原始候选，不将未入选版本冒充最终收益。

### 开发集：原始算法/候选

| 初始程序 | 方法 | 硬成功 | 偏好误差m↓ | 位置RMS m | 朝向差° | 可见比例 | 生成秒/次 |
|---|---|---:|---:|---:|---:|---:|---:|
| coordinate_walk | baseline | 24/24 | 0.3908 | 0.2861 | 18.57 | 0.99709 | 1.072 |
| coordinate_walk | M2 | 24/24 | 0.1321 | 0.2756 | 16.02 | 0.99709 | 1.031 |
| coordinate_walk | M3 | 24/24 | 0.2804 | 0.2944 | 18.29 | 0.99709 | 1.087 |
| whole_restart | baseline | 23/24 | 0.6705 | 0.6191 | 87.45 | 0.99516 | 1.395 |
| whole_restart | M2 | 23/24 | 0.6351 | 0.6104 | 87.03 | 0.99516 | 1.403 |
| whole_restart | M3 | 23/24 | 0.6180 | 0.6069 | 87.03 | 0.99516 | 1.458 |

### 候选选择集：原始算法/候选

| 初始程序 | 方法 | 硬成功 | 偏好误差m↓ | 位置RMS m | 朝向差° | 可见比例 | 生成秒/次 |
|---|---|---:|---:|---:|---:|---:|---:|
| coordinate_walk | baseline | 24/24 | 0.5388 | 0.2941 | 19.24 | 0.99385 | 1.062 |
| coordinate_walk | M2 | 24/24 | 0.2511 | 0.3134 | 19.87 | 0.99385 | 1.051 |
| coordinate_walk | M3 | 24/24 | 0.4193 | 0.3374 | 22.08 | 0.99385 | 1.043 |
| whole_restart | baseline | 24/24 | 0.4609 | 0.5696 | 83.68 | 0.99147 | 1.494 |
| whole_restart | M2 | 23/24 | 0.4083 | 0.5390 | 84.33 | 0.99147 | 1.491 |
| whole_restart | M3 | 23/24 | 0.3880 | 0.5360 | 84.33 | 0.99147 | 1.422 |

### 新私有终测汇总：原始算法/候选

| 初始程序 | 方法 | 硬成功 | 偏好误差m↓ | 位置RMS m | 朝向差° | 可见比例 | 生成秒/次 |
|---|---|---:|---:|---:|---:|---:|---:|
| coordinate_walk | baseline | 48/48 | 0.4674 | 0.3062 | 21.85 | 0.98714 | 1.076 |
| coordinate_walk | M2 | 47/48 | 0.1791 | 0.2907 | 20.59 | 0.98714 | 1.045 |
| coordinate_walk | M3 | 48/48 | 0.3465 | 0.3082 | 21.27 | 0.98714 | 1.072 |
| whole_restart | baseline | 45/48 | 0.3667 | 0.5326 | 88.87 | 0.98126 | 1.460 |
| whole_restart | M2 | 44/48 | 0.3342 | 0.5382 | 92.27 | 0.98265 | 1.466 |
| whole_restart | M3 | 44/48 | 0.3158 | 0.5298 | 91.26 | 0.98276 | 1.455 |

### 选择与终测解释

| 初始程序 | 策略 | 选择集入选 | 原始候选终测联合门槛 | 预先选择版本的终测偏好增益m |
|---|---|---|---|---:|
| coordinate_walk | M2 | 是 | 失败 | 0.2884 |
| coordinate_walk | M3 | 是 | 通过 | 0.1209 |
| whole_restart | M2 | 否 | 失败 | 0.0000 |
| whole_restart | M3 | 否 | 失败 | 0.0000 |

coordinate_walk：M2偏好误差降低0.2884m，但发生1次`mesh_passage`失败，硬成功48→47、有效种子对72→69，因此终测联合门槛失败。M3偏好误差降低0.1209m，位置RMS .3062→.3082m、朝向21.85→21.27°、可见性不降、有效对72→72，硬成功保持48/48，满足原联合门槛。

whole_restart：两候选在选择集均硬成功24→23、有效对36→33而被拒绝。终测原始候选也均45→44，失败类型均为`mesh_passage`；两策略的最终版本严格保留基线45/48，偏好增益0，没有据终测补选。全部与有效对多样性等完整指标见各split_summary及comparison_scores。

沿用原控制器的“预先选择版本偏好增益”MetaImp@1均值：M2为 **0.14418m**，M3为 **0.06043m**；该单项指标包含M2的终测硬退化，不能当作联合合规收益。原始候选在终测保留联合收益为M2的0/2、M3的1/2，且每策略/初始程序仅一次模型采样。coordinate_walk上M3相对M2偏好增益差为−0.1675m，任务聚类bootstrap 95%区间[−0.2552,−0.0855]；这是固定候选的任务变动，不是元策略独立采样不确定性。一个合规例子不足以证明M3普遍更强，也不追加调用追求正结果。

## 实际成本与结论

| 编码任务 | 完成 | 服务请求 | 输入Token | 输出Token | 合计Token | 编码秒 |
|---|---|---:|---:|---:|---:|---:|
| M3_creation | completed | 4 | 73,488 | 4,235 | 77,723 | 131.65 |
| coordinate_walk_M2 | completed | 3 | 48,794 | 1,245 | 50,039 | 56.46 |
| coordinate_walk_M3 | completed | 4 | 70,494 | 1,496 | 71,990 | 71.68 |
| whole_restart_M2 | completed | 3 | 49,188 | 1,576 | 50,764 | 77.36 |
| whole_restart_M3 | completed | 3 | 49,865 | 1,712 | 51,577 | 63.28 |

本轮 **5/5编码完成、17个服务请求、302,093 Token**。其中M2下游100,803、M3下游123,567，M3实际多消耗约22.6%，但两组额度完全相同。M3创建的77,723单列，不计入双方下游比较。前两次失败249,859 Token另列，含历史累计551,952；货币费用未返回，不能估算实际账单。

四候选静态审计与动态执行均4/4通过；独立对照墙钟838.91秒（含编码与场景评估，不含创建/准备）。5个编码任务墙钟和400.43秒含并行重叠，不是总耗时。576次算法评估的可信控制器CPU合计493.55秒、受限进程CPU合计344.29秒；两者统计范围不同，不等于远程推理资源。编辑器资源逐任务见costs，峰值为单进程RSS而非容器聚合内存。本轮无GPU、渲染或训练。

可以确认：原生补丁及Diff导出恢复，M3实际元策略修改通过原行为资格，固定对照完整执行。不能确认：旧丢失补丁是否本来互不冲突，M3是否普遍优于M2，或是否已实现可重复的RSI能力提升。本轮不调整评分、门槛、策略或增加模型样本。

## 运行与复核

实际目录为`/srv/lab-disks/disk2/users/xymo/rsi/m3_patch_fix_run`。不调用推理服务的复核（输出目录需为新目录）：

```bash
cd /srv/lab-disks/disk2/users/xymo/rsi/scene_rsi_phase2
PY=/srv/lab-disks/disk2/users/xymo/rsi/world_run/env/bin/python
BASE=/srv/lab-disks/disk2/users/xymo/rsi/m3_patch_fix_run
"$PY" -B -m unittest discover -s tests -v
"$PY" -B scripts/check_native_editor.py --out /tmp/m3-patch-offline --guard "$BASE/editor_guard" --patch-regressions
"$PY" -B scripts/summarize_evidence_m3.py --run "$BASE/live" --out /tmp/m3-patch-results
```

真实运行复用了以下三个入口；创建阶段另以检查包装逐字段比较旧self_task，见checks/task_equivalence.json。创建后只有原资格通过才调用prepare，先提交冻结M3，再产生新条件；prepare成功才调用execute。现有live已存在，重复create会拒绝，execute也有已开始拒绝，不隐式重试：

```bash
"$PY" -B scripts/run_evidence_m3.py --phase create --run "$BASE/live" \
  --geometry /srv/lab-disks/disk2/users/xymo/rsi/continuous_run/geometry \
  --guard "$BASE/editor_guard" --security "$BASE/security" --editor-security "$BASE/editor_verified"
# 原资格通过并提交冻结后才执行；本轮已执行，不用于追加试验：
"$PY" -B scripts/run_evidence_m3.py --phase prepare --run "$BASE/live" \
  --geometry /srv/lab-disks/disk2/users/xymo/rsi/continuous_run/geometry \
  --guard "$BASE/editor_guard" --security "$BASE/security" --editor-security "$BASE/editor_verified"
"$PY" -B scripts/run_evidence_m3.py --phase execute --run "$BASE/live" \
  --geometry /srv/lab-disks/disk2/users/xymo/rsi/continuous_run/geometry \
  --guard "$BASE/editor_guard" --security "$BASE/security" --editor-security "$BASE/editor_verified"
```

[冻结协议](PROTOCOL.md)、[测试日志](checks)、[实际源码/补丁/事件/成本与评价](results)共同构成记录；真正的私有条件仅在服务器live/comparison/*/private_tasks.json，不上传到GitHub。
