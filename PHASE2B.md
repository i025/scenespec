# Phase 2B：候选执行接口与离线验证

**Phase 2B 尚未完成。** 当前交付执行器接口、只读环境调查、独立评分、离线模拟测试和管理员部署要求。没有候选进程、恶意程序、容器或 Slurm 作业被启动，没有真实 Codex 调用，没有算法修复或 dev/val 改善结论。

审查的代码基线为 `5c39ebf`；实际开始点 `11d1b45` 只额外包含用户指定的开发约定。开发位于 `phase2b/candidate-runner`。开始修改前原有 **108 项测试全部通过**。不修改原测试、原 SceneSpec/Verifier、基准、参考解和保留测试协议；不新增文件哈希验证。原有 P2A 静态审计实现保留。

## 当前服务器调查与验收状态

在 Codex 执行边界和单独的宿主控制器只读检查中，PATH 与常见固定目录均未发现 Apptainer、Singularity、Podman、Docker、bubblewrap、runc/crun、nsjail、runsc、firejail 或 Slurm 客户端。常见 Apptainer/Singularity/Slurm 配置和 modulefiles 目录也未发现。没有提供管理员认可的独立执行服务地址、部署配置或操作协议。

当前可见 cgroup 挂载只读，资源委派未验证。这里只能描述当前可见环境，不能推断其他计算节点或管理员私有服务不存在。没有运行 `systemd-run`、连接服务 socket、提交作业、下载镜像或访问 GPU。

| 边界 | 当前证据 | 验收结论 |
|---|---|---|
| 挂载 | 无已部署后端或容器内挂载观察 | 未验证 |
| 网络 | 无后端网络策略与拒绝测试 | 未验证 |
| 进程 | 无候选 PID/IPC 隔离与后代清理证据 | 未验证 |
| 资源 | 未验证 CPU、内存、PID、磁盘、时间、输出的整体限制 | 未验证 |
| 凭据 | 未验证 Home、环境、文件描述符和代理 socket 均不可见 | 未验证 |
| 设备 | 未验证无 GPU 设备、驱动和权限提升通道 | 未验证 |

先前 `unshare /bin/true` 的成功，以及文件系统所有者映射导致的自动探测跳过，均不是本阶段隔离验收。本次调查不运行该空操作。机器可读报告中的 `not_verified` 和 `live_test_run=false` 不得解释为通过或已经尝试过真实攻击。

## 接口与证据范围

`scene_rsi.meta.candidate_environment.inspect_candidate_environment()` 仅检查公开元数据，不启动任何程序。即使找到运行时或 Slurm，报告仍不授予执行权限。

```python
from pathlib import Path
from scene_rsi.meta.candidate_runner import CandidateRunner, load_audited_candidate
from scene_rsi.meta.candidate_evaluation import evaluate_execution

candidate = load_audited_candidate(Path('/controller/audit/candidate-...'))
spec_json = Path('/controller/one-scene-spec.json').read_bytes()
execution = CandidateRunner().run(candidate, spec_json)
report = evaluate_execution(spec_json, execution)
```

`load_audited_candidate()` 接受**控制器私有目录**中的 P2A `accepted_static_only` 记录和完整检查项，关联归档的候选及父 Git 版本；不执行、导入或检出候选源码。Git 版本标识不代表执行安全。控制目录的写权限属于信任根，不能把任意调用者提供的 JSON 当作授权。没有新建文件摘要、哈希清单或重复哈希校验。

`CandidateRunner.run(AuditedCandidate, SceneSpec_JSON_bytes)` 校验输入后固定返回 `blocked / no_verified_isolation_backend`。不存在可注入命令、执行回调、`verified=true`、普通 subprocess 降级或其他绕过开关。读取 Git 元数据的可信 Git 子进程不属于候选执行。输入只能包含 SceneSpec 字段，不接受完整仓库、任务集、日志或参考状态。

输出记录区分退出状态、SceneState、日志、声明限制和资源。拒绝时 `state/exit_code/wall_seconds` 及候选 CPU/内存/PID 指标均为 `null`；控制器检查时间单独记录。**未运行不能算作 0% 成功率，也不能伪造为零资源开销。**

`RunLimits` 默认声明：1 CPU、10 秒累计 CPU、256 MiB 内存、16 个进程、15 秒执行时长、1 MiB 输入、1 MiB stdout、64 KiB stderr、16 MiB 临时空间。当前只有离线输入和捕获记录的大小检查真实执行，OS 限制尚未实施，所以始终记录 `limits_enforced=false`。

`offline_capture()` 只解析调用方提供的惰性字节，明确标记 `offline_fixture`；它模拟正常退出、超时、CPU/内存/PID 终止和输出超限，不声称验证这些 OS 行为。超过上限的日志只保留有界前缀。成功输出必须是一份严格 JSON SceneState；拒绝重复键、非有限数、非 JSON、额外评分/资源字段。没有 pickle 或生成代码执行。

`evaluate_execution()` 在控制器内使用原始 `SceneVerifier(grid_step=0.1)`，不会导入候选。结构有效的状态仍由 Verifier 判断对象完整性、变换、边界、碰撞、约束和路径。超时、非零退出或输出超限不交给 Verifier；拒绝记录不评分。离线样例不能更名为 live 证据；即使诊断分数不变，硬失败仍是失败。

当前没有生产后端适配器、真实执行或 dev/val 选择器。Meta-Agent 保持固定，没有 RSI 证据。

## 管理员需要提供的部署能力

可选方案是经过站点验收的 rootless 容器服务、配置完整的 Apptainer/Singularity 服务，或 Slurm 管理的专用 CPU 执行环境。服务需有固定运行时版本、受控镜像版本、独立低权限身份、管理员认可的挂载/网络/设备策略和控制器可取得的验收记录。仅安装二进制或提供一个 `srun` 命令不够。

- 最小只读载荷仅包含经审计版本的 `scene_rsi/agent/` 及固定 schema/geometry 支持代码。完整 Git 仓库、评价器、基准目录、val/test 数据、参考解和控制器日志都不挂载。候选每次只通过 stdin 接收一个 SceneSpec JSON，stdout 返回一个 SceneState JSON，stderr 仅作有界日志。
- 宿主 Home、CWD、宿主根目录、共享 `/tmp`、Docker/Podman socket、SSH agent、云和 Git 凭据均不可见。清空继承环境、关闭非必要文件描述符，使用独立临时目录；不能靠 Prompt 或 `CUDA_VISIBLE_DEVICES` 隐藏设备。
- 候选无外部网络，也不能连接宿主 loopback、元数据服务或宿主 Unix socket。独立 PID/IPC 命名空间和最小 `/proc` 防止读取或操作宿主进程。禁止提权、特权容器、宿主 namespace 与 GPU/驱动注入，最好使用无 GPU 的专用节点。
- 对整个后代进程组实施 CPU 配额和累计 CPU 时间、内存和 swap、PID、临时文件字节/数量限制。默认预算见 `RunLimits`。单进程 `RLIMIT` 或 Slurm 的资源申请参数不能替代整体强制限制。
- 服务端 watchdog 强制 wall time，并在 CPU/内存/PID/输出超限或连接中断时终止全部后代。日志必须边读边限额，不能先无限 `capture_output` 再截断；临时空间也要有硬限制。必须确认完整清理、输出冻结后，控制器才评分。
- 服务返回实际退出/信号/终止原因、候选 wall time、整个 cgroup 的 CPU、内存峰值、PID 峰值，以及截断标志。缺少的指标保留 `null`；排队时间和控制器资源另记。服务不得接收任意宿主挂载、任意命令或调用方的授权布尔值。

Apptainer 默认可绑定 Home、CWD 等宿主目录，必须按站点实际配置审查并关闭隐式绑定；`--containall` 不能单独证明网络与资源边界。[官方挂载文档](https://apptainer.org/docs/user/latest/bind_paths_and_mounts.html)、[网络文档](https://apptainer.org/docs/user/latest/networking.html)。Slurm `task/cgroup` 可约束资源，但其配置和可用控制器必须验证；作业分配本身不建立文件系统和网络隔离。[Slurm cgroup 文档](https://slurm.schedmd.com/cgroups.html)、[配置文档](https://slurm.schedmd.com/cgroup.conf.html)。

## 上线前验收顺序（当前均未运行）

1. 管理员部署后端及适配器。先用固定可信观察程序获取挂载、网络、进程 namespace、身份、环境、文件描述符、设备策略和资源限制证据；任何缺项都拒绝执行不受信任代码。
2. 仅在这些基础边界验证后，运行受控恶意回归：读取私有 canary、连接管理员控制的网络 canary、无限循环、受限 fork/内存消耗、stdout/stderr 洪泛及尝试写入评价器。使用临时假凭据与测试副本，不能以真实用户凭据或实际可信评价器为攻击目标。确认拒绝、终止、日志截断与无残留进程。
3. 保存每项预期和实测结果、后端配置版本、退出原因及资源记录。修复任何失败后重新验收。离线 mock、运行时路径存在或手填报告不能替代此过程。
4. 首先将固定基线和可信人工算法修复补丁通过同一已验收路径执行，再比较冻结 dev/val 清单。以硬约束成功率为主，单列错误类型、时间和资源；诊断均分只作辅助。val 数据和逐项反馈只留在独立控制器，test 不参与。通过后再考虑真实 Codex 提案。

## 可复现命令与当前结果的解释

在项目根目录运行，无第三方 Python 依赖：

```bash
SCENE_PYTHON=/home/xymo/miniforge3/bin/python
"$SCENE_PYTHON" -B -m unittest discover -s tests -v
"$SCENE_PYTHON" -B -m scene_rsi.meta.candidate_environment
"$SCENE_PYTHON" -B scripts/reproduce_phase2b_offline.py \
  --dataset ../phase1_run/dataset --out ../phase2b_run/reproduction
```

脚本要求干净已提交源码和新的输出目录，只导出实际数据集的 dev SceneSpec，不读取实际 val/test 任务或参考状态。人工注释补丁只验证静态版本接入与拒绝路径，没有算法修改。独立小型 JSON 样例只验证传输和可信评分，包括正常/缺对象状态、超时、PID/内存终止、非零退出与输出超限。

产物 `environment.json`、`candidate-execution.json`、`summary.json`、`fixtures/` 和 P2A `audit/` 分开保留静态结果、拒绝记录和离线样例。`comparison` 中两版 dev/val 成功率、错误类型和候选资源均为 `null / not_run`，没有新的实验对照结论。

相同请求不会重置 P2A 配额或审计根绑定；完整重复实验应从新的 clone 和新输出目录开始。新 clone 是独立实验，不能冒充原请求续跑。恶意源码单元测试中的程序始终是惰性文本；它们证明入口保持拒绝，不能称为真实安全隔离回归通过。
