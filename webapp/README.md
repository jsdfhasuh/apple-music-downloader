# Webapp

提供 Apple Music 下载的 Web 面板与 Telegram 私聊机器人。实际下载由名为 `applemusic_download` 的容器执行，webapp 通过 `docker exec` 调用它。

## 持久化目录

推荐把 webapp 相关的持久化内容全部放到同一个宿主机 `data/` 目录，并挂载到容器内 `/app/data`。

容器内会使用这些持久化路径：

- `/app/data/config.yaml`
- `/app/data/downloads.db`
- `/app/data/telegram_tasks.db`
- `/app/data/logs/webapp.log`
- `/app/data/logs/telegram-bot.log`

## 前置条件

- 已安装 Docker
- 下载容器已运行，且名称为 `applemusic_download`
- 下载目录已挂载到宿主机，并会同时挂载给 webapp 容器（例如 `/downloads`）

## 快速开始（Docker）

在仓库根目录执行：

```bash
docker build -f webapp/Dockerfile -t apple-music-webapp:test .
```

准备 `data/config.yaml`，至少包含：

- `completed-root-folder`
- `telegram-bot-token`（可选）
- `telegram-allowed-chat-id`（可选）
- `telegram-webapp-base-url`
- `telegram-store-path`

如果你希望 FLAC 转换成功后自动删除原始 `.m4a`，把 `convert-keep-original` 设为 `false`。

启动 webapp：

```bash
docker run -d --name apple-music-webapp \
  -p 5000:5000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /path/to/downloads:/downloads \
  -v ./data:/app/data \
  apple-music-webapp:test
```

浏览器访问：

```
http://127.0.0.1:5000
```

## 本地运行（开发）

```bash
python3 -m venv .venv
.venv/bin/pip install -r webapp/requirements.txt
.venv/bin/python -m webapp.app
```

可选启动 bot：

```bash
.venv/bin/python -m webapp.telegram_bot
```

## 配置

容器环境默认优先读取 `/app/data/config.yaml`。

本地开发时，如果没有 `data/config.yaml`，则回退到仓库根目录 `config.yaml`。

可选：使用 `WEBAPP_CONFIG_PATH` 指定任意配置文件：

```bash
export WEBAPP_CONFIG_PATH=/path/to/webapp-config.yaml
```

如果 Telegram 配置缺失，容器只启动 Flask webapp，不启动 bot。

## 使用方式

- Web 页面提交 Apple Music 链接即可创建任务
- Web 页面可以搜索并订阅歌手；订阅后会扫描该歌手的专辑列表，把历史未成功或文件缺失的专辑加入现有串行队列
- 任务列表会显示 web 与 telegram 两种来源
- 选中任务后可看到实时进度与日志
- Telegram 支持在一条消息中发送多个 Apple Music 链接，机器人会按顺序自动入队并对消息内重复链接去重

Telegram：

- 仅私聊有效
- 只响应配置的 `telegram-allowed-chat-id`
- 发送 Apple Music 链接即可触发下载
- 歌手订阅命令：`/artist_search <关键词>`、`/subscribe <artist_url>`、`/subscriptions`、`/unsubscribe <artist_id>`、`/scan_subscriptions`

## 日志

- 容器日志：`docker logs apple-music-webapp`
- 容器内文件日志：
  - `/app/data/logs/webapp.log`
  - `/app/data/logs/telegram-bot.log`

## 常见问题

1. Web 页面提交后一直不动
   - 检查 `applemusic_download` 容器是否运行
   - 确认 webapp 容器挂载了 `/var/run/docker.sock`

2. Telegram 提示 `HTTP Error 409: Conflict`
   - 同一个 bot token 只能有一个轮询实例
   - 关掉重复的 bot 进程，只保留容器里的一个

3. 日志里出现 `task not found`
   - 任务在内存里，容器重启会丢
   - 重新提交链接即可

4. 转码或 NFO 报路径不存在
   - 确认 `/downloads` 已正确挂载到 webapp 容器
