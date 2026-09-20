# 已训练 NNUE 的原生与网页运行时

当前发布 `rapfi-calibrated-v1`，使用真实 Rapfi 教师数据训练，支持 15×15 自由五子棋。
宽度为 mapping=64、channels=64、value_hidden=128、policy_hidden=32，
权重为 102,109,456 字节（97.38 MiB），SHA-256 固定在 `models/web-model.json`。

## 实际对弈

网页默认在人机和机机对弈中使用该模型。第一次 AI 行棋下载权重并显示进度，
下载结束验证长度、SHA-256、头部、张量布局、取值范围、规则和尺寸。
后续同一 Worker 共用不可变模型；悔棋/重开打断搜索时销毁 Worker，
新 Worker 从按模型摘要隔离的 Cache Storage 重新加载。缓存不可用时仍可在内存运行，
损坏缓存重新下载。加载失败停在原棋局并提供重试，不会暗中改用手工评估。

设置中的 AI 选项可明确选择基础引擎。选择 20×20 或标准规则时切换到基础引擎，
设置页同时注明 NNUE 适用范围。最近一步分析显示真实使用的模型名。
人人模式不需要下载模型。页面推理不请求后端，也不需要 NVIDIA/GPU。

原生入口：

    build/dev/bin/gomoku-worker --model artifacts/models/rapfi-calibrated-v1/weights.gnn
    build/dev/bin/pbrain-gomoku --model artifacts/models/rapfi-calibrated-v1/weights.gnn

Windows 对应 `.exe`，64 位比赛程序名为 `pbrain-gomoku64.exe`。
比赛入口只读取本地权重；不兼容规则或内存预算明确报错。
JSON `analyze` 可传 `evaluator: "nnue"` 或 `"handcrafted"`，显式 NNUE 请求在缺失模型时失败。

## 推理和搜索

`NnueModel` 位于引擎库，仅依赖标准 C++。`NnueEvaluator` 每次搜索独立维护双视角
棋形、3×3 空间特征、全局与九区域和以及撤销栈。落子只更新其影响范围，
搜索取消、达到时间/节点上限时配对恢复。整数累加使用 int64，舍入为最近偶数。

价值头提供当前行棋方的胜/和/负 logits，经稳定 softmax 和有界反双曲正切
映射到搜索分数；policy 头对候选着排序。即时成五和防守优先于神经网络排序。
当前搜索仍使用前 16 个安静候选着，没有置换表或 VCF/VCT，不能当作完备求解器。

权重加载暂存输入并复制到经过校验的 C++ 张量。97 MiB 是文件和模型主体大小，
不是浏览器全部内存占用；加载时还有输入缓冲、缓存响应与 WASM 堆增长的开销。
运行时拒绝超过 512 MiB 的单模型；WASM 堆按需增长，上限 2 GiB，不会启动即分配上限。
没有 WebGPU、pthread、SharedArrayBuffer 或跨源隔离头要求。

## 发布与验证

`models/web-model.json` 固定 Release URL 和摘要。`pnpm model:download` 将权重放到
被 Git 忽略的 `apps/web/public/models/<sha256>/weights.gnn`，生成 `active.json`。
本地有相同模型导出时，`pnpm model:stage` 自动校验并复制；无本地资产的开发构建保留基础引擎。
Pages 构建必须成功下载模型，失败不会发布无模型替代品。
网站部署从本次 CI 取原生检查器和 WASM，执行 `pnpm model:verify` 后再上传静态目录。
用户从网站同源地址取得权重，Release 的跨域设置不会影响浏览器。

当前真实模型的检查：

- 64 个真实保留局面，原生与 WASM 各比较 192 个价值 logits 和 14,400 个 policy logits，
  与独立 Python int64 参考逐项相等，最大绝对误差为 0。
- 原生 1,490 次增量更新/撤销与完整重建相等，并检查节点中断恢复。
- WASM 即时成五、不兼容尺寸拒绝、损坏模型替换保持原模型，以及完整 NNUE 自对弈。
- 浏览器资产下载、校验、缓存损坏恢复、取消旧 Worker、重新开始和真实网页人机回应。

数值报告输出到 `artifacts/runtime/rapfi-calibrated-v1.json`，随模型 Release 保存。
这是运行时验收，不是模型棋力领先基础引擎或达到 Gomocup 参赛水平的证明。
未来发布新模型时必须一起更新模型固定清单、独立参考局面和验证报告。
