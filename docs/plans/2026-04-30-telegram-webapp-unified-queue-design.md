# Telegram + Webapp Unified Queue Design

**Goal**
- Bot 提交和前端提交共用同一下载队列，并且前端可见 bot 触发的任务进度
- 容器启动时自动同时启动 webapp 与 Telegram bot
- 任务日志继续以 SSE 形式提供；服务日志和 bot 日志通过容器 stdout 可观测

**Non-Goals**
- 不做“历史聊天记录回扫”能力
- 不做全局日志面板（仅保留任务级日志与容器日志）

**Current Behavior**
- 前端只订阅自己提交的 `taskId` 的 SSE
- Bot 只调用 `/api/downloads` 并在 Telegram 内轮询任务状态
- 容器只启动 `webapp/app.py`

**Proposed Changes**

1) **Task Model**
- 新增字段：
  - `source`: `web` | `telegram`
  - `createdAt`: ISO 字符串或 epoch 秒
- `DownloadTask.toDict()` 返回新字段

2) **API 扩展**
- `POST /api/downloads` 支持可选 `source` 字段
  - 未传时默认 `web`
- 新增 `GET /api/tasks`
  - 返回当前内存任务的摘要列表
  - 字段：`taskId`, `url`, `status`, `stage`, `progress`, `source`, `createdAt`
  - 默认按 `createdAt` 倒序

3) **Front-End 任务列表 + 详情**
- 新增任务列表区域
  - 展示来源、状态、阶段、进度、URL
- 新增任务详情区域沿用现有 SSE
  - 选择任务后订阅 `/api/tasks/<taskId>/stream`
- 自动选择规则
  - 用户未手动选择时，默认跟随最新 `running` 任务
  - 用户手动选择后不抢焦点
- 前端定时轮询 `GET /api/tasks`（如 2-3 秒）

4) **Telegram Bot**
- 调用 `/api/downloads` 时传 `source=telegram`
- 其他逻辑不变

5) **Container 入口**
- 新增启动脚本同时启动 webapp + bot
- Dockerfile 改为 `CMD ["bash", "webapp/start.sh"]`
- 标准输出作为服务日志与 bot 日志来源

**Logging Strategy**
- 任务日志：继续通过 SSE
- 服务日志：Flask stdout/stderr
- 机器人日志：bot stdout/stderr

**Risks & Mitigations**
- 轮询任务列表带来额外请求：保持 2-3 秒间隔，列表只返回摘要
- 多任务并发时 UI 焦点切换：仅在用户未手动选择时自动跟随

**Testing**
- 后端：`GET /api/tasks` 返回正确摘要与排序
- 后端：`source` 字段在 `POST /api/downloads` 中可用
- 前端：任务列表更新 + 手动选择不被自动抢焦点
- 运行时：容器启动后 webapp 与 bot 都在
