# Pump.fun Replay Sprint — Paper Bot

[![make.fun bounty](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122.svg)](https://make.fun/bounties/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122?utm_source=github&utm_medium=readme&utm_campaign=badge)
[![rank](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/Hagai17/pump-replay-sprint-jackxyz72/rank.svg)](https://make.fun/bounties/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122?utm_source=github&utm_medium=readme&utm_campaign=rank)
[![reward](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/Hagai17/pump-replay-sprint-jackxyz72/reward.svg)](https://make.fun/bounties/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122?utm_source=github&utm_medium=readme&utm_campaign=reward)

Entry for [Pump.fun Replay Sprint — Paper Bot, Real Ranking](https://make.fun/bounties/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122) on make.fun: a paper-trading bot for the `agent-fun/replay/v2` harness, graded on net PnL over a recorded 24h window of pump.fun bonding-curve activity with 10 SOL of paper money.

## Strategy

Capital preservation first, trading only on a strict gate:

- **Tape**: a rolling ~110-slot record of every curve's trades (direction, size, wallet, price, real reserves).
- **Entry**: a mature curve (≥600 slots old) holding 20–75 SOL of real reserves, with broad and accelerating buy pressure over a 40-slot window (≥3 SOL net inflow, ≥6 buys from ≥6 distinct wallets, no wallet above half the buy flow, price up 5–60% over the window, real reserves intact) — and the fill is taken on a **shallow pullback 3–12% under the window high**, not at the high. Breakout-chasing at the high was the single biggest loser in testing; the pullback entry cut the dev-window loss by ~85%.
- **Sizing**: 0.06 SOL per position, at most 3 positions and 0.6 SOL of exposure at once; every order also respects the harness cap of 1% of the curve's real reserves.
- **Exits**: first of a 10% stop loss, a 28% take profit, a trailing stop (arms at +10%, fires 12% under the peak), a flow fade, a single outsized sell, or 600 slots held.
- **Guardrail probes**: cheap 0.012 SOL round trips paced against block time keep the run above the bar of 20+ coins with a round trip of at least 0.01 SOL, at a worst-case cost that stays a small fraction of capital.

Deterministic by construction — no clock, no environment, no randomness — so the two graded executions produce byte-identical action logs.

## Layout

- `main.py` — the bot (entrypoint; Python 3.13, standard library only).
- `vendor/makefun_bot.py` — the kit's protocol shim, vendored as the SDK instructs.
- `candidates.md` — strategy notes and the local grading log the parameters were chosen from.

## Local grading

With the competition kit unpacked beside this repository:

```
node cli/makefun.mjs grade-local --kit . --bot <this repo> --driver process
```

## License

MIT — see `LICENSE`.
