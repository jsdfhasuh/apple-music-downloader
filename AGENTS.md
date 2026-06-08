# AGENTS.md

本文件是给 Codex 和其他自动化开发代理使用的仓库规则。请优先遵守这里的项目边界、运行环境和验证方式。

## 项目边界

- 这个仓库主要维护 `webapp/`：Flask 面板、Telegram 私聊机器人、任务队列、下载历史、歌手订阅、前端页面和相关测试。
- Go 下载器核心来自他人项目 fork，主要入口是 `main.go` 和 `utils/`。除非用户明确要求，不要对下载器核心做大范围重构，也不要直接执行完整 upstream merge。
- Webapp 不直接下载媒体。它通过 Docker 调用下载器容器：

```bash
docker exec -w /app applemusic_download apple-music-dl --json <url>
```

- 修改 webapp 的状态解析、失败判定或日志解析时，必须同时检查 `main.go` 的真实 stdout/stderr 行为。下载器日志里出现 `Error` 或 `Failed` 不一定等于最终任务失败。

## 远程 Docker 调试

- 当前 Docker context：`unraid-2375`
- 远程 Docker daemon：`tcp://192.168.100.3:2375`
- Webapp 容器：`apple-music-webapp`
  - 镜像：`apple-music-webapp:latest`
  - 网络：`br0`
  - IP：`192.168.100.94`
  - Web 服务：`http://192.168.100.94:5000`
- 下载器核心容器：`applemusic_download`
  - 镜像：`apple-music-downloader:test`
  - 网络：`br0`
  - IP：`192.168.100.93`

需要 Docker 调试时，优先使用上述远程 Docker 和既有容器，不要随意新建本地替代容器。常用只读检查：

```bash
docker context show
docker ps --filter name=apple-music-webapp --filter name=applemusic_download
docker inspect apple-music-webapp applemusic_download
docker logs apple-music-webapp
docker logs applemusic_download
```

如果需要检查 webapp 运行状态，优先用：

```bash
curl http://192.168.100.94:5000/api/tasks
curl http://192.168.100.94:5000/api/history
```

## 本地 Webapp 调试

- 调试前端、Web API、任务列表、SSE、历史展示时，默认在本地运行 Flask webapp，不新建本地 webapp Docker 容器：

```bash
python -m webapp.app
```

- 本地浏览器访问：

```text
http://127.0.0.1:5000
```

- 本地 webapp 仍通过当前 Docker context `unraid-2375` 调用远程下载器核心容器 `applemusic_download`。
- 本地真实下载目录是 `W:\downloads\music`。
- Windows 下 Python 的 `Path("/downloads")` 会解析为 `C:\downloads`，因此本地调试需要保持这些 junction：
  - `C:\downloads\ALAC` -> `W:\downloads\music\ALAC`
  - `C:\downloads\completed` -> `W:\downloads\music\completed`
- 如果后续调试 AAC 或 Atmos，再补：
  - `C:\downloads\AAC` -> `W:\downloads\music\AAC`
  - `C:\downloads\Atmos` -> `W:\downloads\music\Atmos`
- 不要改 `webapp/app.py` 的 `/downloads` 运行时语义来适配单台 Windows 机器；优先用本地 junction 保持和容器路径一致。

## 开发规则

- 改队列、任务生命周期、历史、重试、SSE 或下载后处理时，先读 `webapp/app.py`。
- 改 Telegram 交互、命令、通知、轮询或 task tracking 时，先读 `webapp/telegram_bot.py`。
- 改前端行为时，先读 `webapp/static/app.js` 和 `webapp/templates/index.html`。
- 改 Apple Music 歌手搜索或订阅扫描时，先读 `webapp/apple_music.py`。
- 保持 webapp 与下载器容器之间的 `/downloads` 共享挂载假设，除非用户明确要求重新设计部署。
- 任务队列当前应保持单 active task 的串行模型；失败任务不应阻断后续 queued task。
- 已完成 URL 的去重逻辑要以真正完成后的历史记录为准；`force=true` 才绕过已完成记录。
- 不要提交真实运行配置、token、cookie、数据库或日志。

## 配置和敏感数据

Webapp 配置解析顺序以 `webapp/config_loader.py` 为准：

1. `WEBAPP_CONFIG_PATH`
2. `data/config.yaml`
3. 仓库根目录 `config.yaml`
4. `webapp/config.yaml`
5. `webapp/config.example.yaml`

敏感值包括但不限于：

- `media-user-token`
- `authorization-token`
- `telegram-bot-token`
- `telegram-allowed-chat-id`
- 本地或容器内的 `downloads.db`
- `telegram_tasks.db`
- `data/logs/` 和 `/app/data/logs/`

提交前必须确认没有把真实运行配置或持久化数据加入版本控制。

## 测试和验证

前端 Node 测试：

```bash
node --test webapp/tests/test_app_js.js
```

Python webapp 测试：

```bash
python -m pytest webapp/tests/test_app.py webapp/tests/test_telegram_bot.py webapp/tests/test_build_nfo.py -q
```

Go 下载器测试或构建：

```bash
go test ./...
go build -o main.exe -v ./main.go
```

Docker 构建：

```bash
docker build -f webapp/Dockerfile -t apple-music-webapp:test .
```

当前本地基线：

- Docker CLI 已可用，context 为 `unraid-2375`，能看到 `apple-music-webapp` 和 `applemusic_download` 两个运行中容器。
- `node --test webapp/tests/test_app_js.js` 当前通过。
- 当前本地已存在根目录 `tools/`，包含 `tools.convert_to_flac` 和 `tools.build_nfo`。新环境或新 checkout 仍需先确认 `tools/` 是否存在，再验证 FLAC 转换、NFO、Docker build 或完整 Python 测试。
- `python -m pytest webapp/tests/test_build_nfo.py -q` 当前通过。
- 完整 Python 测试在 Windows 本地仍可能受 SQLite 临时 DB 文件锁、Windows 路径分隔符和长短路径差异影响；不要把完整套件说成已通过，除非在当前环境重新跑通。

## 上游更新

- `scripts/downloader-upstream-check.sh` 只生成上游变化报告和 patch，不会自动合并。
- `scripts/auto-update-downloader-production.sh` 面向下载器核心生产更新，默认容器名是 `applemusic_download`，会检查 webapp 是否空闲后再替换下载器容器。
- 上游下载器变更要手工迁移到本 fork 的下载器核心文件，保护 `webapp/`、本地 Docker 模型和文档。
