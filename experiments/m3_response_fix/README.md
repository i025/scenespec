# M3 原生编码响应截断：最小修复与一次失败构建

**原 4,096 输出 Token 截断已修复并被真实响应验证，但唯一一次新构建仍未产生 M3。** 新失败为补丁格式被原生编辑器拒绝，随后本地请求门槛拒绝继续请求。没有资格检查、场景查询或 M2/M3 能力对照；不能把编码失败解释为 RSI 能力失败。

基线为旧实验的实际失败提交 `f37076e`，接口修复先提交于 `ac4fd37`，之后才进行一次真实构建。离线阶段没有新模型调用。冻结的 M2、Benchmark、Verifier、Meta 合约、资格检查、接受规则和研究反馈未修改。

## 原故障证据

[离线诊断](offline_diagnosis.json)对应原 `experiments/evidence_m3/results/creation/coding/` 的实际 CLI、JSONL、预算和五次请求：

| 服务请求（从0开始） | 输入 Token | 输出 Token | 响应 |
|---|---:|---:|---|
| 0 | 8,833 | 65 | completed |
| 1 | 18,776 | 48 | completed |
| 2 | 23,620 | 35 | completed |
| 3 | 33,472 | 47 | completed |
| 4 | 34,523 | 4,096 | incomplete |

最后 CLI 错误是 `Incomplete response returned, reason: max_output_tokens`。确切原因是 `scene_rsi/meta/coding.py::serve()` 将请求的 `max_output_tokens` 显式覆盖为 4,096。末响应归属为推理227、消息58、工具调用3,811 Token；不是工具返回文本的24,000额度，也不是只限制最终回答。保留事件不能还原未完成工具调用的完整参数，不能声称它在返回完整 Python JSON。

该参数限制生成响应中的可见和推理输出，触顶可返回 incomplete，见[官方说明](https://developers.openai.com/api/docs/guides/reasoning)。16,384低于[模型公布的输出能力](https://developers.openai.com/api/docs/models/gpt-6.1-sol)；本次亦实际返回了超过4,096的完成响应，没有绕过服务限制。

旧导出候选和Diff为空，保留的只读seed等于M2；但旧bridge只在CLI成功时导出临时编辑文件，失败scratch已销毁。因此只能确认没有已记录的完整补丁，**不能证明scratch中从未出现任何局部修改**。

## 最小接口修复

- `scene_rsi/meta/coding.py`：单响应额度4,096→16,384，保存服务提供的 `incomplete_details`。其余模型、工具、时限、请求数、输入和总Token预算保持原样。
- `scripts/native_codex_bridge.py`：CLI普通失败时也导出原有界、受路径检查的算法文件，外部保存真实Git Diff；保留非零退出状态。整个容器被强杀时仍无法保证导出。
- `scripts/check_native_editor.py`：增加本地假服务的“实际编辑后响应incomplete”回归，确认保留补丁并拒绝候选。
- `tests/test_coding_backend.py`：验证真实发送的响应额度、incomplete原因、用量登记和失败补丁不可进入候选消费器。`tests/test_paired_repair.py`的25,000合成配额用例显式保留其原4,096响应额度，维持原测试语义；未改变生产配额或接受规则。

原生提示原本已要求直接编辑 `algorithm.py`、最终仅简短说明，不返回源码或大型JSON，本轮没有改提示。公共接口仍为改进任务输入、代码补丁输出，元策略不依赖CLI事件格式。

## 唯一一次新构建

调用前[逐项核对](checks/task_equivalence.json)旧 `self_task.json`：objective、analysis、strategy、feedback、contract、文件全部相同；仅响应额度不同。使用全新独立Git工作区、冻结M2、Codex CLI 0.163.0、gpt-6.1-sol/medium。预算仍为600秒、最多6次服务请求、400,000总Token、单请求196,608字节、累计1MiB输入；权限和受限执行环境不变。

[构建诊断](build_diagnosis.json)、[完整事件](results/creation/coding/events.jsonl)、[CLI错误](results/creation/coding/cli.stderr)、[服务用量](results/creation/coding/model_calls.json)均已保留。

| 请求 | 输入 Token | 输出 Token | 响应 |
|---|---:|---:|---|
| 0 | 8,833 | 63 | completed |
| 1 | 18,774 | 43 | completed |
| 2 | 24,079 | 77 | completed |
| 3 | 33,973 | 316 | completed |
| 4 | 35,293 | **4,893** | **completed** |

第五响应4,846 Token归属工具调用、47为推理，没有incomplete，验证原上限问题已解除。随后CLI记录 `apply_patch verification failed: invalid patch: multiple operations target /workspace/algorithm.py`。补丁被拒绝；导出的22,441字节文件与冻结M2相同，Diff为0字节，candidate Git为空。没有可供判断完整性或执行资格的修改，也没有人工修补。

最终错误为本地relay的 `403 coding request quota`，不是GitHub或模型凭据403。此错误在路径、原始请求大小或已登记请求数检查处产生；已登记5次小于6，排除次数耗尽；总输入/总Token拒绝使用不同文案。结合 `/responses` URL和bridge行为，**高度指向下一请求超过196,608字节**，但拒绝请求未保存路径与Content-Length，无法报告确切字节数，仍保留这一不确定性。没有放宽输入预算或追加调用。

`results/creation/gate.json`的false由“没有完整合规编辑”产生；`qualification_executed=false`。它不是M3诊断/Probe/多目标行为资格失败。原21个反事实、最多42次策略执行的资格检查及两初始算法公平对照均未进入，未生成新的私有任务、M3或M4。

## 测试与成本

| 项目 | 实际结果 |
|---|---|
| 完整单元测试 | **251/251，31.894秒** |
| 真实受限环境安全回归 | **8/8**，私有文件、网络、进程、评价器写入、提限、内存、输出、死循环 |
| 真实CLI＋本地假模型正常编辑 | 通过；0次推理调用 |
| 真实CLI＋本地假模型编辑后截断 | 补丁保留，状态failed，候选拒绝；0次推理调用 |
| 新真实构建 | **0/1完成**，5次上游请求；不重试 |
| 新构建成本 | 输入120,952＋输出5,392＝**126,344 Token**；143.73秒 |
| 旧失败成本，单列 | **123,515 Token**；105.36秒 |
| 两次历史合计 | **249,859 Token**；无货币费用数据 |
| 新静态/资格/算法评估 | 均未进入，不报告能力分数 |

完整测试日志见[checks](checks)。首次测试有两个合成配额用例因新默认响应额度与其25,000总额相冲突而失败；修正用例的显式额度后251项通过，初次失败日志也保留。未改动生产预算算法。新构建本地编辑器CPU约0.806秒、单进程RSS峰值139.31MiB；它们不是容器聚合内存或远程推理资源。父代策略的两次受限执行资源另见 `results/parent/*/execution.json`。全程未使用GPU、Blender或训练。

## 复核与运行

实际工作目录为 `/srv/lab-disks/disk2/users/xymo/rsi/m3_response_fix_run`。以下只复核已有记录或使用本地假模型，不调用推理服务；验收输出目录必须不存在：

```bash
PY=/srv/lab-disks/disk2/users/xymo/rsi/world_run/env/bin/python
BASE=/srv/lab-disks/disk2/users/xymo/rsi/m3_response_fix_run
"$PY" -B -m unittest discover -s tests -v
"$PY" -B scripts/check_native_editor.py --out /tmp/m3-editor-offline --guard "$BASE/editor_guard"
"$PY" -B scripts/check_native_editor.py --out /tmp/m3-editor-incomplete --guard "$BASE/editor_guard" --fail-after-edit
"$PY" -B scripts/summarize_evidence_m3.py --run "$BASE/live" --out /tmp/m3-response-replay
```

本次真实调用是下面的同等反馈检查包装；`live`目录已存在，重复运行会在调用前拒绝。不要删除目录或循环重试来追加本轮预算：

```python
from functools import partial
from pathlib import Path
from scripts import run_evidence_m3 as experiment
from scene_rsi.meta.coding import run_codex, git, save
base = Path('/srv/lab-disks/disk2/users/xymo/rsi/m3_response_fix_run')
old = experiment.read(experiment.ROOT / 'experiments/evidence_m3/results/self_task.json')
assert all(r['passed'] for r in experiment.read(base / 'security/acceptance.json'))
assert experiment.read(base / 'editor_security/acceptance.json')['native_patch_applied']
assert experiment.read(base / 'editor_failure/acceptance.json')['failed_cli_patch_preserved']
backend = partial(run_codex, runtime=base / 'runtime', guard=base / 'editor_guard',
                  parent_git=git(experiment.ROOT, 'rev-parse', 'HEAD'))
def checked(task, directory):
    assert {k:v for k,v in task.items() if k != 'budget'} == {k:v for k,v in old.items() if k != 'budget'}
    differences = {k:[old['budget'][k],v] for k,v in task['budget'].items() if old['budget'][k] != v}
    assert differences == {'output_tokens_per_request':[4096,16384]}
    save(base / 'checks/task_equivalence.json', {'all_nonbudget_fields_identical':True,
         'budget_differences':differences, 'new_independent_workspace':True, 'coding_tasks_authorized':1})
    return backend(task, directory)
experiment.create(base / 'live', checked)
```

本轮按停止规则归档。若另行研究后续编码问题，应先用离线CLI测试验证单文件修改格式和原输入预算内的错误反馈处理，再登记新的构建；不能将本次失败改写为资格通过或M3能力退化。
