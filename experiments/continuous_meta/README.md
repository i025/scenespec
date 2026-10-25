# 连续 3D 场景生成：M0/M1 首轮提案可行性记录

**本轮未完成生成算法改进比较。** 实际发出 4 次 Codex CLI / 4 次上游请求，
全部在预设 180 秒 CLI 时限内未完成结构化提案，返回 `TimeoutExpired`。
完整候选为 0，未进入候选静态审计或动态评估，没有自动算法修改，未启动 K=2、
独立模型重复或 M2。不能把这个结果解释为 M0/M1 算法能力相等或不能改进。
没有候选输出，因此未生成前后对照渲染，未占用 A100。

基础 `64ab246`；实验协议、共同适配和控制器在 `ba38772` 冻结，然后才调用模型。
Continuous Mesh 的任务定义、资产、原几何/物理/通行/可见性评分器、三种历史基线，
以及 M0/M1 元策略源码均未修改。使用原八个 dev 任务；选择集与最终测试均为
原分布的新私有抽样，没有新增任务类型。旧报告的 test 未用于本轮终测。

## 三个研究问题

1. **能否改进较强的连续优化器？尚未检验。** 两种初始算法的真实受限运行与
   冻结代码一致，但本轮没有完整模型算法可供比较。
2. **M1 是否优于 M0？无证据，也无法估计。** M0 实际选择 random 探测，M1
   选择 dense；机制发生了分叉，但四次模型输出均未完成。不能用调用失败推断
   诊断/实现算法的能力，也不能计算通过审计后的质量差异。
3. **收益能否保留在新私有测试？尚未检验。** 终测只测得初始算法性能；四条轨迹
   均保留初始版本。`scores.json` 中差值为 0 是版本回退的结果，不是新算法零收益。

## 实际调用与成本

所有调用同一 `gpt-6.1-sol`、medium，180 秒 CLI 上限、最多一次上游请求、
输入请求整体 <=98,304 字节、输出 <=8,192 Token。名额先登记，最多两个并行。
每个策略在 repair/joint 上各一次。没有重试、额外模型请求或人工修改候选。

| slot | 初始算法 | 元策略 / 探测 | 上游请求字节 | 中继耗时秒 | 结果 |
|---|---|---|---:|---:|---|
| c0 | repair | M0 / random | 68,730 | 179.37 | CLI 超时 |
| c1 | repair | M1 / dense | 91,904 | 179.54 | CLI 超时 |
| c2 | joint | M1 / dense | 91,870 | 179.52 | CLI 超时 |
| c3 | joint | M0 / random | 68,719 | 181.93 | CLI 超时 |

四个请求均收到 HTTP 200、模型标识及 `in_progress` 状态；截止前没有完整
`{code,rationale}` 文件。CLI 超时退出后中继记录 `BrokenPipeError`。
这证明请求已到达服务，不能区分服务排队、推理、流式传输等因素各自的耗时。
中继耗时包含等待与传输，不是模型计算时长；清理可略超 CLI 时限。

**4/4 缺失 Token 用量，实际 Token 总量和金额未知，不能记为零。**
`model_costs.json.total_tokens` 为 null；`known_total_tokens=0` 仅指没有可用统计。
四个中继时长合计约 720.36 秒，存在并行，不能当作实验墙钟。主实验墙钟为
510.25 秒，含基线、元策略执行、模型等待与最终评价，不含开发、准备、测试与重评分。

保存了真实配额、dev 请求、冻结策略决策、模型中继记录及超时异常。
原超时分支没有保存捕获中的 CLI 部分 stdout/stderr，因此这四次的完整 CLI
事件流和逐次 CLI 耗时缺失。交付中用**离线模拟超时**测试修复了该日志路径，
以后会保存已有事件；没有补造本轮缺失事件，也没有改变时限或重新调用模型。

[真实调用记录](results/model_costs.json) · [调用状态](results/calls.json) ·
[结果层级](results/outcome.json) · [扩展停止依据](results/expansion_gate.json)

## 共同适配与选择边界

既有 Apptainer + seccomp `LayoutProcess` 没有改动：候选单进程、256MiB、
5 CPU 秒，无文件/网络访问、进程创建、宿主 Home/凭据/GPU。静态审计沿用原限制，
仅允许入口已经预加载的 random；AST 不是执行边界。8 项真实恶意行为回归通过。
本轮模型没有产生代码，不能声称“模型候选隔离执行通过”。

两种初始算法通过同一 `layout(packet)` 状态机接口，每步返回连续支撑坐标
`u/v/yaw` 与 JSON memory；冻结的原 `baselines.state` 转成完整 xyz/Euler。
每次恰好最多 24 个独立外部 Mesh/像素查询，最终只能选择已查询输出。
物理稳定与通行在候选退出后由全新外部 MeshWorld 评价。模型无评分器源码、
参考构造器或隐藏任务。纯函数不能自行增加查询或修改评价器。

四类 dev × 两个算法种子 × 两种初始方法，共 **16/16** 与原实现最终姿态
精确相同，查询数均为 24。接口保留原支撑可行流形，不是新增自由体六轴搜索。
适配把原进程内查询与候选程序分开：候选 5 CPU 秒、外部查询另有 5 CPU 秒阈值，
所有比较都使用同一适配。带容器启动的耗时不与旧进程内数字直接排名。

独立选择提前固定：硬成功数和完整合法种子对数量不降低，可见均值损失 <=.01，
位置 RMS 多样性（全部对、合法对分别）>=初始 90%，旋转多样性 >=80%，
偏好误差至少改善 .01m。通过者按选择集误差排序，否则保留初始算法。
后续父版本仅按 dev 更新，选择集反馈不进入提案请求。

K=2/重复的门槛为：至少一个初始算法上，选择后的 M0/M1 误差差 >=.025m，
且任务级配对 95% 区间排除 0。本轮没有候选，门槛未触发，停止在四次调用。
保留初始程序所产生的 `[0,0]` 区间不代表对元策略等效性的统计证明。
即便有提案，单次模型抽样的任务级区间也不能估计模型抽样不确定性。

## 新私有任务上的初始算法成绩

8 dev、8 新 selection、12 新 test，每任务两个固定算法种子。20/20 新私有
参考场景在调用模型前通过全部检查；模型和算法均不读取参考解。
最终测试在全部可能的提案结束后开始，没有反馈给模型。

| 算法 | dev | selection | 新 test | test 偏好误差 m | 可见比例 | 位置多样性 RMS m | 生成墙钟秒/次 |
|---|---:|---:|---:|---:|---:|---:|---:|
| repair | 16/16 | 16/16 | 23/24 | .30962 | .98538 | .51488 | 1.10048 |
| joint | 16/16 | 15/16 | 23/24 | .13607 | .99035 | .24443 | 1.03383 |

test 每行是 12 个条件 × 两种子，不是 24 个独立条件。repair 的唯一失败为
通行未认证；joint 的唯一失败为基本物理稳定性。joint 在选择集也有一次稳定性
失败。每个条件都有合法参考场景，未把这些算法失败算成任务无解。

test 的合法种子对均为 11 对，合法对位置 RMS 分别 .51526/.22748m；
旋转多样性均值分别 78.96/11.68 度。joint 偏好误差更低、布局更集中，
这些只是两个**原始算法**在新任务上的差异，不是 M0/M1 收益。
全部均值包括硬失败输出，没有只选成功场景。与旧 test 的不同数字不可直接
解释为退化，因为新房间参数和算法种子不同。

主实验实际执行 112 个基线场景，全部 24 查询，共 2,688 次查询，无运行时错误。
生成阶段控制器 CPU 75.89 秒，受限运行时 CPU 65.46 秒（含 Apptainer 启动），
合计 141.34 秒，不含独立终评；生成墙钟逐场相加 120.19 秒。
原始 RSS 是控制器子进程的累计高水位，未冒充单个候选峰值。

**222 项完整测试通过；112 条保存状态独立重评分，硬结果一致，质量最大误差 0。**
重评分未执行生成代码或调用模型。[检查记录](checks) · [生成成本](results/evaluation_costs.json)

## 隐藏数据和复现

实际 selection/test 种子、任务、参考解与逐项输出只在服务器
`/srv/lab-disks/disk2/users/xymo/rsi/continuous_meta_run/live`，权限 700。
公开归档仅有 dev 包、初始算法 dev 轨迹、模型请求/调用记录、保留集汇总。
结构化比较未发现隐藏包或隐藏种子泄露；没有上传真正隐藏测试内容。
后续不可针对本轮终测再改算法并报告同一测试集作为新盲测。

不调用模型，复核本次保存状态：

```bash
cd /srv/lab-disks/disk2/users/xymo/rsi/scene_rsi_phase2
PY=/srv/lab-disks/disk2/users/xymo/rsi/world_run/env/bin/python
GEO=/srv/lab-disks/disk2/users/xymo/rsi/continuous_run/geometry
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES=''
$PY -B -m unittest discover -s tests
$PY -B scripts/replay_continuous_meta.py --run /srv/lab-disks/disk2/users/xymo/rsi/continuous_meta_run/live --geometry "$GEO" --out /tmp/continuous_meta_score_replay.json
```

以下会创建另一套私有抽样；运行最后一条**实际消耗模型配额**，首轮四次，
触发协议门槛时最多总共十六次，不保证模型输出/调用成功或复现逐字成绩。
完整测试的本地服务用例、容器和代理需要服务器现有执行权限：

```bash
RUN=/srv/lab-disks/disk2/users/xymo/rsi/continuous_meta_repeat
$PY -B scripts/check_layout_isolation.py --out "$RUN/security"
$PY -B scripts/run_continuous_meta.py --prepare --run "$RUN/live" --geometry "$GEO"
$PY -B scripts/run_continuous_meta.py --check-adapter --run "$RUN/live" --geometry "$GEO"
$PY -B scripts/run_continuous_meta.py --run "$RUN/live" --geometry "$GEO" --security "$RUN/security"
```

[调用前冻结协议](PROTOCOL.md) 是原始预注册依据。本轮证明了共同接口、受限基线与
独立评价可运行，但提案完成率为零，**没有验证连续场景的自动算法提升或元级优势**。
