# Phase 2 当前交付：P2A 安全设计与离线验证

本次遵循用户指定的降级条件：**隔离无法正确落实时，只交付 P2A 安全设计和离线测试，不执行候选代码。** 没有完成自动代码改进闭环，也没有真实模型提案记录、候选动态得分或 RSI 证据。

基线为 `f116002d464f5ca8c6e529ea58b9d223ac4ab47c`。开始开发前已复跑该版本全部 **68 项测试，全部通过**。代码位于 `phase2/fixed-meta-safe-proposals` 分支及独立工作树；原 `main` 工作树保持 Phase 1。原 `PHASE1.md`、研究协议、SceneSpec、Verifier、几何、FixedSceneAgent、生成器、评测器、已有数据及保留测试协议均不修改。

## 实际环境证据与未完成原因

当前执行边界内未发现 Podman、Docker、bubblewrap、runc/crun、nsjail 等运行时。已有当前用户的 subordinate UID/GID 映射；`unshare --user --map-root-user --mount --net --pid --fork /bin/true` **成功**，所以不能声称内核完全禁止命名空间。

上述成功来自控制器手工检查固定系统空操作。自动预检更保守：要求系统可执行文件及其祖先目录均由 UID 0 持有，而本次可见的 `/`、`/usr`、`/usr/bin` 和系统二进制显示为 UID 65534，因此自动报告为 `not_run / trusted_system_unshare_unavailable`。这是信任检查未满足，不是命名空间创建失败。交付的 `environment-observations.json` 单独保存手工检查结果；没有放宽预检规则或据此启用执行。

但当前可见 cgroup 挂载只读，尚无经验证的资源委派、候选隔离后端、模型凭据代理或真实隔离回归结果。命名空间创建成功不证明文件系统、网络、GPU、凭据、进程组资源和超时清理边界都正确。没有连接用户 systemd bus、安装运行时、修改服务器配置、尝试 Docker 权限或访问 GPU。此结论只针对当前执行边界，不代表管理员无法提供其他执行服务。

`meta-preflight` 保存观察事实和缺项；能力字段永远不被当作授权。即使报告中出现运行时路径，或调用方伪造 `verified=true`，也不会启用执行。当前没有 `--unsafe`、普通子进程降级或可注入的执行回调。

## 实际实现的接口

| 接口 | 行为 |
|---|---|
| `prepare_request(...)` | 保留 dev-only 导出；增加 Git 控制目录内的可信来源登记，不把登记信息导出给模型 |
| `safe_plan(repo, bundle, limits=None)` | 校验来源、干净父提交和内容；读取有界快照；生成固定 argv、模型、schema、提示与资源限制设计；`execute=false` |
| `reserve_proposal_attempt(...)` | 在提案前落盘并锁定候选名额、总声明 Token 和时间预算；中断不会恢复名额 |
| `request_proposal(...)` | 记录预约后无条件返回 `blocked / missing_verified_backend`；不会启动 Codex 或连接模型服务 |
| `ingest_offline_proposal(...)` | 导入明确标注的人工 JSON/JSONL 样例，保留原始输出、理由和可解析的样例 Token 数据，再复用 Phase 1 静态审计 |
| `record_candidate(..., reserved_candidate_id=...)` | 复用同一个离线预约，避免 P2 提案和 P1 静态审计各消耗一次 K；已有调用形式仍可用 |
| `probe_capabilities(namespace_probe=False)` | 默认只读采集；显式参数只运行可信系统 `/bin/true` 命名空间探测 |

新模型输出严格限定为 JSON 对象 `{ "patch": "...", "rationale": "..." }`。拒绝多余字段、重复键、非法 UTF-8、非有限数字、空字段和超限输入；不会执行文本中的命令。Token 解析仅处理已知 Codex JSONL 事件字段，缺失值保持 `null`，拒绝自相矛盾的终止事件。`fixture_usage` 与真实消耗分开；当前所有记录的 `model_calls=0`、真实 Token/费用为 `null`。

请求文件必须是有界常规文件；拒绝符号链接、FIFO、设备、socket 和异常目录。目录使用不跟随符号链接的文件描述符读取，随后把同一份内存快照存入控制器私有目录。超过上限的输出只归档注明不完整的有界前缀，不能冒充完整模型输出。内容哈希不能证明来源，所以还需可信 `prepare_request()` 在 Git common dir 中留下的登记。该登记不是数字签名，也不能抵抗已经能修改控制器文件的同 UID 进程；未来隔离必须让模型完全不可访问控制器。

同一请求的提案和旧静态审计共用 K 与审计根目录；更换输出目录、提高限制、重放预约或先预约后中断都不能恢复额度。总 Token/时间是**保守预约账本**：当前实现强制限制预约数量，但没有模型服务可执行，因此不声称已经强制限制真实推理费用。CPU、内存、PID、wall time、输出和磁盘的运行时限制只是部署契约，记录为 `limits_enforced=false`。

固定提示仍为 Phase 1 提示，没有追加人工发现的生成器缺陷、修复建议或合法参考解。只有 dev SceneSpec 和允许公开的 dev 反馈可以进入请求。val 和 test、完整仓库/Git 历史、评价器实现、控制器日志、用户 Home、模型凭据、SSH/代理环境均不得进入模型运行环境。

## 后续真实执行的部署契约（未实现）

提案运行时需要管理员认可的 rootless runtime 或独立受控执行服务，以及固定镜像、非特权身份、最小只读挂载、空白有界输出/临时目录、无 GPU、进程组资源上限和超时整体清理。只允许访问受控模型代理；宿主认证信息由代理注入，不能通过 `HOME`、`CODEX_HOME`、auth 文件或环境变量交给模型工具。代理还必须约束模型标识、端点、请求次数与输入/输出/累计 Token。**只允许访问模型域名但同时暴露宿主凭据，不满足本项目要求。**

候选运行时必须进一步禁用外部网络，输入仅为 SceneSpec JSON，输出仅为有界 SceneState JSON。不使用 pickle，不导入控制器或评价器。进程退出后由可信控制器使用原始 SceneVerifier 评分。安全验收必须实际尝试读取私有 canary、网络访问、无限循环、fork/内存/输出消耗、评价器写入与 GPU 设备访问，并验证拒绝、限额及完整清理；当前这些运行时验收均未执行。

完成上述人工构造测试补丁的真实隔离验收之后，才能启用真实 Codex 提案。不能把运行时二进制存在、Prompt 约束、工作区只读、静态语法通过或离线 mock 测试作为验收替代。

P2C 后续设计：每次运行冻结 Meta-Agent、模型、提示、任务清单与预算，所有尝试先登记，再分别记录静态审计、隔离执行、dev 和 val 硬成功率及成本。val 的任务和逐项反馈不返回提案端。以 val 硬约束成功率为主，设置 dev 硬成功率不下降的晋升条件；诊断均分最多用于硬成功率相同情况下的辅助比较。选出的代码以 Git 版本标识保存；test 不参与此阶段。**选择器、晋升流程和候选动态评测尚未实现。**

## 离线测试证据如何解读

| 证据层级 | 当前状态 |
|---|---|
| Phase 1 回归 | 68/68 通过；后续完整日志还包含 P2A 新增测试 |
| 静态审计 | 人工注释补丁可入库，越界评价器补丁被拒绝 |
| 恶意样例策略回归 | 私有读取、网络、无限循环和过量输出程序只作为惰性字符串；没有执行/导入 |
| OS 隔离回归 | 未运行；不能声称隔离通过 |
| 真实 Codex 调用 | 0 次；只有版本/help 元数据检查与拒绝记录 |
| 候选 dev/val 改善 | 未测量，没有新的对比得分 |
| test | 不参与；未消费最终测试登记 |
| RSI | 无证据，也未实现元级自修改 |

## 可复现命令

在 Phase 2 工作树根目录，使用 Python 3.10+：

```bash
SCENE_PYTHON=/home/xymo/miniforge3/bin/python
"$SCENE_PYTHON" -m unittest discover -s tests -v
"$SCENE_PYTHON" -m scene_rsi meta-preflight --namespace-probe
"$SCENE_PYTHON" scripts/reproduce_phase2_offline.py \
  --dataset ../phase1_run/dataset \
  --out ../phase2_run/reproduction --namespace-probe
```

脚本只读取已有数据集的 dev 数据，不重新生成或改写它；运行全部测试，准备专用 `offline-fixture-model` 请求（这个名字不是实际模型选择），导入人工注释补丁与越界补丁，验证真实请求拒绝和 K 超限。产物包含 `tests.log`、`capabilities.json`、`plan.json`、`summary.json`、人工 fixtures、每次预约及提案/静态审计记录。原始模型输出字段保存的是**人工样例**，不是实际模型输出。

一次请求的名额不会为重复演示重置。同一控制器仓库内再次使用相同请求必须沿用其审计目录和剩余配额。完整独立复现应从新的 Git clone 开始（worktree 共享控制登记，不算新控制器实例），使用新的输出目录；新的 clone 是独立实验，不能冒充原请求续跑。

如需分别检查 CLI，可先用 Phase 2 的 `meta-prepare --model YOUR_PINNED_MODEL_ID` 创建注册请求，然后：

```bash
"$SCENE_PYTHON" -m scene_rsi meta-safe-plan --repo . --bundle /path/to/request
"$SCENE_PYTHON" -m scene_rsi meta-import-offline --repo . --bundle /path/to/request \
  --proposal /path/to/synthetic-proposal.json --events /path/to/synthetic-events.jsonl \
  --out /path/to/trusted-audit
"$SCENE_PYTHON" -m scene_rsi meta-propose --repo . --bundle /path/to/request --out /path/to/trusted-audit
```

`meta-propose` 在登记拒绝记录后退出码为 3；离线导入静态接受为 0、拒绝为 1；参数/来源错误为 2。没有可绕过拒绝的命令选项。

## 核对依据

本机 `codex-cli 0.163.0` 的 `exec --help` 已核对，未调用模型。结构化最终 JSON 和 JSONL 用量格式见 [OpenAI 非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode)。rootless 运行时和 cgroup 约束参考 [Podman 文档](https://docs.podman.io/en/latest/markdown/podman.1.html) 与 [Linux cgroup v2 文档](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)。这些文档不构成本机隔离已通过的证据；本机事实以 capability 报告为准。
