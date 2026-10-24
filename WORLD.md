# 第一个 Interactive Code World

这是一个可实际交互的室内推动任务：蓝色 Agent 将橙色方块推到绿色目标区域，深色方块为静态障碍。房间、物体尺寸和初始状态沿用原来的 `SceneSpec` / `SceneState`；原始 `SceneVerifier` 检查布局与完成状态，旧 `FixedSceneAgent` 和代码改进接口均保留。

第一版使用 **PyBullet 的真实三维形状碰撞和 TinyRenderer CPU 渲染**。控制是离散格点上的运动学移动和推动：每步 1 米、不可拉取、推动前对真实形状做 8 段扫掠检查。没有自由落体、惯性、抓取或连续力控制，不把它描述成完整机器人动力学。一个可移动方块让整个状态空间足够小，可以精确判断可解性。

## 安装与实际交互

原静态项目只需标准库。可选 world 环境使用 Python 3.11，运行依赖仅 PyBullet、NumPy、Pillow，版本见 `requirements-world.txt`。

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-world.txt
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m scene_rsi.world serve
```

本次服务器已创建独立环境，不修改系统 Python：

```bash
cd /srv/lab-disks/disk2/users/xymo/rsi/scene_rsi_phase2
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORLD_PY=/srv/lab-disks/disk2/users/xymo/rsi/world_run/env/bin/python
"$WORLD_PY" -B -m scene_rsi.world serve --port 8765
```

服务只监听 `127.0.0.1`。在自己的电脑上转发后访问 `http://127.0.0.1:8765`：

```bash
ssh -N -L 8765:127.0.0.1:8765 xymo@服务器地址
```

- WASD / 方向键移动和推动；R 或 Reset 重置。完成后本轮结束。
- Run fixed agent 使用固定测试 Agent；Show a reference route 使用独立完整搜索的参考路线。
- The workshop 是可完成的入门房间；Generated room 使用保存的生成器配置；Corner trap 是故意不可解的诊断样例。
- Load scene JSON 可加载 `examples/world/detour.json`：初始场景可解，但固定 Agent 会失败。Agent 推入死局后，界面区分“初始可解、当前被推死”和“初始场景本身不可解”。
- 相机按钮旋转实际三维视角；Export run JSON 保存动作、状态、推动、碰撞和完成记录。导入的场景只有 JSON 数据，没有代码入口。

## 可解性与固定 Agent

`planning.solve()` 搜索完整状态 `(Agent 格点, 方块格点)`，与执行器共享明确的移动/推动规则。找到目标为 `solvable`，穷尽图仍找不到为 `unsolvable`，节点预算耗尽为 `unknown`，不会把搜索截断算作不可解。结论针对这里的离散推动规则，不推广到连续物理或可抓取规则。

新 `FixedPushAgent` 只寻找能减少方块到目标曼哈顿距离的推动，并用普通路径搜索走到推动位置。它不调用可解性 oracle，也不修改自身。原来的 `FixedSceneAgent` 仍只负责静态摆放。

评估会把 oracle 找到的路线另行放进真实 PyBullet 世界重放。重放不一致记录为 `physics_mismatch`，不归咎于 Agent。固定 Agent 的结果分为：完成、场景不可解、可解但 Agent 能力不足、Agent 步数超时、可解性未知。当前默认最多 80 步；`timeout` 指步数预算，不冒充 OS 强制执行时限。

每个任务保存完整轨迹、参考动作、碰撞、推动、超时和实际 CPU/wall time。碰撞计数表示被阻止的碰撞动作，不是接触点数量。生成时间单独记录，评估时间包括搜索、固定 Agent 和参考路线重放；不包含图像导出。内存是进程生命周期 RSS 高水位，不冒充单任务峰值。所有渲染明确选择 DIRECT + ER_TINY_RENDERER，不加载 EGL/OpenGL 或训练模型。

## Codex 的结构化实验流程

本轮由当前 Codex 会话读取开发集失败汇总，写出 `examples/world/proposals.json`，再运行受信任实验控制器。程序没有额外调用 Codex CLI 或外部模型服务。自动实验只允许修改两个布尔配置：

```json
[
  {"name": "avoid-corners", "changes": {"avoid_corners": true}},
  {"name": "certified-layouts", "changes": {"require_solvable": true}}
]
```

每轮最多 4 个提案。不能通过提案修改房间尺寸、障碍数量、物理规则、评价器、任务预算或导入 Python。`require_solvable` 对生成结果做可解性筛选；它改变生成器产出的场景，不改变任务评价规则。

流程为：生成基线 → 固定 Agent 尝试 → 写 `dev_feedback.json` → Codex 提出 JSON 配置 → 开发集比较 → 独立验证集不退化检查 → 保存 `selected_generator.json`。反馈只含开发集聚合信息和配置，不含 val 场景、逐项结果或参考路线。完整轨迹和参考解保留在控制器实验目录中。生成 CLI 和服务可以通过 `--config` 使用保存的结果；本次有效配置也保存到 `examples/world/selected_generator.json`。

选择以开发集任务成功率为主、可解率为辅助，验证集要求两者均不下降；诊断分数不参与选择。房间固定 6×6 米、6 个障碍、一个 Agent 和一个方块，目标初始曼哈顿距离至少 2。各生成器使用相同 seed 列表，但筛选后的具体布局不同，所以这是**生成器质量对照，不是同一任务上的 Agent 能力提升**。

新 world 使用 dev seeds `100..111`、val seeds `1000..1007`。没有读取或修改原 Phase 1 数据集、参考解或保留测试。旧 P2A/P2B 任意代码提案与执行仍保持原有拒绝机制；本轮不启用任意 Python 候选执行，不实现 Meta-Agent 自修改，不声称 RSI。

## 复现实验与导出

输出目录必须是新的：

```bash
"$WORLD_PY" -B -m unittest discover -s tests -v
"$WORLD_PY" -B -m scene_rsi.world experiment \
  --proposals examples/world/proposals.json --out ../world_run/reproduction
"$WORLD_PY" -B -m scene_rsi.world generate --seed 2 --out /tmp/generated-room.json
"$WORLD_PY" -B -m scene_rsi.world evaluate \
  --task /tmp/generated-room.json --out /tmp/generated-room-result.json
"$WORLD_PY" -B -m scene_rsi.world demo \
  --task examples/world/demo.json --out ../world_run/export
```

`experiment` 保存每轮场景、结果、参考轨迹、聚合反馈和选中配置。`demo` 保存真实 CPU 三维帧、GIF、轨迹以及自包含 `replay.html`；HTML 是记录播放器，手动交互请使用 live server。

本次实测开发集：基线完成 **8/12**，筛选后完成 **10/12**；可解率从 **8/12** 变为 **12/12**。验证集：基线完成 **1/8**，筛选后完成 **5/8**；可解率从 **1/8** 变为 **8/8**。筛选后余下的 2 个 dev 和 3 个 val 失败是固定 Agent 能力不足，不是场景不可解。`avoid-corners` 在这批开发 seeds 上没有改善。样本很小，结果仅适用于这些冻结 seeds；生成时间开销和路线长度也一并报告，不声称普遍提升。

最终完整测试 **144/144 通过**，其中新增 17 项 world 测试，原有 127 项测试文件不修改。浏览器验收的 **14 项交互检查通过**，包含键盘、推动、完成、导出、死局、JSON 导入、参考重放和移动端布局。

浏览器验收脚本为 `scripts/check_world_browser.py`，它是可选开发工具，不是 world 运行依赖；本次在 Ubuntu 20.04 用 Playwright 1.48 / Chromium 130，禁用 GPU，只打开本机页面。服务器缺少的字体库只私有解压到实验目录，没有安装系统包。

当前尚未实现多方块任务、抓取搬运、自由动力学、模型自动调用 CLI、任意 Python 的隔离执行、神经视频模型或元级自修改。
