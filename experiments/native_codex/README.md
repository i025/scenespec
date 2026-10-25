# 原生 Codex 工作区编辑：一次能力测试与四组 K=1

基于 `9377e67`，在真实推理前提交协议与实现 `1222476`。本轮完成 **1 次不计分能力测试 + 4 次正式编码任务，5/5 均产生完整 Git 补丁**；正式候选静态审计 4/4、隔离执行 4/4。原生读写链路已在本轮跑通，尚不能从四个样本推断长期稳定性，也没有证明 M1 普遍更强或获得 RSI。

## 接口与冻结边界

`improvement_task(request, contract)` 把原有策略输出转换为共同任务：研究目标、原始分析/提案策略、dev 反馈、`algorithm.py`、允许文件及预算。`backend(task, directory)` 返回 `status / patch / summary` 和可用成本；外部控制器消费 Git patch。测试包含完全没有 Codex 事件的 API 模拟后端。

M0、M1 文件、原有 `request_for` 的探测/反馈映射、连续 Benchmark、三种基线、资产、几何/物理评分及 `eligible` 均保持不变。M0 两组选择 random，M1 两组选择 dense；这来自冻结策略，不是后端改写。共同适配只改变旧指令中的交付形式。历史 JSON 执行器保留供旧实验复现，新实验使用原生后端，不要求模型返回完整源码 JSON。

每次 Codex 都在新的独立 Git 库读取 `TASK.json`、`CONTRACT.md` 和初始 `algorithm.py`，直接编辑唯一算法文件；外部收集 diff、候选提交、最后说明、JSONL 和服务用量。它没有完整仓库历史、评价器、资产文件、任务构造器、参考解、私有任务或控制器日志。所有候选都自动审计和评估，无人工逐轮修补。正式提案全部封闭后才运行终测；终测不参与重选。

## 验收与真实调用

先完成八项原有恶意候选回归，以及真实 CLI + 本地模拟模型的读取、原生编辑、私有路径拒绝、任意程序拒绝和保护文件验收，均无真实推理。然后执行一次真实不计分测试：原始 Repair、两个公开 dev 条件、两个种子，审计通过、24 查询、4/4 硬成功。其补丁或反馈没有传给正式任务。

正式实验为 M0/M1 × Repair/Joint，每组 K=1；相同 `gpt-6.1-sol`、medium、CLI 0.163.0、原生工具和上限。每任务 600 秒、最多 6 次上游请求、每请求至多 4096 输出 Token，输入单次 196608 字节/累计 1048576 字节。工具迭代不是额外独立候选。没有重试新候选、K=2、额外子 Agent 或 M2。

正式数据为原有 8 个 dev 条件、全新私有 8 个选择条件和 12 个终测条件，每条件两种算法种子，所有组共享。20 个私有参考布局全部合法；没有扩大任务族或复用上轮 test。公开目录仅包含 dev 明细、模型实际记录及私有集聚合分数。真实私有任务、种子、参考解和逐项终测状态保留在服务器私有目录。直接结构比较确认公开任务均属于 dev、没有私有任务/种子匹配，没有新增哈希验证。

## 完成率与算法变化

| 组别 | 原生编辑/非空补丁 | 静态审计 | dev/选择集无运行错误 | 预设选择规则通过 |
|---|---:|---:|---:|---:|
| M0 / Repair / c0 | 1/1 | 1/1 | 1/1 | 否 |
| M1 / Repair / c1 | 1/1 | 1/1 | 1/1 | 是 |
| M0 / Joint / c2 | 1/1 | 1/1 | 1/1 | 否 |
| M1 / Joint / c3 | 1/1 | 1/1 | 1/1 | 否 |

四个候选都是算法函数变化：c0 增加多轮三维距离投影和随机重启；c1 在搜索后期对已合法布局做保留朝向的距离精修，并交替分配端点移动量；c2 用有界迭代投影替换原有单步 Joint 更新；c3 增加朝向候选和交替端点更新。它们没有改变任务、资产或外部 24 次 Mesh/像素查询上限，也不是只改 METHOD。没有将生成代码安装到冻结基线。

实际补丁与模型说明：[c0](results/c0/coding/candidate.diff)、[c1](results/c1/coding/candidate.diff)、[c2](results/c2/coding/candidate.diff)、[c3](results/c3/coding/candidate.diff)。各目录同时保存源文件、Git 版本、任务、JSONL、调用和静态/动态结果。

## 场景质量

以下每格为“硬成功数；平均空间偏好误差/米”。dev 和选择集各 16 次，终测各 24 次；候选行是**原始候选**，包含未入选者。

| 算法 | dev | 私有选择集 | 新私有终测 |
|---|---|---|---|
| Repair 初始 | 16/16；.27163 | 15/16；.12162 | 23/24；.31360 |
| M0 Repair c0 | 16/16；.07108 | 16/16；<.00001 | 24/24；.03733 |
| M1 Repair c1 | 16/16；.11193 | 15/16；.00323 | 24/24；.12513 |
| Joint 初始 | 16/16；.10458 | 16/16；.00619 | 23/24；.06385 |
| M0 Joint c2 | 16/16；.09449 | 16/16；<.00001 | 23/24；.04223 |
| M1 Joint c3 | 16/16；.05448 | 16/16；<.00001 | 24/24；.03091 |

选择规则保持原样：无运行错误、硬成功不下降、可见性下降不超过 .01、位置与合法配对多样性至少保留 90%、旋转多样性至少保留 80%、合法配对数不下降，且偏好误差降低至少 .01 米。c0 在选择集牺牲了位置/旋转多样性；c2/c3 也失去多样性。此外 Joint 初始在本次选择集已低于 .01 米，即使误差降为零也无法满足最小改善门槛。这个局部地板效应限制了强优化器的比较，未据此修改任务或门槛。

终测的多样性、可见性与时间如下；配对是同一任务的两个算法种子，位置指标为物体位置 RMS，朝向为平均角差。

| 算法 | 位置多样性 m | 朝向多样性 ° | 合法配对数/12 | 合法配对位置 m | 可见比例 | 单次生成秒 |
|---|---:|---:|---:|---:|---:|---:|
| Repair 初始 | .52413 | 89.945 | 11 | .52390 | .98940 | 1.094 |
| c0 | .29395 | 37.785 | 12 | .29395 | .98826 | 1.038 |
| c1 | .37596 | 83.097 | 12 | .37596 | .98788 | 1.061 |
| Joint 初始 | .26056 | 10.214 | 11 | .25550 | .99001 | 1.025 |
| c2 | .24842 | 5.989 | 11 | .23743 | .99301 | 1.012 |
| c3 | .24851 | 7.222 | 12 | .24851 | .99582 | 1.055 |

选择后的 M0/Repair 保留初始，M1/Repair 使用 c1：误差改善 .18846 米，按 12 个任务聚类的配对 bootstrap 95% 区间 [.09530, .30854]。但 c1 的终测位置多样性只保留约 71.7%，**未通过终测多目标门槛**。M0/M1 Joint 均保留初始，选择后差异为零。四个原始候选均未满足终测全部多目标条件；不因看到了 c3 的终测改善而补选它。

终测错误：初始 Repair 一次 mesh_passage，初始 Joint 和 c2 各一次 physical_stability，c0/c1/c3 无硬失败。全部 336 次正式场景生成均完成 24 查询，无运行错误。成功和偏好改善来自改动后的查询/更新算法；没有额外 Mesh 查询或可解性拒绝采样，但内部解析计算量有变化。表内生成计时沿用旧接口，包含查询循环和候选运行，独立最终稳定性评分在该计时之后。

## 实际成本

| 编码任务 | 上游请求数 | 输入 Token | 输出 Token | 合计 Token | 编码用时秒 |
|---|---:|---:|---:|---:|---:|
| 不计分能力测试 | 3 | 39,449 | 1,133 | 40,582 | 54.22 |
| M0 Repair c0 | 3 | 44,379 | 1,494 | 45,873 | 62.83 |
| M1 Repair c1 | 5 | 119,579 | 1,894 | 121,473 | 84.19 |
| M0 Joint c2 | 3 | 44,522 | 1,614 | 46,136 | 54.85 |
| M1 Joint c3 | 6 | 121,332 | 2,890 | 124,222 | 131.59 |

正式 4 个编码任务共 17 次服务请求、337,704 Token；连同能力测试共 5 个任务、20 次请求、378,286 Token。所有请求均获得实际用量，无缺失。M0 合计 92,009，M1 合计 245,695 Token；相同上限不等于相同实际消耗。服务未返回金额，费用留空，不根据订阅 Token 推算美元。

正式实验墙钟 643.32 秒，含基线、冻结策略执行、提案和最终评估，不含先前环境验收/参考解检查。编码任务最多两并发，333.45 秒的编码任务时间总和不是实验墙钟。正式 336 次布局的控制器 CPU 共 221.24 秒、候选/Apptainer 子进程 CPU 共 195.89 秒（原有计量口径，不含未计入的最终评分）。编码桥接器及 CLI/helpers 合计 CPU 2.08 秒，单进程 RSS 峰值约 137–139 MiB；不是整个容器的聚合 RSS，也不是远程模型计算成本。所有过程 CPU-only，未调用 A100、Blender 或训练。

相比旧 JSON 实验，本次同时把提案时限从 180 提到 600 秒、从单请求改成最多 6 次工具迭代并增加读写工具。因此不能把完成率变化全部归因于原生编辑形式。每个条件仅一次模型采样；任务 bootstrap 区间不能代表模型采样不确定性，更不能证明 M1 的普遍优势。

## 执行边界与限制

复用 Apptainer 的用户/挂载/PID/网络隔离，无宿主 Home、凭据、外网和 GPU。编辑阶段仅暴露三份公开任务文件、空 CLI 状态与有界模型代理。原生 patch 在 64 MiB 会话临时文件系统中编辑，任务/合约/Git 只读，最后只导出至多 128 KiB 算法文件。固定 C reader 只允许列文件和读取三份任务文件；Landlock 限定可执行文件为 CLI、code-mode-host 与固定 reader，禁止解释器/任意程序执行。编辑阶段不能运行候选 Python 或自行测试。

CLI 的 `danger-full-access` **仅在上述外部限制内使用**；嵌套 CLI 文件沙箱阻止原生写入，因此不把 CLI 自身权限选项当隔离。CPU 固定一核且不能扩大；每进程 CPU 45 秒、虚拟地址空间 16 GiB（V8 预留，非物理内存）、文件 8 MiB、128 文件描述符、任务墙钟 600 秒。共享 UID 的 NPROC=4096 **不是独立容器 PID 配额**，用户 systemd 声明的 cgroup 限额实测未生效，也未依赖它。这是受限读取/编辑器，不声称成为任意代码的通用安全沙箱。

候选 Python 始终由**未改动**的 `LayoutProcess` 执行：256 MiB 地址空间、5 秒 CPU、NPROC=0、禁 open/socket/fork，原有墙钟/输出限制。可信 MeshWorld 在容器外执行查询和最终评价；没有普通宿主 subprocess 降级执行生成代码。详见冻结的 [协议](PROTOCOL.md) 和 [验收记录](checks)。原生模式及 JSONL 语义参见 [官方说明](https://developers.openai.com/codex/noninteractive/)，CLI 格式处理只在后端内部。

## 测试与复现

全套 **227 项测试通过**，既有测试未修改。八项真实候选安全回归通过；离线原生编辑验收通过；336 个保存状态独立重评分：硬结果全部一致、最大质量误差为 0，未重新执行候选代码或调用模型，见 [checks/score-replay.json](checks/score-replay.json)。正式调用与补丁在 [results](results)，模型成本在 [costs.json](results/costs.json)，保留版本的比较在 [scores.json](results/scores.json)。私有任务与状态不上传公开仓库，本服务器可重放同一批存档；新一轮生成新私有条件，不宣称公开记录可还原隐藏任务。

在现有服务器项目目录中运行（下列 RUN 必须是新目录；真实命令会消耗模型额度，使用宿主已有认证代理，不向容器提供令牌）：

```bash
PY=/srv/lab-disks/disk2/users/xymo/rsi/world_run/env/bin/python
GEOMETRY=/srv/lab-disks/disk2/users/xymo/rsi/continuous_run/geometry
RUN=/srv/lab-disks/disk2/users/xymo/rsi/native_codex_replication
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES=''
mkdir -m 700 "$RUN"
cc -O2 -static -Wall -Wextra scripts/native_editor_guard.c -o "$RUN/editor_guard"
"$PY" -B -m unittest discover -s tests -v
"$PY" -B scripts/check_layout_isolation.py --out "$RUN/security"
"$PY" -B scripts/check_native_editor.py --out "$RUN/editor_acceptance" --guard "$RUN/editor_guard"
# 第一次真实编码任务，不计分；未通过则控制器拒绝正式执行
"$PY" -B scripts/run_native_continuous.py --smoke --run "$RUN/live" --geometry "$GEOMETRY" --guard "$RUN/editor_guard" --security "$RUN/security" --editor-security "$RUN/editor_acceptance"
# 仅四个正式编码任务；禁止隐式重启/追加候选
"$PY" -B scripts/run_native_continuous.py --run "$RUN/live" --geometry "$GEOMETRY" --guard "$RUN/editor_guard" --security "$RUN/security" --editor-security "$RUN/editor_acceptance"
"$PY" -B scripts/replay_continuous_meta.py --run "$RUN/live/formal" --geometry "$GEOMETRY" --out "$RUN/replay.json"
"$PY" -B scripts/summarize_native_continuous.py --run "$RUN/live" --out "$RUN/public_results"
```

本次实际私有存档位于 `/srv/lab-disks/disk2/users/xymo/rsi/native_codex_run/live`，mode 700。可将上方 replay 的 run 指向该目录的 formal 复查同批 336 个保存状态，不调用模型或执行候选源码。

本轮结论是原生工作区编辑能产出完整、可执行的算法候选；连续质量确有改善，但完整多目标泛化未成立，元策略优势仍未确认。不将这些结果称为 RSI。
