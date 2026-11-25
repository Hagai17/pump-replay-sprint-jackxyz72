# Strategy notes and local grading log

Competition: `u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122` — harness
`pumpfun-replay/v2`, 10 SOL paper capital, ranked by net PnL. Guardrail bar: a round
trip of at least 0.01 SOL on at least 20 coins, max drawdown ≤ 50%. A prize needs net
PnL above holding cash (0 after fees). Fees: 95 + 30 bps curve fee per trade, plus a
fixed tx fee on every order sent, filled or not — so order count itself is a cost.

## Reference points on the dev window (kit's own baselines, real grader)

| bot | net PnL | fills | coins | max DD |
| --- | --- | --- | --- | --- |
| hold cash | 0 | 0 | 0 | 0 |
| equal weight (SDK example) | ≈ −0.02 SOL | 5 | 5 | 0.9% |
| pilot sniper | −0.475 SOL | 833 | 347 | 5.2% |
| pilot reversion | −3.995 SOL | 2,335 | 197 | 49.3% |
| pilot momentum (local sim) | −9.38 SOL | 410 | 48 | 95% |

The dev window (a 1/16 sample of 24h of pump.fun, 128,058 events) punishes activity:
every house pilot loses, and the more it trades the more it loses. The design conclusion
is capital preservation first — trade rarely, small, and only on strong evidence — while
still clearing the 20-coin round-trip bar cheaply.

## The bot

- Rolling tape per curve (~110 slots): slot, direction, SOL size, wallet, post-trade
  price and real reserves for every recorded trade.
- Entry gate (all must hold): age ≥ 600 slots; real reserves 20–75 SOL; over a 40-slot
  window ≥ 3 SOL net inflow, ≥ 6 buys from ≥ 6 distinct wallets, top wallet ≤ 50% of
  buy flow, price up 5–60%, newer-half net inflow ≥ older-half, real reserves ≥ 90% of
  their window-start level — and the price 3–12% **below** the window high (a pullback
  inside strength, not a breakout chase).
- Sizing: 0.06 SOL per position, ≤ 3 positions, ≤ 0.6 SOL total exposure, always inside
  the harness's 1%-of-real-reserves order cap.
- Exits (first trigger wins): −10% stop, +28% take profit, trailing stop armed at +10%
  and firing 12% under the peak close value, net flow over 20 slots below −2 SOL, a
  single recorded sell ≥ 3 SOL, or 900→600 slots held (600 in the final tuning).
- Probes: 0.012 SOL round trips on liquid curves (≥ 8 SOL real reserves, traded within
  the last 10 slots), sold on the first batch after the buy lands, paced against block
  time toward 24 completed round trips — the bar, with margin, at ~0.0005 SOL of fees
  per trip.
- No adds: pyramiding tested neutral on PnL and positive on fees, so it is off
  (`MAX_ADDS = 0`); the add path stays in the code, gated, for future windows.
- Deterministic: no clock, no environment, no randomness; iteration only over
  insertion-ordered structures. Two graded executions reproduce byte for byte (the
  grader's L1 regrade line confirms this on every run below).

## Iteration log (grade-local, process driver, dev window)

1. **v1 — breakout momentum.** Entry at the window high (≥ 97% of it), initial 0.06 SOL
   + one 0.09 SOL add, exits −12% / +45% / trail 15%/18%, max hold 900.
   → **−0.494 SOL**, 211 fills, 54 coins, 87 trips, win rate 20.7%, DD 6.3%.
2. **Entry gate tightened** (net inflow 2→3 SOL, buyers 5→6, reserves floor 15→20 SOL,
   min rise 3%→5%). → **−0.342 SOL**, 165 fills, 70 trips, win rate 20.0%, DD 4.4%.
   Fewer, better entries; win rate unchanged — the entries themselves were the problem.
3. **Exit profile tightened** (−10% / +28% / trail 10%/12%, hold 600). → **−0.349 SOL**.
   No change: exits were never the binding constraint.
4. **Pullback entry** (price 3–12% under the window high, rest of the gate unchanged).
   → **−0.058 SOL**, 117 fills, 51 trips, win rate 23.5%, DD 1.3%, 90% interval
   −0.19..+0.08 SOL. The structural fix: with 2 slots of latency, buying the high buys
   the top; buying the dip inside intact inflow keeps the same runners and skips the
   blow-off entries.
5. **Adds off** (`MAX_ADDS = 0`). → **−0.064 SOL**, 102 fills, curve fees 0.099→0.067
   SOL, interval tightened to −0.15..+0.02. Same expectancy, less fee drag and a
   tighter distribution — kept.
6. **Net inflow 3→4 SOL.** → **−0.076 SOL**. Slightly worse; reverted to 3 SOL. The
   remaining dev-window loss (~0.06 SOL ≈ probe fees + a thin negative edge on ~27 main
   trips) is at the noise floor of one window; further knob-turning would overfit it.

Final configuration = iteration 5 with iteration 6 reverted: **−0.064 SOL** on the dev
window, guardrails met (36 coins with a qualifying round trip, DD ~1%), ranked, and
within ~0.06 SOL of hold-cash on a window where the house's own active bots lose
0.5–9.4 SOL. On a friendlier final window the same gate keeps the runners it catches;
on a hostile one the damage stays near the probe budget.

Stress (rebuilt windows, `--stress`): 8 block surrogates of the dev window landed
between **−0.29 and 0.00 SOL** (median ≈ −0.10; 5 of the first 6 lost money, the dev
score above 4 of 6). No copy came close to a blowup: the worst rebuilt window costs
2.9% of capital, versus the 50% drawdown guardrail. The shipped parameters sit in the
flat region between iterations 4–6 rather than on any single window's best point,
which is what that tight, bounded spread reflects.
