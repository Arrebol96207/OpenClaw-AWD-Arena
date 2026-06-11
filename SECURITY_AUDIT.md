# OpenClaw-AWD-Arena 安全审计报告

**当前同步日期**: 2026-06-09  
**审计范围**: `referee-engine`、`target-image`、`orchestrator`、`frontend`  
**状态说明**: 本文按当前代码与测试状态同步。早期审计中多条 Critical 链路已经被修复，下面区分“已修复并验证”和“仍需关注”。

---

## 一、已修复并有测试覆盖的高风险链路

### 1. CTF 靶机登录 SQL 注入

**位置**: `target-image/ctf/app.py`

当前登录查询已改为参数化：

```python
c.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
```

**验证**: `target-image/ctf/tests/test_stage1_boundaries.py::test_sqli_login_bypass_is_rejected` 确认 `' OR '1'='1` 类 payload 返回 401。

### 2. `/api/debug/run` 命令注入

**位置**: `target-image/ctf/app.py`

当前实现先拒绝 shell 元字符，再使用 `subprocess.run(cmd.split(), shell=False)`。旧版 `sh -c "echo {cmd}"` 的直接 shell 拼接链路已移除。

**验证**: `test_tools_ping_blocks_command_injection` 和边界测试覆盖命令注入类 payload。

### 3. SSRF 到内部接口的组合链

**位置**: `target-image/ctf/app.py`

当前防护点：
- `is_allowed_fetch_target()` 拒绝 loopback、private、link-local、Docker 常见私网段和受限端口。
- `_is_internal_request()` 要求请求来源是 localhost 且带 `X-Internal-Request: 1`，不再是 OR 逻辑。
- redirect 到内部资源会在 fetch 前被阻断。

**验证**:
- `test_preview_fetch_blocks_direct_localhost_port_3000`
- `test_preview_fetch_blocks_loopback_redirector_before_following_redirect`
- `test_flag4_ssrf_chain_is_blocked_before_credential_sync`
- `test_webhook_test_rejects_direct_internal_target`
- `test_flag6_webhook_redirect_chain_is_blocked_before_internal_audit`

### 4. `/api/export/report` 读文件与凭证导出

**位置**: `target-image/ctf/app.py`

当前导出文件路径做了目录边界校验；credential 类导出需要一次性 `job_token`，使用后即失效。

**验证**:
- `test_export_report_blocks_path_traversal_even_with_internal_header`
- `test_flag4_requires_one_time_job_token`

### 5. 模板 include 路径边界

**位置**: `target-image/ctf/app.py`

当前 `_read_template_include()` 使用 `Path.resolve()` + `relative_to()`，拒绝兄弟目录前缀绕过和绝对路径。

**验证**:
- `test_template_include_path_boundary_rejects_sibling_prefix`
- `test_flag5_template_report_rejects_path_traversal_include`

### 6. 会话过期

**位置**: `target-image/ctf/app.py`

当前 `SESSION_TTL_SECONDS = 1800`，并在读取 session 时执行 `_prune_expired_sessions()`。旧版“内存 session 永不过期”的风险已缓解。

### 7. Flag 注入与提交并发

**位置**: `referee-engine/flag_manager.py`

当前 DB flag 注入使用 SQLite hex literal，避免 flag 内容进入 SQL 字符串拼接；`submitted_flag_claims` 使用 `asyncio.Lock` 保护检查与写入。

**验证**:
- `referee-engine/tests/unit/test_flag_manager.py`
- `referee-engine/tests/unit/test_submission_flow.py`

### 8. 前端/API 管理密钥暴露面

**位置**: `frontend/src/api.ts`、`frontend/src/components/Layout.tsx`

当前前端管理 API key 存入 `sessionStorage`，关闭标签页后清除；后端配置读取仍保留原始密钥，但配置类 API 返回会脱敏。  
本地 Docker Compose 默认启用 `REFEREE_ALLOW_INSECURE_NO_AUTH=1`，且后端只绑定 `127.0.0.1:8000`、前端只绑定 `127.0.0.1:8080`，用于单机试玩时免填 Referee Key；核心 `api_auth.py` 的库级默认仍是未配置 key 且未显式开启免密时返回 503。`/health` 只公开非敏感 `auth_mode` 标签（`dev_no_auth` / `api_key` / `unconfigured`）和 `deployment_exposure` 标签（`local_only` / `shared_network` / `mixed` / `unknown`），用于排障时确认当前鉴权模式与监听范围，不返回密钥内容。共享网络/远程部署应设置 `REFEREE_API_KEY`、关闭 `REFEREE_ALLOW_INSECURE_NO_AUTH`，并显式调整 `FRONTEND_BIND_HOST` / `REFEREE_BIND_HOST`。

**验证**:
- `referee-engine/tests/unit/test_api_auth.py` 覆盖库级默认拒绝、显式开发免密、配置 key 后精确匹配、`auth_mode` 标签和状态 payload 不泄漏密钥。
- `referee-engine/tests/unit/test_health.py` 覆盖健康 payload 的 loaded/active match 计数、编排模式、`auth_mode` 和 `deployment_exposure` 字段。
- `test_configured_key_takes_precedence_over_dev_auth_flag` 固定了配置 `REFEREE_API_KEY` 时优先要求 Key，即使 `REFEREE_ALLOW_INSECURE_NO_AUTH=1` 也不能绕过共享部署的管理鉴权。
- `frontend/tests/smoke.spec.ts` 覆盖运行中控制台的开发免密 UI、`/health.auth_mode` 与 `/health.deployment_exposure`：受保护列表可直接请求，顶部不显示 API Key 输入框，并显示“仅本机”监听范围；配置 key 时仍走 Key 有效路径。

### 9. 容器运行与 SSH 初始化

**位置**: `referee-engine/main.py`

当前 Agent 容器收紧了 capabilities；SSH 私钥安装改为由容器内 owner 用户创建 `.ssh`，避免 `cap_drop=["ALL"]` 后 root `docker exec` 写入失败。主后端、前端默认配置和旧 `orchestrator/round_orchestrator.py` 的默认 Agent 镜像已统一为 `openclaw/local-agent:ssh`，旧 `alpine/openclaw:latest` 只保留为本地镜像构建基础和历史配置兼容映射。

**验证**: `referee-engine/tests/e2e/test_match_ssh_integration.py` 通过真实 Docker 路径验证。

---

## 二、仍需关注的风险与优化项

### H1. 真实密钥轮换

`.env` 曾包含真实 `REFEREE_API_KEY` / commentator API key。即使 build context 已通过 `.dockerignore` 收敛，真实密钥仍应由使用者轮换。

### H2. 日志脱敏基础层已收敛，仍可扩大覆盖

`referee-engine/redaction.py` 已集中覆盖 flag、token、API key、password、Bearer、cookie/set-cookie、长 hex secret 等基础脱敏；`main.py`、`commentator.py` 和 `database.py` 已复用该 helper。  
`player_code_export.py` 的专用文件内容脱敏也已覆盖 flag、API key、Bearer、Basic auth、Cookie/Set-Cookie、password、私钥块等常见泄露形态，并保留独立测试。后续仍建议继续审计所有新增持久化/导出路径，避免新输出通道绕过基础 helper 或导出专用规则。

### H3. WebSocket ticket 已加短票据、来源绑定与限流

当前 WebSocket ticket 为随机短票据，60 秒 TTL，消费即失效，并绑定签发时的客户端 host 与 User-Agent；默认也禁用了 `api_key` query 兼容鉴权，避免管理密钥出现在浏览器 URL 中。`/api/ws-ticket` 还带有 per-host 内存窗口限流，超限返回 429 和 `Retry-After`。  
**验证**: `referee-engine/tests/unit/test_platform_security.py` 覆盖缺失鉴权、禁用 query key、ticket 复用失败、过期失败、host/UA 不匹配失败、header key 兼容路径，以及 ticket 签发限流。

### H4. SLA 检查已扩展到关键业务入口

当前 SLA 不再只依赖 `/health`，还会检查 `/login` 与 `/downloads`；保留健康端点但破坏登录或文档中心会被判定为 `DEGRADED` 并扣分。
**验证**: `referee-engine/tests/unit/test_flag_manager.py` 覆盖全部探针正常、业务探针失败降级、health 失败短路为 `DOWN`。
后续仍可继续增加内容断言、静态资源和报告生成等更深业务探针。

### H5. `orchestrator/round_orchestrator.py` 旧网络分配逻辑已收口

旧 orchestrator 当前会枚举 `10.201.0.0/24` 到 `10.201.255.0/24` 候选段，并跳过 Docker 中已存在且重叠的子网；候选耗尽时会显式失败，而不是盲目创建冲突网络。
**验证**: `referee-engine/tests/unit/test_round_orchestrator.py` 覆盖 hash 首选、重叠子网跳过、候选池耗尽和已存在网络复用。

### H6. SQLite 写入韧性已做轻量缓解

数据库已启用 WAL、`busy_timeout`、参数化查询，并在异步数据库入口统一加入 transient lock retry：遇到 `database is locked` / busy / table locked 等短暂锁冲突会指数退避重试，非锁类 SQL 错误仍立即抛出。  
**验证**: `referee-engine/tests/unit/test_platform_security.py` 覆盖锁错误重试成功、非锁错误不重试、重试耗尽后抛出。  
后续如果出现极高频事件/日志写入，仍建议引入集中 DB worker 或写入队列来进一步削峰。

### H7. Agent 配置写入已避免宿主临时文件落盘

Agent/OpenClaw provider 配置中包含 LLM `apiKey`。当前主路径 `referee-engine/agent_client.py` 与旧 orchestrator 均已改为通过 `docker exec -u root -i ... cat > openclaw.json` 从 stdin 写入容器，不再先写宿主 `NamedTemporaryFile` 再 `docker cp`，降低异常退出或清理失败时密钥残留在宿主临时目录的风险。
后续仍建议持续审计新增的配置注入路径，避免重新引入“敏感配置先落宿主盘”的旁路。

### H8. Agent 初始化失败路径已补重试与清理保护

AWD 启动阶段不再因为首轮 `ready_count == 0` 就立即终止比赛，而是先进入已有的 bounded readiness retry 窗口；重试后仍然 0 个 Agent 就绪时才进入 error 并清理资源。同时 `destroy_match()` 避免从 startup task 内部销毁时取消自己，修复错误清理路径抛出 `CancelledError` 的问题。

### H9. 事件后台持久化失败已受控记录

`MatchState.add_event()` 仍保持“先写入内存事件、后台异步持久化”的低延迟行为，但后台 DB 写入失败现在会被 `_persist_event_background()` 捕获并记录 warning，避免出现无人认领的 task 异常，同时保留同步 `add_event_and_persist()` 对关键事件的强持久化语义。

### H10. 比赛结束/销毁会等待后台任务取消完成

`end_match()` 和 `destroy_match()` 已统一通过 `_cancel_task()` 取消并等待 startup、flag refresh、match timer 等后台任务进入完成态，避免结束比赛返回后旧循环继续写事件、广播或改状态。直接销毁 active match 时也会先停止 flag refresh、SLA checker 与 match timer，再拆容器/网络。`_match_timer()` 自身也会在父任务被取消时清理 heartbeat、defense keepalive、attack keepalive 子任务；攻击阶段派生的 attack prompt dispatch、prompt verification 和 buffered-message drain 任务也会被 bounded wait、取消或记录异常，避免计时器子循环和攻击阶段派生任务在比赛结束或销毁后遗留。当前测试覆盖 `end_match()` 返回前 flag refresh task 已处理 `CancelledError`，直接 `destroy_match()` active match 时 flag/timer task 已处理 `CancelledError`，`_match_timer()` 取消时 heartbeat/keepalive 子任务已处理 `CancelledError`，以及慢 attack prompt dispatch 会被取消。

### H11. 靶机 DB 文件权限与挑战设计边界

靶机本身是 CTF 资产，部分弱点可能是有意挑战面。建议文档持续标注“故意漏洞”和“平台漏洞”的边界，避免未来维护时误删挑战设计或误保留平台风险。

### H12. 前端没有独立登录态

前端 SPA 路由没有登录页，实际安全依赖后端 API key。对于本地控制台可接受；如果部署到共享网络，建议加入管理会话或反向代理 Basic/OIDC。

### H13. 架构体积

`referee-engine/main.py` 仍承担比赛生命周期、容器编排、SSH 管理、API、WebSocket、狼人杀流程等职责。当前已先把 WebSocket ticket 状态机拆到 `ws_ticket.py`，把 WebSocket 鉴权决策拆到 `ws_auth.py`，把选手只读 token 索引/签发/撤销拆到 `player_tokens.py`，把纯 API key 策略拆到 `api_auth.py`，把部署侧 CORS 与前端 dist 完整性规则拆到 `deployment_config.py`，把配置模板持久化拆到 `template_store.py`，把公共输出脱敏、事件可见性规则和事件分页协议拆到 `public_payload.py`，把外连 URL 安全策略拆到 `outbound_policy.py`，把 Markdown 报表渲染拆到 `match_report.py`，把历史/活跃比赛摘要合并与狼人杀摘要字段构造拆到 `match_summary.py`，把健康检查 loaded/active 计数与编排模式 payload 拆到 `health.py`，把历史比赛容器元数据/排行榜快照恢复拆到 `history_restore.py`，把 Docker API version 与子网选择逻辑拆到 `docker_networking.py`，把比赛配置与 API DTO 拆到 `match_models.py`，把选手状态接口的排行榜摘要/分数变化计算、排行榜身份展示字段和结束/历史榜单快照回退逻辑拆到 `player_status.py`，把 `target-ssh` helper 生成、容器路径/账号校验与探测失败分类拆到 `target_ssh.py`，把选手代码导出包状态判断和安全导出目录清理边界收敛到 `player_code_export.py`，并把 Flag 提交后的选手反馈文案拆到 `submission_feedback.py`，降低安全状态逻辑、部署配置、模板脱敏、公共响应清洗、事件分页、SSRF 防护、报表输出、历史摘要、健康检查、历史恢复、网络分配、请求/响应 schema、选手状态计算、WebSocket 鉴权、选手 token、SSH 维护通道、选手代码导出、提交反馈和巨型入口文件的耦合。短期不影响运行，但长期仍建议继续按 match lifecycle、container runtime、routes、websocket、werewolf 模块拆分。

### H14. 前端离线浏览器级烟测已接入验证链

`frontend/tests/smoke-static.spec.ts` 会在 Vite preview 上拦截 `/health` 与 `/api/*`，不依赖后端服务即可检查核心路由渲染、错误边界、`/health.auth_mode` 对应的“本地免密”徽章、`/health.deployment_exposure` 对应的“仅本机”徽章、自动生成名称/最近模型交互和移动端横向溢出。`npm run test:smoke:static` 由 `frontend/scripts/smoke-static.mjs` 承包完整生命周期：先运行普通生产构建并扫描 `frontend/dist`，确认不含 `__error-probe` / `Route error boundary probe` 等 e2e 探针文本；再运行 `vite build --mode e2e` 生成带错误边界探针路由的静态测试产物并执行 Playwright smoke；最后再次运行普通 `npm run build` 并重复产物守卫，避免本地正式产物停留在测试构建状态。`scripts/verify.ps1 -SkipLiveSmoke` 会调用该静态 smoke 脚本，同时跳过依赖真实 `localhost:8000/8080` 的 live smoke。前端脚本已区分 `test:smoke:static` 与 `test:smoke:live`，`test:smoke` 仅保留为 live smoke 兼容别名，`test:smoke:static:playwright` 也指向完整静态 smoke 生命周期，避免直接在生产 `dist` 上裸跑 Playwright 时缺少 e2e 探针路由；live smoke 已适配本地开发免密和配置 API Key 两种模式。Playwright 的 `test-results/` 与 `playwright-report/` 运行产物已加入 `.gitignore`，避免失败 trace/report 污染工作区。

### H15. 换行与编辑器格式护栏已补齐

`.gitattributes` 现在约束 Git 归一化：源码/文档默认 LF，Shell 与 Dockerfile 强制 LF，PowerShell 脚本使用 CRLF；`.editorconfig` 同步这些保存规则，并要求源码默认 UTF-8、最终换行和去除行尾空白。`scripts/verify.ps1` 会先运行 `git -c core.safecrlf=false diff --check` 捕获真实补丁格式问题（例如 `referee-engine/main.py` 的尾随空白），同时静音 Windows 下预期的换行转换 warning；临时 `--no-index` 对照验证确认该写法仍会报告 trailing whitespace。随后脚本会展开默认 `docker compose config`，确认本地开发免密模式下 referee 与 frontend 端口都绑定到 `127.0.0.1`，避免后续改 compose 时把免密控制台意外暴露到局域网或公网监听地址。这些 warning 不应通过批量重写无关文件来“消除”。

---

## 三、当前验证快照

最近一次已知通过的验证：

```text
verify.ps1 -SkipLiveSmoke: passed
patch format check: passed
default compose localhost bind check: passed
local artifact ignore check: passed
local Python syntax check: passed
referee container production Python syntax check: passed
referee-engine unit tests: 331 passed
target-image/ctf boundary tests: 23 passed
frontend audit: 0 vulnerabilities
frontend tsc/build: passed
frontend static smoke: 5 passed
frontend live smoke: 5 passed (separate latest local run, 本地开发免密模式)
health endpoint: auth_mode=dev_no_auth, deployment_exposure=local_only
```

建议每次改动后至少运行：

```bash
.\scripts\verify.ps1
```

如当前没有启动前后端服务，可先用 `.\scripts\verify.ps1 -SkipLiveSmoke` 做带静态浏览器烟测的离线回归；如本机没有可用浏览器，可用 `.\scripts\verify.ps1 -SkipSmoke` 跳过 Playwright smoke；如只想做最快补丁体检，可用 `.\scripts\verify.ps1 -Quick` 只跑补丁格式检查、默认 compose 端口本机绑定检查、本地产物忽略规则检查和 Python 语法检查（等价于 `-SkipReferee -SkipTarget -SkipFrontend`）。
首次拉取项目或清理过前端依赖后，使用 `.\scripts\verify.ps1 -InstallFrontendDeps` 先安装 `frontend/node_modules`。
默认全量验证还会在 Compose 的 `referee-engine` 镜像内执行生产 Python 文件的 `python -m py_compile`，用于捕获本机 Python 版本较新而容器 Python 3.9 不兼容的语法/注解问题；脚本会把 Windows 相对路径转换为容器内可识别的 `/workspace` 相对路径，并在 `git`、`docker compose`、`python`、`cmd /c npm`、`cmd /c npx`、`test-referee.ps1` 等 native command 后统一检查退出码，避免子命令失败却误报通过。
验证脚本还会检查根 `.gitignore` 与前端、后端、Agent、Hermes runtime、`target-image` 根、CTF 靶机、hardtest 镜像上下文的 `.dockerignore` 是否继续排除 `.env`、数据库/缓存、测试、Playwright 报告、前端 `dist`/`node_modules` 等本地产物，避免 git 工作区和 Docker build context 意外携带本地密钥或运行产物。
同一检查还会扫描所有 `Dockerfile` / `Dockerfile.*`，要求每个 Dockerfile 同目录都有 `.dockerignore`，防止未来新增镜像上下文时漏掉本地产物排除规则。
`referee-engine/templates.json` 属于运行时模板持久化文件，已从 git 索引移除并由 `.gitignore` 管理；验证脚本会检查 `.env`、本地 DB、模板状态、前端构建产物和测试结果等路径不能被 git 跟踪。
验证脚本在进入容器语法检查、后端单测或靶机边界测试前会先探测 Docker daemon，Docker Desktop 未启动时会给出明确提示；`-Quick` 仍保持不依赖 Docker。
`scripts/test-referee.ps1` 独立运行时也会先探测 Docker daemon，并在 `finally` 清理 one-off 容器时保留 pytest 原始失败，避免 cleanup 错误盖住真正的测试失败原因。

---

## 四、结论

早期最危险的链路 **SSRF → X-Internal-Request 绕过 → `/api/debug/run` RCE** 当前已被代码和测试覆盖地阻断；登录 SQLi、模板路径穿越、credential 导出复用、flag 提交并发等也已修复或缓解。

下一阶段最值得做的是：统一日志脱敏、继续加深 SLA 内容断言、拆分 `main.py`，以及给共享网络部署增加更完整的管理认证。
