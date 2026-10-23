# Scene-RSI V0 — 3D 场景生成基线与独立验证器

> **版本定位：这是 Scene-RSI 的实验地基，不是已经实现的 RSI。** 当前不存在 LLM、Meta-Agent、跨代自修改或模型训练。代码专门为后续引入这类机制提供一个可复现、可验证的任务环境。

## 已实现

- `SceneSpec`：从 JSON 描述房间尺寸、固定资产、物体、空间约束、门口区域及通行要求。
- `FixedSceneAgent`：无学习、无模型调用的确定性布局基线（可替换）。
- `SceneVerifier`：**独立于 Agent** 的硬约束验证器，检查物体存在性、尺寸/边界、非法碰撞、`near_wall`、参考物体朝向下的 `in_front_of`、`keep_clear` 与粗粒度路径可达性。
- `scene_state.json`：机器可读结果；`evaluation.json`：结构化错误清单。
- `floorplan.svg`：二维俯视图；`scene.obj` + `scene.mtl`：可以导入 Blender 的**三维长方体代理几何**（不是精细物体模型）。
- 16 项自动测试，8 个基础场景，以及一个**明确可行但固定基线失败**的挑战样本。

## 快速开始

要求 Python **3.10+**，无需 pip 安装第三方包；从本项目根目录运行：

```bash
python -m unittest discover -s tests -v
python -m scene_rsi generate --spec examples/bedroom_001.json --out runs/bedroom_001
python -m scene_rsi benchmark --spec-dir examples --out runs/benchmark_summary.json
python -m scene_rsi benchmark --spec-dir benchmarks/dev --out runs/challenge_summary.json
python -m scene_rsi validate --spec benchmarks/dev/corner_desk_challenge.json --state benchmarks/golden/corner_desk_challenge.state.json
```

- `runs/bedroom_001/scene_state.json` 是固定生成器生成的物体位置与朝向。
- `runs/bedroom_001/evaluation.json` 是独立验证器返回的有效性、评分和错误。
- `runs/bedroom_001/floorplan.svg` 可直接用浏览器打开。
- `runs/bedroom_001/scene.obj` 可在 Blender（File → Import → Wavefront (.obj)）中打开。

## 项目结构

```text
scene_rsi_v0/
├── scene_rsi/
│   ├── schema.py                 # 共享只读数据格式
│   ├── geometry.py               # XY+Z 几何辅助方法
│   ├── agent/fixed_agent.py      # 将来允许 Meta-Agent 修改的任务代码
│   ├── evaluator/verifier.py     # 可信外部评分，禁止 Agent 修改
│   ├── export.py                 # SVG / OBJ 可视化
│   └── __main__.py               # 命令行接口
├── examples/                     # 8 个基础 smoke-test 场景
├── benchmarks/dev/               # 有明确已知缺陷的挑战任务
├── benchmarks/golden/            # 确认可行的挑战任务参考状态
├── tests/                        # 自动化测试
├── runs/                         # 实际运行结果
└── RESEARCH_PROTOCOL.md          # 后续开展 RSI 对照实验的协议
```

## 场景格式：约定非常重要

- 坐标单位为**米**，地面是 `XY` 平面，`Z` 轴向上。
- `SceneState.objects[].x/y` 表示物体**底面中心**；代理几何从 z=0 向上延伸资产高度。
- `yaw_deg` 仅允许 `0, 90, 180, 270`，表示相对于 `Z` 轴的逆时针旋转；资产自身的**前方**定义为本地 `+Y`。
- `assets` 是由外部数据提供的受信任尺寸，不允许未来可变 Agent 为规避验证而修改。
- `near_wall` 测量物体外轮廓与墙面的距离，不是中心距离。
- `in_front_of` 同时检查参考物体前向投影间隙和横向覆盖；不把世界 `-Y` 固定理解为「前面」。
- `regions` 使用矩形的左下角 `x/y` 与 `width/depth`。
- `access_paths` 使用障碍物膨胀后的四连通栅格 BFS，作为近似可通行性检验。

具体样例见 `examples/bedroom_001.json`。

## 本次实际验证结果（仅限自带样例）

| 验证项 | 结果 |
|---|---|
| 自动化测试 | 16 / 16 通过 |
| 8 个基础样例 | 8 / 8 满足预定义硬约束 |
| `corner_desk_challenge` | 固定 Agent **未能通过** |
| 同一个挑战的人工构造合法状态 | 验证器 **通过** |

基础样例用于检查**程序能否工作**，不是随机抽样的研究 benchmark，不能据此声称泛化成功率 100%。挑战任务中桌子必须**同时靠近北墙和东墙**；固定基线只按一个靠墙约束放置，因此会失败。这提供了一个可复现的后续算法改进目标。人工构造的合法状态证明这个挑战不是无解任务。

## 与严格 RSI 的关系

真正的 Scene-RSI 实验需要加入可持久修改代码的 `Meta-Agent`，然后让后代 Meta-Agent 在相同预算下从**全新初始生成器**出发改进，量化 `MetaImp@K`。不能把循环移动家具、反复修改单个场景、或固定 Meta-Agent 提升 Scene-Agent 的任务分数称为完整 RSI。

请参阅 `RESEARCH_PROTOCOL.md` 中的对照组、公平性约束和隐藏测试流程。

## 主要限制与下一步

1. **只验证带有固定长方体代理几何的室内布局。** 没有真实资产网格、材质、阴影、物理接触或照片级质量指标。
2. **二维足迹碰撞近似。** 不支持堆叠、悬挂、物理可放置性；栅格 BFS 不等于真实机器人或人体导航。
3. **样例很少且有人工构造偏好。** 最终研究需要自动生成及人工核验的更大任务集、跨场景族 held-out 测试和不重复使用的最终测试集。
4. **没有实际 RSI。** 下一步是在隔离执行环境中添加受约束的代码补丁、版本归档、固定 Meta-Agent 对照和后代 Meta-Agent 的独立 `MetaImp@K` 测试。
5. **验证器必须放在 Agent 无写权限的环境。** 当前目录分区只是示意；真正的研究运行需要容器/进程权限隔离、不可变测试资源、资源限额和审计日志。
