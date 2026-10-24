# 原始生成器测量与自动改进阻塞记录

**首次真实 Codex 算法改进实验尚未完成。** 本次从 `34d2300` 保留原算法，仅增加测量器；已完成基线和环境调查。Codex CLI 模型调用 0 次，候选执行 0 次，未选择新算法，没有跨任务算法提升或 RSI 证据。

## 实际代码与复现

新增 `scene_rsi/world/generator_benchmark.py`、入口 `scripts/benchmark_world_generator.py` 和 7 项独立测试。原生成器、SceneSpec/SceneState、Verifier、求解器、PyBullet 环境、固定 Agent、Blender 导出器、既有测试和基准均未修改。没有新增哈希校验或自定义协议版本。

测量器包装原 `Random.sample` 与筛选搜索函数，只做计数和计时；原 `generate()` 及其 RNG 序列、256 次预算均原样执行。测试比较多 seed 两种模式的完整场景，验证观测前后输出一致且补丁恢复。工具只运行仓库已有可信代码，不接受候选路径、代码回调或任意导入。

```bash
cd /srv/lab-disks/disk2/users/xymo/rsi/scene_rsi_phase2
WORLD_PY=/srv/lab-disks/disk2/users/xymo/rsi/world_run/env/bin/python
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES=
"$WORLD_PY" -B -m unittest discover -s tests -v
"$WORLD_PY" -B scripts/benchmark_world_generator.py --samples 32 --out /tmp/generator-baseline-new
```

输出目录必须是新的。原 151 项及新 7 项测试合计 **158/158 通过**，日志见 `checks/`。本次正式测量在干净提交 `dfd9bb0bb85c1d4de51cd153eb8005f094c40614` 上运行；`measurements/summary.json` 记录 Git 状态。物理和搜索使用 CPU，不需要 GPU。

## 固定条件与指标

每个条件 32 个 seeds，开关 `require_solvable` 各运行一次，总计 384 次请求；`avoid_corners=False`。dev 使用 6×6/6、7×5/8、5×7/5，seeds 2000..2031；val 使用不同尺寸/障碍组合 4×4/3、6×8/10、8×6/12，seeds 90000..90031。条件在测量前固定，没有候选参与设计或选择。它们是本轮新条件，不是原 Phase 1 隐藏测试；没有读取或修改保留测试。

`measurements/dev_feedback.json` 仅有开发集聚合统计，没有 val 条件、逐项反馈、场景状态、参考动作或人工缺陷分析。完整 dev/val 原始数据保留在控制器目录 `../algorithm_run/reproduction/`；val 逐项记录不进入 Git 提交或改进 Agent 请求包。仓库只保存 dev 记录和分组/总体汇总。公开结果不代表允许候选访问完整仓库。

- 生成有效率：返回并通过原 Verifier 的场景数 / 请求数；生成失败保留在分母。
- 可解率：独立搜索找到解的场景数 / 请求数，另报有效场景条件下的比例；`unknown` 不算不可解。所有找到的参考路线由原 PyBullet 环境实际重放。
- 拒绝：分别记录距离不足、角落选项、搜索证明不可解和搜索未知。总尝试包括被丢弃的提案，不能只计算最后一个成功场景。
- 多样性：直接比较坐标的唯一布局比例、障碍格覆盖率、两两障碍集合 Jaccard 距离及箱子格点经验熵；不使用文件摘要。
- 难度：只在可解样本中统计最短动作数、该路线推动数、超出箱子到目标曼哈顿距离的推动数及搜索状态数；固定 Agent 成功率另外报告。
- 时间：生成时间包含观测开销和生成内的筛选搜索；独立评分、参考路线重放另计，不计渲染。共享服务器上单次测量的时间只作本次成本描述。

## 实测汇总

| 划分 | require_solvable | 几何有效 | 可解 | 固定 Agent 完成 | 采样次数 | 拒绝数 | 平均生成 ms |
|---|---|---|---|---|---|---|---|
| dev | False | 96/96 | 26/96 | 19/96 | 105 | 9 | 0.232 |
| dev | True | 96/96 | 96/96 | 74/96 | 388 | 292 | 4.374 |
| val | False | 96/96 | 19/96 | 9/96 | 111 | 15 | 0.247 |
| val | True | 96/96 | 96/96 | 62/96 | 622 | 526 | 7.958 |

False/True 下每个条件都是 32/32 几何有效，且没有生成预算耗尽或搜索未知。未经筛选的 dev 可解率为 27.08%，val 为 19.79%；筛选后均为 100%。可解的 237 个场景记录全部完成 PyBullet 参考重放；没有物理不一致。

未经筛选的 24 次拒绝全部因为初始箱子到目标距离不足 2。筛选后 dev 的 292 次拒绝包含 43 次距离不足、249 次不可解；val 的 526 次包含 85 次距离不足、441 次不可解。平均尝试从 dev 1.09 次到 4.04 次、val 1.16 次到 6.48 次，最大值分别 22/42 次。这是已有拒绝采样的代价，不是自动修改算法的证据。

| 条件 | 筛选 | 可解 / 32 | 唯一布局 / 32 | 障碍 Jaccard 距离均值 | 箱子位置熵 bits | 可解样本最短动作均值 |
|---|---|---|---|---|---|---|
| dev 6×6/6 | False | 9 | 32 | 0.906 | 4.265 | 15.00 |
| dev 6×6/6 | True | 32 | 32 | 0.903 | 3.531 | 12.12 |
| dev 7×5/8 | False | 4 | 32 | 0.858 | 4.179 | 8.25 |
| dev 7×5/8 | True | 32 | 32 | 0.865 | 3.617 | 11.44 |
| dev 5×7/5 | False | 13 | 32 | 0.914 | 3.867 | 10.23 |
| dev 5×7/5 | True | 32 | 32 | 0.912 | 3.566 | 11.44 |
| val 4×4/3 | False | 4 | 32 | 0.867 | 3.554 | 7.75 |
| val 4×4/3 | True | 32 | 32 | 0.876 | 2.732 | 7.19 |
| val 6×8/10 | False | 10 | 32 | 0.875 | 4.562 | 18.50 |
| val 6×8/10 | True | 32 | 32 | 0.874 | 4.101 | 15.12 |
| val 8×6/12 | False | 5 | 32 | 0.845 | 4.601 | 14.40 |
| val 8×6/12 | True | 32 | 32 | 0.849 | 3.867 | 13.75 |

本批每组未出现重复场景，但每组仅 32 个样本，不能据此宣称多样性充分。筛选后六组箱子位置的经验熵都降低，说明筛选改变了样本分布。不同模式中的可解样本也不同：val 最短路线平均从 15.16 步降至 12.02 步，不能把固定 Agent 更高完成率直接归因于更强算法或更难任务。

## 实际隔离检查与未完成部分

先前 PATH 调查未发现运行时；本次进一步查到其他项目的用户级 Apptainer 1.5.4。它确实可运行，但不能据此通过候选隔离验收。清空继承环境，设置最小挂载、私有 PID/网络等选项，同时对固定可信 `/bin/true` 请求 1 CPU、512 MiB 和 8 PID 上限。运行时在载荷启动前返回 255：

```text
cannot use cgroups - system is not configured for cgroups v2 in unified mode
```

宿主是混合 v1/v2 层级，v2 的控制器列表为空，当前用户的 v1 CPU/内存/PID 目录为 root 所有、0755。资源配置实测失败；其余挂载、网络、凭据、设备与完整进程清理仍未完成验收。没有运行攻击样例、候选程序或用普通 subprocess 降级。详见 `execution_environment.json`、`checks/apptainer-resource-probe.log`；[Apptainer 官方资源限制条件](https://apptainer.org/docs/user/latest/cgroups.html)要求非 root 资源控制使用统一 v2 及委派。

Codex CLI 0.163.0 已安装，支持结构化最终输出与 JSONL 调用统计，见 [OpenAI 官方非交互文档](https://learn.chatgpt.com/docs/non-interactive-mode)。本次没有启动模型推理；CLI 本身的 workspace 权限不替代外部隔离。已有 P2A/P2B 的提案和执行拒绝机制保留，也没有把静态审计当作安全执行。

自动提案、最多三轮算法补丁、候选动态审计、独立选择和改进前后同条件 Blender 三视图均未进行。没有实际 after 算法，所以不把 False/True 参数消融渲染成算法前后对比，也不重复包装旧图。原 A100/Blender 渲染接口和已有真实样例完整保留。

继续实验需要站点提供受限 CPU 执行服务，或启用统一 cgroup v2 并委派 CPU/内存/PID 控制器；之后仍需验证挂载、网络、凭据、设备和清理。模型提案进程还需 dev-only 文件系统及受限、凭据不暴露的模型访问路径。详细已有部署边界见 `PHASE2B.md`，本次不另建大型隔离框架。

结论：已测出原生成器在不同条件下的拒绝采样收益和成本；**自动算法修改、跨任务算法提升均尚未验证，更不是真正的 RSI**。`experiment_status.json` 如实保留 0 次模型调用、0 个候选及空选择。
