#!/bin/sh
# Builds the Alertmanager config from env, then starts Alertmanager.
set -eu
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    printf '%s' "$TELEGRAM_BOT_TOKEN" > /tmp/telegram_bot_token
    chmod 600 /tmp/telegram_bot_token
    sed "s|__TELEGRAM_CHAT_ID__|${TELEGRAM_CHAT_ID}|" /etc/alertmanager/alertmanager.yml.tmpl > /tmp/alertmanager.yml
    echo "alertmanager: sending alerts to Telegram chat ${TELEGRAM_CHAT_ID}"
else
    cp /etc/alertmanager/alertmanager-null.yml /tmp/alertmanager.yml
    echo "alertmanager: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set - alerts are NOT delivered"
fi
exec /bin/alertmanager --config.file=/tmp/alertmanager.yml --storage.path=/alertmanager "$@"
