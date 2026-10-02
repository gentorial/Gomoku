# 搜索与 GUI 日志

运行时诊断默认使用 `info` 级别，写入项目根目录的 `logs/`。目录中的日志已由
`.gitignore` 忽略，只有 `.gitkeep` 纳入版本管理。每行是一条 JSON，时间戳为
`timestampMs`，文件按 UTC 日期分组。

| 文件                           | 来源                                                   |
| ------------------------------ | ------------------------------------------------------ |
| `engine-YYYY-MM-DD.jsonl`      | 原生 JSON Worker，同时输出到 stderr                    |
| `gui-YYYY-MM-DD.jsonl`         | Vite 开发服务器收集的网页 GUI、WASM 搜索和模型交互日志 |
| `gui-desktop-YYYY-MM-DD.jsonl` | Tkinter 桌面 GUI 操作和引擎交互                        |
| `server-YYYY-MM-DD.jsonl`      | 可选 API 服务的请求、Worker 排队、执行和异常           |

网页日志同时输出到浏览器控制台。静态部署没有本地文件写入服务，因此仅输出到
浏览器控制台；开发模式的落盘由 Vite 完成，不依赖 API 服务。修改代码后应重启
Vite，使新增的日志收集插件生效。

## 配置

通过进程环境变量配置，没有自动加载 `.env`：

```powershell
$env:GOMOKU_LOG_LEVEL = "debug"
pnpm dev
```

- `GOMOKU_LOG_LEVEL=info`：搜索开始、每层完成、结束统计、GUI 操作、模型加载和选择。
- `GOMOKU_LOG_LEVEL=debug`：额外记录候选排序、优先着法、根节点各着法结果、浅层战术、
  TT/Alpha-Beta 截断、PVS 重搜及 VCF 结果。
- `GOMOKU_LOG_LEVEL=off`：关闭上述诊断日志；既有 Fastify 请求日志和训练指标单独管理。
- `GOMOKU_LOG_DIR`：指定日志目录，建议使用绝对路径。默认是项目根目录的 `logs/`。

网页日志级别在 Vite 启动或构建时确定，修改环境变量后需要重启或重新构建。
新日志需要重新构建原生/WASM 引擎及 TypeScript 包；已有引擎进程需重启才能使用新版。

## 内容和关联

通过 `requestId` 关联 GUI 到引擎的请求与搜索记录。搜索记录包括局面哈希、执棋方、
棋盘/规则、限制和 TT/PVS/VCF 配置。`search.iteration` 记录最佳落子、评分、PV、
本层耗时与节点增量；`search.end` 记录累计统计和最终结果。

结束日志的 `reason` 区分 `time_limit`、`node_limit`、`cancelled`、`max_depth`、
`mate_found` 和 `terminal`。通信协议里的原有 `completed/limit/cancelled/terminal`
保持兼容。`attemptedDepth` 与 `completedDepth` 区分中断层和最后完成层，
`fallbackUsed` 表示尚未完成一层就返回兜底着法。

根节点 PVS 零窗口搜索的评分可能是上界，`search.root_move` 的 `reason=pvs_bound`
明确标记这种情况；重搜后才有完整窗口结果。候选日志记录保留顺序和直接获胜/防守点。
VCF 的 `no_win` 只表示未找到连续冲四胜法，`unknown` 表示预算内无法完成证明。

日志只涉及神经网络的下载、缓存、校验、安装、棋盘/规则匹配和评估器选择边界。
不记录网络内部张量、权重、激活、逐层运算或逐叶评估。

## 性能与失败处理

详细日志限制在搜索树前两层（`ply <= 2`），每次搜索最多 500 条，超出的数量由
结束日志的 `suppressedEvents` 报告。汇总日志不受此限。网页落盘分批发送，内存队列
最多保留 1000 条，拥塞时优先丢弃详细日志；日志收集失败后继续输出到控制台。
详细日志耗时计入搜索预算，排查时应适当增大时间限制。

日志写入失败或回调异常不会打断对局。原生 stdout 始终只输出通信协议。
搜索核心未配置日志回调时不输出日志；独立 Gomocup 适配器保持此默认行为。
日志文件按天增长，目前没有自动清理策略。
