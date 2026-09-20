# Gomoku

面向五子棋 AI 研发的多语言 monorepo：一套 C++ 原生规则与搜索引擎，
连接网页、桌面调试界面、Gomocup、训练数据生成和棋力评测。

网页默认使用经过真实 Rapfi 教师数据训练的 `rapfi-calibrated-v1` NNUE，
价值头与落子排序头均接入 C++ Alpha-Beta，并编译成 WASM 在浏览器运行。
支持 15×15 自由五子棋；其他规则/尺寸使用明确标注的手工评估基础引擎。
训练、导出、原生增量推理、浏览器缓存和 Pages 发布链路已接通；
置换表、专门的 VCF/VCT 搜索和正式棋力评测尚未实现。

## 快速开始

需要 Node.js 22.13+、pnpm 11.9、CMake 3.24+、Ninja、Git 和 Python 3.11+。
构建原生程序还需要支持 C++20 的编译器；只构建网站时使用脚本安装的 Emscripten。
推荐 Node.js 22 LTS；Windows 可使用 Visual Studio 的开发者终端或 MinGW-w64，
Linux 可使用 GCC/Clang。

在仓库根目录运行：

    pnpm install --frozen-lockfile
    pnpm wasm:setup
    pnpm model:download
    pnpm build:web
    pnpm dev

访问 [本地网站](http://127.0.0.1:5173/)。默认仅启动 Vite：规则校验与 AI 搜索
在浏览器 Web Worker 中执行编译后的 C++ WASM，无需启动 API 服务。
Ctrl+C 会关闭开发服务和它启动的子进程。

wasm:setup 将固定版本 Emscripten 4.0.23 安装到被忽略的 .tools/emsdk，首次下载较大。
已有 SDK 可通过 EMSDK 指定根目录，推荐相同版本。首次构建还会从 GitHub 获取
固定提交的 nlohmann/json，由原生 JSON Worker 与 WASM 适配器共享。
独立比赛构建不下载第三方依赖，也不需要 Node.js 或 Python。

## 已接通的功能

- 15×15、20×20；Freestyle 和 Standard。其他规则明确拒绝。
- 原生落子、撤销、终局判断、局面哈希、基础迭代加深 Alpha-Beta。
- 手工评估、即时获胜/防守排序、时间与节点上限、取消、合法备用着。
- 真实训练的 NNUE 价值/policy 双头、局部增量更新和撤销；同一权重支持原生与 WASM。
- JSON Lines Worker、WASM 与独立 Gomocup 适配器共享同一引擎库。
- 网站同机人人、人机、机机对弈；先后手、双方 AI 思考时间、暂停/继续/单步。
- 极简棋盘与 Lucide 图标操作；设置和最近一步分析位于二级菜单，支持悔棋、重开、棋谱导出。
- 后端原生规则校验、进程池、任务排队、局面版本、取消、崩溃/超时恢复。
- 桌面人机与双人对弈，规则同样由原生 Worker 裁定。
- 自对弈 NDJSON、双程序交换先后手对战、原生规则校验、按开局隔离及对称去重的训练分片。
- 正式 line11 双视角网络、WDL/policy 混合监督、CPU/XPU/CUDA 后端选择、训练恢复与整数参考导出。
- C++、WASM/原生一致性、三模式与取消竞态、HTTP/进程并发、Gomocup 协议和数据闭环测试。

## 目录与依赖

    apps/web                 React + Vite 网页
    apps/server              Fastify 对局服务与 Worker 进程池
    apps/engine-worker       JSON Lines 原生入口
    apps/gomocup             Gomocup 协议原生入口
    apps/desktop             Tkinter 调试入口
    adapters/json            原生 Worker 与 WASM 共用的 JSON 编解码
    adapters/wasm            Emscripten C ABI 入口
    engine/game              规则与棋盘，无协议/搜索依赖
    engine/search            搜索与时间控制
    engine/eval              评估器及 NNUE 扩展边界
    packages/contracts       Zod 契约、TypeScript 类型、生成的 JSON Schema
    packages/sdk-ts          可选 HTTP 服务客户端 SDK
    packages/engine-wasm     浏览器引擎、Web Worker、生成的 WASM 资源
    tools                    Python 协议客户端、自对弈、arena
    training                 分片、正式网络、训练/验证/恢复、量化导出
    models                   模型产物约定
    tests/fixtures           跨语言共享测试局面
    docs                     架构、协议与研发路线

原生依赖方向是适配器 → 搜索/评估 → 规则。网站通过 Web Worker 调用 WASM；
可选后端通过进程边界调用原生程序。前端对局控制器依赖统一 Engine 接口，
搜索节点内部不发生 HTTP、JSON、Python 调用。

详见 [架构](docs/architecture.md)、[协议](docs/protocols.md)、
[研发路线](docs/roadmap.md) 和 [NNUE 正式架构](docs/nnue-design.md)。

## 开发命令

    pnpm engine:build         # 只构建原生库与两个入口
    pnpm wasm:setup           # 安装固定版本 Emscripten（首次）
    pnpm wasm:build           # C++ → WebAssembly
    pnpm model:download       # 获取并校验固定版本的已训练模型
    pnpm model:verify         # 真实模型的 Python/C++/WASM 一致性与完整自对弈
    pnpm build:web            # WASM + 公共包 + 可静态部署的网站
    pnpm web:verify           # 检查静态资源路径和 WASM 产物
    pnpm build                # 原生 + WASM + 所有 TypeScript 包 + 网站
    pnpm dev                  # 网页开发模式，无需 API
    pnpm dev:full             # 同时启动可选 API 服务（先构建原生引擎）
    pnpm test                 # 构建后运行引擎与应用测试
    pnpm check                # 构建、应用测试、类型与格式检查（训练测试单独运行）
    pnpm format               # 格式化支持的源文件
    pnpm --filter @gomoku/contracts schema

单独调试后端可运行：

    pnpm build:packages
    pnpm --filter @gomoku/server dev

后端环境变量见 [.env.example](.env.example)。它们从进程环境读取，不会自动加载 .env。
GOMOKU_ENGINE_PATH 可以覆盖可执行文件绝对路径；GOMOKU_WORKERS 默认 2，范围 1..16。
API 默认位于 [本地健康检查](http://127.0.0.1:3001/api/health)。当前网页不调用 HTTP API。

## 网站部署与对弈模式

pnpm build:web 的产物是 apps/web/dist，将整个目录交给静态文件托管即可。
JS Worker 和 WASM 都在 assets 目录中，并带内容哈希；无需部署 Node.js/C++ 服务。
服务器应正确返回 JavaScript 与 application/wasm 的 MIME 类型；建议开启 gzip/Brotli。
如果托管于子路径，设置 GOMOKU_WEB_BASE=/你的路径/，或在网页构建命令中指定 Vite --base。

GitHub Actions 已配置为 PR 自动构建与测试、main 分支通过全部检查后自动部署到
[GitHub Pages](https://gentorial.github.io/Gomoku/)。部署会复用本次 CI 已验证的 WASM，
自动读取 Pages 子路径，并检查 Worker/WASM 资源是否存在。Windows/Linux 的
开发引擎、独立比赛 ZIP、WASM 和网站均提供下载产物。
详细触发条件、手动发布和排查步骤见 [构建与部署](docs/ci-cd.md)。
模型由 [web-model.json](models/web-model.json) 固定 SHA-256 与发布 URL，构建时从
GitHub Release 下载，连同网站托管在 Pages；用户浏览器不跨域请求 Release。
约 97 MiB 的权重按需加载并保存在 Cache Storage，损坏缓存会重新获取；
校验失败明确报错，不会悄悄改用基础引擎。模型详情见 [运行时说明](docs/nnue-runtime.md)。

页面只保留棋盘、行棋方、手数和图标操作栏。通过设置按钮选择模式并开始对局：

- 人人：同一设备黑白轮流下棋，每次悔棋撤回一手。
- 人机：选择人类执黑/白和 AI 每步时间；悔棋回到人类上一个决策点。
- 机机：分别设置黑白每步时间，自动运行直到终局；支持暂停、继续、单步。
  机机悔棋撤回一手并保持暂停。

设置在开始对局时生效。分析菜单显示刚落下的 AI 一手对应的深度、节点、用时、
行棋方视角评分与预想变化，并可导出棋谱。评分不是胜率，搜索中的杀棋分数
也不是完备求解证明。分析菜单显示实际使用的模型；训练后的棋力提升尚未经过固定条件对战评测。

WASM 搜索是独立 Worker 内的单线程任务，不阻塞页面；取消会终止 Worker，
下一次请求重建模块。无需 SharedArrayBuffer 或跨源隔离响应头。
首次加载仍需下载静态资源；未实现 Service Worker 安装缓存或刷新恢复棋局。

## 桌面界面

安装 uv 后，在根目录运行：

    uv run --package gomoku-desktop gomoku-desktop

桌面界面位于 apps/desktop，由 uv workspace 管理。
Linux 桌面需要 Python 的 Tk 支持。桌面客户端默认连接构建出的 JSON Worker，
旧的 START BLACK / MOVE row col 自定义协议已由版本化 JSON 协议替代。

## 自对弈和训练

Python 子项目由 uv workspace 管理，默认不安装 PyTorch：

    uv run --package gomoku-tools gomoku-selfplay --games 2 --output data/selfplay.jsonl
    uv run --package gomoku-training gomoku-data data/selfplay.jsonl
    uv run --package gomoku-tools gomoku-arena --games 2 --time-ms 20

arena 默认让本仓库的两个比赛程序实例交换先后手。通过 --engine-a 和 --engine-b
指定其他 Gomocup 可执行文件；支持 --rule、--size、--output。
程序记录对局、胜负和协议失败，不根据几局样本宣称 Elo 提升。

正式网络为 `line11-dual-v1`：长度 11 的四方向棋形映射、双视角局部特征、
WDL 价值头和 policy 着法头。默认通道宽度 64，训练设备与网络定义分离。
以下为本机 Intel XPU 环境的命令；仅使用 CPU 时将 `--extra xpu` 换成
`--extra cpu`，并在配置中指定 `device = "cpu"`。

    uv sync --locked --package gomoku-training --extra xpu
    uv run --frozen --package gomoku-training --extra xpu gomoku-device --check --steps 10
    uv run --frozen --package gomoku-training --extra xpu gomoku-prepare data/annotations.jsonl --output data/training
    uv run --frozen --package gomoku-training --extra xpu gomoku-train --config training/configs/line11.toml
    uv run --frozen --package gomoku-training --extra xpu gomoku-train --config training/configs/line11.toml --resume
    uv run --frozen --package gomoku-training --extra xpu gomoku-export artifacts/training/run01/best.pt --output artifacts/models/run01

先准备真实棋谱或老师标注，再运行 prepare；训练要求非空且独立的训练/验证集合。
现有确定性自对弈可能只覆盖一个开局组，重复两局不能构成可靠的独立验证集。
完整输入规范、CPU/CUDA 安装、核显兼容设置、测试集评估和可复制检查命令见
[训练框架说明](training/README.md) 与 [正式配置](training/configs/line11.toml)。

训练端输出 PyTorch 检查点和版本化 `weights.gnn`。量化权重已有独立 Python 整数
参考推理，并接入同格式的 C++/WASM 加载器。新导出仍标记 `runtimeCompatible=false`，
只有经过逐模型数值和对局验收的发布产物才能标记兼容。
Rapfi 官方 NNUE 教师已接入：支持可恢复的平衡开局自对弈、固定版本 binpack 转换、
原生规则核验和带摘要的教师数据清单，命令见 [教师工具](tools/README.md)。
针对该教师使用 [蒸馏配置](training/configs/rapfi-v1.toml)；数据与训练权重保存在本机产物目录。
多卡 DDP、正式 NNUE 棋力评测与自动模型晋级尚未实现。
这些后续模块复用同一数据和模型规范，详见 [NNUE 正式架构](docs/nnue-design.md)。
大规模数据和权重不进入普通 Git 历史。

## 独立 Gomocup 构建

只需原生工具链：

    cmake --preset competition
    cmake --build --preset competition
    cpack --config build/competition/CPackConfig.cmake -B artifacts

程序位于 build/competition/bin，ZIP 发布包位于 artifacts。
Windows x64 可执行文件名为 pbrain-gomoku64.exe；其他平台为 pbrain-gomoku。
Windows 默认静态链接编译器运行库。
原生 Worker 和比赛程序均可传入 `--model /path/to/weights.gnn` 使用 NNUE；
不传参数保留无模型基础引擎。比赛程序不会下载权重，模型尺寸和规则不匹配时明确拒绝。

适配器支持 START、BEGIN、TURN、BOARD/DONE、INFO、END、ABOUT、RESTART；
不支持的普通命令返回 UNKNOWN，不支持的规则在需要应答时返回 ERROR。
INFO 本身不输出应答。支持规则与棋盘范围详见协议文档。

这只是可参与协议联调的基础程序，尚未经过官方裁判程序的参赛验收或棋力认证。
正式提交前需要核对当届规则、程序身份、资源限制、模型与发布产物。
参考 [官方协议](https://plastovicka.github.io/protocl2en.htm)、
[2026 公告](https://gomocup.org/news/gomocup-2026-announcement/)。

## 当前边界

- 网站的本地棋局保存在浏览器内存，刷新后清空。人人模式为同机对弈；
  账号、网络房间与棋局持久化尚未实现。
- 可选 API 仍是本地研发服务，内存中最多 256 盘；公网访问控制与容量策略待实现。
- 分析接口目前返回完整结果；没有接入 WebSocket/SSE 实时搜索流。
- 搜索是选择性的基础实现，安静候选着截断为前 16 个，不能当作完备求解器。
- Renju/Caro/Swap2、SIMD、置换表、VCF/VCT、正式棋力基准待实现。
- CI 已配置；本地通过不等于远端 Windows/MSVC 和 Linux 作业已运行。
