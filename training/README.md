# NNUE 训练框架

训练器使用正式的 `line11-dual-v1` 网络：四方向长度 11 的棋形映射、
双视角特征、局部空间交互，以及 WDL 价值头和 policy 着法头。
网络宽度在配置中调整，数据格式、训练循环与导出器共用。

目前可运行数据准备、单设备训练、验证、断点恢复、量化导出与整数参考推理。
原生引擎和网页已接入相同的 C++ NNUE 加载、增量推理与价值/policy 搜索接口。
已接入真实 Rapfi 教师采集和蒸馏训练；有效棋力仍需固定条件下的对战验证。

## 安装与设备

在仓库根目录运行，三组选一；`uv.lock` 固定 PyTorch 及各后端依赖。
Python 推荐 3.13，训练依赖不进入 C++ 引擎或网页。

```powershell
uv sync --locked --package gomoku-training --extra xpu
uv run --frozen --package gomoku-training --extra xpu gomoku-device --device xpu
uv run --frozen --package gomoku-training --extra xpu gomoku-device --device xpu --check --batch-size 16 --steps 10
```

CPU 使用 `--extra cpu`；NVIDIA CUDA 12.8 构建使用 `--extra cuda`。
三个 extra 互斥。更换后端时 uv 会同步相应环境，后续命令也应带上所选 extra。
不需要更换显卡来构建训练框架。

本机 Intel Arc B390 在 Windows + PyTorch 2.11 下使用 Level Zero V2 时，连续训练
会报 `UR_RESULT_ERROR_OUT_OF_RESOURCES`。训练运行层在 Windows 默认设置
`SYCL_UR_USE_LEVEL_ZERO_V2=0`，使用 Intel 提供的兼容适配器；仅影响当前进程及其子进程，
不会修改系统环境或显卡驱动。用户已有的同名环境变量会被保留，实际选择写入设备和训练记录。
相关开关见 [Intel SYCL 环境变量](https://github.com/intel/llvm/blob/sycl/sycl/doc/EnvironmentVariables.md)。

`device=auto` 按 CUDA、XPU、CPU 的顺序选择当前安装环境可用的设备，并写入运行记录。
明确指定不可用设备会报错，不会悄悄回退 CPU。默认 FP32；BF16 需在目标设备验证。
梯度累积由 `batch_size` 与 `micro_batch_size` 控制，前者是每次优化器更新的样本数。
默认有效 batch 16、micro batch 1；提高后者前应运行同样的设备检查。
参考 [PyTorch XPU 指南](https://github.com/pytorch/pytorch/blob/main/docs/source/notes/get_start_xpu.md)
与 [uv 的 PyTorch 后端配置](https://docs.astral.sh/uv/guides/integration/pytorch/)。

## 输入与数据准备

支持两类输入：现有的完整 `gomoku-record-v1` 棋谱，以及已经标注的
`gomoku-sample-v1` 局面。大数据使用逐行 JSONL/NDJSON；普通 JSON 文件
作为小型数据交换格式支持，读取时会整文件解析。

完整棋谱通过 C++ 规则重放验证，按最终结果产生 value 标签，实际落点作为
`played_move` policy 标签。该标签的 policy 损失权重为老师着法的 0.25。
现有确定性自对弈产生的数据不等于强老师数据，重复棋谱会被去重。

已标注局面的格式示例（只说明格式，字段中的老师身份须填写真实来源）：

```json
{
  "format": "gomoku-sample-v1",
  "gameId": "game-0001",
  "openingId": "opening-0001",
  "position": {
    "size": 15,
    "rule": "freestyle",
    "moves": [
      { "x": 7, "y": 7 },
      { "x": 8, "y": 7 },
      { "x": 6, "y": 6 },
      { "x": 8, "y": 8 },
      { "x": 5, "y": 5 },
      { "x": 9, "y": 9 }
    ]
  },
  "result": null,
  "teacher": {
    "id": "teacher-version-and-network-id",
    "perspective": "side_to_move",
    "score": { "kind": "eval", "value": 180 },
    "search": { "nodes": 100000 }
  },
  "policy": {
    "kind": "best_move",
    "moves": [{ "x": 4, "y": 4, "probability": 1.0 }]
  }
}
```

`result` 为绝对颜色的 `black_win / white_win / draw`，未知时省略或置 null。
老师评价必须统一为当前行棋方视角。可提供 `wdl: [win, draw, loss]` 概率或原始
`score`。WDL 完整时优先使用 WDL；标量通过训练配置中的 `score_scale` 做 tanh 归一化。
`mate / lower_bound / upper_bound` 会保留，但不参与普通标量回归。
不同老师的标量尺度需要在导入前校准到共同尺度，或分别组织数据集，不能直接混用。

policy 支持 `best_move / distribution / derived_distribution / played_move`，
最多 32 个非零项，坐标必须合法且不重复，概率和为 1。由评分转换的分布必须附带
`transform` 描述。没有 policy 标签可以省略，不构造虚假的访问分布。
原始文件的摘要和老师描述进入清单；原始文件须保留，以便重新构造目标。
Rapfi 进程采集、固定版本 binpack 转换与教师身份校验由 tools 完成，见
[教师工具说明](../tools/README.md)。prepare 可直接读取其已发布清单并逐批校验摘要：

```powershell
uv run --frozen --package gomoku-training --extra xpu gomoku-teacher install
uv run --frozen --package gomoku-training --extra xpu gomoku-teacher generate --config tools/configs/rapfi.toml
uv run --frozen --package gomoku-training --extra xpu gomoku-prepare --teacher-corpus data/teachers/rapfi-v1/manifest.json --output data/training/rapfi-v1 --validation-fraction 0.2
uv run --frozen --package gomoku-training --extra xpu gomoku-train --config training/configs/rapfi-v1.toml
```

此前须执行 `pnpm engine:build`，数据校验会调用 C++ Worker。
Rapfi 对应 [蒸馏配置](configs/rapfi-v1.toml)，其 `score_scale = 400` 来自教师
`sigmoid(eval / 200)` 的评分定义。默认正式网络宽度 64，有效 batch 32、micro batch 4、
1,500 次优化器更新，老师/结果权重为 0.9/0.1，policy 权重为 0.5。
采集在 CPU 上运行，学生网络可使用本机 Intel XPU。生成量、搜索预算、训练步数可独立扩展。
这次教师任务采用 70% / 20% / 10% 的开局划分；阶段快照和完整数据沿用同一参数。

也可以直接读取自己的已标注 JSONL：

```powershell
pnpm engine:build
uv run --frozen --package gomoku-training --extra xpu gomoku-prepare data/annotations.jsonl --output data/training --size 15 --rule freestyle
```

prepare 使用磁盘 SQLite 去重和记录进度，默认从第 6 手取样，按开局组划分
80% / 10% / 10% 的 train / validation / test。比例是稳定哈希分配目标，
小数据集不保证各组数量恰好符合比例，也不会为了凑数量把同一开局拆进不同集合。
完整棋谱默认按前 6 手的对称规范棋盘分组；标注局面使用提供的 openingId，
同一个 gameId 不能改变组。局面的旋转和镜像被视为重复，全局保留输入顺序中的第一次。
需要合并多个来源的标签时，应先完成标注合并再 prepare。

准备过程使用有界缓冲，最终发布二进制 NumPy 分片和带摘要的 manifest，
训练只按需 mmap 分片。SQLite 暂存与最终数据在发布时会同时占用磁盘空间。
`--resume` 要求输入摘要和所有准备配置一致；它从最后提交的记录继续。
已发布数据集不允许覆盖，训练开始时校验所用分片摘要。

## 训练、恢复与验证

编辑 [正式配置](configs/line11.toml)。所有路径相对配置文件所在目录解析。
每个数据集固定一种规则与棋盘尺寸；相同结构支持 15/20 路，模型按实际训练组合发布。

```powershell
uv run --frozen --package gomoku-training --extra xpu gomoku-train --config training/configs/line11.toml
uv run --frozen --package gomoku-training --extra xpu gomoku-train --config training/configs/line11.toml --resume
```

每次运行保存 `run.json`、数据 manifest 快照、逐行指标、`initial.pt`、`last.pt`、`best.pt` 和状态。
第 0 步先评估并保存初始权重，供相同独立集合上的训练前后比较；最佳模型不会劣于初始验证值。
检查点包含模型、优化器、学习率计划配置、步数、采样游标及随机数状态。
同时保存 PyTorch/设备信息、Git 版本和训练源码摘要，以识别尚未提交的代码变化。
写入通过同目录临时文件原子替换；读取使用 `weights_only=True`。
指标包含老师/结果/policy 的有效样本数、普通教师标量 MSE、policy 前 1/5 名一致率，
以及 mate 样本数、交叉熵和胜负分类准确率。
缺失标签不会按零值或和棋处理。这些衡量拟合和泛化，不代表 Elo 或比赛胜率。

`--stop-after N` 在完成 N 次更新后暂停，不改变总步数或学习率计划。
Ctrl+C 请求在当前完整更新完成后保存。`--resume` 拒绝更改影响训练的配置和数据；
可以改变输出位置、设备或 CPU 线程数，以支持迁移，跨设备不承诺逐位相同轨迹。
从已有模型开始新一轮数据训练，使用新的 output 和 `--initialize 路径/last.pt`，
重新开始优化器与采样状态，同时记录来源检查点摘要、步数、数据摘要和代码来源。
进程崩溃后从最近成功发布的检查点恢复。

混合监督之后可按独立 validation 的评分误差决定是否做教师校准。
[校准配置](configs/rapfi-calibrate.toml) 保持同一正式网络，执行固定 1,000 步、
使用教师评分训练 value，同时以 0.1 权重维持 policy；原始终局标签仍保留在数据内。
该阶段的损失不能直接与混合阶段日志比较，应用 assess 统一配置重算。

```powershell
uv run --frozen --package gomoku-training --extra xpu gomoku-train --config training/configs/rapfi-calibrate.toml --initialize artifacts/training/rapfi-v1/best.pt
```

确定校准策略时使用 validation，test 留作策略确定后的最终报告。

教师标签的优先级为显式 WDL、带符号 mate、普通 eval。正/负 mate 按当前行棋方
编码为确定胜/负，用交叉熵监督 value；距离不作为普通分数缩放。
缺失标签、零 mate 和上下界标签不被当成确定胜负。旧版校准漏掉了 mate 的 value
监督；[修正后的对照配置](configs/rapfi-mate-v2.toml) 保持原网络、数据和 1,000 步预算，
从旧校准的相同初始检查点开始，隔离标签修正的影响：

```powershell
uv run --frozen --package gomoku-training --extra xpu gomoku-train --config training/configs/rapfi-mate-v2.toml --initialize artifacts/training/rapfi-calibrated-v1/initial.pt
```

新旧模型应使用同一个修正后的 loss 配置重算独立集，并使用 tools 的固定开局
配对对战比较。损失下降本身不等于棋力提升，也不自动替换网页发布模型。

本轮 3,188 个测试局面中，mate 标签准确率从 55.40% 提高到 66.19%，普通教师
标量 MSE 从 0.06604 降至 0.06455；终局结果分类准确率从 70.29% 降至 56.18%。
同搜索的 16 盘配对战绩为 5 胜、2 和、9 负，故 v2 未晋级网页模型。
完整对照见 [本轮记录](../docs/benchmarks/2026-09-21-vcf.json)，后续调参需要新的保留开局。

训练按固定间隔查看独立 validation 的固定样本上限。完整测试集单独运行：

```powershell
uv run --frozen --package gomoku-training --extra xpu gomoku-evaluate artifacts/training/run01/best.pt --manifest data/training/manifest.json --split test --device xpu
```

当前训练执行器是单进程单设备，多进程启动会明确拒绝，避免无同步的重复训练。
数据分片、网络定义和检查点边界可复用；DDP 通信和跨 rank 状态协调尚未实现。

## 导出与运行时边界

```powershell
uv run --frozen --package gomoku-training --extra xpu gomoku-export artifacts/training/run01/best.pt --output artifacts/models/run01 --device cpu
```

导出会枚举两套完整棋形表，写出 `weights.gnn`、manifest 和固定参考向量。
表项与权重为 int16，NumPy 参考实现以 int64 累加并使用精确的整数舍入。
布局详见 [line11 格式](../models/line11-format.md)。默认 64 通道的两套完整表
约占 97 MiB，另加输出头权重；这也是后续网页发布需要测量的实际资源成本。

导出文件可由 Python 整数参考和 C++/WASM 加载器读取，但新导出仍默认
`runtimeCompatible=false`，防止未验收模型自动晋级。已发布的 `rapfi-calibrated-v1`
通过独立参考、原生/WASM 精确一致性和完整对局检查；数值一致性、训练损失和棋力
是不同的验收项目。运行 `pnpm model:verify` 检查当前固定发布模型。

可在训练和导出后生成完整的独立集报告，同时用 32 个真实测试局面比较整数导出与 PyTorch：

```powershell
uv run --frozen --package gomoku-training --extra xpu gomoku-export artifacts/training/rapfi-v1/best.pt --output artifacts/models/rapfi-v1
uv run --frozen --package gomoku-training --extra xpu gomoku-assess artifacts/training/rapfi-v1/best.pt --baseline artifacts/training/rapfi-v1/initial.pt --manifest data/training/rapfi-v1/manifest.json --export-manifest artifacts/models/rapfi-v1/manifest.json --output artifacts/assessment/rapfi-v1 --device xpu
```

`assessment.json` 包含初始/选中模型的完整 validation/test 指标、产物摘要和数值误差，
比较时统一采用选中模型的监督权重和评分尺度，避免两次配置不同导致损失不可比。
`reference-vectors.json` 保存真实局面及整数输出，供后续 C++/WASM 回归。
测试集只用于最终报告，最佳检查点仍由 validation 选择。数值误差超出阈值时返回失败，
仍保存实际测量结果。报告不自动晋级模型，也不宣称已测棋力。

## 检查

```powershell
uv run --frozen --package gomoku-training --extra xpu python -m unittest discover -s training/tests -v
```

CI 使用 CPU extra，在 Windows/Linux 验证数据、恢复和导出，并复用同次 CI
构建的原生规则程序。小数据、较窄通道配置只用于确定性测试，仍使用同一个正式网络。
默认网络另有真实前向、反向和优化器更新检查。长时间训练、设备性能及棋力对战独立运行。

还可运行默认完整宽度的端到端验证（输出目录必须为空）：

```powershell
uv run --frozen --package gomoku-training --extra xpu python training/tests/verify_pipeline.py --output artifacts/pipeline-check --device xpu --steps 6
```

它生成明确标记为测试用途的 96 条合成标注，执行数据准备、训练暂停/恢复、完整测试集评估、
两套完整棋形表导出，并比较 8 个局面的全部 value/policy 输出。`verification.json` 保存结果；
其中的样本、损失与权重仅用于验证实现，不代表老师质量或实际棋力。CPU 使用 `--device cpu`。
