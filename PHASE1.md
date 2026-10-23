# Scene-RSI 第一阶段：实验基础

这是固定场景生成器的基准和固定改进器的**请求/审计接口**。本阶段没有模型训练、Meta-Agent 自修改、候选代码动态执行、自动晋升或 RSI 证据。原始固定基线保持不变。

## 保留的实际接口

- `SceneSpec.from_dict/load`、`SceneState.from_dict/load/to_dict` 保持原样。
- `FixedSceneAgent.generate(spec) -> SceneState` 保持原样。
- `SceneVerifier(grid_step=0.1).verify(spec, state) -> dict` 保持原样；成功标准仍为所有硬约束通过。
- `schema.py`、`geometry.py`、`evaluator/`、原始测试的 SHA-256 固定在 `original_contract.json`。这是防误改检查，不能代替外部只读挂载。
- 旧的 `generate`、`validate`、`benchmark` 命令继续可用；`benchmark` 增加统计和来源信息。

## 任务集与合法解

`scene_rsi.benchmark.dataset.build_dataset(out, seed, per_family)` 构造六个结构族，每族生成 `per_family` 个任务。默认总数 24，每个 split 为 8 个任务。随机数局部使用固定种子；生成配置、版本、任务内容哈希与复杂度写入清单。同配置生成的任务及清单字节一致，时间/资源测量不要求字节一致。

划分在结构族层面完成：dev、val、test 的资产组合与关系图不同，而不只是同模板换种子。每个任务先构造参考解，再由**未修改的原始验证器**检查；检查失败就拒绝生成。基准包含多物体、同时靠两面墙、参考朝向下的关系链/分支/汇合/环、空白通道和可达性要求。所有可行性都只相对于原项目的长方体几何与栅格导航语义。

完整数据集是可信控制器资料：任务在 `dev/`、`val/`、`test/`，参考解在 `private/witnesses/`，清单在 `manifest.json`。`load_dataset(root, split)` 检查路径、哈希、划分与引用。生成器源码、种子、完整清单、val/test 以及参考解均不进入改进 Agent 请求包。未来真实盲测应由独立控制器生成并保存新种子和任务；公开固定种子适合复现，不能抵御已获得完整基准的参与者。

## 统计与实验记录

`scene_rsi.benchmark.runner.run_dataset(root, split, out)` 只运行受信任的原始固定基线。每个任务记录原验证器结果、失败阶段、约束索引/类型、任务内容哈希和资源。Agent 异常或返回格式错误计为失败，并保留在分母里。

报告包含任务微平均成功率、场景族宏平均成功率、按族和复杂度分组统计、失败类型计数及出现该失败的任务数。Wilson 95% 区间采用任务独立同分布近似；同族模板相关性意味着它不是严格泛化置信区间。验证器原有均分仅作为诊断，单独注明有效分数的样本数。

来源记录包括 Git HEAD/dirty 状态、源码与评价器 SHA-256、数据清单哈希、Python/平台。资源包含 wall time、CPU time、进程生命周期峰值 RSS；RSS 不是各任务新增内存。纯基线没有模型调用或 GPU 使用。当前基线不强制单任务时间/内存限额，因此这个 runner 不能用于执行未知候选代码。

## Fixed Meta-Agent 请求与补丁审计

`scene_rsi.meta.prepare_request(repo, dataset, out, dev_report=...)` 要求干净的父版本 Git 工作树，生成独立请求包。只复制当前 Agent 源码、共享 schema/geometry、dev 任务和筛选后的 dev 失败字段；不复制控制器 Git 历史、评价器实现、生成器或隐藏任务。

固定配置和提示有版本/哈希，输出协议为结构化补丁提案。`proposal_plan(...)` 提供后续 Codex CLI 的调用计划，当前不会启动子进程调用模型。命令参数依据本机 Codex 0.163.0 的帮助及 [OpenAI 非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode) 核对。

可在 `meta-prepare` 中用 `--model` 固定具体模型标识，`--max-candidates` 设置候选数（默认 8）；没有指定模型时保持未配置，不自动选择默认模型。`meta-plan --bundle ...` 只输出计划。候选预算按同一请求计数，拒绝/中断后已登记的尝试也消耗名额；Git common dir 内的控制器登记将请求绑定到唯一审计目录，换 `--out` 不能重置预算。该控制记录不进入源码提交或请求包。尚无跨克隆/跨主机预算服务。

`scene_rsi.meta.record_candidate(repo, bundle, patch, out)` 保存原始补丁，检查父版本、请求完整性、可改路径、Git 应用和 Python AST 语法。仅允许 `scene_rsi/agent/` 内常规 Python 源文件；评价器、资产定义、测试、控制器与 meta 代码不允许修改。每个提案的接受/拒绝结果、时间、资源和 Git 来源均写入独立记录。接受意味着静态入库，**不意味着测试通过或代码安全**。

补丁输入只接受常规文件，拒绝符号链接、FIFO 和设备。可接受补丁默认上限 256 KiB，入口另设 1 MiB 硬读取上限；超过硬上限时只归档有界前缀及前缀哈希，完整原文哈希明确记为未知，不会伪造完整归档。

候选源码以独立 Git 对象存档，保留父提交和候选提交。候选从不导入或执行，也不改写当前工作树。动态单元测试/dev/val 结果明确标为未执行。模型 tokens/成本未知时为 `null`，不能把它们当成零成本模型实验。

## 隔离边界与保留测试

共享服务器没有部署本项目可验证的候选执行隔离后端。候选执行入口无条件报 `IsolationUnavailable`，不退回普通 `subprocess`。AST 检查、文件夹权限、Git allowlist 和 Codex `read-only`/`workspace-write` 本身都不构成完整隐藏数据隔离。

后续 proposal 环境必须只挂载 dev 请求包和输出目录；不得挂载控制器仓库、完整数据集、参考解、用户 home、宿主凭据或 GPU 设备。固定模型版本、工具和提示，并限制网络到必要模型服务。候选执行环境还必须禁网、无 GPU、非特权、限制 CPU/内存/PID/时间/输出，使用受信任外部验证器通过有界 JSON 协议评分。只有落实并验证这些边界后，才能接通模型调用与候选动态评测。

`evaluate --split test` 默认拒绝。最终测试需显式 `--final-test --frozen-commit FULL_HEAD`、干净 Git 状态，报告只能写到 `dataset/private/test_results/`。读取测试任务前独占登记 `private/final_test_ledger.json`；重复或中断后重试都拒绝。这是可信操作员的防误用与审计机制，无法阻止拥有宿主文件访问权的人复制/删除数据；不能声称其提供 OS 隔离。

旧 `generate`、`validate` 和目录式 `benchmark` 拒绝直接读入受 manifest 管理的数据集文件，以免绕过新的划分/保留集入口；原始 examples 和 golden 文件仍使用旧命令。冻结 Git 版本在登记、开始评测和结束时都要保持一致。

日常复现只评测 dev/val，不消耗保留集。生成器验证 test 参考解属于数据集可行性认证，不是候选/基线的最终测试评分。

## 可复现命令

在项目根目录，先使用 Python 3.10+。本机可设：

```bash
SCENE_PYTHON=/home/xymo/miniforge3/bin/python
"$SCENE_PYTHON" -m unittest discover -s tests -v
"$SCENE_PYTHON" -m scene_rsi check-integrity
"$SCENE_PYTHON" -m scene_rsi make-dataset --seed 20261023 --per-family 4 --out ../phase1_manual/dataset
"$SCENE_PYTHON" -m scene_rsi evaluate --dataset ../phase1_manual/dataset --split dev --out ../phase1_manual/dev.json
"$SCENE_PYTHON" -m scene_rsi evaluate --dataset ../phase1_manual/dataset --split val --out ../phase1_manual/val.json
"$SCENE_PYTHON" -m scene_rsi meta-prepare --repo . --dataset ../phase1_manual/dataset --dev-report ../phase1_manual/dev.json --out ../phase1_manual/request
"$SCENE_PYTHON" -m scene_rsi meta-plan --bundle ../phase1_manual/request
"$SCENE_PYTHON" -m scene_rsi meta-record --repo . --bundle ../phase1_manual/request --patch /path/to/candidate.patch --out ../phase1_manual/candidates
```

完整重复运行请使用新输出目录：

```bash
SCENE_RSI_PYTHON=/home/xymo/miniforge3/bin/python bash scripts/reproduce.sh ../phase1_run 20261023 4
```

最终测试仅在冻结并决定消费保留集后手动运行：

```bash
SCENE_FROZEN_COMMIT=$(git rev-parse HEAD)
"$SCENE_PYTHON" -m scene_rsi evaluate --dataset ../phase1_manual/dataset --split test --final-test --frozen-commit "$SCENE_FROZEN_COMMIT" --out ../phase1_manual/dataset/private/test_results/final.json
```

本阶段尚未实现：真实 Codex 提案执行、隔离候选评测、按 val 自动选优与晋升、强制执行的模型 token/费用/总时间预算、多个新初始生成器的 MetaImp@K 实验、元级自修改以及真实资产/物理/渲染评价。
