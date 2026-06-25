English / [简体中文](./README-CN.md)

### ！！Must be installed first [MP4Box](https://gpac.io/downloads/gpac-nightly-builds/)，And confirm [MP4Box](https://gpac.io/downloads/gpac-nightly-builds/) Correctly added to environment variables

### Add features

1. Supports inline covers and LRC lyrics（Demand`media-user-token`，See the instructions at the end for how to get it）
2. Added support for getting word-by-word and out-of-sync lyrics
3. Support downloading singers `go run main.go https://music.apple.com/us/artist/taylor-swift/159260351` `--all-album` Automatically select all albums of the artist
4. The download decryption part is replaced with Sendy McSenderson to decrypt while downloading, and solve the lack of memory when decrypting large files
5. MV Download, installation required[mp4decrypt](https://www.bento4.com/downloads/)
6. Add interactive search with arrow-key navigation `go run main.go --search [song/album/artist] "search_term"`

### Special thanks to `chocomint` for creating `agent-arm64.js`

For acquisition`aac-lc` `MV` `lyrics` You must fill in the information with a subscription`media-user-token`

- `alac (audio-alac-stereo)`
- `ec3 (audio-atmos / audio-ec3)`
- `aac (audio-stereo)`
- `aac-lc (audio-stereo)`
- `aac-binaural (audio-stereo-binaural)`
- `aac-downmix (audio-stereo-downmix)`
- `MV`

# Apple Music ALAC / Dolby Atmos Downloader

Original script by Sorrow. Modified by me to include some fixes and improvements.

## GitHub Container Images

This fork publishes two GitHub Container Registry images from `.github/workflows/docker.yml`:

- Downloader: `ghcr.io/jsdfhasuh/apple-music-downloader`
- Flask dashboard and Telegram bot: `ghcr.io/jsdfhasuh/apple-music-downloader-webapp`

The workflow builds both images for `linux/amd64`. Pushes to `main`, `v*` tags, and manual workflow dispatches publish images; pull requests build the images without pushing them.

Published tags include `latest` for the default branch, branch or tag refs, and `sha-<commit>`.

## Running with Docker

1. Make sure the decryption program [wrapper](https://github.com/WorldObservationLog/wrapper) is running

2. Start the downloader with Docker:
   ```bash
   # show help
   docker run --network host -v ./downloads:/downloads ghcr.io/jsdfhasuh/apple-music-downloader --help

   # start downloading some albums
   docker run --network host -v ./downloads:/downloads ghcr.io/jsdfhasuh/apple-music-downloader https://music.apple.com/ru/album/children-of-forever/1443732441

   # start downloading single song
   docker run --network host -v ./downloads:/downloads ghcr.io/jsdfhasuh/apple-music-downloader --song https://music.apple.com/ru/album/bass-folk-song/1443732441?i=1443732453

   # start downloading select
   docker run -it --network host -v ./downloads:/downloads ghcr.io/jsdfhasuh/apple-music-downloader --select https://music.apple.com/ru/album/children-of-forever/1443732441

   # start downloading some playlists
   docker run --network host -v ./downloads:/downloads ghcr.io/jsdfhasuh/apple-music-downloader https://music.apple.com/us/playlist/taylor-swift-essentials/pl.3950454ced8c45a3b0cc693c2a7db97b

   # for dolby atmos
   docker run --network host -v ./downloads:/downloads ghcr.io/jsdfhasuh/apple-music-downloader --atmos https://music.apple.com/us/album/1989-taylors-version-deluxe/1713845538
   
   # for aac
   docker run --network host -v ./downloads:/downloads ghcr.io/jsdfhasuh/apple-music-downloader --aac https://music.apple.com/us/album/1989-taylors-version-deluxe/1713845538

   # for see quality
   docker run --network host -v ./downloads:/downloads ghcr.io/jsdfhasuh/apple-music-downloader --debug https://music.apple.com/ru/album/miles-smiles/209407331
   ```

You can change `config.yaml` by mounting a volume:

> **Note:** Before running the following command, make sure that a `config.yaml` file exists in your current directory. You can create your own, or copy the default one from the repository (if available). If `./config.yaml` does not exist, Docker will create an empty directory instead of a file, which will cause the container to fail.
```bash
docker run --network host -v ./downloads:/downloads -v ./config.yaml:/app/config.yaml ghcr.io/jsdfhasuh/apple-music-downloader [args]
```

## How to use
1. Make sure the decryption program [wrapper](https://github.com/WorldObservationLog/wrapper) is running
2. Start downloading some albums: `go run main.go https://music.apple.com/us/album/whenever-you-need-somebody-2022-remaster/1624945511`.
3. Start downloading single song: `go run main.go --song https://music.apple.com/us/album/never-gonna-give-you-up-2022-remaster/1624945511?i=1624945512` or `go run main.go https://music.apple.com/us/song/you-move-me-2022-remaster/1624945520`.
4. Start downloading select: `go run main.go --select https://music.apple.com/us/album/whenever-you-need-somebody-2022-remaster/1624945511` input numbers separated by spaces.
5. Start downloading some playlists: `go run main.go https://music.apple.com/us/playlist/taylor-swift-essentials/pl.3950454ced8c45a3b0cc693c2a7db97b` or `go run main.go https://music.apple.com/us/playlist/hi-res-lossless-24-bit-192khz/pl.u-MDAWvpjt38370N`.
6. For dolby atmos: `go run main.go --atmos https://music.apple.com/us/album/1989-taylors-version-deluxe/1713845538`.
7. For aac: `go run main.go --aac https://music.apple.com/us/album/1989-taylors-version-deluxe/1713845538`.
8. For see quality: `go run main.go --debug https://music.apple.com/us/album/1989-taylors-version-deluxe/1713845538`.

[Chinese tutorial - see Method 3 for details](https://telegra.ph/Apple-Music-Alac高解析度无损音乐下载教程-04-02-2)

## Downloading lyrics

1. Open [Apple Music](https://music.apple.com) and log in
2. Open the Developer tools, Click `Application -> Storage -> Cookies -> https://music.apple.com`
3. Find the cookie named `media-user-token` and copy its value
4. Paste the cookie value obtained in step 3 into the setting called "media-user-token" in config.yaml and save it
5. Start the script as usual

## Flask dashboard

1. Make sure the downloader container is already running and is named `applemusic_download`
2. Install the Flask dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r webapp/requirements.txt
```

3. Start the dashboard:

```bash
.venv/bin/python webapp/app.py
```

4. Open:

```text
http://127.0.0.1:5000
```

Notes:
- The dashboard calls the existing downloader through `docker exec`
- The web UI always uses the auto highest-quality path with Hi-Res/ALAC priority
- Interactive modes such as `--search` and `--select` are intentionally not exposed
- The page now shows a shared task list for both web-submitted and Telegram-submitted downloads
- The task details panel streams live logs, status changes, and final output paths for the selected task

Telegram private bot:

```bash
.venv/bin/python webapp/telegram_bot.py
```

- In containers, Telegram config is read from `/app/data/config.yaml` when present.
- For local development, it falls back to the repository root `config.yaml`.
- You can also set `WEBAPP_CONFIG_PATH` to a custom config file.
- Required keys are `telegram-bot-token`, `telegram-allowed-chat-id`, `telegram-webapp-base-url`, and optional `telegram-store-path`
- The bot only accepts private-chat messages from `telegram-allowed-chat-id`
- The bot extracts all Apple Music URLs in a message, deduplicates repeated URLs within that message, and calls `/api/downloads` for each link in order
- `/force` also supports multiple Apple Music URLs in a single message and applies force mode to every extracted link
- Task state is persisted in the path from `telegram-store-path`, defaulting to `data/telegram_tasks.db`
- Completion and failure messages are sent back to the same Telegram chat

Webapp container runtime:

- Published image: `ghcr.io/jsdfhasuh/apple-music-downloader-webapp`
- Local build: `docker build -f webapp/Dockerfile -t apple-music-webapp:test .`
- `webapp/Dockerfile` now starts both `webapp/app.py` and `webapp/telegram_bot.py`
- Flask service logs and Telegram bot logs both go to the container stdout/stderr stream
- Use `docker logs <container-name>` to inspect backend and bot logs together
- The same logs are also written inside the container to `/app/data/logs/webapp.log` and `/app/data/logs/telegram-bot.log`
- If `webapp/config.yaml` does not contain Telegram credentials, the container starts the webapp and skips the bot

Additional API:

```bash
curl -X POST http://127.0.0.1:5000/api/downloads \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://music.apple.com/...","force":false}'
```

Response:

```json
{"taskId":"...","status":"running"}
```

Rules:
- A URL is only marked as downloaded after the task actually finishes with `completed`
- Submitting the same completed URL again will reuse the stored record instead of downloading again
- `force=true` bypasses the dedup check and starts a fresh download
- This endpoint always uses the auto highest-quality strategy with Hi-Res/ALAC priority
- After NFO generation, the album folder is moved to `completed-root-folder/<artist>/<album>`, defaulting to `/downloads/completed`

## Get translation and pronunciation lyrics (Beta)

1. Open [Apple Music](https://beta.music.apple.com) and log in.
2. Open the Developer tools, click `Network` tab.
3. Search a song which is available for translation and pronunciation lyrics (recommend K-Pop songs).
4. Press Ctrl+R and let Developer tools sniff network data.
5. Play a song and then click lyric button, sniff will show a data called `syllable-lyrics`.
6. Stop sniff (small red circles button on top left), then click `Fetch/XHR` tabs.
7. Click `syllable-lyrics` data, see requested URL.
8. Find this line `.../syllable-lyrics?l=<copy all the language value from here>&extend=ttmlLocalizations`.
9. Paste the language value obtained in step 8 into the config.yaml and save it.
10. If don't need pronunciation, do this `...%5D=<remove this value>&extend...` on config.yaml and save it.
11. Start the script as usual.

Noted: These features are only in beta version right now.
