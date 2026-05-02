# Getting started

This guide is for someone who has never run a Bitcoin miner before. We will
install Python, get a wallet address you control, run the miner once, and
then read the logs together. No prior crypto background is assumed.

## 1. Install Python 3.11+

Hope-Hash only uses the standard library, so Python is the only thing you
need.

- **Windows.** Open the Microsoft Store and install "Python 3.11" (or
  download `python-3.11.x-amd64.exe` from python.org). Tick "Add Python to
  PATH" in the installer. Confirm with `py -3.11 --version`.
- **macOS.** `brew install python@3.11`. Confirm with `python3.11 --version`.
- **Linux.** Use your package manager (`apt install python3.11`,
  `dnf install python3.11`, etc.) or [pyenv](https://github.com/pyenv/pyenv).

## 2. Install Hope-Hash

```bash
git clone https://github.com/devAsmodeus/Hope-Hash.git
cd Hope-Hash
python -m pip install -e .
```

The `-e .` installs the project in editable mode — you can hack on the code
and the next run will pick up the changes.

## 3. Get a mainnet BTC address

You need a wallet address you control. The miner sends any (extremely
unlikely) reward to that address. Pick one:

- **Easiest.** Install [Sparrow Wallet](https://sparrow-wallet.com/),
  [Electrum](https://electrum.org/), or [BlueWallet](https://bluewallet.io/),
  generate a new wallet, copy the receive address.
- **Hardware-backed.** Trezor / Ledger work the same — they expose a
  receive address.

Mainnet only. Hope-Hash refuses testnet / regtest addresses. Acceptable
formats:

- `1...` (P2PKH)
- `3...` (P2SH)
- `bc1q...` (bech32 / P2WPKH / P2WSH)
- `bc1p...` (bech32m / Taproot)

## 4. First run

```bash
hope-hash bc1q5n2x4pvxhq8sxc7ck3uxq8sxc7ck3uxqzfm2py mylaptop
```

The first argument is your address; the second is a free-form worker name.
Once the connection is up you should see something like:

```
[net] connected to solo.ckpool.org:3333
[stratum] subscribed: extranonce1=ab12cd34, en2_size=4
[stratum] authorize sent for worker bc1q....mylaptop
[stratum] new difficulty: 1.0
[stratum] new job job_id=4f2 clean=true
[stats] hashrate ≈ 87 KH/s  |  pool diff = 1.0
[stratum] *** SHARE ACCEPTED *** (id=3)
```

`SHARE ACCEPTED` means the pool sees your work. It does **not** mean you
earned anything; that only happens when a hash drops below the network
target (a real block, expectation: ~10¹³ days). The point is to watch the
protocol live.

Stop with `Ctrl+C`. The supervisor closes the socket cleanly and joins all
threads.

## 5. Common pitfalls

- **No internet / firewall.** `solo.ckpool.org:3333` must be reachable. On
  corporate networks port 3333 is sometimes blocked — try a phone hotspot
  to confirm.
- **Wrong address format.** The validator prints the exact reason: bad
  checksum, mixed case, testnet prefix. Fix the address, retry.
- **`pip install -e .` fails on Windows with "Microsoft Visual C++
  required".** Hope-Hash itself has no C extensions; this usually means an
  ancient pip. Update with `py -3.11 -m pip install -U pip`.
- **`--tui` looks empty on Windows.** Stock CPython on Windows ships
  without `curses`. Either install `windows-curses` separately or skip
  `--tui` (the miner works without it).

## 6. Next

- [`deploy.en.md`](deploy.en.md) — Docker compose, Prometheus, Grafana,
  Telegram, healthchecks for a long-running setup.
- [`architecture.en.md`](architecture.en.md) — protocol, threading model,
  hot path, performance notes.
- Read [`getting-started.ru.md`](getting-started.ru.md) if you prefer
  Russian.
