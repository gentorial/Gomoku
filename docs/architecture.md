# 架构与模块边界

## 一个引擎，多种程序入口

CMake 编译两个库：gomoku_game 与 gomoku_engine。
gomoku_game 包含规则、局面和哈希；gomoku_engine 链接它，包含搜索与评估。
gomoku-worker、gomoku-wasm 和 pbrain-gomoku 分别链接同一引擎库，不互相调用。

网页 → MatchController → Engine 接口 → Web Worker → WASM → 引擎库。
可选 HTTP SDK → Fastify → JSON Lines → 原生 Worker → 引擎库。
桌面 → JSON Lines → Worker → 引擎库。
Gomocup 裁判 → 比赛文本协议 → pbrain → 引擎库。
训练使用自对弈产物，不进入线上搜索进程。

新增入口必须适配公开头文件，不能把协议解析、数据库、用户对象或 UI 状态
放进 engine。adapters/json 复用原生 Worker 与 WASM 的编解码和同步命令；
比赛目标和核心测试不需要 JSON 依赖。

## 状态所有权

浏览器 MatchController 拥有本地对局：模式、配置、落子历史、分析结果和调度状态。
控制器仅依赖 contracts 中的 Engine 接口，与 React、WASM 加载方式和 C++ 解耦。
人人模式的两方都是人类；人机模式的一方是 AI；机机模式的两方都是 AI。
落子、撤销与开局均交给 WASM 原生规则重放裁定；JS 不另写一份胜负规则。

同一对局每次只执行一项操作。取消时 AbortSignal 终止整个 Web Worker，
控制器的 generation 同时拒绝迟到结果。即使引擎忽略取消也不能提交旧着法。
机机双方使用独立时间预算，回合间短暂让出事件循环；暂停取消搜索及下一步定时器，
单步恰好提交一手并保持暂停。悔棋在人人/机机撤一手，人机撤回人类上一个决策点。

可选后端拥有独立 Game：id、version、配置、落子历史、分析结果。
每次请求将完整 Position 交给 Worker，从空棋盘重放验证。
原生规则库裁定合法性、下一行棋方和胜负。前端棋子颜色按记录次序显示，
可以预先禁用已占位置，但它的显示逻辑不产生权威胜负。

每盘棋同一时刻只有一个变更操作。人类落子和 AI 落子都会增加 version。
重复版本或并发请求返回 409。悔棋取消当前分析，然后由原生库重新校验局面。
提交结果时同时检查操作身份与 AbortSignal；即使引擎忽略取消，旧结果也不能提交。

Worker 一次处理一个分析；后端默认两个常驻进程和最多 64 项排队任务。
取消或超时会结束相关 Worker，下一项工作启动新进程。可选 API 默认使用基础引擎；
以后可以改为先发送 stop、超时后再杀进程，以保留模型预热收益。
Native Worker 已支持 stop 和原生搜索取消标记。

## 搜索与评估

search 接受 const Position，搜索在副本上进行；内部通过 play/undo 更新状态。
Evaluator 生命周期为 reset → push/evaluate/pop；push 接收走子后的局面。
每个搜索会话拥有自己的评估器。中断路径也必须配对恢复状态。

评估器可选已训练的 line11 NNUE 或五格窗口手工评估；分数始终相对于输入局面的行棋方。
迭代加深仅发布完整完成的深度，时间/节点上限中断后保留上一次结果；
没有完成一层时仍提供合法备用着。终局允许 bestMove=null。

搜索使用迭代加深、PVS 和每次搜索独立的 4 MiB 置换表。
`Threats` 随落子/撤销增量维护双方成五点和冲四点，与 NNUE 累加器分开。
己方成五优先；对手唯一成五点强制防守，对手有两个成五点且己方不能先赢时判负。
必须防守的链条可以越过名义深度，安静叶节点再尝试有预算的 VCF；具体语义见
[战术搜索](tactical-search.md)。这些检查由三个入口共享。

普通候选着保留邻近两格内的空位，安静候选着最多 16 个。
VCF 独立枚举全部冲四点，不受 policy 前 16 名限制。整个 Alpha-Beta 仍是选择性搜索，
并没有因为增加 VCF 就成为完备求解器。

## NNUE 接入路径

正式设计见 [NNUE 正式架构](nnue-design.md)。直接实现局部棋形映射、
增量特征与价值/着法双头，数据、导出与 C++/WASM 共用明确的模型规范。
训练设备可配置为 CPU、Intel XPU 或 NVIDIA CUDA，支持本地或远程执行，
分片与训练状态考虑多卡扩展；开发机不要求拥有 NVIDIA 显卡。
Python 已实现 `line11-dual-v1` 的数据分片、训练、恢复、验证、量化导出和
整数全量参考，详见 [训练框架](../training/README.md) 与 [模型格式](../models/line11-format.md)。
原生增量推理继续使用这些算子和编码；全量参考长期用于一致性检查。
训练依赖保留在 Python 项目内，当前执行器为单进程单设备。
Rapfi 教师作为 tools 管理的外部 CPU 进程运行，原始 binpack 和标注通过带摘要的
清单交给 training；老师的代码、权重和训练依赖都不进入 C++/WASM 发布包。

接入验收应比较：

1. Python 导出前后与 C++ 推理的数值结果。
2. 随机合法落子/撤销后，增量累加器与全量重建结果。
3. 规则、棋盘尺寸、模型版本是否匹配。
4. 搜索节点吞吐、时间行为、模型常驻内存。
5. 相同开局和时间条件下，与固定基线交换颜色对战的结果。

局面哈希区分规则、尺寸、行棋方；TT 属于单次搜索，不跨权重或会话复用评估。
量化权重由原生与 WASM 的同一加载器校验；网页在 Worker 内检查完整 SHA-256，
按摘要缓存，取消后的新 Worker 可以重新从缓存加载。训练检查点不进入运行时。
各模型独立验收后才标记兼容；具体生命周期和发布方式见 [NNUE 运行时](nnue-runtime.md)。

## 工具与构建

pnpm 管理网页、服务端和 contracts/sdk/engine-wasm 三个共享包，按拓扑顺序构建。
uv workspace 管理桌面、工具、训练；训练的 PyTorch 是显式可选依赖。
CMake 独立管理 C++。根目录的 Node 脚本只是统一命令入口，比赛构建不依赖它。

wasm 预设通过 Emscripten 将相同 C++20 库编译到 packages/engine-wasm/generated。
生成的 .mjs/.wasm 不入库；固定 SDK 与源码在 CI/本地重建。Vite 将 Worker 与 WASM
作为带哈希的静态资源打包，网页只需要静态托管。默认 pnpm dev 不启动服务器。
WASM 使用 C ABI 传递版本化 JSON，一次请求一次返回；JSON 不进入搜索节点热路径。
启用 C++ 异常以保留限时搜索的撤销语义；NNUE 使用 WASM SIMD128，
不使用 pthreads 或共享内存。

训练数据准备使用本地 SQLite 做磁盘去重和可恢复暂存，发布后由不可变分片提供训练数据。
网页后端目前使用内存存储；后续可替换存储和进程调度，保持 Position、Game 与版本化契约的语义。

## 验证职责

- C++：规则、哈希恢复、战术着、零时限、取消、评估器栈恢复。
- Python：Gomocup 与 Worker 同局面结果、错误恢复、取消、整局自对弈和训练数据。
- TypeScript：真实 Worker 的 HTTP 链路、并发落子、过期结果、进程取消与重启。
- WebAssembly：实际生成模块与原生程序对同一局面的结果一致、异常恢复、时间/节点上限。
- 网页控制器：真实 WASM 裁定三种模式的棋局，确定性搜索替身覆盖自动行棋与取消竞态。
- 共享 fixture：协议之间使用相同局面，避免分别写出自洽但不一致的测试。
- CI：Windows/Linux 全栈检查，以及不安装网页或训练依赖的 Windows/Linux 比赛构建。
- 发布：Windows/Linux 独立比赛 ZIP 解压启动检查；主分支全项通过后复用已验证 WASM，
  按 GitHub Pages 实际路径构建和部署静态网站。详见 [构建与部署](ci-cd.md)。
