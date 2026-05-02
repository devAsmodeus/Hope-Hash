# Hope-Hash — учебный solo BTC miner на чистом stdlib.
#
# Слим-Python база: ctypes-backend подхватывает libcrypto-3 из самого образа,
# поэтому никаких apt-get install для openssl-libs не нужно.
#
# Pip install -e . — это сам проект, не сторонняя зависимость (CLAUDE.md
# разрешает; pyproject.toml объявляет dependencies = []).

FROM python:3.11-slim

# Не записываем .pyc; разрешаем print/log без буферизации (важно для
# `docker logs -f`, иначе строки висят пока буфер не наполнится).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Сначала только метаданные — слой с зависимостями кэшируется отдельно
# от исходников (хотя dependencies=[], pip всё равно строит wheel один раз).
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --upgrade pip \
 && pip install -e .

# Volume для SQLite-журнала шар. Default путь --db hope_hash.db ставим под
# /data, чтобы был один canonical монтируемый location.
VOLUME ["/data"]
WORKDIR /data

# Прокидываем порты: 8000 — общий для metrics+webui (compose их и пробрасывает),
# 9090 — если кто-то держит --metrics-port отдельно от --web-port.
EXPOSE 8000 9090

# Healthcheck без curl: stdlib urllib умеет всё необходимое и уже в образе.
# Проверяем /healthz на metrics-порту 8000 (compose именно туда мапит).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; \
    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)" \
  || exit 1

ENTRYPOINT ["hope-hash"]

# Пустой default — пользователь обязательно передаёт BTC-адрес и флаги.
# Compose-файл подставляет их явно через `command:`.
CMD ["--help"]
