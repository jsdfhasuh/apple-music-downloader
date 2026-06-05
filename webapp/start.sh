#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/data/logs

(python -m webapp.app 2>&1 | tee -a /app/data/logs/webapp.log) &
webappPid=$!

botPid=""

if python - <<'PY'
from webapp.config_loader import getConfigValue, resolveConfigPath

configPath = resolveConfigPath()
token = getConfigValue(configPath, "telegram-bot-token") or ""
chatId = getConfigValue(configPath, "telegram-allowed-chat-id") or ""
token = token.strip()
chatId = chatId.strip()
isConfigured = (
    token
    and chatId.isdigit()
    and token != "your-telegram-bot-token"
    and chatId != "your-telegram-chat-id"
)
raise SystemExit(0 if isConfigured else 1)
PY
then
  (python -m webapp.telegram_bot 2>&1 | tee -a /app/data/logs/telegram-bot.log) &
  botPid=$!
else
  printf '[webapp-start] telegram bot disabled: missing telegram config\n' | tee -a /app/data/logs/telegram-bot.log
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
