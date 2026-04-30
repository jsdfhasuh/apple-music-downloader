[English](./README.md) / 简体中文

### ！！必须先安装[MP4Box](https://gpac.io/downloads/gpac-nightly-builds/)，并确认[MP4Box](https://gpac.io/downloads/gpac-nightly-builds/)已正确添加到环境变量

### 添加功能

1. 支持内嵌封面和LRC歌词（需要`media-user-token`，获取方式看最后的说明）
2. 支持获取逐词与未同步歌词
3. 支持下载歌手 `go run main.go https://music.apple.com/us/artist/taylor-swift/159260351` `--all-album` 自动选择歌手的所有专辑
4. 下载解密部分更换为Sendy McSenderson的代码，实现边下载边解密,解决大文件解密时内存不足
5. MV下载，需要安装[mp4decrypt](https://www.bento4.com/downloads/)

### 特别感谢 `chocomint` 创建 `agent-arm64.js`
对于获取`aac-lc` `MV` `歌词` 必须填入有订阅的`media-user-token`

- `alac (audio-alac-stereo)`
- `ec3 (audio-atmos / audio-ec3)`
- `aac (audio-stereo)`
- `aac-lc (audio-stereo)`
- `aac-binaural (audio-stereo-binaural)`
- `aac-downmix (audio-stereo-downmix)`
- `MV`

# Apple Music ALAC/杜比全景声下载器

原脚本由 Sorrow 编写。本人已修改，包含一些修复和改进。

## 使用方法
1. 确保解密程序 [wrapper](https://github.com/WorldObservationLog/wrapper) 正在运行
2. 开始下载部分专辑：`go run main.go https://music.apple.com/us/album/whenever-you-need-somebody-2022-remaster/1624945511`。
3. 开始下载单曲：`go run main.go --song https://music.apple.com/us/album/never-gonna-give-you-up-2022-remaster/1624945511?i=1624945512` 或 `go run main.go https://music.apple.com/us/song/you-move-me-2022-remaster/1624945520`。
4. 开始下载所选曲目：`go run main.go --select https://music.apple.com/us/album/whenever-you-need-somebody-2022-remaster/1624945511` 输入以空格分隔的数字。
5. 开始下载部分播放列表：`go run main.go https://music.apple.com/us/playlist/taylor-swift-essentials/pl.3950454ced8c45a3b0cc693c2a7db97b` 或 `go run main.go https://music.apple.com/us/playlist/hi-res-lossless-24-bit-192khz/pl.u-MDAWvpjt38370N`。
6. 对于杜比全景声 (Dolby Atmos)：`go run main.go --atmos https://music.apple.com/us/album/1989-taylors-version-deluxe/1713845538`。
7. 对于 AAC (AAC)：`go run main.go --aac https://music.apple.com/us/album/1989-taylors-version-deluxe/1713845538`。
8. 要查看音质：`go run main.go --debug https://music.apple.com/us/album/1989-taylors-version-deluxe/1713845538`。

[中文教程-详见方法三](https://telegra.ph/Apple-Music-Alac高解析度无损音乐下载教程-04-02-2)

## 下载歌词

1. 打开 [Apple Music](https://music.apple.com) 并登录
2. 打开开发者工具，点击“应用程序 -> 存储 -> Cookies -> https://music.apple.com”
3. 找到名为“media-user-token”的 Cookie 并复制其值
4. 将步骤 3 中获取的 Cookie 值粘贴到 config.yaml 文件中并保存
5. 正常启动脚本

## Flask 网页端

1. 确保下载容器已经在运行，并且容器名为 `applemusic_download`
2. 安装 Flask 依赖：

```bash
python3 -m venv .venv
.venv/bin/pip install -r webapp/requirements.txt
```

3. 启动网页端：

```bash
.venv/bin/python webapp/app.py
```

4. 打开浏览器访问：

```text
http://127.0.0.1:5000
```

说明：
- 网页端会通过 `docker exec` 调用现有容器内的 `apple-music-dl`
- 网页端默认自动选择最高音质，按 Hi-Res/ALAC 优先策略下载
- 页面不再提供 AAC/Atmos 手动选择，不支持 `--search`、`--select` 这类交互模式
- 页面会实时显示日志、阶段状态和已解析的下载结果路径

Telegram 私聊机器人：

```bash
.venv/bin/python webapp/telegram_bot.py
```

- Telegram 配置从 `config.yaml` 读取
- 必填配置项是 `telegram-bot-token`、`telegram-allowed-chat-id`、`telegram-webapp-base-url`，可选 `telegram-store-path`
- 机器人只接受 `telegram-allowed-chat-id` 指定私聊里的消息
- 每条消息只提取第一个 Apple Music URL，并调用 `/api/downloads`
- 任务映射会保存在 `telegram-store-path` 指定路径，默认是 `webapp/data/telegram_tasks.db`
- 下载完成或失败后，会回发到同一个 Telegram 私聊

额外接口：

```bash
curl -X POST http://127.0.0.1:5000/api/downloads \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://music.apple.com/...","force":false}'
```

返回：

```json
{"taskId":"...","status":"running"}
```

规则：
- 同一个 URL 只有在真正下载完成后，才会被记录为 `completed`
- 同一个 URL 再次提交时，默认不会重复下载
- 传 `force=true` 会强制重新下载
- 这个接口固定按自动最高音质策略执行，优先走 Hi-Res/ALAC
- NFO 生成后会把专辑目录移动到 `completed-root-folder/<歌手>/<专辑>`，默认是 `/downloads/completed`
