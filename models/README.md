# 模型产物

当前网页发布模型为 `rapfi-calibrated-v1`，由真实 Rapfi 教师数据蒸馏训练，
仅支持 15×15 自由五子棋。原生与 WASM 共享 C++ 增量推理实现。
`web-model.json` 固定实际发布的模型 ID、规则、尺寸、字节数、SHA-256 与 Release URL。

训练框架目前产生两类产物：

- `last.pt / best.pt`：PyTorch 模型、优化器、采样及随机数状态，用于训练恢复。
- `weights.gnn / manifest.json / reference-vectors.json`：两套完整棋形查找表、
  int16 权重及跨语言参考向量。独立 NumPy 参考使用 int64 运算。

具体结构、编码、舍入和布局见 [line11-dual-v1 格式](line11-format.md)，
操作命令见 [训练框架](../training/README.md)。默认 64 通道的导出表约 97 MiB。
新导出默认 `runtimeCompatible=false`，独立通过 Python/C++/WASM 一致性检查后，
发布 manifest 才记录兼容状态与验证报告。检查点不直接进入运行时。
已发布模型通过 64 个真实局面的精确一致性、1,490 次增量/撤销检查及 WASM 完整自对弈；
这不代表已经完成 Elo 或比赛棋力认证。

部署权重清单至少固定：格式版本、特征版本、结构、规则、棋盘尺寸、量化方式、
文件大小、SHA-256，以及训练数据和引擎版本。不同模型的缓存必须隔离或清空。

大权重和训练数据放在发布产物或外部存储中，不提交到普通 Git 历史。
