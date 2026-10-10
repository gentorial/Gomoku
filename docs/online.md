# 联机对战

联机模式让两名玩家通过服务器对弈：用房间号邀请好友，或按规则与棋盘随机匹配；
支持认输和需要对方同意的悔棋，不计时。棋局以服务器为准，每一步都由与网页、
原生引擎相同的 C++ 规则（WASM）判定。

## 组成

- `apps/server/src/online.ts`：房间、匹配队列、落子校验、认输、悔棋与闲置清理，
  不涉及网络，便于测试。
- `apps/server/src/online-route.ts`：Fastify 上的 `/ws` WebSocket。只接受允许的
  Origin（无 Origin 的非浏览器客户端除外）；单条消息上限 4 KiB；每个连接最多连发
  20 条、每秒补充 10 条；30 秒心跳清理失联连接。
- `apps/server/src/referee.ts`：进程内 WASM 规则引擎，只做 inspect/play，不做搜索。
- `apps/web/src/online/controller.ts` 与 `Online.tsx`：网页端连接、自动重连和界面。

## 协议

客户端与服务器各自只发送 JSON 消息，定义在 `packages/contracts`，并导出为
`schema/online-client.json` 与 `schema/online-server.json`。

1. 客户端先发 `hello`（昵称 1..16 字，可附上次的 `token`），服务器回 `welcome`
   给出重连令牌，然后是 `lobby`、`queued` 或当前房间的 `room`。
2. `create` 建房得到 6 位房间号（不含易混字符 0/1/I/O）；`join` 按房间号加入。
   第二人到达时随机分配黑白。`match` 按规则和棋盘排队，有人等待即直接开局。
3. `move`、`resign`、`undo`、`undoReply`、`leave` 只对自己所在的房间生效。
   悔棋收回请求方最近的一手；若对方已经应着，一并收回对方那手。对方同意前任何
   一方落子都会撤销请求。进行中离开房间视为认输。
4. 每次变化后服务器向房间双方发送完整的 `room`（含 `you`、双方昵称与在线状态、
   全部着法、`status`、`ending` 和待处理的 `undo`），客户端只渲染这份状态。

令牌存在浏览器本地；刷新或断网后自动重连并回到原座位。同一令牌在另一页面
连接时，旧连接以代码 4000 关闭且不再自动重连。

## 配置

地址集中在 [`config/online.json`](../config/online.json)：

- `url`：网页连接的 `wss://` 地址，网页构建时读取；为 `null` 时不显示联机模式。
  `GOMOKU_ONLINE_URL` 可临时覆盖，`pnpm dev:full` 用它连接本地服务。
- `origins`：服务端允许的网页来源，服务启动时读取；`GOMOKU_ORIGINS` 可覆盖。

更换服务器只需修改这个文件并重新部署网页与服务端。

## 服务器环境变量

| 环境变量          | 含义                                 |
| ----------------- | ------------------------------------ |
| `GOMOKU_AI=0`     | 不启动原生引擎，只提供联机与健康检查 |
| `GOMOKU_ONLINE=0` | 关闭联机                             |
| `GOMOKU_ORIGINS`  | 覆盖允许的网页来源，逗号分隔         |
| `GOMOKU_WASM`     | 打包部署时 WASM 文件路径             |
| `GOMOKU_CONFIG`   | 打包部署时配置文件路径               |
| `HOST`、`PORT`    | 监听地址，默认 127.0.0.1:3001        |

房间与玩家只保存在内存中：最多 2,000 个房间，双方都离线且 30 分钟无操作的房间
和玩家被清除；服务重启会结束所有对局。`/api/health` 返回当前房间数与玩家数。

## 部署

`pnpm online:bundle` 把服务端打成 `dist/online/server.mjs`，并附上
`gomoku-engine.wasm` 与 `online.json`，主机只需要 Node.js 22 或更高版本。例如在 Docker 中：

```sh
docker run -d --name gomoku-online --restart unless-stopped --user node \
  --memory 256m --cpus 1 -p 127.0.0.1:3101:3101 -v "$PWD/app:/app:ro" -w /app \
  -e GOMOKU_AI=0 -e HOST=0.0.0.0 -e PORT=3101 -e GOMOKU_WASM=/app/gomoku-engine.wasm \
  -e GOMOKU_CONFIG=/app/online.json node:22-slim node server.mjs
```

网页通过 HTTPS 提供时必须使用 `wss://`，由反向代理终止 TLS。Caddy 的
`reverse_proxy` 会自动转发 WebSocket 升级。

## 当前边界

- 不计时，没有账号、排位、观战、聊天或棋局持久化。
- 单进程单实例；横向扩展需要共享房间状态。
