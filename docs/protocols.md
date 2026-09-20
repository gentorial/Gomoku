# 协议 v1

## 通用语义

- x 为列，y 为行，从左上角 (0,0) 开始。界面的字母数字坐标仅用于显示。
- 内部 Position 包含 size、rule、moves。moves 是从空棋盘开始、黑方先行的完整历史。
- size 为 15 或 20；rule 为 freestyle 或 standard。当前不支持禁手、Caro 或 Swap2。
- 内部协议不接受任意乱序历史、重复占位或终局后继续落子。
- Gomocup BOARD 可以乱序；适配器使用独立的原生 from_stones 接口恢复，
  不把 BOARD 顺序误当成落子历史。

TypeScript 契约源是 packages/contracts/src/index.ts。
运行 pnpm --filter @gomoku/contracts schema 更新 schema 目录的机器可读文件。
C++ 边界执行自己的输入校验；跨语言 fixture 和实际进程测试用于检测实现偏差。
增加字段或变更语义时同时更新所有适配器与测试，破坏性变更提升 v。

## JSON Lines Worker

每行一个 UTF-8 JSON 请求和应答，输出及时刷新。请求以 v=1 和唯一字符串 id 开头。
标准输出只写协议；诊断信息写标准错误。请求限制 64 KiB。

关于引擎：

    {"v":1,"id":"q1","method":"about"}

校验局面：

    {"v":1,"id":"q2","method":"inspect","position":{"size":15,"rule":"freestyle","moves":[]}}

落子：

    {"v":1,"id":"q3","method":"play","position":{"size":15,"rule":"freestyle","moves":[]},"move":{"x":7,"y":7}}

分析：

    {"v":1,"id":"q4","method":"analyze","position":{"size":15,"rule":"freestyle","moves":[]},"limits":{"timeMs":300,"maxDepth":4,"maxNodes":0}}

停止：

    {"v":1,"id":"q5","method":"stop","targetId":"q4"}

成功应答为 {v,id,ok:true,result}，错误为 {v,id,ok:false,error:{code,message}}。
result.kind 区分 position、analysis、about、stopped。
stop 的应答与被停止搜索的最终应答可能以任意顺序到达，必须按 id 对应。
单 Worker 同时只接受一个分析，新分析在旧分析未结束时会失败。stdin EOF 会取消并退出。

limits.timeMs 范围 0..10000；0 表示立即给出备用着。
maxDepth 范围 1..12，默认 4；maxNodes 范围 0..10000000，0 表示不设置节点上限。
取消后返回最后完成的一层或合法备用着，reason=cancelled；不自动提交到棋局。

分析结果包含 bestMove、pv、score、depth、nodes、elapsedMs、reason、evaluator。
analyze 可传 `evaluator: "nnue"` 或 `"handcrafted"`。显式 NNUE 请求在模型缺失或
规则/棋盘不匹配时失败。原生 Worker 可通过 `--model weights.gnn` 加载模型；
未传 evaluator 时优先使用已加载且兼容的 NNUE，否则使用基础引擎。
about 中可选的 nnue 字段提供已加载模型的规则、尺寸与字节数；无模型为 null。
浏览器 analysis 另带可选 model 对象（id、label、sha256），记录经过完整校验的模型身份。
score.perspective 固定为 side_to_move，value 是引擎分数，不是胜率。
kind=mate 使用内部大分值编码距离；它来自当前选择性搜索树，不是完备求解证明。
终局 bestMove=null。状态为 playing、black_win、white_win 或 draw。

## 浏览器 WASM 与本地对局

WASM 的 gomoku_request 接收/返回同样的 JSON 信封，支持 about、inspect、play、analyze。
adapters/json 与原生 Worker 共用编解码。浏览器通过专用 Web Worker 的 postMessage
发送请求，C ABI 为同步执行；浏览器取消使用 AbortSignal 终止 Worker，而不是发送 stop。
WASM 原始入口不支持 stop，原生 Worker 继续支持它。模块下一次使用时按需重新加载。

MatchConfig 定义 mode（human-human/human-ai/ai-ai）、size、rule、humanColor，
以及 blackTimeMs/whiteTimeMs（100..3000）与 evaluator（nnue/handcrafted）。人类一方不使用思考时间。
网页控制器负责选择当前由谁落子；合法性、下一行棋方和胜负始终取引擎返回值。
局面只保存在当前网页内存，同机人人模式不涉及网络协议。

## 可选 HTTP API

所有路径以 /api 开头，错误结构固定为 {error:{code,message}}。

- GET /health：检查原生 Worker 能否实际响应。
- POST /games：接收 size、rule、humanColor、thinkTimeMs，返回 Game。
- GET /games/:id：获取当前 Game。
- POST /games/:id/moves：接收 {version,move}，提交人类一步。
- POST /games/:id/analyze：接收 {version}，在 AI 回合搜索并提交一步。
- POST /games/:id/undo：接收 {version}，撤回到人类上一个决策点。
- POST /games/:id/cancel：接收 {version}，取消当前分析并提升版本。

humanColor=white 时，新局需要先调用 analyze 让 AI 执黑开局。
thinkTimeMs 为 50..3000。当前网站使用本地 WASM，此 API 保留为服务器端人机联调入口。
HTTP 分析当前为一次请求、一次完整结果，尚无流式分析订阅。

400 表示输入或落子不合法，404 表示未知对局，409 表示过期版本/回合冲突/取消，
503 表示引擎不可用、超时或队列容量不足。v1 服务只面向本地研发。

## Gomocup

遵循 [官方协议](https://plastovicka.github.io/protocl2en.htm) 的已实现子集：

- START 15/20：初始化，回复 OK；不立即落子。
- BEGIN：要求空棋盘，搜索并输出 x,y。
- TURN x,y：记录对方着法，搜索并输出 x,y。
- BOARD ... DONE：接收 x,y,field；1=己方，2=对方，允许乱序。
  根据棋子数量和当前需要己方行棋，推断黑白映射。连续棋与 field=3 不支持。
- INFO rule：0=freestyle、1=standard，其他规则拒绝。
- INFO timeout_turn / time_left / max_memory：设置资源预算。
  未使用/未知 INFO 忽略，INFO 不输出回复。
- END：退出；ABOUT：程序信息；RESTART：清空同尺寸棋盘并回复 OK。

RECTSTART、TAKEBACK、SWAP2BOARD 等可选命令未实现，按协议返回 UNKNOWN。
当前标准规则支持 15/20 两种尺寸，但正式赛事分组应以当届规定为准。
比赛适配器采用单线程命令循环；搜索用本地时限退出，END 在搜索返回后处理。

默认每步预算 1000 ms，收到 INFO 后以外部预算为准。
当前简易分配器使用 min(单步预算, 剩余时间/25)，留 10 ms 余量，最多搜索 10 秒。
这不是最终的比赛时间管理算法。小于 16 MiB 的内存预算会被拒绝。

## 棋谱与训练数据

gomoku-record-v1 包含 format、size、rule、moves、status，可附带 source。
网页导出单个 JSON；自对弈生成每行一盘完整棋谱的 NDJSON。
训练数据校验通过原生规则库重放，拒绝未完局、结果不符、非法局面及超时判负样本。
价值标签相对于样本局面的行棋方，胜/负/和分别为 1/-1/0。
