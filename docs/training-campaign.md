# NNUE v3 训练闭环

本轮在现有 `line11-dual-v1` 完整网络上改进权重。普通教师数据、MultiPV 数据、
当前引擎错题、训练、独立对战和原生/WASM 对齐检查都能实际执行，并保存断点。
代码入口为 `training/src/gomoku_training/campaign.py`。采集和训练未完成时，
不能把运行配置中的目标数量当成已取得的结果。

## 五项改动

1. **统一 value 目标。** 模型输出的价值为 `u = P(win) - P(loss)`。
   Rapfi 普通评价使用 `tanh(score / 400)`，有符号 mate 使用 `+1 / -1`，
   真正终局结果使用 `+1 / 0 / -1`；全部在同一尺度做 MSE。
   普通局面中教师/结果权重为 0.95/0.05；教师已经证明 mate 时，后续棋手走错
   产生的终局结果不稀释该标签。未知结果和分数上下界不伪造为精确标签。
   旧配置默认 `legacy`，新配置明确使用 `expected_score`。

2. **至少一百万个不同局面。** 普通采集每步 20,000 节点，开局平衡使用
   100,000/200,000 节点，随机开局 6–12 手；每盘最多抽取 64 个局面。
   抽样覆盖对局各阶段，并保留进入/离开 mate 区间的邻近位置。
   按 D4（旋转和镜像）规范化后的棋盘、规则、行棋方做全局去重；
   采集以 SQLite 中实际不同局面数达到 1,000,000 为停止条件。
   训练准备再次用 C++ 检查合法性并独立去重，最终分片也必须达到这个数量。
   训练增广的八种方向不增加这个计数。按前六手的规范化开局分组，固定
   80%/10%/10% 训练、验证、测试划分，同盘及其搜索分支不跨组。

3. **真实 MultiPV policy。** 另采集 128 × 16 盘、每步 100,000 节点、
   最多四条变化，长局逐步减少候选数，每盘最多取 48 个位置。
   普通候选分数以温度 200 做 softmax；多个已证明获胜的候选均分目标概率。
   未知分数和普通/必杀分数不混在一个 softmax 中，全部败势保留老师首选着法。
   标签标明 `derived_distribution` 和转换方式，保留原始候选分数。
   这不是 MCTS 访问次数分布。相同概率下仍保留教师原始首选着法，
   同时记录 top-1、top-5 及半径二候选池中 top-16 的覆盖率。
   后一项是安静局面的几何候选池指标，不包含强制防守/必胜着法规则。

4. **当前引擎错题回流。** 从训练组冻结 256 个不同开局，让当前网页权重
   在原生引擎中以 80 ms、最大深度 8 自对弈。每盘最多选 32 个根局面、
   16 个非终局主变化末端；根局面兼顾均匀取样与行棋前后的价值突变。
   用 Rapfi 200,000 节点、MultiPV=4 重新分析。
   `YXBOARD/YXNBEST` 适配器只使用同一完整迭代的候选，保留原始协议日志，
   不拼接被中断的新深度与旧深度候选。主变化分支不会继承实际对局的胜负。
   自对弈和教师采集的步数上限只截断采集，未终局棋谱的结果仍然未知。
   合并数据时按回标、MultiPV、普通采集顺序保留重复局面的较高预算标签。

5. **正式训练与棋力晋级。** 相同完整网络先利用已采集的冻结数据训练
   5,000 步，其最佳权重继续初始化百万局面的 50,000 步主训练。
   两阶段 batch=64，总计 3,520,000 次样本训练；热身数据和权重都继续被使用。
   本机配置是 Intel Arc B390、BF16、micro-batch=8，设备可选 CPU/XPU/CUDA。
   AdamW、预热/余弦学习率、D4 增广和量化感知训练共用原有框架。
   只比较主训练的验证最佳与最后一个 checkpoint，先在 16 个验证组开局上
   以 300 ms 交换黑白选出一个候选，再对该候选做 32 个全新测试组开局、
   共 64 盘的 **3 秒**独立晋级赛。两个开局集在候选训练前冻结，
   排除之前用过的八个评测开局。晋级要求无引擎失败、总得分超过 50%，
   且以独立开局对为单位的单侧符号检验 `p <= 0.05`。
   该规则可能拒绝实际小幅变强的模型；不会把下降的 loss 当成棋力提升证据。

## 运行和恢复

前提是已安装项目 Python 环境、原生 worker 与固定版本 Rapfi，并有作为
incumbent 的 `.pt` checkpoint 和已导出的 `.gnn`（默认使用本仓库本机已有的
`rapfi-calibrated-v1`）。所有大数据和权重保存在忽略目录 `data/`、`artifacts/`。

完整运行，达到晋级条件时发布 release、更新网页权重并提交推送：

```powershell
.venv/Scripts/python.exe -u -m gomoku_training.campaign --publish --push
```

默认不带 `--publish --push` 时生成本地决策和所有验证产物。
评测默认 `--arena-workers 4`，同时运行四组交换黑白的对局；每路独立持有裁判和
两份引擎进程，每步仍分别使用完整的 300 ms / 3 秒预算。每局结束即保存结果，
并行完成顺序不影响计分，恢复时按开局 ID 和执棋颜色跳过已完成的对局。
每局的 `arenaWorkers` 记录执行时的并发设置，旧报告中缺省表示串行。
在核数或内存较少的机器上可用 `--arena-workers 1`；并发会改变 CPU 竞争和每秒
搜索量，因此比较棋力时应保持双方相同的调度环境，不把加速倍数当成棋力指标。
单命令能完成缺失的阶段；若希望采集与训练并行，可分别先启动下面三项。
本轮已经按并行方式启动。第二、三项会使用相同目录锁，避免重复写入。

```powershell
.venv/Scripts/python.exe -u -m gomoku_training.corpus --config tools/configs/rapfi-v3-general.toml --output artifacts/campaigns/rapfi-v3/collection --minimum 1000000 --workers 8 --max-batches 20 --wait-existing
.venv/Scripts/python.exe -u -m gomoku_tools.rapfi generate --config tools/configs/rapfi-v3-multipv.toml --resume
.venv/Scripts/python.exe -u -m gomoku_training.campaign --publish --push
```

相同命令可恢复已有进度。已完成的教师任务和来源文件有 SHA-256 校验；
每盘自对弈及教师回标单独落盘；数据准备使用 SQLite 事务；训练保存模型、
优化器、随机状态、采样器位置；arena 每完成一盘原子保存，恢复只补未完成对局。
训练配置或数据改变时需新输出目录，不能冒充同一条断点继续。
Rapfi 自对弈发生异常提前退出时最多尝试三次，失败日志保留，不发布不完整任务。

主要进度文件：

- `artifacts/campaigns/rapfi-v3/status.json`：整轮所处阶段或失败原因。
- `artifacts/campaigns/rapfi-v3/collection/status.json`：已完成批次的真实去重数量。
- `artifacts/training/rapfi-v3-warmup/metrics.jsonl`、`artifacts/training/rapfi-v3/metrics.jsonl`：训练和验证曲线。
- `data/teachers/rapfi-v3-relabel/manifest.json`：自对弈回标总量、搜索分支数、教师分歧数。
- `artifacts/campaigns/rapfi-v3/data-quality.json`：各集合的样本数、mate/普通评价、policy 类型、未知/终局结果和局面阶段分布。
- `artifacts/campaigns/rapfi-v3/promotion-arena.json`：完整 3 秒对局和每步搜索记录。
- `artifacts/campaigns/rapfi-v3/publication.json`：是否通过晋级，是否已发布、推送及 release 链接。

训练和对战会持续数小时，耗时取决于本机吞吐和棋局长度。自动流程仅在原生和
WASM 都对齐所选权重的整数参考输出后考虑发布；晋级赛没通过时保留网页现有模型。
对战计时是本机原生引擎的 3 秒预算，手机浏览器的单位时间搜索量仍取决于设备。
发布后的模型通过不可变 GitHub release 地址和 SHA-256 固定，GitHub Actions
沿用现有 Pages 构建；无需反复轮询 CI。

## 有限检查

新增检查针对容易污染整轮结果的边界：mate 目标及视角、截断棋局的未知结果、
MultiPV 的完整迭代边界、软标签并列第一的 D4 变换、全局去重、主变化结果隔离、
arena 断点恢复和晋级门槛。实际教师回标已用历史问题棋谱运行。
训练结束只做所选 checkpoint 的保留集评价、八个参考局面的 Python/C++/WASM
一致性及引擎增量/撤销检查，晋级时再构建一次网页。不会以全仓库重复检查替代训练。
