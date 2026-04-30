# Webapp

提供 Apple Music 下载的 Web 面板与 Telegram 私聊机器人。实际下载由名为 `applemusic_download` 的容器执行，webapp 通过 `docker exec` 调用它。

## 前置条件

- 已安装 Docker
- 下载容器已运行，且名称为 `applemusic_download`
- 下载目录已挂载到宿主机，并会同时挂载给 webapp 容器（例如 `/downloads`）

## 快速开始（Docker）

在仓库根目录执行：

```bash
docker build -f webapp/Dockerfile -t apple-music-webapp:test .
```

准备 `config.yaml`（根目录），至少包含：

- `completed-root-folder`
- `telegram-bot-token`（可选）
- `telegram-allowed-chat-id`（可选）
- `telegram-webapp-base-url`
- `telegram-store-path`

启动 webapp：

```bash
docker run -d --name apple-music-webapp \
  -p 5000:5000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /path/to/downloads:/downloads \
  -v ./config.yaml:/app/config.yaml \
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
.venv/bin/python webapp/app.py
```

可选启动 bot：

```bash
.venv/bin/python webapp/telegram_bot.py
```

## 配置

默认读取仓库根目录 `config.yaml`。

可选：使用 `webapp/config.yaml` 或环境变量：

```bash
export WEBAPP_CONFIG_PATH=/path/to/webapp-config.yaml
```

如果 Telegram 配置缺失，容器只启动 Flask webapp，不启动 bot。

## 使用方式

- Web 页面提交 Apple Music 链接即可创建任务
- 任务列表会显示 web 与 telegram 两种来源
- 选中任务后可看到实时进度与日志

Telegram：

- 仅私聊有效
- 只响应配置的 `telegram-allowed-chat-id`
- 发送 Apple Music 链接即可触发下载

## 日志

- 容器日志：`docker logs apple-music-webapp`
- 容器内文件日志：
  - `/app/logs/webapp.log`
  - `/app/logs/telegram-bot.log`

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
