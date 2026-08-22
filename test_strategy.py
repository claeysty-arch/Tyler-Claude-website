#!/usr/bin/env python3
"""
test_strategy.py — Backtest a "daily DCA + per-lot take-profit" strategy.

THE STRATEGY IN PLAIN ENGLISH
-----------------------------
1. Every trading day, buy a fixed dollar amount of one ticker (default SPY).
2. Each purchase is tracked as its own "lot" with its own cost basis.
   (A "lot" is just one specific purchase: how many shares, at what price, on what day.)
3. When a lot's price is >= 2% above THAT LOT'S OWN purchase price, sell THAT lot.
   Other lots are unaffected — each one has its own independent target.
4. Lots that never reach +2% simply stay open forever. We never force a sale.

So this is dollar-cost-averaging in, with a per-lot profit-taking rule on the way out.

WHAT THIS SCRIPT PRINTS
-----------------------
  * Realized P&L and Unrealized P&L as two SEPARATE numbers (never merged).
  * A buy-and-hold comparison using the same money on the same schedule.
  * Peak cash required, max simultaneous open lots, age of oldest unsold lot,
    total fees paid, and max drawdown.
  * A PNG chart with three lines: realized P&L, unrealized P&L, buy-and-hold P&L.

HONESTY NOTES (please read — backtests lie easily)
--------------------------------------------------
  * NO LOOKAHEAD. Any decision that uses a day's CLOSING price is executed at the
    NEXT day's OPEN. We never get to trade on a price we couldn't have known yet.
  * COSTS ARE REAL. Every fill pays a per-trade commission AND slippage. There is
    no zero-cost mode. Buys fill slightly above the quoted price, sells slightly
    below — which is how it actually works.
  * Dividends: by default we use split- and dividend-adjusted prices, so total
    return is captured for BOTH the strategy and buy-and-hold. Same basis for
    both, so the comparison is fair.

USAGE
-----
    python3 test_strategy.py                 # run the real backtest (needs internet)
    python3 test_strategy.py --selftest      # run engine checks on synthetic data (no internet)
"""

# =============================================================================
# SECTION 0 — IMPORTS
# =============================================================================
import sys
import heapq                      # a "priority queue" — explained in Section 4
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd
import matplotlib
matplotlib.use("Agg")             # "Agg" = render to a file, not to a window.
                                  # Required on servers with no display attached.
import matplotlib.pyplot as plt


# =============================================================================
# SECTION 1 — PARAMETERS  (everything you'd want to change lives right here)
# =============================================================================

TICKER              = "SPY"          # What to buy. Any yfinance symbol: "QQQ", "AAPL", ...
START_DATE          = "2000-01-01"   # Backtest start (YYYY-MM-DD).
END_DATE            = None           # None = today. Or a string like "2020-12-31".

DAILY_BUY_AMOUNT    = 100.00         # Dollars of stock bought every trading day.
SELL_INCREMENT_PCT  = 2.0            # Sell a lot once it is this % above its own cost.

# --- Costs. These are deliberately non-zero. Do not set them to 0 and believe the result. ---
COMMISSION_PER_TRADE = 1.00          # Flat dollars per fill (charged on buys AND sells).
SLIPPAGE_PCT         = 0.05          # % worse than the quoted price on every single fill.
                                     # Buys fill HIGHER, sells fill LOWER. Minimum 0.05.

# --- Modeling choices ---
USE_ADJUSTED_PRICES     = True       # True  = prices adjusted for splits AND dividends
                                     #         (captures total return; recommended).
                                     # False = raw prices; dividends are then ignored.
ALLOW_FRACTIONAL_SHARES = True       # True  = buy exactly $DAILY_BUY_AMOUNT of stock.
                                     # False = round down to whole shares.

OUTPUT_PNG = "strategy_backtest.png" # Where the chart gets saved.


# =============================================================================
# SECTION 2 — THE "LOT" — one purchase, tracked individually
# =============================================================================
# This is the heart of the strategy. Instead of one blended average cost, every
# single day's purchase is its own little position with its own target price.

@dataclass
class Lot:
    buy_date: date          # when we bought it
    shares: float           # how many shares this lot holds
    cost_per_share: float   # what we ACTUALLY paid per share, slippage included
    commission_paid: float  # the commission charged on this buy (a sunk cost for this lot)
    trigger_price: float    # sell this lot once the market price reaches this level

    # Total cash this lot consumed when we bought it.
    @property
    def total_cost(self) -> float:
        return self.shares * self.cost_per_share + self.commission_paid


# =============================================================================
# SECTION 3 — DATA LOADING
# =============================================================================

def load_price_data(ticker: str, start: str, end: str | None) -> pd.DataFrame:
    """
    Download daily bars from Yahoo Finance via yfinance (free).

    We need exactly two columns: Open and Close.
      - Close is what we make DECISIONS on (at the end of a day).
      - Open is where we EXECUTE those decisions (the next morning).
    That split is what makes the backtest lookahead-free.
    """
    import yfinance as yf   # imported here so --selftest works without yfinance installed

    print(f"Downloading {ticker} daily bars from {start} to {end or 'today'} ...")
    df = yf.download(
        ticker,
        start=start,
        end=end,
        auto_adjust=USE_ADJUSTED_PRICES,  # True => Open/Close already adjusted
        progress=False,
    )

    if df is None or df.empty:
        raise SystemExit(
            f"ERROR: no data returned for '{ticker}'.\n"
            "Common causes:\n"
            "  * the symbol is wrong, or the date range has no trading days;\n"
            "  * no internet access, or a proxy/firewall is blocking\n"
            "    query1.finance.yahoo.com / fc.yahoo.com (Yahoo's data hosts);\n"
            "  * Yahoo is rate-limiting you — wait a minute and retry.\n"
            "You can verify the engine itself with:  python3 test_strategy.py --selftest"
        )

    # Newer yfinance returns MultiIndex columns like ('Close','SPY'). Flatten them
    # so we can just say df['Close'].
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[["Open", "Close"]].dropna()
    df.index = pd.to_datetime(df.index)
    print(f"Got {len(df):,} trading days "
          f"({df.index[0].date()} .. {df.index[-1].date()}).\n")
    return df


# =============================================================================
# SECTION 4 — THE BACKTEST ENGINE
# =============================================================================
#
# DAILY ORDER OF OPERATIONS (this ordering is what prevents lookahead):
#
#   MORNING (at today's OPEN price):
#     1. Execute sells that were DECIDED at yesterday's close.
#     2. Execute today's fixed-dollar buy.
#   EVENING (at today's CLOSE price):
#     3. Mark everything to market and record the day's numbers.
#     4. Check which open lots have hit their +2% target. Those become
#        pending sells for TOMORROW morning. We do NOT sell them today.
#
# A note on the heap (step 4): checking all open lots every day would be slow,
# because after 25 years there can be thousands of them. Instead we keep open
# lots in a MIN-HEAP ordered by trigger_price — a structure that always hands
# back the smallest trigger first. So we just peek at the cheapest trigger and
# pop while it's <= today's close. Everything still in the heap has a higher
# trigger and definitely hasn't fired. Same answer, far less work.

def run_backtest(df: pd.DataFrame) -> dict:
    sell_multiple = 1.0 + SELL_INCREMENT_PCT / 100.0
    slip = SLIPPAGE_PCT / 100.0

    # ---- Strategy state ----
    open_lots: list = []          # the min-heap of open lots (see note above)
    lot_seq = 0                   # tiebreaker so the heap never compares Lot objects
    pending_sells: list[Lot] = [] # decided last night, executed this morning

    cash = 0.0                    # cash sitting in the account
    total_deposited = 0.0         # external money we've had to put in, cumulative
    realized_pnl = 0.0            # profit from lots we've actually CLOSED
    total_commissions = 0.0       # every commission ever paid
    total_slippage = 0.0          # every dollar lost to slippage
    n_buys = n_sells = 0
    max_open_lots = 0
    peak_cash_required = 0.0
    peak_capital_deployed = 0.0

    # ---- Buy-and-hold state (the control group) ----
    # Same dollars, same days, same commission and slippage — it just never sells.
    bh_shares = 0.0
    bh_deposited = 0.0
    bh_cash = 0.0                 # leftover cash if fractional shares are disabled

    history = []                  # one row per day, for the chart and the stats

    dates = df.index
    opens = df["Open"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)

    for i in range(len(df)):
        today = dates[i]
        open_px = opens[i]
        close_px = closes[i]

        # -------------------------------------------------------------
        # STEP 1 (morning) — execute sells decided at YESTERDAY's close.
        # -------------------------------------------------------------
        for lot in pending_sells:
            # Selling: slippage pushes our fill DOWN (we get less than quoted).
            fill = open_px * (1.0 - slip)
            proceeds = lot.shares * fill

            cash += proceeds - COMMISSION_PER_TRADE
            total_commissions += COMMISSION_PER_TRADE
            total_slippage += lot.shares * open_px * slip

            # Realized profit for this lot = what we got out, minus what we put in,
            # minus BOTH commissions (the buy commission was this lot's cost too).
            realized_pnl += proceeds - (lot.shares * lot.cost_per_share) \
                            - COMMISSION_PER_TRADE - lot.commission_paid
            n_sells += 1
        pending_sells = []

        # -------------------------------------------------------------
        # STEP 2 (morning) — today's fixed-dollar purchase.
        # -------------------------------------------------------------
        # Buying: slippage pushes our fill UP (we pay more than quoted).
        buy_fill = open_px * (1.0 + slip)

        if ALLOW_FRACTIONAL_SHARES:
            shares = DAILY_BUY_AMOUNT / buy_fill
        else:
            shares = float(int(DAILY_BUY_AMOUNT // buy_fill))

        if shares > 0:
            gross = shares * buy_fill
            needed = gross + COMMISSION_PER_TRADE

            # We only deposit fresh money when the account can't cover the buy.
            # Proceeds from earlier sales get recycled first — that's why this
            # strategy can need less total capital than plain buy-and-hold.
            if cash < needed:
                deposit = needed - cash
                cash += deposit
                total_deposited += deposit

            cash -= needed
            total_commissions += COMMISSION_PER_TRADE
            total_slippage += shares * open_px * slip

            lot_seq += 1
            lot = Lot(
                buy_date=today.date(),
                shares=shares,
                cost_per_share=buy_fill,          # our REAL cost, slippage included
                commission_paid=COMMISSION_PER_TRADE,
                trigger_price=buy_fill * sell_multiple,
            )
            # Push onto the heap keyed by trigger price (lowest trigger first).
            heapq.heappush(open_lots, (lot.trigger_price, lot_seq, lot))
            n_buys += 1

        # --- Buy-and-hold does the exact same purchase, and then nothing else. ---
        if ALLOW_FRACTIONAL_SHARES:
            bh_sh = DAILY_BUY_AMOUNT / buy_fill
        else:
            bh_sh = float(int(DAILY_BUY_AMOUNT // buy_fill))
        if bh_sh > 0:
            bh_cost = bh_sh * buy_fill + COMMISSION_PER_TRADE
            if bh_cash < bh_cost:
                add = bh_cost - bh_cash
                bh_cash += add
                bh_deposited += add
            bh_cash -= bh_cost
            bh_shares += bh_sh

        # -------------------------------------------------------------
        # STEP 3 (evening) — mark to market and record the day.
        # -------------------------------------------------------------
        # Unrealized P&L = for every lot we still hold, what it's worth now minus
        # what it cost us (including that lot's buy commission, already spent).
        holdings_value = 0.0
        open_cost = 0.0
        for _, _, lot in open_lots:
            holdings_value += lot.shares * close_px
            open_cost += lot.total_cost
        unrealized_pnl = holdings_value - open_cost

        max_open_lots = max(max_open_lots, len(open_lots))
        peak_cash_required = max(peak_cash_required, total_deposited)
        peak_capital_deployed = max(peak_capital_deployed, total_deposited - cash)

        bh_value = bh_shares * close_px + bh_cash
        bh_pnl = bh_value - bh_deposited

        history.append({
            "date": today,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": realized_pnl + unrealized_pnl,
            "buy_hold_pnl": bh_pnl,
            "open_lots": len(open_lots),
            "deposited": total_deposited,
        })

        # -------------------------------------------------------------
        # STEP 4 (evening) — decide tomorrow's sells using TODAY's close.
        # -------------------------------------------------------------
        # Pop every lot whose trigger has been reached. Because the heap is
        # ordered by trigger price, once the cheapest trigger is above today's
        # close, nothing else can have fired either — so we can stop.
        while open_lots and open_lots[0][0] <= close_px:
            _, _, lot = heapq.heappop(open_lots)
            pending_sells.append(lot)     # executes TOMORROW at the open. No lookahead.

    # -------------------------------------------------------------
    # Wrap-up. Note: any lot in `pending_sells` on the final day never got a
    # chance to execute, so we put it back — it is still an open position.
    # -------------------------------------------------------------
    for lot in pending_sells:
        lot_seq += 1
        heapq.heappush(open_lots, (lot.trigger_price, lot_seq, lot))

    hist = pd.DataFrame(history).set_index("date")
    final_close = closes[-1]

    # Recompute final unrealized including the lots we just put back.
    holdings_value = sum(l.shares * final_close for _, _, l in open_lots)
    open_cost = sum(l.total_cost for _, _, l in open_lots)
    final_unrealized = holdings_value - open_cost
    hist.iloc[-1, hist.columns.get_loc("unrealized_pnl")] = final_unrealized
    hist.iloc[-1, hist.columns.get_loc("total_pnl")] = realized_pnl + final_unrealized

    # Age of the oldest lot we never managed to sell.
    oldest_age_days = None
    oldest_date = None
    if open_lots:
        oldest_date = min(l.buy_date for _, _, l in open_lots)
        oldest_age_days = (dates[-1].date() - oldest_date).days

    return {
        "history": hist,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": final_unrealized,
        "open_lots": len(open_lots),
        "holdings_value": holdings_value,
        "cash": cash,
        "total_deposited": total_deposited,
        "peak_cash_required": peak_cash_required,
        "peak_capital_deployed": peak_capital_deployed,
        "max_open_lots": max_open_lots,
        "oldest_age_days": oldest_age_days,
        "oldest_date": oldest_date,
        "total_commissions": total_commissions,
        "total_slippage": total_slippage,
        "n_buys": n_buys,
        "n_sells": n_sells,
        "bh_value": bh_shares * final_close + bh_cash,
        "bh_deposited": bh_deposited,
        "bh_pnl": bh_shares * final_close + bh_cash - bh_deposited,
        "bh_shares": bh_shares,
        "start": dates[0].date(),
        "end": dates[-1].date(),
        "final_close": final_close,
    }


# =============================================================================
# SECTION 5 — MAX DRAWDOWN
# =============================================================================

def max_drawdown(series: pd.Series) -> tuple[float, object, object]:
    """
    Largest peak-to-trough DROP, in dollars, of a profit curve.

    We measure drawdown on the P&L curve rather than on account value, because
    account value here rises mostly from new deposits — that would hide the pain.
    This answers: "from the best it ever looked, how much did I watch evaporate?"
    """
    running_peak = series.cummax()
    drawdown = series - running_peak
    trough = drawdown.idxmin()
    worst = float(drawdown.min())
    peak_at = series.loc[:trough].idxmax() if len(series.loc[:trough]) else trough
    return worst, peak_at, trough


# =============================================================================
# SECTION 6 — REPORTING
# =============================================================================

def money(x: float) -> str:
    """Format a dollar amount with a sign, e.g. -$1,234.56"""
    return f"{'-' if x < 0 else ''}${abs(x):,.2f}"


def print_report(r: dict) -> None:
    hist = r["history"]
    strat_dd, strat_pk, strat_tr = max_drawdown(hist["total_pnl"])
    bh_dd, _, _ = max_drawdown(hist["buy_hold_pnl"])
    total_fees = r["total_commissions"] + r["total_slippage"]

    L = "=" * 78
    print(L)
    print(f"  DAILY DCA + {SELL_INCREMENT_PCT:g}% PER-LOT TAKE-PROFIT  —  {TICKER}")
    print(f"  {r['start']}  ..  {r['end']}   ({len(hist):,} trading days)")
    print(L)

    print("\nSETTINGS")
    print(f"  Buy per trading day .......... ${DAILY_BUY_AMOUNT:,.2f}")
    print(f"  Sell a lot at ................ +{SELL_INCREMENT_PCT:g}% over its own cost")
    print(f"  Commission per fill .......... ${COMMISSION_PER_TRADE:,.2f}")
    print(f"  Slippage per fill ............ {SLIPPAGE_PCT:g}%")
    print(f"  Prices ....................... {'split/dividend-adjusted' if USE_ADJUSTED_PRICES else 'raw (dividends ignored)'}")
    print(f"  Execution .................... decide on close, fill at NEXT open")

    # ---- THE HEADLINE. Two separate numbers, deliberately never summed alone. ----
    print("\n" + "-" * 78)
    print("  PROFIT  (the two numbers that matter, kept separate)")
    print("-" * 78)
    print(f"  REALIZED P&L   (locked in, lots actually sold) ....  {money(r['realized_pnl']):>18}")
    print(f"  UNREALIZED P&L (paper, still-open lots) ..........  {money(r['unrealized_pnl']):>18}")
    print(f"  {'-' * 74}")
    print(f"  Combined ........................................  {money(r['realized_pnl'] + r['unrealized_pnl']):>18}")
    print("\n  Reminder: unrealized profit is not money. It moves every day and")
    print("  by construction these are the lots that NEVER hit their target.")

    # ---- Control group ----
    print("\n" + "-" * 78)
    print("  BUY-AND-HOLD COMPARISON (same dollars, same days, same costs, never sells)")
    print("-" * 78)
    print(f"  Buy-and-hold P&L .................................  {money(r['bh_pnl']):>18}")
    print(f"  Buy-and-hold capital contributed .................  {money(r['bh_deposited']):>18}")
    print(f"  Buy-and-hold final value .........................  {money(r['bh_value']):>18}")
    bh_ret = r["bh_pnl"] / r["bh_deposited"] * 100 if r["bh_deposited"] else 0.0
    print(f"  Buy-and-hold return on contributed capital .......  {bh_ret:>17.2f}%")

    strat_total = r["realized_pnl"] + r["unrealized_pnl"]
    st_ret = strat_total / r["total_deposited"] * 100 if r["total_deposited"] else 0.0
    print(f"\n  Strategy capital contributed .....................  {money(r['total_deposited']):>18}")
    print(f"  Strategy return on contributed capital ...........  {st_ret:>17.2f}%")
    diff = strat_total - r["bh_pnl"]
    verdict = "STRATEGY AHEAD" if diff > 0 else "BUY-AND-HOLD AHEAD"
    print(f"\n  Difference (strategy minus buy-and-hold) .........  {money(diff):>18}   << {verdict}")

    # ---- Everything else that was asked for ----
    print("\n" + "-" * 78)
    print("  CAPITAL, RISK, AND FRICTION")
    print("-" * 78)
    print(f"  Peak cash required (most you ever had to fund) ...  {money(r['peak_cash_required']):>18}")
    print(f"  Peak capital deployed (money actually at work) ...  {money(r['peak_capital_deployed']):>18}")
    print(f"  Largest number of lots open at once ..............  {r['max_open_lots']:>18,}")
    print(f"  Open lots at the end .............................  {r['open_lots']:>18,}")
    if r["oldest_age_days"] is not None:
        yrs = r["oldest_age_days"] / 365.25
        print(f"  Oldest unsold lot ................................  "
              f"{r['oldest_age_days']:>13,} days  ({yrs:.1f} yrs)")
        print(f"    ... bought on ..................................  {str(r['oldest_date']):>18}")
    else:
        print("  Oldest unsold lot ................................        (none — all sold)")
    print(f"  Total fees paid (commissions + slippage) .........  {money(total_fees):>18}")
    print(f"    of which commissions ...........................  {money(r['total_commissions']):>18}")
    print(f"    of which slippage ..............................  {money(r['total_slippage']):>18}")
    print(f"  Trades: {r['n_buys']:,} buys, {r['n_sells']:,} sells")
    fee_pct = total_fees / r["total_deposited"] * 100 if r["total_deposited"] else 0.0
    print(f"  Fees as % of capital contributed .................  {fee_pct:>17.2f}%")

    print(f"\n  MAX DRAWDOWN — strategy P&L ......................  {money(strat_dd):>18}")
    print(f"    peak {strat_pk.date()}  ->  trough {strat_tr.date()}")
    print(f"  MAX DRAWDOWN — buy-and-hold P&L ..................  {money(bh_dd):>18}")

    print("\n" + L)


# =============================================================================
# SECTION 7 — THE CHART
# =============================================================================

def make_chart(r: dict, path: str) -> None:
    """Three lines over time: realized P&L, unrealized P&L, and buy-and-hold P&L."""
    hist = r["history"]
    fig, ax = plt.subplots(figsize=(14, 7.5))

    ax.plot(hist.index, hist["realized_pnl"],  lw=1.8, color="#1b7f4b",
            label="Realized P&L (locked in)")
    ax.plot(hist.index, hist["unrealized_pnl"], lw=1.8, color="#c2601a",
            label="Unrealized P&L (paper, open lots)")
    ax.plot(hist.index, hist["buy_hold_pnl"],  lw=1.8, color="#2b5fa8", alpha=0.85,
            label="Buy & hold P&L (same schedule)")

    ax.axhline(0, color="#555", lw=0.9)   # breakeven reference
    ax.set_title(
        f"{TICKER}: daily ${DAILY_BUY_AMOUNT:,.0f} DCA with +{SELL_INCREMENT_PCT:g}% "
        f"per-lot take-profit\n{r['start']} to {r['end']}  "
        f"(commission ${COMMISSION_PER_TRADE:g}/fill, slippage {SLIPPAGE_PCT:g}%)",
        fontsize=13, pad=14,
    )
    ax.set_xlabel("Date")
    ax.set_ylabel("Profit / Loss (USD)")
    ax.grid(alpha=0.28, ls="--", lw=0.6)
    ax.legend(loc="upper left", framealpha=0.93)
    ax.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:,.0f}")
    )
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"\nChart saved to: {path}")


# =============================================================================
# SECTION 8 — SELF-TEST  (proves the engine is correct without needing internet)
# =============================================================================

def selftest() -> int:
    """
    Runs the engine on hand-made price series where the right answer is known
    by arithmetic. This checks the LOGIC — it is not a claim about real returns.
    """
    global DAILY_BUY_AMOUNT, SELL_INCREMENT_PCT, COMMISSION_PER_TRADE, SLIPPAGE_PCT
    global ALLOW_FRACTIONAL_SHARES
    failures = []

    def check(name, got, want, tol=1e-6):
        ok = abs(got - want) <= tol
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {got:.6f}, want {want:.6f}")
        if not ok:
            failures.append(name)

    DAILY_BUY_AMOUNT = 100.0
    SELL_INCREMENT_PCT = 2.0
    COMMISSION_PER_TRADE = 1.0
    SLIPPAGE_PCT = 0.05
    ALLOW_FRACTIONAL_SHARES = True
    slip = SLIPPAGE_PCT / 100.0

    # --- Test A: flat market. Nothing can ever hit +2%, so nothing is ever sold. ---
    print("\nTest A — flat market: no lot should ever trigger")
    idx = pd.date_range("2020-01-01", periods=10, freq="D")
    flat = pd.DataFrame({"Open": [100.0] * 10, "Close": [100.0] * 10}, index=idx)
    a = run_backtest(flat)
    check("A realized P&L is exactly zero", a["realized_pnl"], 0.0)
    check("A open lots == 10", a["open_lots"], 10)
    check("A sells == 0", a["n_sells"], 0)
    # Each lot is under water by exactly slippage + commission.
    want_unreal = sum(
        (100.0 / (100.0 * (1 + slip))) * 100.0 - 100.0 - 1.0 for _ in range(10)
    )
    check("A unrealized == slippage+commission drag", a["unrealized_pnl"], want_unreal, 1e-6)
    check("A oldest lot age == 9 days", a["oldest_age_days"], 9)

    # --- Test B: no lookahead. Price spikes at day 1's CLOSE, so the sell must
    #     fill at day 2's OPEN — at the LOWER price, not the spike. ---
    print("\nTest B — no lookahead: close-triggered sell must fill at NEXT open")
    idx = pd.date_range("2020-01-01", periods=3, freq="D")
    spike = pd.DataFrame(
        {"Open": [100.0, 100.0, 101.0], "Close": [100.0, 130.0, 101.0]}, index=idx
    )
    b = run_backtest(spike)
    # Walk it through by hand:
    #   Day 0: open 100 -> buy lot0 at 100.05. Close 100 < trigger 102.05, nothing.
    #   Day 1: open 100 -> buy lot1 at 100.05. Close is 130, so BOTH lots trigger
    #          and are queued for tomorrow. Note we do NOT get to sell at 130.
    #   Day 2: open is 101 -> both lots fill HERE, at 101, not at the 130 spike.
    # A lookahead bug would sell at 130 and print a fat profit. Correct code
    # sells at 101 and actually LOSES money after costs. That is the test.
    buy_fill = 100.0 * (1 + slip)
    sh = 100.0 / buy_fill
    sell_fill = 101.0 * (1 - slip)          # day-2 OPEN, not the 130 close
    per_lot = sh * sell_fill - sh * buy_fill - 1.0 - 1.0
    check("B realized uses next OPEN not spike close", b["realized_pnl"], 2 * per_lot, 1e-6)
    check("B sells == 2", b["n_sells"], 2)
    if b["realized_pnl"] > 0:
        failures.append("B leaked lookahead profit")
        print("  [FAIL] B: realized profit is positive — lookahead leak!")
    else:
        print("  [PASS] B: realized is negative, so it did NOT sell into the spike")

    # --- Test C: a clean winner. Rise enough that the lot clears +2% net of costs. ---
    print("\nTest C — clean winner: realized profit is arithmetically exact")
    idx = pd.date_range("2020-01-01", periods=2, freq="D")
    up = pd.DataFrame({"Open": [100.0, 110.0], "Close": [110.0, 110.0]}, index=idx)
    c = run_backtest(up)
    buy_fill = 100.0 * (1 + slip)
    sh = 100.0 / buy_fill
    sell_fill = 110.0 * (1 - slip)
    # Day 2 also buys a lot, so realized comes only from the day-1 lot.
    want = sh * sell_fill - sh * buy_fill - 2.0
    check("C realized P&L exact", c["realized_pnl"], want, 1e-6)

    # --- Test D: costs are never zero. ---
    print("\nTest D — costs are always charged")
    check("D commissions == 2 buys + 1 sell x $1 (test C)", c["total_commissions"], 3.0)
    if c["total_slippage"] <= 0:
        failures.append("D slippage not charged")
        print("  [FAIL] D: slippage was zero")
    else:
        print(f"  [PASS] D: slippage charged (${c['total_slippage']:.4f})")

    # --- Test E: realized and unrealized must be reported separately and must
    #     reconcile with the account: cash + holdings - deposits == total P&L. ---
    print("\nTest E — accounting identity: cash + holdings - deposits == realized + unrealized")
    idx = pd.date_range("2020-01-01", periods=40, freq="D")
    wave = [100 + 8 * ((k % 7) - 3) for k in range(40)]
    w = pd.DataFrame({"Open": wave, "Close": wave}, index=idx)
    e = run_backtest(w)
    lhs = e["cash"] + e["holdings_value"] - e["total_deposited"]
    rhs = e["realized_pnl"] + e["unrealized_pnl"]
    check("E books balance", lhs, rhs, 1e-6)

    print("\n" + "=" * 60)
    if failures:
        print(f"SELF-TEST FAILED: {len(failures)} problem(s): {failures}")
        return 1
    print("SELF-TEST PASSED — engine logic verified on synthetic data.")
    print("(This validates the mechanics only. It says nothing about real returns.)")
    print("=" * 60)
    return 0


# =============================================================================
# SECTION 9 — MAIN
# =============================================================================

def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()

    # Guardrail: the brief said model costs honestly. Enforce it in code.
    if SLIPPAGE_PCT < 0.05:
        raise SystemExit("ERROR: SLIPPAGE_PCT must be at least 0.05 (%). No zero-cost backtests.")

    end = END_DATE or date.today().isoformat()
    df = load_price_data(TICKER, START_DATE, end)
    results = run_backtest(df)
    print_report(results)
    make_chart(results, OUTPUT_PNG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
