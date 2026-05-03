# Деплой: Docker compose, Prometheus, Grafana, Telegram

Это про долгий запуск: контейнеризованный майнер, Prometheus снимает с
него метрики, Grafana их рисует, Telegram присылает уведомления, а
`/healthz` обслуживает мониторинг аптайма.

## 1. Что нужно

- Docker 24+ и Docker Compose v2 (`docker compose version`).
- Mainnet BTC-адрес, который ты контролируешь.
- Опционально: токен Telegram-бота (`@BotFather`) и численный chat_id.

## 2. Поднять стек

Из корня репозитория:

```bash
export BTC_ADDRESS="bc1q...твой_реальный_адрес..."
export WORKERS=2          # подбери под свой CPU
docker compose up -d
docker compose logs -f miner
```

Поднимется три контейнера:

- `hope-hash-miner` — сам майнер, открывает `8000` (Prometheus
  `/metrics` и `/healthz`) и `8001` (web-дашборд).
- `hope-hash-prometheus` — скрейпит `miner:8000/metrics` каждые 15с.
- `hope-hash-grafana` — с провиженным Prometheus-датасорсом и готовым
  дашбордом `hope-hash.json`.

## 3. Открыть дашборды

- **Web-дашборд** — <http://localhost:8001>. Live-sparkline хешрейта,
  счётчики шар, текущий пул, текущий job ID, SSE-стрим событий.
- **Grafana** — <http://localhost:3000>. Логин по умолчанию
  `admin / admin` (переопредели через env `GRAFANA_USER` /
  `GRAFANA_PASSWORD`). Дашборд Hope-Hash уже загружен в папку «Hope-Hash».
- **Prometheus** — <http://localhost:9090>. Удобно для ad-hoc PromQL
  (`rate(hopehash_shares_total[5m])` и т.д.).
- **Healthcheck** — `curl http://localhost:8000/healthz`. HTTP 200 если
  reader-нить жива и EMA-хешрейт свежий, 503 иначе. Подходит для k8s
  liveness и uptime-мониторинга.

## 4. Telegram-уведомления

Создай бота через [@BotFather](https://t.me/BotFather), забери токен,
напиши боту хотя бы одно сообщение, чтобы он увидел чат, потом узнай
chat_id через <https://api.telegram.org/bot<TOKEN>/getUpdates>.

Добавь в `.env` (или передай напрямую):

```
HOPE_HASH_TELEGRAM_TOKEN=123456:abcdef-your-bot-token
HOPE_HASH_TELEGRAM_CHAT_ID=123456789
HOPE_HASH_TELEGRAM_INBOUND=1     # opt-in: включает /stats /stop /restart
```

Перезапусти стек: `docker compose up -d --force-recreate miner`.

Получишь уведомления на старт / стоп / share-accepted / disconnect. При
`HOPE_HASH_TELEGRAM_INBOUND=1` бот принимает команды `/stats`, `/stop`,
`/restart`, `/help` — только от твоего `chat_id`, остальные игнорируются.

## 5. Постоянство данных

Compose монтирует `./data:/data` для SQLite-журнала шар (`hope_hash.db`)
и использует named volumes для Prometheus и Grafana. Рестарты
контейнеров сохраняют историю; `docker compose down -v` стирает volume'ы.

## 6. Без compose, на одном хосте

```bash
docker build -t hope-hash:0.7.0 .
docker run -d --name hope-hash \
  -e HOPE_HASH_TELEGRAM_TOKEN=... \
  -e HOPE_HASH_TELEGRAM_CHAT_ID=... \
  -p 8000:8000 -p 8001:8001 \
  -v $(pwd)/data:/data \
  hope-hash:0.7.0 \
  bc1q...твой_адрес... mybox \
  --workers 2 \
  --metrics-port 8000 --web-port 8001 --web-host 0.0.0.0 --no-banner
# позиционные аргументы: <BTC_ADDRESS> <WORKER_NAME> — литерал "mybox"
# здесь это просто имя воркера для пула; подставьте любое.
```

## 7. За reverse-proxy

У web-дашборда нет встроенной авторизации — он по умолчанию слушает
только loopback и расчёт на reverse-proxy с auth для любого внешнего
выставления. Пример nginx:

```
location /hope-hash/ {
    auth_basic "hope-hash";
    auth_basic_user_file /etc/nginx/htpasswd;
    proxy_pass http://127.0.0.1:8001/;
    # SSE требует выключенный буферинг и длинные таймауты.
    proxy_buffering off;
    proxy_read_timeout 1h;
}
```

## 8. См. также

- [`getting-started.ru.md`](getting-started.ru.md) — первый запуск на
  голом железе, до Docker.
- [`architecture.ru.md`](architecture.ru.md) — что делают нити, откуда
  берутся метрики.
- [`deploy.en.md`](deploy.en.md) — английская версия.
