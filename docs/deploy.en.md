# Deploy: Docker compose, Prometheus, Grafana, Telegram

This guide covers the long-running setup: a containerised miner with
Prometheus scraping its metrics, Grafana visualising them, Telegram
notifications, and a `/healthz` endpoint for uptime monitoring.

## 1. Prerequisites

- Docker 24+ and Docker Compose v2 (`docker compose version`).
- A mainnet BTC address you control.
- Optional: a Telegram bot token (`@BotFather`) and your numeric chat ID.

## 2. Start the stack

From the repo root:

```bash
export BTC_ADDRESS="bc1q...your_real_address..."
export WORKERS=2          # adjust to your CPU
docker compose up -d
docker compose logs -f miner
```

Three containers come up:

- `hope-hash-miner` — the miner itself, exposing `8000` (Prometheus
  `/metrics` and `/healthz`) and `8001` (web dashboard).
- `hope-hash-prometheus` — scrapes `miner:8000/metrics` every 15s.
- `hope-hash-grafana` — provisioned with the Prometheus datasource and
  the bundled `hope-hash.json` dashboard.

## 3. Open the dashboards

- **Web dashboard** — <http://localhost:8001>. Live hashrate sparkline,
  share counters, current pool, current job ID, SSE event stream.
- **Grafana** — <http://localhost:3000>. Default login `admin / admin`
  (override via `GRAFANA_USER` / `GRAFANA_PASSWORD` env vars). The
  Hope-Hash dashboard is preloaded under the "Hope-Hash" folder.
- **Prometheus** — <http://localhost:9090>. Useful for ad-hoc PromQL
  (`rate(hopehash_shares_total[5m])` etc.).
- **Healthcheck** — `curl http://localhost:8000/healthz`. Returns
  HTTP 200 when the reader thread is alive and the EMA hashrate is fresh,
  503 otherwise. Suitable for k8s liveness / uptime monitoring.

## 4. Telegram notifications

Create a bot with [@BotFather](https://t.me/BotFather), grab the token,
write to your bot once so it sees your chat, then find your chat ID via
<https://api.telegram.org/bot<TOKEN>/getUpdates>.

Add to `.env` (or pass directly):

```
HOPE_HASH_TELEGRAM_TOKEN=123456:abcdef-your-bot-token
HOPE_HASH_TELEGRAM_CHAT_ID=123456789
HOPE_HASH_TELEGRAM_INBOUND=1     # opt-in: enable /stats /stop /restart commands
```

Restart the stack: `docker compose up -d --force-recreate miner`.

You will get notifications on start / stop / share-accepted / disconnect.
With `HOPE_HASH_TELEGRAM_INBOUND=1` the bot also accepts `/stats`,
`/stop`, `/restart`, and `/help` — only from your `chat_id`, others are
dropped.

## 5. Persistence

The compose file mounts `./data:/data` for the SQLite share journal
(`hope_hash.db`) and uses named volumes for Prometheus and Grafana state.
Container restarts keep history; `docker compose down -v` wipes volumes.

## 6. Adapting for a single host without compose

```bash
docker build -t hope-hash:0.7.0 .
docker run -d --name hope-hash \
  -e HOPE_HASH_TELEGRAM_TOKEN=... \
  -e HOPE_HASH_TELEGRAM_CHAT_ID=... \
  -p 8000:8000 -p 8001:8001 \
  -v $(pwd)/data:/data \
  hope-hash:0.7.0 \
  bc1q...your_address... docker --workers 2 \
  --metrics-port 8000 --web-port 8001 --web-host 0.0.0.0 --no-banner
```

## 7. Behind a reverse proxy

The web dashboard has no auth on purpose — it is bound to loopback by
default and assumes a reverse proxy in front of it for any non-local
deployment. Example nginx snippet:

```
location /hope-hash/ {
    auth_basic "hope-hash";
    auth_basic_user_file /etc/nginx/htpasswd;
    proxy_pass http://127.0.0.1:8001/;
    # SSE needs unbuffered + long timeouts.
    proxy_buffering off;
    proxy_read_timeout 1h;
}
```

## 8. See also

- [`getting-started.en.md`](getting-started.en.md) — first run on bare
  metal, before Docker.
- [`architecture.en.md`](architecture.en.md) — what the threads do, where
  the metrics come from.
- [`deploy.ru.md`](deploy.ru.md) — Russian version.
