#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/logs

(python webapp/app.py 2>&1 | tee -a /app/logs/webapp.log) &
webappPid=$!

botPid=""

if python - <<'PY'
from webapp.config_loader import getConfigValue, resolveConfigPath

configPath = resolveConfigPath()
token = getConfigValue(configPath, "telegram-bot-token") or ""
chatId = getConfigValue(configPath, "telegram-allowed-chat-id") or ""
raise SystemExit(0 if token.strip() and chatId.strip() else 1)
PY
then
  (python webapp/telegram_bot.py 2>&1 | tee -a /app/logs/telegram-bot.log) &
  botPid=$!
else
  printf '[webapp-start] telegram bot disabled: missing telegram config\n' | tee -a /app/logs/telegram-bot.log
fi

cleanup() {
  kill "$webappPid" >/dev/null 2>&1 || true
  if [ -n "$botPid" ]; then
    kill "$botPid" >/dev/null 2>&1 || true
  fi
}

trap cleanup TERM INT

if [ -n "$botPid" ]; then
  wait -n "$webappPid" "$botPid"
else
  wait "$webappPid"
fi
exitCode=$?
cleanup
wait "$webappPid" >/dev/null 2>&1 || true
if [ -n "$botPid" ]; then
  wait "$botPid" >/dev/null 2>&1 || true
fi
exit "$exitCode"
