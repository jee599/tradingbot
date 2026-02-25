#!/usr/bin/env python3
"""Minimal backtest engine for the MA+RSI+BB+MTF strategy.

Usage:
    # Download klines first (creates CSV):
    python3 scripts/backtest.py download --symbol XRPUSDT --days 180

    # Run backtest on CSV:
    python3 scripts/backtest.py run --csv data/XRPUSDT_60_klines.csv

    # Run backtest fetching data live (testnet):
    python3 scripts/backtest.py run --symbol XRPUSDT --days 30

This script is NON-INVASIVE: it imports indicator/strategy logic from src/
but never places orders or touches exchange state.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.indicators import calc_all_indicators
from src.strategy import generate_signals
from src.config import Config


# ──────────────────────────────────────────────
# Data
# ──────────────────────────────────────────────

def download_klines(symbol: str, interval: str = "60", days: int = 180) -> pd.DataFrame:
    """Download klines from Bybit V5 public API (no auth needed)."""
    import requests

    url = "https://api.bybit.com/v5/market/kline"
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000
    all_rows = []

    print(f"Downloading {symbol} {interval}m klines for {days} days...")
    cursor_end = end_ms

    while cursor_end > start_ms:
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": 200,
            "end": cursor_end,
        }
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if data.get("retCode") != 0:
            print(f"API Error: {data.get('retMsg')}")
            break
        rows = data.get("result", {}).get("list", [])
        if not rows:
            break
        all_rows.extend(rows)
        oldest_ts = int(rows[-1][0])
        if oldest_ts <= start_ms:
            break
        cursor_end = oldest_ts - 1
        time.sleep(0.15)  # respect rate limits

    if not all_rows:
        print("No data downloaded.")
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=[
        "timestamp", "open", "high", "low", "close", "volume", "turnover"
    ])
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)

    print(f"Downloaded {len(df)} candles ({df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]})")
    return df


def save_csv(df: pd.DataFrame, path: str):
    """Save DataFrame to CSV."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"Saved to {path}")


def load_csv(path: str) -> pd.DataFrame:
    """Load kline CSV."""
    df = pd.read_csv(path)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


# ──────────────────────────────────────────────
# Backtest Engine
# ──────────────────────────────────────────────

@dataclass
class Trade:
    entry_idx: int
    entry_price: float
    side: str  # "Buy" or "Sell"
    qty: float
    confidence: int
    entry_time: str = ""
    exit_idx: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""
    exit_time: str = ""
    pnl_pct: float = 0.0
    fee_pct: float = 0.0
    net_pnl_pct: float = 0.0


@dataclass
class BacktestConfig:
    initial_capital: float = 1000.0
    leverage: int = 1
    position_size_pct: float = 5.0
    max_position_size_pct: float = 10.0
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 4.0
    trailing_activate_pct: float = 2.0
    trailing_callback_pct: float = 1.0
    taker_fee_pct: float = 0.055  # 0.055% per side
    min_confidence: int = 2
    time_exit_hours: int = 48


def run_backtest(df: pd.DataFrame, cfg: BacktestConfig = None) -> dict:
    """Run backtest on OHLCV DataFrame.

    Returns dict with trades list and metrics.
    """
    cfg = cfg or BacktestConfig()

    # Calculate indicators
    df = calc_all_indicators(df)

    trades: list[Trade] = []
    equity_curve = []
    capital = cfg.initial_capital
    position: Trade | None = None
    trailing_active = False
    trailing_high = 0.0

    # Need at least 200 bars for indicators
    start_idx = 200

    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        price = row["close"]

        # Generate signals using data up to current bar
        signals = generate_signals(df.iloc[:i + 1])
        combined = signals["combined_signal"]
        confidence = signals["confidence"]

        # Check exit conditions if in position
        if position is not None:
            entry = position.entry_price
            if position.side == "Buy":
                pnl = ((price - entry) / entry) * 100
            else:
                pnl = ((entry - price) / entry) * 100

            exit_reason = None

            # Stop loss
            if pnl <= -cfg.stop_loss_pct:
                exit_reason = "SL_HIT"
            # Take profit
            elif pnl >= cfg.take_profit_pct:
                exit_reason = "TP_HIT"
            # Trailing stop
            elif pnl >= cfg.trailing_activate_pct:
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
            # Signal reverse
            if exit_reason is None:
                if position.side == "Buy" and combined == -1:
                    exit_reason = "SIGNAL_REVERSE"
                elif position.side == "Sell" and combined == 1:
                    exit_reason = "SIGNAL_REVERSE"
            # Time exit
            if exit_reason is None and (i - position.entry_idx) >= cfg.time_exit_hours and pnl < 0:
                exit_reason = "TIME_EXIT"

            if exit_reason:
                fee = cfg.taker_fee_pct * 2  # entry + exit
                net_pnl = pnl - fee
                position.exit_idx = i
                position.exit_price = price
                position.exit_reason = exit_reason
                position.exit_time = str(row.get("timestamp", ""))
                position.pnl_pct = round(pnl, 4)
                position.fee_pct = round(fee, 4)
                position.net_pnl_pct = round(net_pnl, 4)
                trades.append(position)

                # Update capital
                margin = capital * (cfg.position_size_pct / 100)
                capital += margin * (net_pnl / 100) * cfg.leverage
                position = None
                trailing_active = False
                trailing_high = 0.0

        # Check entry conditions if no position
        if position is None and combined != 0 and confidence >= cfg.min_confidence:
            side = "Buy" if combined == 1 else "Sell"
            position = Trade(
                entry_idx=i,
                entry_price=price,
                side=side,
                qty=0,  # not needed for backtest
                confidence=confidence,
                entry_time=str(row.get("timestamp", "")),
            )
            trailing_active = False
            trailing_high = price

        equity_curve.append({"idx": i, "capital": round(capital, 2)})

    # Close any open position at last bar
    if position is not None:
        price = df.iloc[-1]["close"]
        entry = position.entry_price
        if position.side == "Buy":
            pnl = ((price - entry) / entry) * 100
        else:
            pnl = ((entry - price) / entry) * 100
        fee = cfg.taker_fee_pct * 2
        net_pnl = pnl - fee
        position.exit_idx = len(df) - 1
        position.exit_price = price
        position.exit_reason = "END_OF_DATA"
        position.pnl_pct = round(pnl, 4)
        position.fee_pct = round(fee, 4)
        position.net_pnl_pct = round(net_pnl, 4)
        trades.append(position)
        margin = capital * (cfg.position_size_pct / 100)
        capital += margin * (net_pnl / 100) * cfg.leverage

    metrics = calc_metrics(trades, cfg.initial_capital, capital)
    return {
        "config": {
            "initial_capital": cfg.initial_capital,
            "leverage": cfg.leverage,
            "position_size_pct": cfg.position_size_pct,
            "stop_loss_pct": cfg.stop_loss_pct,
            "take_profit_pct": cfg.take_profit_pct,
            "trailing_activate_pct": cfg.trailing_activate_pct,
            "trailing_callback_pct": cfg.trailing_callback_pct,
            "taker_fee_pct": cfg.taker_fee_pct,
            "min_confidence": cfg.min_confidence,
            "total_bars": len(df),
        },
        "metrics": metrics,
        "trades": [t.__dict__ for t in trades],
        "equity_curve": equity_curve,
        "final_capital": round(capital, 2),
    }


def calc_metrics(trades: list[Trade], initial_capital: float, final_capital: float) -> dict:
    """Calculate performance metrics from trade list."""
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "total_return_pct": 0,
            "max_drawdown_pct": 0,
            "expectancy_pct": 0,
            "avg_win_pct": 0,
            "avg_loss_pct": 0,
            "avg_holding_bars": 0,
        }

    wins = [t for t in trades if t.net_pnl_pct > 0]
    losses = [t for t in trades if t.net_pnl_pct <= 0]

    win_rate = len(wins) / len(trades) * 100
    avg_win = np.mean([t.net_pnl_pct for t in wins]) if wins else 0
    avg_loss = np.mean([t.net_pnl_pct for t in losses]) if losses else 0

    total_win_pnl = sum(t.net_pnl_pct for t in wins)
    total_loss_pnl = abs(sum(t.net_pnl_pct for t in losses))
    profit_factor = total_win_pnl / total_loss_pnl if total_loss_pnl > 0 else float("inf")

    total_return = ((final_capital - initial_capital) / initial_capital) * 100
    avg_holding = np.mean([t.exit_idx - t.entry_idx for t in trades])

    # Expectancy
    wr = len(wins) / len(trades)
    expectancy = (wr * avg_win) - ((1 - wr) * abs(avg_loss))

    # Max drawdown (simple sequential peak-to-trough on trade PnL)
    equity = initial_capital
    peak = equity
    max_dd = 0
    for t in trades:
        margin = equity * 0.05  # simplified
        equity += margin * (t.net_pnl_pct / 100)
        if equity > peak:
            peak = equity
        dd = ((equity - peak) / peak) * 100
        if dd < max_dd:
            max_dd = dd

    # Exit reason distribution
    exit_reasons = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

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
        "avg_holding_bars": round(avg_holding, 1),
        "exit_reasons": exit_reasons,
    }


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def print_results(results: dict):
    """Pretty-print backtest results."""
    m = results["metrics"]
    c = results["config"]

    print("\n" + "=" * 60)
    print("  BACKTEST RESULTS")
    print("=" * 60)
    print(f"  Bars: {c['total_bars']} | Leverage: {c['leverage']}x")
    print(f"  Position Size: {c['position_size_pct']}% | Fee: {c['taker_fee_pct']}%/side")
    print(f"  SL: -{c['stop_loss_pct']}% | TP: +{c['take_profit_pct']}%")
    print(f"  Min Confidence: {c['min_confidence']}/4")
    print("-" * 60)
    print(f"  Total Trades:     {m['total_trades']}")
    print(f"  Win Rate:         {m['win_rate']:.1f}% ({m.get('wins', 0)}W / {m.get('losses', 0)}L)")
    print(f"  Profit Factor:    {m['profit_factor']:.2f}")
    print(f"  Total Return:     {m['total_return_pct']:+.2f}%")
    print(f"  Max Drawdown:     {m['max_drawdown_pct']:.2f}%")
    print(f"  Expectancy:       {m['expectancy_pct']:+.4f}% per trade")
    print(f"  Avg Win:          {m['avg_win_pct']:+.4f}%")
    print(f"  Avg Loss:         {m['avg_loss_pct']:.4f}%")
    print(f"  Avg Holding:      {m['avg_holding_bars']:.0f} bars")
    print(f"  Initial Capital:  ${c['initial_capital']:.2f}")
    print(f"  Final Capital:    ${results['final_capital']:.2f}")
    print("-" * 60)
    print("  Exit Reasons:")
    for reason, count in sorted(m.get("exit_reasons", {}).items(), key=lambda x: -x[1]):
        print(f"    {reason}: {count}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Backtest engine for MA+RSI+BB+MTF strategy")
    sub = parser.add_subparsers(dest="command")

    # download
    dl = sub.add_parser("download", help="Download kline data from Bybit")
    dl.add_argument("--symbol", default="XRPUSDT")
    dl.add_argument("--interval", default="60")
    dl.add_argument("--days", type=int, default=180)
    dl.add_argument("--output", default=None)

    # run
    run = sub.add_parser("run", help="Run backtest")
    run.add_argument("--csv", default=None, help="Path to kline CSV")
    run.add_argument("--symbol", default="XRPUSDT")
    run.add_argument("--days", type=int, default=90)
    run.add_argument("--capital", type=float, default=1000.0)
    run.add_argument("--leverage", type=int, default=1)
    run.add_argument("--size-pct", type=float, default=5.0)
    run.add_argument("--sl", type=float, default=2.0)
    run.add_argument("--tp", type=float, default=4.0)
    run.add_argument("--min-confidence", type=int, default=2)
    run.add_argument("--output-json", default=None, help="Save results to JSON")

    args = parser.parse_args()

    if args.command == "download":
        df = download_klines(args.symbol, args.interval, args.days)
        if df.empty:
            sys.exit(1)
        out = args.output or f"data/{args.symbol}_{args.interval}_klines.csv"
        save_csv(df, out)

    elif args.command == "run":
        if args.csv:
            df = load_csv(args.csv)
            print(f"Loaded {len(df)} candles from {args.csv}")
        else:
            df = download_klines(args.symbol, "60", args.days)
            if df.empty:
                print("No data. Use --csv or check network.")
                sys.exit(1)

        cfg = BacktestConfig(
            initial_capital=args.capital,
            leverage=args.leverage,
            position_size_pct=args.size_pct,
            stop_loss_pct=args.sl,
            take_profit_pct=args.tp,
            min_confidence=args.min_confidence,
        )
        results = run_backtest(df, cfg)
        print_results(results)

        if args.output_json:
            # Don't save full equity curve to JSON (too large)
            out = {k: v for k, v in results.items() if k != "equity_curve"}
            Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
            with open(args.output_json, "w") as f:
                json.dump(out, f, indent=2, default=str)
            print(f"\nResults saved to {args.output_json}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
