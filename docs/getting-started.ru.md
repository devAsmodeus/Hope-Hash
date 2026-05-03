# С чего начать

Это руководство для тех, кто никогда не запускал майнер биткоина. Поставим
Python, заведём адрес кошелька, запустим майнер один раз и разберём логи.
Никаких предварительных знаний по крипте не требуется.

## 1. Поставить Python 3.11+

Hope-Hash использует только стандартную библиотеку, так что Python — это
всё, что нужно.

- **Windows.** В Microsoft Store найти «Python 3.11» или скачать
  `python-3.11.x-amd64.exe` с python.org. В инсталляторе поставить галку
  «Add Python to PATH». Проверить: `py -3.11 --version`.
- **macOS.** `brew install python@3.11`. Проверить: `python3.11 --version`.
- **Linux.** Через пакетный менеджер (`apt install python3.11`,
  `dnf install python3.11`) или через [pyenv](https://github.com/pyenv/pyenv).

## 2. Установить Hope-Hash

```bash
git clone https://github.com/devAsmodeus/Hope-Hash.git
cd Hope-Hash
python -m pip install -e .
```

`-e .` ставит проект в editable-режиме — можно править код, и следующий
запуск подхватит изменения.

## 3. Завести mainnet BTC-адрес

Нужен адрес кошелька, который ты контролируешь. Майнер отправит туда
награду, если найдёт блок (шанс примерно нулевой, но формально путь должен
быть). Варианты:

- **Самый простой.** Поставить [Sparrow Wallet](https://sparrow-wallet.com/),
  [Electrum](https://electrum.org/) или [BlueWallet](https://bluewallet.io/),
  создать новый кошелёк, скопировать receive-адрес.
- **Аппаратный.** Trezor / Ledger работают так же — отдают receive-адрес.

Только mainnet. Hope-Hash отказывает testnet- и regtest-адресам.
Допустимые форматы:

- `1...` (P2PKH)
- `3...` (P2SH)
- `bc1q...` (bech32 / P2WPKH / P2WSH)
- `bc1p...` (bech32m / Taproot)

## 4. Первый запуск

```bash
hope-hash bc1q5n2x4pvxhq8sxc7ck3uxq8sxc7ck3uxqzfm2py mylaptop
```

Первый аргумент — твой адрес, второй — произвольное имя воркера. Когда
коннект поднимется, увидишь что-то такое:

```
[net] подключён к solo.ckpool.org:3333
[stratum] subscribed: extranonce1=ab12cd34, en2_size=4
[stratum] authorize отправлен для воркера bc1q....mylaptop
[stratum] новая сложность: 1.0
[stratum] новая работа job_id=4f2 clean=true
[stats] хешрейт ≈ 87 KH/s  |  pool diff = 1.0
[stratum] *** ШАР ПРИНЯТ *** (id=3)
```

`ШАР ПРИНЯТ` означает, что пул видит твою работу. Это **не** заработок;
заработок наступит только когда хеш упадёт ниже сетевого target (реальный
блок, ожидание ~10¹³ дней). Смысл — посмотреть протокол вживую.

Останов — `Ctrl+C`. Supervisor чисто закрывает сокет и джойнит все нити.

## 5. Типичные проблемы

- **Нет интернета или фаервол.** `solo.ckpool.org:3333` должен быть
  достижим. В корпоративных сетях порт 3333 часто закрыт — проверь через
  мобильный hotspot.
- **Неверный формат адреса.** Валидатор печатает конкретную причину:
  плохая checksum, смешанный регистр, testnet-префикс. Исправь — повтори.
- **`pip install -e .` падает на Windows с «Microsoft Visual C++
  required».** В Hope-Hash нет C-расширений; обычно это древний pip.
  Обнови: `py -3.11 -m pip install -U pip`.
- **`--tui` показывает пустой экран на Windows.** Стандартный CPython на
  Windows идёт без `curses`. Либо отдельно поставить `windows-curses`,
  либо просто не использовать `--tui` (майнер работает и без него).

## 6. Дальше

- [`deploy.ru.md`](deploy.ru.md) — Docker compose, Prometheus, Grafana,
  Telegram, healthchecks для долгого запуска.
- [`architecture.ru.md`](architecture.ru.md) — протокол, threading-модель,
  hot path, заметки про производительность.
- Если предпочитаешь английский — [`getting-started.en.md`](getting-started.en.md).
