# OpenClaw AWD 裁判引擎

## 快速开始

### 1. 安装依赖

```bash
cd referee-engine
pip install -r requirements.txt
```

### 2. 启动服务

```bash
python main.py
```

服务将在 `http://localhost:8000` 启动

### 3. 测试 API

```bash
curl http://localhost:8000/health
```

## API 文档

### 提交 Flag

```bash
POST /api/submit
Content-Type: application/json

{
  "player_id": 1,
  "flag": "FLAG{...}"
}
```

规则说明：
- 只有攻击阶段允许提交 Flag
- 不允许提交自己的 Flag
- 裁判会根据 Flag 自动识别真实归属；`target_player_id` 可选，仅作为审计字段保留
- 同一个 Flag 可以被不同选手各自提交一次得分
- 同一名选手对同一个 Flag 只能成功得分一次

失败原因示例：
- `invalid_flag`
- `own_flag`
- `flag_already_claimed_by_attacker`

### 开始比赛

```bash
POST /api/matches/start
Content-Type: application/json

{
  "match": {
    "name": "Test Match",
    "duration": 7200
  },
  "llm": {
    "provider": "anthropic",
    "baseUrl": "https://api.anthropic.com"
  },
  "players": [
    {
      "id": 1,
      "name": "Player 1",
      "model": "[REDACTED]",
      "gatewayPort": 18789
    }
  ]
}
```

响应:
```json
{
  "match_id": "match_1710777600",
  "status": "started"
}
```

### 获取比赛状态

```bash
GET /api/matches/{match_id}
```

响应:
```json
{
  "match_id": "match_1710777600",
  "status": "running",
  "players": {
    "1": {
      "status": "running",
      "cpu_usage": 25.5,
      "memory_usage": {
        "usage_mb": 512.0,
        "limit_mb": 2048.0,
        "percent": 25.0
      }
    }
  }
}
```

接口职责说明：
- `GET /api/matches/{match_id}`：返回比赛总览、排行榜摘要、`events_count` 与 `recent_events`
- `GET /api/matches/{match_id}/events`：返回事件时间线
- `GET /api/matches/{match_id}/submissions`：返回提交事实记录

当前计分链路约定：
- `submissions` 是提交事实主记录
- `events` 用于时间线 / 回放
- 计分引擎当前按传入的 submission 列表计算得分，比赛运行态统一传入 `persisted_submissions`
- `validate_submission()` 会显式返回当次 `submission_record`，提交主流程不再依赖运行时列表尾项取记录

### 结束比赛

```bash
POST /api/matches/{match_id}/end
```

响应:
```json
{
  "match_id": "match_1710777600",
  "status": "ended"
}
```

### 列出所有比赛

```bash
GET /api/matches
```

响应:
```json
{
  "matches": [
    {
      "match_id": "match_1710777600",
      "status": "running"
    }
  ]
}
```

### WebSocket 连接

```javascript
const ws = new WebSocket('ws://localhost:8000/ws');

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Received:', data);
};
```

事件类型:
- `MATCH_STARTED` - 比赛开始
- `MATCH_ENDED` - 比赛结束
- `READY_UPDATE` - Agent 就绪状态更新
- `SCORE_UPDATE` - 分数更新
- `FLAG_CAPTURED` - Flag 被夺取
- `SERVICE_DOWN` - 服务宕机

## 测试

### 使用测试配置启动比赛

```bash
curl -X POST http://localhost:8000/api/matches/start \
  -H "Content-Type: application/json" \
  -d @test_config.json
```

### 查看 API 文档

访问 `http://localhost:8000/docs` 查看自动生成的 Swagger 文档

## 目录结构

```
referee-engine/
├── main.py              # FastAPI 应用主文件
├── requirements.txt     # Python 依赖
├── test_config.json     # 测试配置
└── README.md            # 本文件
```

## 开发

### 添加新的 API 端点

在 `main.py` 中添加新的路由:

```python
@app.post("/api/custom")
async def custom_endpoint():
    return {"message": "Custom endpoint"}
```

### 添加 WebSocket 事件

在 `RefereeEngine` 类中调用 `broadcast()`:

```python
await referee.broadcast({
    "type": "CUSTOM_EVENT",
    "data": {"key": "value"}
})
```
