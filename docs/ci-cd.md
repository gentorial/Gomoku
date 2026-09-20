# 构建与网站部署

## 触发方式

主工作流是 `.github/workflows/ci.yml`：

- Pull request：运行 Windows/Linux 完整检查和独立比赛包构建，不部署网站。
- 推送到 `main`：运行同样的检查，全部成功后调用 `pages.yml` 部署网站。
- 手动运行：进入 Actions → CI → Run workflow。选择 `main` 会构建并部署；
  选择其他分支只构建与检查。

PR 有新提交时会取消旧的检查。同一分支的发布流程按顺序执行，正在进行的部署
不会被新提交中断。失败的构建或测试不会覆盖现有网站。

## 构建与产物

Windows 使用 MSVC x64，Linux 使用 GCC；均通过 CMake/Ninja 构建 C++20 引擎。
完整检查安装 Node.js 22、仓库锁定的 pnpm、Python 3.13 和 Emscripten 4.0.23，
执行 `pnpm check`，涵盖原生/WASM/TypeScript 构建、各层测试、类型与格式检查。
随后重新生成 JSON Schema，检查它们与提交内容一致，包括新增的未跟踪文件。

`training` 在 Windows/Linux 安装 uv 锁定的 CPU PyTorch 环境，复用同次 CI 的原生
Worker，验证数据去重与恢复、缺失标签、训练恢复、梯度累积和量化导出一致性。
同时执行默认完整宽度网络的前向、反向及优化器更新。该任务成功也是 Pages 发布前提。
常规 CI 不启动大规模训练，也不要求 GPU；XPU/CUDA 在对应设备单独验证。
教师适配器的回归测试使用仓库内带来源摘要的真实 Rapfi binpack 夹具，
覆盖评分/结果视角、MultiPV、截断拒绝、并发写锁与快照；CI 不下载或执行外部教师。

pnpm 依赖和 Emscripten SDK 使用缓存；SDK 缓存键包含安装脚本的哈希、操作系统
和架构。Actions 固定到完整提交 SHA，注释标明对应版本。

成功运行后，在 Actions 页面的 Artifacts 中可下载：

- `native-Windows-X64`、`native-Linux-X64`：开发用 JSON Worker 与 Gomocup 可执行文件，保留 14 天。
- `engine-wasm`：Linux 检查实际验证过的 `.mjs` 和 `.wasm`，保留 14 天。
- `gomocup-Windows-X64`、`gomocup-Linux-X64`：独立构建的比赛 ZIP 包，保留 30 天。
- `website`：发布路径配置完成后的静态网站，主分支发布时生成，保留 14 天。
- `github-pages`：GitHub 官方 Pages 部署使用的中间产物。

比赛构建是独立的矩阵任务，不安装网页、Python、Emscripten 或 JSON 依赖。
CPack 打包后，检查脚本会从 ZIP 解压出程序，在新的目录中实际运行
START、BEGIN、RESTART、END，避免只验证编译而忽略发布包是否可启动。
Linux 开发二进制面向对应 runner 的系统环境；不承诺跨所有 Linux 发行版兼容。

## GitHub Pages

仓库部署目标为 [五子棋网站](https://gentorial.github.io/Gomoku/)。
仓库 Settings → Pages 的发布来源已设置为 GitHub Actions，`github-pages`
环境允许 `main` 分支部署，不需要额外的 PAT 或服务器密钥。

`.github/workflows/pages.yml` 是由 CI 调用的可复用工作流：

1. 从本次 CI 运行中下载已验证的 `engine-wasm`，不重新编译引擎。
2. 使用 `actions/configure-pages` 读取真实网站 URL 和 `base_path`。
3. 下载固定 Release 模型并校验摘要；复用同次 CI 的原生检查器与 WASM，检查真实模型数值与完整对局。
4. 将路径传入 `GOMOKU_WEB_BASE` 构建网站，校验入口、CSS、图标、Worker、WASM 及模型文件的路径和摘要。
5. 上传静态目录并使用 `actions/deploy-pages` 发布。

项目站点自动使用 `/Gomoku/`，用户站点或自定义域名根路径使用 `/`。
该路径同时作用于页面资源、Worker、Worker 内加载的 WASM 和 NNUE 模型。
改名或配置自定义域名后，再运行一次主分支 CI 即可按 Pages 元数据重新构建。

部署任务只授予 `pages: write` 与 `id-token: write`；构建任务只有对应的读取权限。
只有主分支能进入部署流程，PR 检查不获取部署权限。
网站完全静态，GitHub Pages 不承载 Fastify API、原生 Worker 或训练进程。
模型由 `models/web-model.json` 固定，从 GitHub Release 下载后复制到站点同源目录。
发布新模型前先上传版本化模型资源，再推送更新的固定清单与参考向量；部署校验失败会保留原网站。
大权重、检查点和教师数据不进入普通 Git 历史。

## 本地验证

已安装依赖和 Emscripten 时：

    pnpm check
    pnpm web:verify
    pnpm engine:competition
    cpack --config build/competition/CPackConfig.cmake -B artifacts
    cmake -P scripts/verify-competition.cmake

打包检查要求 `artifacts` 中只有本次要验证的一个 `gomoku-*.zip`；CI 使用干净目录。

模拟 GitHub Pages 子路径，在 PowerShell 中运行：

    $env:GOMOKU_WEB_BASE = "/Gomoku/"
    pnpm --filter @gomoku/web build
    pnpm web:verify
    pnpm --filter @gomoku/web preview

然后访问 `http://127.0.0.1:4173/Gomoku/`。测试结束后用
`Remove-Item Env:GOMOKU_WEB_BASE` 恢复默认路径，再构建普通根路径网站。
Linux/macOS 可在对应命令前加 `GOMOKU_WEB_BASE=/Gomoku/`。

## 排查

- 初次接入或 fork：在 Settings → Pages 选择 GitHub Actions，允许 `github-pages`
  环境从 `main` 部署，并确认仓库允许使用工作流中的 Actions。
- `Check` 失败：先运行本地 `pnpm check`；Schema 漂移时重新生成并提交全部 Schema 文件。
- `Gomocup` 失败：检查 CMake、编译器、CPack 或 ZIP 解压启动步骤的日志。
- Pages 构建失败：检查 `engine-wasm` 下载、Pages 设置和 `web:verify` 的路径提示。
- 部署失败：检查 `github-pages` 环境的分支限制、审批规则及 Pages 设置；
  可在 Actions 中重新运行失败任务，或手动运行主分支 CI。

参考 [GitHub Pages 自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
及 [Vite 静态部署说明](https://vite.dev/guide/static-deploy#github-pages)。
