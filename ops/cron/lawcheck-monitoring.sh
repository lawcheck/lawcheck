#!/bin/sh
# Еженедельный мониторинг Pro-сайтов: дёргаем внутреннюю ручку API.
# Про вычисление каталога — см. комментарий в lawcheck-backup.sh.
set -e
DIR=$(cd "$(dirname "$0")/../.." && pwd)

KEY=$(grep "^INTERNAL_KEY=" "$DIR/.env" | cut -d= -f2)
curl -fsS -X POST -H "X-Internal-Key: $KEY" https://lawchek.ru/internal/monitoring/run
curl -fsS -X POST -H "X-Internal-Key: $KEY" https://lawchek.ru/internal/heartbeat/monitoring >/dev/null
echo " $(date -Is)"
