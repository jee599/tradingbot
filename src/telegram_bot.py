"""텔레그램 알림 모듈."""

from __future__ import annotations

import logging
import requests
from src.config import Config

logger = logging.getLogger("xrp_bot")


class TelegramNotifier:
    """텔레그램 봇 알림 발송."""

    def __init__(self):
        self.token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            logger.warning("TELEGRAM: 토큰 또는 채팅 ID 미설정 - 알림 비활성화")

    def send(self, message: str):
        """메시지 발송."""
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.error(f"TELEGRAM: 발송 실패 status={resp.status_code}")
        except Exception as e:
            logger.error(f"TELEGRAM: 발송 에러 - {e}")

    # --- 알림 포맷 ---

    def notify_entry(self, side: str, price: float, qty: float, leverage: int,
                     sl: float, tp: float, sl_pct: float, tp_pct: float,
                     signals: dict, confidence: int):
        """진입 알림."""
        emoji = "🟢" if side == "Buy" else "🔴"
        direction = "LONG" if side == "Buy" else "SHORT"
        sig_icons = {1: "✅", -1: "❌", 0: "⬜"}
        sig_str = " ".join(
            f"{k}{sig_icons.get(v, '⬜')}"
            for k, v in signals.items()
            if k not in ("combined", "confidence")
        )
        msg = (
            f"{emoji} <b>{direction} 진입</b> | XRP @ ${price:.4f}\n"
            f"지표: {sig_str} ({confidence}/4)\n"
            f"수량: {qty:.1f} XRP | 레버: {leverage}x\n"
            f"SL: ${sl:.4f} (-{sl_pct:.1f}%) | TP: ${tp:.4f} (+{tp_pct:.1f}%)"
        )
        self.send(msg)

    def notify_exit(self, exit_reason: str, pnl_pct: float, net_pnl: float,
                    fee_total: float, holding_hours: float):
        """청산 알림."""
        emoji = "✅" if pnl_pct > 0 else "❌"
        sign = "+" if pnl_pct > 0 else ""
        msg = (
            f"{emoji} <b>청산</b> | {exit_reason} {sign}{pnl_pct:.2f}%\n"
            f"순수익: {sign}${net_pnl:.2f} (수수료 ${fee_total:.2f} 차감)\n"
            f"보유: {holding_hours:.1f}시간"
        )
        self.send(msg)

    def notify_daily_summary(self, summary: str):
        """일일 서머리 발송."""
        self.send(summary)

    def notify_warning(self, message: str):
        """경고 알림."""
        self.send(f"⚠️ {message}")

    def notify_critical(self, message: str):
        """긴급 알림."""
        self.send(f"🛑 {message}")

    def format_daily_summary(self, total_equity: float, equity_change_pct: float,
                             realized_pnl: float, unrealized_pnl: float,
                             trades_today: list, current_position: dict | None,
                             stats_7d: dict) -> str:
        """일일 서머리 포맷."""
        today_str = __import__("src.utils", fromlist=["date_today"]).date_today()
        wins = sum(1 for t in trades_today if t.get("net_pnl_pct", 0) > 0)
        losses = len(trades_today) - wins

        trade_lines = []
        for t in trades_today:
            pnl = t.get("net_pnl_pct", 0)
            icon = "✅" if pnl > 0 else "❌"
            sign = "+" if pnl > 0 else ""
            direction = t.get("direction", "")
            reason = t.get("exit_reason", "")
            hours = t.get("holding_hours", 0)
            trade_lines.append(f"  {icon} {direction} {sign}{pnl:.1f}% ({reason}) | 보유 {hours:.1f}h")

        trades_str = "\n".join(trade_lines) if trade_lines else "  매매 없음"

        pos_str = "없음"
        if current_position:
            pos_side = current_position.get("side", "")
            pos_size = current_position.get("size", 0)
            pos_entry = current_position.get("entry_price", 0)
            pos_upnl_pct = current_position.get("unrealized_pnl_pct", 0)
            pos_upnl = current_position.get("unrealized_pnl", 0)
            pos_str = (
                f"{pos_side} {pos_size:.0f} XRP @ ${pos_entry:.4f}\n"
                f"   미실현: {'+' if pos_upnl_pct >= 0 else ''}{pos_upnl_pct:.1f}% "
                f"(${pos_upnl:.2f})"
            )

        eq_sign = "+" if equity_change_pct >= 0 else ""
        wr = stats_7d.get("win_rate", 0)
        avg_win = stats_7d.get("avg_win", 0)
        avg_loss = stats_7d.get("avg_loss", 0)
        pf = stats_7d.get("profit_factor", 0)
        mdd = stats_7d.get("max_drawdown", 0)

        return (
            f"📊 <b>일일 리포트</b> | {today_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 총 자산: ${total_equity:.2f} ({eq_sign}{equity_change_pct:.2f}%)\n"
            f"📈 오늘 실현 손익: {'+' if realized_pnl >= 0 else ''}${realized_pnl:.2f}\n"
            f"📊 미실현 손익: {'+' if unrealized_pnl >= 0 else ''}${unrealized_pnl:.2f}\n\n"
            f"🔄 오늘 매매: {len(trades_today)}회 ({wins}승 {losses}패)\n"
            f"{trades_str}\n\n"
            f"📉 현재 포지션: {pos_str}\n\n"
            f"📊 7일 통계:\n"
            f"  승률: {wr:.0f}%\n"
            f"  평균 수익: +{avg_win:.1f}%\n"
            f"  평균 손실: {avg_loss:.1f}%\n"
            f"  PF: {pf:.2f}\n"
            f"  최대 낙폭: {mdd:.1f}%\n\n"
            f"🔧 시스템: 정상\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
