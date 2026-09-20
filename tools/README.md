# 数据与教师工具

`gomoku-tools` 独立于 PyTorch、网页和比赛程序。教师在独立 CPU 进程内运行，
输出原始棋局和搜索标注；`gomoku-training` 校验、分片并训练自己的网络。
Rapfi 的权重不转换成我们的权重，也不会被打包进网页。

## Rapfi 教师

使用官方 [Rapfi 250615 发布包](https://github.com/dhbloo/rapfi/releases/tag/250615)，
固定程序、配置和随包的 mix9svq NNUE。安装器校验整个发布包的 SHA-256，
随后记录程序、配置和权重各自的摘要。更换其中任何一个文件都需要建立新的教师身份。
外部引擎与其许可证保存在 Git 忽略的 `.tools/rapfi-250615`。
引擎按 GPL-3.0 发布，随包网络仓库按 CC0 发布；下载时一并保留许可证。

在仓库根目录执行：

```powershell
pnpm engine:build
uv run --package gomoku-tools gomoku-teacher install
uv run --package gomoku-tools gomoku-teacher generate --config tools/configs/rapfi.toml
```

若已安装训练依赖，使用 `uv run --frozen --package gomoku-training --extra xpu gomoku-teacher ...`
可保留当前 XPU 环境。CPU/CUDA 环境使用对应 extra。

[采集配置](configs/rapfi.toml) 默认是 Windows x64 AVX2 程序；不具备 AVX2 的机器选择
同一发布包内的 SSE 程序，其他平台选择对应的官方可执行文件。Linux 需安装
`bsdtar/libarchive` 来解压 7z，并确保所选程序可执行。安装器不会修改或覆盖已有的不同文件。

默认任务为 16 批、每批 16 盘、8 个并发教师进程，每手请求 100,000 节点。
CPU 核心较少或需要保留更多交互资源时可调低 workers，恢复时也允许调整。
每个教师内部仅用一个搜索线程，避免多线程最佳线程与记录评分来源不同。
使用 6–10 手随机平衡开局，平衡搜索预算为 100,000 / 200,000 节点。
原始 binpack 不记录实际消耗节点，元数据明确区分 requestedNodes 和实际未知值。
棋盘配置覆盖已验证的 freestyle 15/20 路和 standard 15 路；其他组合拒绝运行。

启动前必须通过 `TRACESEARCH` 确认 mix9svq 权重真实加载并返回 NNUE 评价；
只成功启动程序或退回手工评估都不能通过。关闭基于连续小分的和棋裁定，
设置 mate-ply 为 1；结果仍须由本仓库 C++ 规则重放确认。未完成局不伪造胜负标签。

Rapfi 这个版本的自对弈随机数来自毫秒时钟，没有命令行种子。采集器错开各进程启动，
减少同时启动产生相同开局流的机会；后续仍会做全局对称去重。再次生成不承诺逐盘相同，
可复现训练依靠保存的原始数据、摘要、配置、分片及训练 RNG 状态。

## 产物和恢复

每批包含以下文件，全部写完后原子发布 `job-00000.json`：

- `job-00000.binpack`：未经改写的教师原始数据。
- `job-00000.annotations.jsonl`：当前行棋方视角的真实搜索分数、最佳着法和可验证结果。
- `job-00000.games.jsonl`：完整落子和 C++ 重放状态。
- `job-00000.log`：教师命令输出；配置错误即使进程退出码为 0 也被识别为失败。

所有批次完成后发布 `manifest.json`。并发写入同一目录会被操作系统文件锁拒绝，
进程异常退出后锁自动释放。失败、超时或中断后：

```powershell
uv run --package gomoku-tools gomoku-teacher generate --config tools/configs/rapfi.toml --resume
```

恢复会校验已完成批次并跳过它们；未完成批次重新执行。原始参数及老师身份必须相同，
允许改变 workers 数量。不要把 `.partial` 文件作为已完成数据导入。

采集还在运行时，可以把已完成批次冻结成独立快照，用于并行训练：

```powershell
uv run --package gomoku-tools gomoku-teacher snapshot data/teachers/rapfi-v1 --output data/teachers/rapfi-snapshot01
```

快照复制并校验所有原始产物，只发布完整批次。清单记录 `generationComplete` 和
原任务批次数，避免把阶段快照当成完整采集。快照输出必须为空；快照发布后不可覆盖。

## 格式与标签

读取器只接受固定的 **未压缩 `rapfi-binpack-250615`**，不把未知 binpack 当作同一格式。
头部、坐标、规则、开局、MultiPV、缺分标志、截断数据均有显式校验。
MultiPV 信息原样保留，但 policy 监督使用实际搜索最佳着，未制造访问次数分布。
当前采集配置为单 PV。

普通评分按官方配置使用 `winrate = sigmoid(eval / 200)`。我们的有界标量目标
是 `2 * winrate - 1 = tanh(eval / 400)`，因此对应训练配置的 `score_scale = 400`。
绝对值不小于 29,500 的杀棋分数单独存为 mate，不作为普通标量回归；
缺失分数不填 0，不从单个标量虚构完整 WDL。
最终胜负使用黑/白绝对颜色，只在原生规则确认终局且与教师报告一致时加入监督。
原始评分、结果与其来源仍保留，后续可改变混合权重。

然后转入正式训练流程：

```powershell
uv run --frozen --package gomoku-training --extra xpu gomoku-prepare --teacher-corpus data/teachers/rapfi-v1/manifest.json --output data/training/rapfi-v1 --validation-fraction 0.2
uv run --frozen --package gomoku-training --extra xpu gomoku-train --config training/configs/rapfi-v1.toml
```

prepare 校验每批标注摘要、按前 6 手的 D4 规范开局隔离集合，并对全部局面做 D4 去重。
这组命令使用 70% / 20% / 10% 的开局哈希区间，阶段快照和最终数据必须保持相同划分参数。
完整运行、检查点、评估和导出说明见 [训练文档](../training/README.md)。

## 回归检查

`tests/fixtures/rapfi-250615.binpack` 是由真实官方教师生成的两盘回归夹具，
旁边的 JSON 保留来源和摘要。CI 使用这个小文件检验解析及原生规则一致性，
不联网调用教师、不把夹具当作训练数据。

```powershell
uv run --package gomoku-tools python -m unittest discover -s tests/python -v
```
