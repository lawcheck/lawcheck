#!/bin/sh
# Ежедневный дамп БД LawCheck. Ротация: 14 последних.
#
# Каталог деплоя вычисляется от расположения скрипта, а не прописан строкой:
# в августе 2026 код переехал с /root/lawcheck на /home/deploy/lawcheck, а
# скрипт остался со старым путём и три недели падал на `cd` в лог-файл,
# который никто не читает. Скрипт живёт в репозитории и едет с деплоем.
set -e
DIR=$(cd "$(dirname "$0")/../.." && pwd)
cd "$DIR"

F=/root/backups/lawcheck-$(date +%F).sql.gz
docker compose exec -T postgres pg_dump -U lawcheck lawcheck | gzip > "$F"
ls -1t /root/backups/lawcheck-*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm

# Отметка «отработало» — по ней приложение видит пропавшие прогоны. Стоит
# ПОСЛЕ дампа: `set -e` не даст сюда дойти, если дамп не удался, и молчание
# само станет сигналом.
KEY=$(grep "^INTERNAL_KEY=" "$DIR/.env" | cut -d= -f2)
curl -fsS -X POST -H "X-Internal-Key: $KEY" https://lawchek.ru/internal/heartbeat/backup >/dev/null

echo "$(date -Is) backup ok: $F ($(du -h "$F" | cut -f1))"
