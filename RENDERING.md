# Blender 资产导出与实际渲染

从 `26c130c` 扩展，只增加可选视觉导出。现有 PyBullet、SceneSpec、SceneState、FixedSceneAgent、world 生成器、原始 Verifier 和可解性搜索均保留。没有新机器人任务、模型训练或 Meta-Agent 自修改。

## 接口与固定资产

`scene_rsi.render.export_scene(spec, state, out, blender=..., device='auto', gpu=...)` 接收原始数据对象；CLI 接收现有 world 的 `spec + initial` JSON，`--state` 可替换为运行中的 SceneState。输出目录必须是新的。

固定资产在 `assets/world/library.blend`：带轮子、雷达和传感器的蓝色 Agent；有板条、框架和紧固件的木箱；带门、把手和百叶通风口的储物柜。它们是项目自己制作的多材质 Mesh，分别对应现有 `agent / crate / obstacle`，不是外部下载资产，也不读取模型提供的代码。原始静态卧室等其他资产名称尚无视觉映射，会明确报不支持。

资产本地范围为 XY `[-0.5, 0.5]`、Z `[0, 1]`，前向 +Y；按 SceneSpec 的宽深高缩放、SceneState 的底面中心平移、yaw 绕 +Z 旋转。所有细节都在原碰撞包围盒内。原评价器只看原来的几何；视觉 Mesh 不替代碰撞形状，也不反过来改变任务有效性。`scene.json` 只含场景、变换与固定相机数据，不含参考路线。

每个目录包含：

- `scene.blend`：真实 Mesh、Principled 材质、三相机、日光和面积灯；自包含，无外部纹理。保存时默认 CPU，方便在其他电脑打开。
- `scene.glb`：嵌入 Mesh/PBR 材质、三相机与 KHR_lights_punctual 日光；标准 glTF 的 Y-up，重新导入后恢复同一布局。面积灯和大型摄影棚背景不进入 GLB，查看器需要自己的环境照明，不能保证与 Cycles 像素一致。
- `overview.png / reverse.png / top.png`：两个相反斜视角与俯视角。相机参数固定，以房间中心 `(width/2, depth/2, 0.35)` 为目标，偏移是最大边长乘 `(1.15,-1.35,1.35)`、`(-1.35,1.15,1.35)`、`(0,0,2.15)`；正交宽度为最大边长的 1.75 倍，不随任务成败选择视角。
- `result.json / render.json / blender.log`：独立几何检查、真实退出状态、逐视角时间、实际设备、CPU 时间和 Blender 进程 RSS 高水位。RSS 不是 GPU 显存峰值。

墙在视觉上统一剖切至 0.5 米，便于看清任务；原房间高度、边界和碰撞规则不变。当前输出静态快照，不导出动作动画，也不接管现有实时交互 UI。

## 可复现运行

Python 3.11 的 world 依赖仍为 `requirements-world.txt`；新增导出器只用 Python 标准库，另外需要可运行的 Blender 4.2。优先复用现有环境。本次实际使用：

```bash
cd /srv/lab-disks/disk2/users/xymo/rsi/scene_rsi_phase2
WORLD_PY=/srv/lab-disks/disk2/users/xymo/rsi/world_run/env/bin/python
BLENDER=/srv/lab-disks/disk2/users/xymo/projects/bvb-repro/runtime/blender-4.2.0-linux-x64/blender
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1

# 固定库已提交；需要重新制作时才运行这一步。
CUDA_VISIBLE_DEVICES='' "$BLENDER" -b --factory-startup --disable-autoexec --threads 4 \
  --python-exit-code 1 --python scripts/build_world_assets.py -- assets/world/library.blend

# 在运行时选择空闲 GPU；可用 nvidia-smi 查询，不要照抄已被其他作业占用的设备。
GPU=1
"$WORLD_PY" -B scripts/render_world_examples.py --blender "$BLENDER" \
  --gpu "$GPU" --device auto --size 768 --samples 48 --out /tmp/scene-render-examples

# 只导出一个现有任务；--state 可指定后续时刻的 SceneState。
"$WORLD_PY" -B -m scene_rsi.render --task examples/world/demo.json \
  --blender "$BLENDER" --device cpu --out /tmp/scene-render-cpu

# 普通测试 + 真正的 Blender 文件回读验收，后者不使用 GPU。
CUDA_VISIBLE_DEVICES='' "$WORLD_PY" -B -m unittest discover -s tests -v
CUDA_VISIBLE_DEVICES='' "$BLENDER" -b --factory-startup --disable-autoexec --threads 4 \
  --python-exit-code 1 --python scripts/check_blender_exports.py -- \
  /tmp/scene-render-examples /tmp/blender-acceptance.json
```

`auto` 只启用第一个可见 CUDA 设备，初始化或渲染失败时记录原因并回退 CPU；`cpu` 隐藏 CUDA 设备并强制 CPU；`cuda` 用于严格 GPU 验收，失败直接报错。默认最多 4 个 CPU 渲染线程。此处运行的是仓库里受信任的 Blender 脚本，不是任意候选 Python 执行器；旧 P2 的安全拒绝机制保持原状。

## 本次实际实验

九张正式图像均由已有 **Blender 4.2.0 / Cycles / NVIDIA A100-PCIE-40GB / CUDA** 生成，768×768、48 samples、降噪开启；仅使用一张指定空闲 GPU。数据在 [examples/rendered](examples/rendered)，下表为 seed 与既有生成器参数，均设置 `require_solvable=True`。

| seed | 房间 / 障碍 | 几何有效 | 参考路线实际重放 | 固定 Agent | 三视图及导出耗时 |
|---|---|---|---|---|---|
| 11 | 6×6 / 6 | 是 | 完成 | 完成 | 13.78 秒 |
| 22 | 7×5 / 8 | 是 | 完成 | 能力不足 | 13.10 秒 |
| 33 | 5×7 / 5 | 是 | 完成 | 完成 | 12.82 秒 |

这是视觉链路的样例验收，不是 Agent 改进实验；2/3 的任务完成率与渲染无关。三个场景均可解，无碰撞或超时。保留既有控制器的逐步轨迹与参考重放记录；不使用隐藏测试。

已有 Blender 4.2.10 也能发现 A100，但在驱动 535.247.01 上实际 CUDA 与 OptiX 渲染均报 `Unsupported PTX version`；4.2.0 的实际渲染成功，因此没有更改服务器驱动。4.2.10 的自动 CPU 回退另外实际验证成功，相关结果和日志在 `examples/rendered/checks/`。

完整测试 **151/151 通过**（原 144 项 + 新 7 项，原测试不修改）。另用 Blender 重新打开三个 `.blend` 并重新导入三个 `.glb`，检查所有场景物体的包围盒、材质、三相机和日光；验收结果在同目录保存。没有增加哈希校验或自定义协议版本。

Blender 官方接口参考：[GPU 渲染](https://docs.blender.org/manual/en/4.2/render/cycles/gpu_rendering.html)、[glTF 导出](https://docs.blender.org/api/4.2/bpy.ops.export_scene.html)。
