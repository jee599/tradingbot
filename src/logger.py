"""로그 시스템 - 파일 + 콘솔 출력, 매매/시그널/잔고/에러 분리 기록."""

from __future__ import annotations

import json
import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from src.config import Config
from src.utils import date_today, month_str


class BotLogger:
    """봇 전용 로거."""

    def __init__(self):
        self.log_dir = Path(Config.LOG_DIR)
        self._ensure_dirs()
        self._setup_logging()

    def _ensure_dirs(self):
        """로그 디렉토리 생성."""
        for sub in ["", "trades", "signals", "equity", "errors"]:
            (self.log_dir / sub).mkdir(parents=True, exist_ok=True)

    def _setup_logging(self):
        """파이썬 로거 설정."""
        self.logger = logging.getLogger("xrp_bot")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()

        fmt = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 콘솔 (INFO 이상)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        self.logger.addHandler(ch)

        # 메인 로그 파일 (INFO 이상)
        fh = logging.FileHandler(self.log_dir / "bot.log", encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(fmt)
        self.logger.addHandler(fh)

        # 디버그 로그 파일 (전체)
        dh = logging.FileHandler(self.log_dir / "bot_debug.log", encoding="utf-8")
        dh.setLevel(logging.DEBUG)
        dh.setFormatter(fmt)
        self.logger.addHandler(dh)

        # 에러 전용 로그
        eh = logging.FileHandler(self.log_dir / "errors" / "errors.log", encoding="utf-8")
        eh.setLevel(logging.ERROR)
        eh.setFormatter(fmt)
        self.logger.addHandler(eh)

    def debug(self, msg: str):
        self.logger.debug(msg)

    def info(self, msg: str):
        self.logger.info(msg)

    def warning(self, msg: str):
        self.logger.warning(msg)

    def error(self, msg: str):
        self.logger.error(msg)

    def critical(self, msg: str):
        self.logger.critical(msg)

    # --- 구조화된 로그 ---

    def log_trade(self, trade_data: dict):
        """매매 기록을 월별 JSON 파일에 추가."""
        filename = self.log_dir / "trades" / f"trades_{month_str()}.json"
        trades = []
        if filename.exists():
            try:
                trades = json.loads(filename.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError):
                trades = []
        trades.append(trade_data)
        filename.write_text(json.dumps(trades, indent=2, ensure_ascii=False), encoding="utf-8")
        self.info(f"TRADE_LOG: {trade_data.get('trade_id')} | {trade_data.get('side')} | "
                  f"PnL: {trade_data.get('net_pnl_pct', 0):.2f}% | {trade_data.get('exit_reason')}")

    def log_signal(self, signal_data: dict):
        """시그널 기록을 일별 JSON 파일에 추가."""
        filename = self.log_dir / "signals" / f"signals_{date_today()}.json"
        signals = []
        if filename.exists():
            try:
                signals = json.loads(filename.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError):
                signals = []
        signals.append(signal_data)
        filename.write_text(json.dumps(signals, indent=2, ensure_ascii=False), encoding="utf-8")

    def log_equity(self, equity_data: dict):
        """잔고 데이터를 일별 CSV에 추가."""
        filename = self.log_dir / "equity" / f"equity_{date_today()}.csv"
        file_exists = filename.exists()
        fieldnames = [
            "timestamp", "total_equity", "available_balance", "position_margin",
            "unrealized_pnl", "realized_pnl_today", "cumulative_pnl",
            "drawdown_from_peak", "num_trades_today", "win_rate_7d",
        ]
        with open(filename, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            # Avoid CSV schema mismatch when equity_data contains extra keys.
            row = {k: equity_data.get(k, "") for k in fieldnames}
            writer.writerow(row)

    def get_recent_trades(self, limit: int = 50) -> list[dict]:
        """최근 매매 기록 로드."""
        filename = self.log_dir / "trades" / f"trades_{month_str()}.json"
        if not filename.exists():
            return []
        try:
            trades = json.loads(filename.read_text(encoding="utf-8"))
            return trades[-limit:]
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def get_today_trades(self) -> list[dict]:
        """오늘 매매 기록."""
        trades = self.get_recent_trades(limit=100)
        today = date_today()
        return [t for t in trades if t.get("timestamp_open", "").startswith(today)]
