#!/usr/bin/env python3
"""Scalping backtest engine - flexible framework for testing scalp strategies.

Each strategy is a function that takes (df_5m, df_15m, bar_idx) and returns:
  {"signal": +1/0/-1, "reason": str, "confidence": int}

Usage:
    python3 scripts/backtest_scalp.py --strategy momentum --days 90
    python3 scripts/backtest_scalp.py --strategy meanrev --output-json results/meanrev.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.indicators import ema, sma, calc_rsi, calc_bollinger, calc_adx


# ──────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────

def load_data(path_5m: str, path_15m: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load 5m and 15m kline CSVs."""
    df_5m = pd.read_csv(path_5m)
    df_15m = pd.read_csv(path_15m)
    df_5m["timestamp"] = pd.to_datetime(df_5m["timestamp"], utc=True)
    df_15m["timestamp"] = pd.to_datetime(df_15m["timestamp"], utc=True)
    return df_5m, df_15m


def align_15m_to_5m(df_5m: pd.DataFrame, df_15m: pd.DataFrame, idx_5m: int) -> pd.DataFrame:
    """Get 15m data up to the current 5m bar timestamp."""
    ts = df_5m.iloc[idx_5m]["timestamp"]
    return df_15m[df_15m["timestamp"] <= ts]


# ──────────────────────────────────────────────
# Common indicator calculations
# ──────────────────────────────────────────────

def add_common_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add standard indicators to a DataFrame."""
    df = df.copy()
    df["ema5"] = ema(df["close"], 5)
    df["ema8"] = ema(df["close"], 8)
    df["ema13"] = ema(df["close"], 13)
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["sma20"] = sma(df["close"], 20)
    df["rsi"] = calc_rsi(df["close"], 14)
    df["rsi_6"] = calc_rsi(df["close"], 6)

    bb = calc_bollinger(df, 20, 2.0)
    df["bb_upper"] = bb["bb_upper"]
    df["bb_mid"] = bb["bb_mid"]
    df["bb_lower"] = bb["bb_lower"]
    df["bb_pct"] = bb["bb_pct"]
    df["bb_width"] = bb["bb_width"]

    adx_df = calc_adx(df, 14)
    df["adx"] = adx_df["adx"]
    df["plus_di"] = adx_df["plus_di"]
    df["minus_di"] = adx_df["minus_di"]

    vol_ma = sma(df["volume"], 20)
    df["volume_ratio"] = df["volume"] / vol_ma.replace(0, np.nan)
    df["volume_ratio"] = df["volume_ratio"].fillna(1.0)

    df["is_bullish"] = df["close"] > df["open"]
    df["is_bearish"] = df["close"] < df["open"]

    # MACD
    ema12 = ema(df["close"], 12)
    ema26 = ema(df["close"], 26)
    df["macd"] = ema12 - ema26
    df["macd_signal"] = ema(df["macd"], 9)
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # ATR
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].ewm(span=14, adjust=False).mean()

    # Stochastic RSI
    rsi = df["rsi"]
    rsi_min = rsi.rolling(14).min()
    rsi_max = rsi.rolling(14).max()
    stoch_rsi = (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)
    df["stoch_rsi_k"] = stoch_rsi.rolling(3).mean().fillna(0.5)
    df["stoch_rsi_d"] = df["stoch_rsi_k"].rolling(3).mean().fillna(0.5)

    # VWAP approximation (cumulative from session-like reset every 288 bars = 1 day of 5m)
    df["vwap"] = (df["close"] * df["volume"]).rolling(288, min_periods=1).sum() / \
                  df["volume"].rolling(288, min_periods=1).sum().replace(0, np.nan)
    df["vwap"] = df["vwap"].fillna(df["close"])

    return df


# ──────────────────────────────────────────────
# Trade / Backtest Config
# ──────────────────────────────────────────────

@dataclass
class ScalpTrade:
    entry_idx: int
    entry_price: float
    side: str
    confidence: int = 1
    entry_time: str = ""
    exit_idx: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""
    exit_time: str = ""
    pnl_pct: float = 0.0
    fee_pct: float = 0.0
    net_pnl_pct: float = 0.0
    max_favorable: float = 0.0  # MFE
    max_adverse: float = 0.0    # MAE
    trigger: str = ""


@dataclass
class ScalpBacktestConfig:
    initial_capital: float = 1000.0
    leverage: int = 3
    position_size_pct: float = 5.0
    stop_loss_pct: float = 0.8
    take_profit_pct: float = 1.4
    trailing_activate_pct: float = 0.8
    trailing_callback_pct: float = 0.4
    taker_fee_pct: float = 0.055
    fee_buffer_pct: float = 0.15
    time_exit_bars: int = 9  # 9 * 5min = 45min
    max_daily_trades: int = 20
    cooldown_bars: int = 3  # bars between trades
    use_atr_stops: bool = False
    atr_sl_mult: float = 1.5
    atr_tp_mult: float = 2.5


# ──────────────────────────────────────────────
# Backtest Engine
# ──────────────────────────────────────────────

def run_scalp_backtest(
    df_5m: pd.DataFrame,
    df_15m: pd.DataFrame,
    strategy_fn,
    cfg: ScalpBacktestConfig = None,
    strategy_name: str = "unknown",
) -> dict:
    """Run scalping backtest.

    Args:
        df_5m: 5-minute OHLCV data
        df_15m: 15-minute OHLCV data
        strategy_fn: callable(df_5m_slice, df_15m_slice, row) -> {"signal": int, "reason": str, "confidence": int}
        cfg: backtest configuration
        strategy_name: name for reporting

    Returns:
        dict with trades, metrics, equity curve
    """
    cfg = cfg or ScalpBacktestConfig()

    # Pre-calculate indicators
    df_5m = add_common_indicators(df_5m)
    df_15m = add_common_indicators(df_15m)

    trades: list[ScalpTrade] = []
    equity_curve = []
    capital = cfg.initial_capital
    position: ScalpTrade | None = None
    trailing_active = False
    trailing_high = 0.0
    last_trade_idx = -999
    daily_trade_count = {}

    start_idx = 250  # warmup for indicators

    for i in range(start_idx, len(df_5m)):
        row = df_5m.iloc[i]
        price = row["close"]
        ts = row["timestamp"]
        day_key = str(ts.date()) if hasattr(ts, "date") else str(ts)[:10]

        # Check position exit
        if position is not None:
            entry = position.entry_price
            if position.side == "Buy":
                pnl = ((price - entry) / entry) * 100
            else:
                pnl = ((entry - price) / entry) * 100

            # Track MFE/MAE
            if pnl > position.max_favorable:
                position.max_favorable = pnl
            if pnl < position.max_adverse:
                position.max_adverse = pnl

            exit_reason = None

            # Dynamic stops from ATR
            if cfg.use_atr_stops:
                atr_val = row.get("atr", 0)
                if atr_val > 0:
                    sl_pct = (atr_val * cfg.atr_sl_mult / entry) * 100
                    tp_pct = (atr_val * cfg.atr_tp_mult / entry) * 100
                else:
                    sl_pct = cfg.stop_loss_pct
                    tp_pct = cfg.take_profit_pct
            else:
                sl_pct = cfg.stop_loss_pct
                tp_pct = cfg.take_profit_pct

            # Stop loss (check with high/low for intrabar)
            if position.side == "Buy":
                worst_pnl = ((row["low"] - entry) / entry) * 100
            else:
                worst_pnl = ((entry - row["high"]) / entry) * 100
            if worst_pnl <= -sl_pct:
                exit_reason = "SL_HIT"
                # Use SL price, not close
                if position.side == "Buy":
                    price = entry * (1 - sl_pct / 100)
                else:
                    price = entry * (1 + sl_pct / 100)

            # Take profit (check with high/low)
            if exit_reason is None:
                if position.side == "Buy":
                    best_pnl = ((row["high"] - entry) / entry) * 100
                else:
                    best_pnl = ((entry - row["low"]) / entry) * 100
                if best_pnl >= tp_pct:
                    exit_reason = "TP_HIT"
                    if position.side == "Buy":
                        price = entry * (1 + tp_pct / 100)
                    else:
                        price = entry * (1 - tp_pct / 100)

            # Trailing stop
            if exit_reason is None and pnl >= cfg.trailing_activate_pct:
                if not trailing_active:
                    trailing_active = True
                    trailing_high = price
                if position.side == "Buy":
                    if price > trailing_high:
                        trailing_high = price
                    drawdown = ((price - trailing_high) / trailing_high) * 100
                else:
                    if price < trailing_high:
                        trailing_high = price
                    drawdown = ((trailing_high - price) / trailing_high) * 100
                if drawdown <= -cfg.trailing_callback_pct:
                    exit_reason = "TRAILING_STOP"

            # Time exit
            bars_held = i - position.entry_idx
            if exit_reason is None and bars_held >= cfg.time_exit_bars:
                if pnl < cfg.fee_buffer_pct:
                    exit_reason = "TIME_EXIT"

            # Recalculate PnL with actual exit price
            if exit_reason:
                if position.side == "Buy":
                    pnl = ((price - entry) / entry) * 100
                else:
                    pnl = ((entry - price) / entry) * 100
                fee = cfg.taker_fee_pct * 2
                net_pnl = pnl - fee
                position.exit_idx = i
                position.exit_price = price
                position.exit_reason = exit_reason
                position.exit_time = str(ts)
                position.pnl_pct = round(pnl, 4)
                position.fee_pct = round(fee, 4)
                position.net_pnl_pct = round(net_pnl, 4)
                trades.append(position)

                margin = capital * (cfg.position_size_pct / 100)
                capital += margin * (net_pnl / 100) * cfg.leverage
                position = None
                trailing_active = False
                trailing_high = 0.0
                last_trade_idx = i

        # Check entry
        if position is None and (i - last_trade_idx) >= cfg.cooldown_bars:
            # Daily trade limit
            if daily_trade_count.get(day_key, 0) >= cfg.max_daily_trades:
                equity_curve.append({"idx": i, "capital": round(capital, 2)})
                continue

            # Get 15m data aligned to current 5m bar
            df_15m_slice = align_15m_to_5m(df_5m, df_15m, i)

            # Run strategy
            sig = strategy_fn(df_5m.iloc[:i + 1], df_15m_slice, row)
            signal = sig.get("signal", 0)
            confidence = sig.get("confidence", 0)
            trigger = sig.get("trigger", "")

            if signal != 0 and confidence >= 1:
                side = "Buy" if signal == 1 else "Sell"
                position = ScalpTrade(
                    entry_idx=i,
                    entry_price=price,
                    side=side,
                    confidence=confidence,
                    entry_time=str(ts),
                    trigger=trigger,
                )
                trailing_active = False
                trailing_high = price
                daily_trade_count[day_key] = daily_trade_count.get(day_key, 0) + 1

        equity_curve.append({"idx": i, "capital": round(capital, 2)})

    # Close any open position
    if position is not None:
        price = df_5m.iloc[-1]["close"]
        entry = position.entry_price
        if position.side == "Buy":
            pnl = ((price - entry) / entry) * 100
        else:
            pnl = ((entry - price) / entry) * 100
        fee = cfg.taker_fee_pct * 2
        net_pnl = pnl - fee
        position.exit_idx = len(df_5m) - 1
        position.exit_price = price
        position.exit_reason = "END_OF_DATA"
        position.pnl_pct = round(pnl, 4)
        position.fee_pct = round(fee, 4)
        position.net_pnl_pct = round(net_pnl, 4)
        trades.append(position)
        margin = capital * (cfg.position_size_pct / 100)
        capital += margin * (net_pnl / 100) * cfg.leverage

    metrics = calc_scalp_metrics(trades, cfg.initial_capital, capital, cfg)
    return {
        "strategy": strategy_name,
        "config": {
            "initial_capital": cfg.initial_capital,
            "leverage": cfg.leverage,
            "position_size_pct": cfg.position_size_pct,
            "stop_loss_pct": cfg.stop_loss_pct,
            "take_profit_pct": cfg.take_profit_pct,
            "trailing_activate_pct": cfg.trailing_activate_pct,
            "trailing_callback_pct": cfg.trailing_callback_pct,
            "fee_buffer_pct": cfg.fee_buffer_pct,
            "time_exit_bars": cfg.time_exit_bars,
            "use_atr_stops": cfg.use_atr_stops,
            "total_bars": len(df_5m),
        },
        "metrics": metrics,
        "trades": [
            {
                "side": t.side, "entry_price": t.entry_price, "exit_price": t.exit_price,
                "pnl_pct": t.pnl_pct, "net_pnl_pct": t.net_pnl_pct, "exit_reason": t.exit_reason,
                "bars_held": t.exit_idx - t.entry_idx, "mfe": round(t.max_favorable, 4),
                "mae": round(t.max_adverse, 4), "confidence": t.confidence, "trigger": t.trigger,
                "entry_time": t.entry_time, "exit_time": t.exit_time,
            }
            for t in trades
        ],
        "final_capital": round(capital, 2),
    }


def calc_scalp_metrics(trades: list[ScalpTrade], initial: float, final: float, cfg: ScalpBacktestConfig) -> dict:
    """Calculate performance metrics."""
    if not trades:
        return {"total_trades": 0, "win_rate": 0, "profit_factor": 0, "total_return_pct": 0,
                "max_drawdown_pct": 0, "sharpe_approx": 0, "expectancy_pct": 0}

    wins = [t for t in trades if t.net_pnl_pct > 0]
    losses = [t for t in trades if t.net_pnl_pct <= 0]

    win_rate = len(wins) / len(trades) * 100
    avg_win = np.mean([t.net_pnl_pct for t in wins]) if wins else 0
    avg_loss = np.mean([t.net_pnl_pct for t in losses]) if losses else 0
    avg_mfe = np.mean([t.max_favorable for t in trades])
    avg_mae = np.mean([t.max_adverse for t in trades])

    total_win_pnl = sum(t.net_pnl_pct for t in wins)
    total_loss_pnl = abs(sum(t.net_pnl_pct for t in losses))
    profit_factor = total_win_pnl / total_loss_pnl if total_loss_pnl > 0 else float("inf")

    total_return = ((final - initial) / initial) * 100

    # Expectancy
    wr = len(wins) / len(trades)
    expectancy = (wr * avg_win) - ((1 - wr) * abs(avg_loss))

    # Max drawdown
    equity = initial
    peak = equity
    max_dd = 0
    for t in trades:
        margin = equity * (cfg.position_size_pct / 100)
        equity += margin * (t.net_pnl_pct / 100) * cfg.leverage
        if equity > peak:
            peak = equity
        dd = ((equity - peak) / peak) * 100
        if dd < max_dd:
            max_dd = dd

    # Max consecutive losses
    max_consec_loss = 0
    current_streak = 0
    for t in trades:
        if t.net_pnl_pct <= 0:
            current_streak += 1
            max_consec_loss = max(max_consec_loss, current_streak)
        else:
            current_streak = 0

    # Sharpe approximation (daily returns)
    pnl_list = [t.net_pnl_pct for t in trades]
    if len(pnl_list) > 1:
        sharpe = (np.mean(pnl_list) / np.std(pnl_list)) * np.sqrt(252 * 12)  # ~12 trades/day for 5m scalping
    else:
        sharpe = 0

    # Exit reason distribution
    exit_reasons = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

    # Trigger distribution
    trigger_dist = {}
    for t in trades:
        key = t.trigger or "unknown"
        trigger_dist[key] = trigger_dist.get(key, 0) + 1

    avg_bars = np.mean([t.exit_idx - t.entry_idx for t in trades])

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 2),
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "profit_factor": round(profit_factor, 4),
        "total_return_pct": round(total_return, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "expectancy_pct": round(expectancy, 4),
        "sharpe_approx": round(sharpe, 4),
        "avg_mfe": round(avg_mfe, 4),
        "avg_mae": round(avg_mae, 4),
        "avg_holding_bars": round(avg_bars, 1),
        "avg_holding_minutes": round(avg_bars * 5, 1),
        "max_consecutive_losses": max_consec_loss,
        "exit_reasons": exit_reasons,
        "trigger_distribution": trigger_dist,
    }


def print_scalp_results(results: dict):
    """Pretty-print scalp backtest results."""
    m = results["metrics"]
    c = results["config"]

    print(f"\n{'=' * 65}")
    print(f"  SCALPING BACKTEST: {results['strategy']}")
    print(f"{'=' * 65}")
    print(f"  Bars: {c['total_bars']} (5m) | Leverage: {c['leverage']}x | Size: {c['position_size_pct']}%")
    print(f"  SL: -{c['stop_loss_pct']}% | TP: +{c['take_profit_pct']}% | ATR stops: {c.get('use_atr_stops', False)}")
    print(f"  Trailing: activate +{c['trailing_activate_pct']}%, callback -{c['trailing_callback_pct']}%")
    print(f"  Time exit: {c['time_exit_bars']} bars ({c['time_exit_bars']*5}min)")
    print(f"{'-' * 65}")
    print(f"  Total Trades:     {m['total_trades']}")
    print(f"  Win Rate:         {m['win_rate']:.1f}% ({m.get('wins', 0)}W / {m.get('losses', 0)}L)")
    print(f"  Profit Factor:    {m['profit_factor']:.2f}")
    print(f"  Total Return:     {m['total_return_pct']:+.2f}%")
    print(f"  Max Drawdown:     {m['max_drawdown_pct']:.2f}%")
    print(f"  Sharpe (approx):  {m['sharpe_approx']:.2f}")
    print(f"  Expectancy:       {m['expectancy_pct']:+.4f}% per trade")
    print(f"  Avg Win:          {m['avg_win_pct']:+.4f}%")
    print(f"  Avg Loss:         {m['avg_loss_pct']:.4f}%")
    print(f"  Avg MFE:          {m['avg_mfe']:+.4f}%")
    print(f"  Avg MAE:          {m['avg_mae']:.4f}%")
    print(f"  Avg Holding:      {m['avg_holding_minutes']:.0f}min ({m['avg_holding_bars']:.1f} bars)")
    print(f"  Max Consec Losses:{m['max_consecutive_losses']}")
    print(f"  Initial Capital:  ${c['initial_capital']:.2f}")
    print(f"  Final Capital:    ${results['final_capital']:.2f}")
    print(f"{'-' * 65}")
    print(f"  Exit Reasons:")
    for reason, count in sorted(m.get("exit_reasons", {}).items(), key=lambda x: -x[1]):
        pct = count / m["total_trades"] * 100
        print(f"    {reason}: {count} ({pct:.1f}%)")
    if m.get("trigger_distribution"):
        print(f"  Triggers:")
        for trig, count in sorted(m.get("trigger_distribution", {}).items(), key=lambda x: -x[1]):
            print(f"    {trig}: {count}")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scalping backtest engine")
    parser.add_argument("--data-5m", default="data/XRPUSDT_5m_klines.csv")
    parser.add_argument("--data-15m", default="data/XRPUSDT_15m_klines.csv")
    parser.add_argument("--strategy", default="baseline", help="Strategy name to import from scripts/strategies/")
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()

    df_5m, df_15m = load_data(args.data_5m, args.data_15m)
    print(f"Loaded 5m: {len(df_5m)} bars, 15m: {len(df_15m)} bars")

    # Import strategy dynamically
    import importlib
    try:
        mod = importlib.import_module(f"scripts.strategies.{args.strategy}")
        strategy_fn = mod.strategy
        strategy_name = getattr(mod, "STRATEGY_NAME", args.strategy)
        bt_config = getattr(mod, "BT_CONFIG", ScalpBacktestConfig())
    except ImportError as e:
        print(f"Cannot import strategy '{args.strategy}': {e}")
        sys.exit(1)

    results = run_scalp_backtest(df_5m, df_15m, strategy_fn, bt_config, strategy_name)
    print_scalp_results(results)

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_json, "w") as f:
            json.dump({k: v for k, v in results.items() if k != "equity_curve"}, f, indent=2, default=str)
        print(f"\nResults saved to {args.output_json}")
