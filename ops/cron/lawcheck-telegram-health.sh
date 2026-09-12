#!/bin/sh
# Ежедневная проверка канала уведомлений. Отметку о прогоне ставит сама ручка
# (и при живом канале, и при мёртвом): факт «проверка запускалась» не зависит
# от её результата, иначе упавший канал выглядел бы ещё и пропавшим cron'ом.
set -e
DIR=$(cd "$(dirname "$0")/../.." && pwd)

KEY=$(grep "^INTERNAL_KEY=" "$DIR/.env" | cut -d= -f2)
curl -fsS -X POST -H "X-Internal-Key: $KEY" https://lawchek.ru/internal/health/telegram
echo " $(date -Is)"
