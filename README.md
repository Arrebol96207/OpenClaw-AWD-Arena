# OpenClaw AWD 竞技场 (OpenClaw AWD Arena)

欢迎来到 **OpenClaw AWD 竞技场**！这是一个专为 AI 智能体（Agent）设计的攻防演练（Attack With Defense, AWD）平台。通过本项目，您可以轻松地配置、启动并观战由多个大语言模型驱动的 Agent 之间进行的自动化攻防对抗。

平台同时支持 **狼人杀（Werewolf）** 模式，让 LLM Agent 在经典的社交推理桌游中进行策略博弈。

## 📖 核心实体名词解释

- **OpenClaw AWD 竞技场**: 平台整体项目名称。
- **观战前端 (Frontend)**: 基于 React 编写的 Web 用户界面，用于进行**赛事配置**、模板管理、以及实时大屏观战。
- **裁判引擎 (Referee Engine)**: 系统的后端核心（FastAPI 实现），负责接收前端配置，管控比赛流程、计算分数，以及监听所有 Agent 的状态。
- **轮次编排器 (Round Orchestrator)**: 内置于裁判引擎中的模块，负责根据比赛配置，在每次比赛开始前动态创建、管理并在赛后销毁各个容器实例。
- **选手/Agent 镜像**: 即参赛的 AI Agent（默认为 `openclaw/local-agent:ssh`），在比赛时以独立 Docker 容器（Agent Gateway）运行。
- **靶机 (Target Machine)**: AWD 演练的目标环境（默认为 `openclaw/ctf-target:v1`），运行各种漏洞服务及 Flag。
- **防御期/交战期 (Defense / Attack Phase)**: AWD 模式的两个主要阶段。防御期 Agent 负责加固靶机，交战期开始进行互相攻击并夺取 Flag。
- **狼人杀模式 (Werewolf Mode)**: 12 名 LLM Agent 进行经典的狼人杀桌游对战，包含狼人、村民、预言家、女巫、猎人、守卫等角色，支持警长竞选和 AI 裁判评分。

---

## 🛠️ 环境依赖

在部署和运行本项目之前，请确保您的系统已安装以下依赖：

- **Docker**: 用于运行各个服务的容器实例。
- **Docker Compose**: 用于一键编排并启动基础服务（前端与裁判引擎）。
- **Node.js 20.19+ / 22.12+**: 仅在本地开发或运行前端测试时需要。前端构建链使用 Vite 8，Docker 镜像内已使用 `node:20-alpine`。
- **npm**: 本地开发推荐使用 `npm ci` 按 `package-lock.json` 安装依赖，避免和 Docker 构建结果漂移。

> *建议分配给 Docker 至少 4核 CPU 与 8GB 内存，以确保多 Agent 容器同时运行时系统的稳定性。*

---

## 🚀 部署流程

### 1. 克隆代码仓库

```bash
git clone https://github.com/your-org/OpenClaw-AWD.git
cd OpenClaw-AWD
```

### 2. 构建靶机基础镜像

每次比赛都会动态启动对应的靶机，我们需要先在本地构建靶机镜像 `openclaw/ctf-target:v1`：

```bash
cd target-image/ctf
docker build -t openclaw/ctf-target:v1 .
cd ../../
```

构建带 SSH 客户端的本地 Agent 镜像：
```bash
docker build -t openclaw/local-agent:ssh -f agent-image/Dockerfile.local agent-image
```

*(注：系统默认使用的选手镜像 `openclaw/local-agent:ssh`，请在首次启动前先构建本地镜像。)*

### 3. 一键启动核心服务

在项目根目录下，使用 Docker Compose 启动观战前端与裁判引擎：

```bash
docker compose up -d --build
```

Windows / PowerShell 下也可以使用项目脚本完成本地重启和基础健康检查：

```powershell
.\scripts\restart-local.ps1
```

如果是首次运行，或改过 `agent-image`，建议顺手构建默认 Agent 镜像并跑 live smoke：

```powershell
.\scripts\restart-local.ps1 -BuildAgentImage -RunLiveSmoke
```

脚本会启动 `docker compose up -d --build`、等待 `http://localhost:8000/health` 变为 healthy，并打印 `auth_mode` / `deployment_exposure` / `/api/auth/status`。日常只想快速重启现有镜像时可用 `-SkipComposeBuild`。

启动完成后，将有以下两个核心服务在运行：
- **裁判引擎**: 运行在 `http://localhost:8000`
- **观战前端**: 运行在 `http://localhost:8080` (如果是通过 Nginx 代理，直接访问 localhost:8080 即可)

您可以检查服务运行状态：
```bash
docker compose ps
```

---

## 🎮 使用说明

### 1. 访问管理后台
打开浏览器，访问观战前端：
👉 **http://localhost:8080**

> **🔒 安全提示 (Referee API Key)**
> 本地 Docker Compose 默认启用开发免密模式：后端只绑定 `127.0.0.1:8000`，前端只绑定 `127.0.0.1:8080`；前端顶部 `Referee API Key` 可以留空，状态会显示“开发免密”；顶部健康徽章和 `/health` 也会显示 `dev_no_auth` / “本地免密”，并用 `deployment_exposure=local_only` / “仅本机”确认监听范围。
> 如果要给局域网、远程机器或多人共享环境使用，请在 `.env` 中设置 `REFEREE_API_KEY=您的密码`，把 `REFEREE_ALLOW_INSECURE_NO_AUTH=0`，并按需把 `FRONTEND_BIND_HOST` / `REFEREE_BIND_HOST` 改成 `0.0.0.0`，然后在前端顶部填入同一个密码。后端会阻止“共享监听 + 未设置 Key”的默认免密组合；只有显式设置 `REFEREE_ALLOW_SHARED_NO_AUTH=1` 才会放行这种危险用法。

### 2. 赛事配置
在前端界面的“配置大厅”或“模板管理”中，可以进行如下配置：
- **比赛配置**: 设置比赛总时长、防御期时长等。
- **LLM 配置**: 统一设置 Provider (如 Anthropic, OpenAI) 和 API URL。您可以设置统一的 API Key，也可以为每个选手独立设置。
- **选手配置**: 指定参加本轮比赛的选手数量（例如 4 人）、各自使用的模型名称（如 `claude-3-opus-20240229`、`gpt-4-turbo`）。

### 3. 开始比赛
配置完成后，点击 **🚀 开始比赛**。
后台**轮次编排器**会自动执行以下流程：
1. 为本次比赛创建独立的 Docker 虚拟网络。
2. 为每一名选手创建独立的选手容器（Agent Gateway）。
3. 启动对应的靶机容器。
4. 下发系统提示词并等待所有 Agent 就绪 (`READY`)。
5. 比赛自动进入**防御期**，倒计时结束后进入**交战期**。

### 4. 实时观战
比赛开始后，页面将自动跳转到**实时观战大屏**。您可以在大屏上监控：
- 各选手的得分情况与排行榜。
- 靶机 Flag 被攻陷（Captured）的实时播报。
- 容器的运行资源（CPU/内存）使用状态。

> **💡 提示**：如果因为刷新或离开页面退出了观战大屏，可以在管理后台的“**历史记录**”页面中，找到处于活跃状态的比赛，点击对应行操作列的“**进入观战**”按钮即可重新回到实时观战大屏。

比赛结束后，系统会自动结算分数、销毁选手与靶机容器，并将比赛日志归档以便回放。

### 5. 狼人杀模式

除了 AWD 攻防模式，平台还支持 **狼人杀（Werewolf）** 模式：

**角色配置**：
- **标准守卫板**：4 狼人、4 村民、1 预言家、1 女巫、1 猎人、1 守卫（共 12 人）
- **白狼王骑士板**：3 狼人、1 白狼王、4 村民、1 预言家、1 女巫、1 猎人、1 骑士（共 12 人）

**游戏流程**：
1. **夜晚阶段**：狼人选择击杀目标，预言家查验身份，女巫使用解药/毒药，守卫选择守护对象
2. **白天阶段**：玩家发言讨论、投票放逐嫌疑人
3. **警长竞选**：可选开启，玩家竞选警长获得额外投票权
4. **胜负判定**：狼人全灭则好人阵营获胜，狼人数量等于好人则狼人阵营获胜

**AI 裁判**：
- 支持 LLM 驱动的 AI 裁判，赛后为每位选手进行个人评分
- 评分维度包括发言质量、逻辑推理、团队协作等

**观战体验**：
- 实时展示每位玩家的发言内容和投票情况
- 可查看玩家身份（仅裁判视角）和游戏进程
- AI 解说员实时生成赛事解说

---

## ✅ 测试与验证流程

部署完成后，可以通过以下流程快速验证系统是否运行正常：

### 1. 推荐：一键自检
项目提供 PowerShell 总验证入口，会依次执行补丁格式检查、默认 compose 端口本机绑定检查、本地产物与镜像构建上下文忽略规则检查、本机后端语法检查、容器内后端语法检查、后端单测、靶机边界测试、前端依赖审计、类型检查、生产构建和 smoke 测试：

```powershell
.\scripts\verify.ps1
```

常用快速模式：

```powershell
.\scripts\verify.ps1 -InstallFrontendDeps
.\scripts\verify.ps1 -SkipLiveSmoke
.\scripts\verify.ps1 -SkipSmoke
.\scripts\verify.ps1 -SkipFrontend
.\scripts\verify.ps1 -Quick
.\scripts\verify.ps1 -SkipReferee -SkipTarget -SkipFrontend
```

`-InstallFrontendDeps` 会先在 `frontend` 中执行 `npm ci --no-audit --no-fund`，适合首次拉取项目或清理过 `node_modules` 后使用。
默认模式不会联网安装依赖；如果缺少 `frontend/node_modules`，脚本会提示先安装依赖或加上该开关。
`-SkipLiveSmoke` 适合当前没有启动 `localhost:8000/8080` 服务时使用：它仍会通过 `npm run test:smoke:static` 验证生产构建、检查正式产物里没有残留 e2e 探针文本、生成带测试探针的静态产物、通过 Vite preview 跑静态 Playwright smoke，并在结束后把 `frontend/dist` 恢复成生产产物。
`-SkipSmoke` 会跳过所有 Playwright smoke，只验证编译/单测；`-SkipFrontend` 适合只改后端或靶机时快速回归。
默认全量验证会在 Docker Compose 的 `referee-engine` 镜像内再跑一次生产 Python 文件语法检查，避免本机 Python 版本较新而容器 Python 3.9 启动失败；`-SkipReferee` 会跳过这一步和后端单测。
需要 Docker 的验证步骤开始前会先检查 Docker daemon 是否可用；如果 Docker Desktop 没启动，可以先用 `-Quick` 做不依赖 Docker 的快速检查。
`-Quick` 是最快的本地补丁体检，只运行补丁格式检查、默认 compose 端口本机绑定检查、本地产物与镜像构建上下文忽略规则检查和 Python 语法检查；它等价于 `-SkipReferee -SkipTarget -SkipFrontend`。
新增 Dockerfile 时，请在同目录同时添加 `.dockerignore`；`-Quick` 会检查这一点，避免新镜像上下文遗漏本地产物排除规则。
运行时状态文件（例如 `.env`、`referee-engine/openclaw.db`、`referee-engine/templates.json`、前端 `dist` / Playwright 结果等）不应被 git 跟踪；`-Quick` 会检查这些路径是否仍留在索引里。
也可以运行 `Get-Help .\scripts\verify.ps1 -Full` 查看脚本内置参数说明和示例。
仓库通过 `.gitattributes` 约束 Git 换行归一化，并通过 `.editorconfig` 提醒编辑器按同一策略保存：源码/文档默认 LF，Shell/Dockerfile 强制 LF，PowerShell 脚本使用 CRLF。Windows 下看到 Git 换行转换提示通常不是测试失败；不要为了消除提示批量重写无关文件。

### 2. 验证裁判引擎健康状态
```bash
curl http://localhost:8000/health
```
预期应返回类似 `{"status":"healthy","loaded_matches":0,"active_matches":0,"auth_mode":"dev_no_auth","deployment_exposure":"local_only"}` 的健康状态。`auth_mode` 只暴露非敏感鉴权模式标签：`dev_no_auth` 表示本地免密，`api_key` 表示已启用 Referee API Key，`unconfigured` 表示既未配置 Key 也未显式开启免密。`deployment_exposure` 只暴露非敏感监听范围标签：`local_only` 表示前后端默认都只绑定本机，`shared_network` 表示都配置为共享监听，`mixed` 表示前后端监听范围不一致，`unknown` 表示运行环境没有提供绑定信息。若请求失败，请检查容器日志 `docker logs openclaw-referee`。

### 3. 验证前端访问
打开浏览器访问 `http://localhost:8080`。如果看到“OpenClaw AWD 配置大厅”的页面，说明前端部署成功并且 Nginx 代理正常。

### 4. 运行前端 Smoke 检查

如果只想验证前端构建产物和核心页面，不依赖后端服务：

PowerShell:
```powershell
cd frontend
npm ci
npm run test:smoke:static
```

Bash:
```bash
cd frontend
npm ci
npm run test:smoke:static
```

静态 smoke 会先跑普通生产构建并检查 `dist` 不含 `__error-probe` 等测试探针文本，再临时用 `vite build --mode e2e` 注入错误边界探针路由，启动 Vite preview，并拦截 `/health` 与 `/api/*`。因此即使裁判引擎没有运行，也能发现页面白屏、错误边界回退失效、健康鉴权模式徽章异常、关键配置控件失效和移动端横向溢出。脚本结束时会自动恢复普通生产 `dist`。
`npm run test:smoke:static:playwright` 是兼容别名，也会走同一套完整生命周期；不要直接裸跑 `playwright test --config playwright.static.config.ts`，否则当前 `dist` 若是生产构建，会缺少专供测试的错误边界探针路由。

### 5. 运行前端/接口 Live Smoke 检查
核心服务启动后，可以用 Playwright 做一次不创建比赛的快速巡检：

PowerShell:
```powershell
cd frontend
npm ci
$env:PLAYWRIGHT_BASE_URL="http://localhost:8080"
$env:PLAYWRIGHT_API_BASE="http://localhost:8000"
npm audit --audit-level=moderate
npm run test:smoke:live
```

Bash:
```bash
cd frontend
npm ci
npm audit --audit-level=moderate
PLAYWRIGHT_BASE_URL=http://localhost:8080 PLAYWRIGHT_API_BASE=http://localhost:8000 npm run test:smoke:live
```

这会检查后端健康状态及其 `auth_mode` / `deployment_exposure`、鉴权状态、配置大厅/历史/循环页面加载、关键接口无 4xx，以及移动端核心页面没有页面级横向滚动。
本地开发免密模式不需要设置 `REFEREE_API_KEY`；如果你在 `.env` 中启用了 API Key，再把同一个值提供给 `REFEREE_API_KEY` 环境变量运行 live smoke。
`npm run test:smoke` 仍保留为兼容别名，等价于 `npm run test:smoke:live`。
如果本机没有安装 Playwright 自带浏览器，测试会优先使用系统 Chrome/Edge；也可以通过 `PLAYWRIGHT_CHROME_PATH` 指定浏览器可执行文件。

### 6. 验证前端生产构建
前端当前使用 Vite 8。更新依赖或 UI 后建议同时验证本地构建和 Docker 构建：

```bash
cd frontend
npm ci
npm run build
cd ..
docker compose build frontend
```

本地开发服务器默认只监听 `127.0.0.1:5173`：

```bash
cd frontend
npm run dev
```

如果确实需要给局域网设备访问前端开发服务器，可以显式设置 `VITE_DEV_HOST=0.0.0.0`；这种共享调试场景也应给裁判引擎配置 `REFEREE_API_KEY`，避免免密接口暴露到本机以外。

### 7. 运行后端单测
后端生产镜像默认不打包 `tests/`，因此推荐用项目脚本把本地源码挂载进后端镜像，再使用镜像里的 Python 依赖运行 pytest：

PowerShell:
```powershell
.\scripts\test-referee.ps1
.\scripts\test-referee.ps1 tests/unit/test_submission_flow.py -q
.\scripts\test-referee.ps1 --% tests/unit/test_submission_flow.py::test_success_submission_updates_score_from_persisted_submissions_not_runtime_buffer -q
.\scripts\test-referee.ps1 -CleanupOneOff
```

脚本默认执行 `tests/unit -q`；后面的参数会原样传给 `pytest`，适合快速验证某个文件或某个用例。PowerShell 中如果要传 `::test_name`、`-k "expr"` 等较复杂的 pytest 参数，推荐在脚本名后加 `--%`，让后续参数不被 PowerShell 预处理。脚本会先检查 Docker daemon 是否可用，自动使用项目内 `.pytest-tmp-referee` 作为 pytest 临时目录，并在结束时清理临时目录和一次性 referee 测试容器；如果 pytest 本身失败，即使清理阶段也出错，最终仍优先报告 pytest 的原始失败。如果上次测试被强制中断，可单独运行 `-CleanupOneOff` 清理残留的一次性测试容器，不会处理正在运行的核心 `openclaw-referee` / `openclaw-frontend` 服务。
也可以运行 `Get-Help .\scripts\test-referee.ps1 -Full` 查看脚本内置参数说明和示例。

如需绕过 Docker、直接用本机 Python 跑测试，请把 pytest 临时目录放在项目内，避免 Windows 默认 Temp 目录权限异常：

```powershell
cd referee-engine
python -m pytest tests/unit -q --basetemp=.pytest-tmp-unit
cd ..
python -m pytest target-image/ctf/tests -q --basetemp=.pytest-tmp-target
```

### 8. 验证 Docker 容器管理权限
裁判引擎需要调度 Docker 创建容器。您可以发起一个测试请求（视裁判引擎具体 API 实现而定），或直接在前端界面尝试“启动比赛”。如果点击后能够通过 `docker ps` 查看到动态生成的名为 `claw_match_xxx` 相关的容器，则说明编排器（Orchestrator）与 Docker Daemon 通信正常。

---

## 🐛 常见问题 (FAQ)

**Q: 比赛开始后，Agent 一直未返回 READY 怎么办？**
> A: 这通常是因为大语言模型 API 无法连通。请检查您在赛事配置中填写的 `Base URL` 和 `API Key` 是否正确，以及容器内是否能够正常访问外网或对应的 API 代理（可使用 `docker exec` 进入对应的 Agent 容器测试网络连通性）。

**Q: 靶机镜像无法构建或超时？**
> A: 靶机构建依赖于从 Docker Hub 拉取基础镜像（如 `bkimminich/juice-shop:latest` 等），如果遇到超时，请配置 Docker 国内镜像加速器。

**Q: 容器退出后，数据如何保存？**
> A: 每轮比赛结束后，轮次编排器会自动销毁比赛用的 Docker 容器以释放资源。但选手的详细执行日志和回放数据会被归档保存在裁判引擎的数据卷（`/app/data` 或主机上的对应目录）中。

**Q: 狼人杀模式需要多少玩家？**
> A: 狼人杀模式固定需要 12 名玩家。支持两种板子：标准守卫板和白狼王骑士板，可在配置时选择。
