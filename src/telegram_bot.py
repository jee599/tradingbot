"""텔레그램 알림 + 명령어 수신 모듈."""

from __future__ import annotations

import logging
import requests
from src.config import Config

logger = logging.getLogger("xrp_bot")


class TelegramNotifier:
    """텔레그램 봇 알림 발송 + 명령어 수신."""

    def __init__(self):
        self.token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)
        self.last_update_id: int = 0
        self._command_handler = None  # bot.py에서 설정
        if not self.enabled:
            logger.warning("TELEGRAM: 토큰 또는 채팅 ID 미설정 - 알림 비활성화")
        else:
            # 기존 미처리 메시지 건너뛰기
            self._flush_pending_updates()

    def set_command_handler(self, handler):
        """명령어 핸들러 등록. handler(command, args) -> str 형태."""
        self._command_handler = handler

    def _flush_pending_updates(self):
        """봇 시작 시 밀린 메시지 건너뛰기."""
        try:
            url = f"https://api.telegram.org/bot{self.token}/getUpdates"
            resp = requests.get(url, params={"timeout": 0, "limit": 100}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                updates = data.get("result", [])
                if updates:
                    self.last_update_id = updates[-1]["update_id"]
                    logger.info(f"TELEGRAM: 미처리 메시지 {len(updates)}건 건너뜀")
        except Exception as e:
            logger.error(f"TELEGRAM: flush 에러 - {e}")

    def poll_commands(self):
        """새 메시지 확인 + 명령어 처리."""
        if not self.enabled or not self._command_handler:
            return

        try:
            url = f"https://api.telegram.org/bot{self.token}/getUpdates"
            params = {"offset": self.last_update_id + 1, "timeout": 0, "limit": 10}
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code != 200:
                return

            data = resp.json()
            updates = data.get("result", [])

            for update in updates:
                self.last_update_id = update["update_id"]
                msg = update.get("message", {})
                text = msg.get("text", "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                # 인증된 채팅에서만 명령 수락
                if chat_id != self.chat_id:
                    continue

                if text.startswith("/"):
                    parts = text.split(maxsplit=1)
                    command = parts[0].lower().split("@")[0]  # /command@botname 처리
                    args = parts[1] if len(parts) > 1 else ""
                    logger.info(f"TELEGRAM_CMD: {command} {args}")
                    try:
                        response = self._command_handler(command, args)
                        if response:
                            self.send(response)
                    except Exception as e:
                        logger.error(f"TELEGRAM_CMD_ERROR: {command} - {e}")
                        self.send(f"명령 처리 에러: {e}")

        except Exception as e:
            logger.debug(f"TELEGRAM_POLL: {e}")

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
                     signals: dict, confidence: int, symbol_name: str = "XRP",
                     reason: str = ""):
        """진입 알림."""
        emoji = "\U0001f7e2" if side == "Buy" else "\U0001f534"
        direction = "LONG" if side == "Buy" else "SHORT"
        sig_icons = {1: "\u2705", -1: "\u274c", 0: "\u2b1c"}
        default_icon = "\u2b1c"
        sig_str = " ".join(
            f"{k}{sig_icons.get(v, default_icon)}"
            for k, v in signals.items()
            if k not in ("combined", "confidence")
        )
        reason_line = f"사유: {reason}\n" if reason else ""
        position_value = price * qty
        margin_used = position_value / max(leverage, 1)
        msg = (
            f"{emoji} <b>{direction} 진입</b> | {symbol_name} @ ${price:.4f}\n"
            f"{reason_line}"
            f"지표: {sig_str} ({confidence}/4)\n"
            f"수량: {qty} {symbol_name} | 레버: {leverage}x\n"
            f"투입(추정): ${margin_used:.2f} (포지션 ${position_value:.2f})\n"
            f"SL: ${sl:.4f} (-{sl_pct:.1f}%) | TP: ${tp:.4f} (+{tp_pct:.1f}%)"
        )
        self.send(msg)

    def notify_exit(self, exit_reason: str, pnl_pct: float, net_pnl: float,
                    fee_total: float, holding_hours: float, symbol_name: str = "XRP",
                    detail: str = ""):
        """청산 알림."""
        emoji = "\u2705" if pnl_pct > 0 else "\u274c"
        sign = "+" if pnl_pct > 0 else ""

        reason_map = {
            "TP_HIT": "익절 도달",
            "SL_HIT": "손절 도달",
            "TRAILING_STOP": "트레일링 스탑",
            "TIME_EXIT": "시간 청산",
            "MANUAL_CLOSE": "수동 청산",
            "SERVER_TP": "서버 TP",
            "SERVER_SL": "서버 SL",
            "SERVER_CLOSE": "서버 청산",
        }
        reason_kr = reason_map.get(exit_reason, exit_reason)
        detail_line = f"사유 상세: {detail}\n" if detail else ""

        msg = (
            f"{emoji} <b>{symbol_name} 청산</b> | {reason_kr} {sign}{pnl_pct:.2f}%\n"
            f"{detail_line}"
            f"순수익: {sign}${net_pnl:.2f} (수수료 ${fee_total:.2f} 차감)\n"
            f"보유: {holding_hours:.1f}시간"
        )
        self.send(msg)

    def notify_daily_summary(self, summary: str):
        """일일 서머리 발송."""
        self.send(summary)

    def notify_cancel(self, message: str):
        """진입 취소/차단 알림."""
        self.send(f"\u23ed\ufe0f {message}")

    def notify_warning(self, message: str):
        """경고 알림."""
        self.send(f"\u26a0\ufe0f {message}")

    def notify_critical(self, message: str):
        """긴급 알림."""
        self.send(f"\U0001f6d1 {message}")

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
            icon = "\u2705" if pnl > 0 else "\u274c"
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
            f"\U0001f4ca <b>일일 리포트</b> | {today_str}\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"\U0001f4b0 총 자산: ${total_equity:.2f} ({eq_sign}{equity_change_pct:.2f}%)\n"
            f"\U0001f4c8 오늘 실현 손익: {'+' if realized_pnl >= 0 else ''}${realized_pnl:.2f}\n"
            f"\U0001f4ca 미실현 손익: {'+' if unrealized_pnl >= 0 else ''}${unrealized_pnl:.2f}\n\n"
            f"\U0001f504 오늘 매매: {len(trades_today)}회 ({wins}승 {losses}패)\n"
            f"{trades_str}\n\n"
            f"\U0001f4c9 현재 포지션: {pos_str}\n\n"
            f"\U0001f4ca 7일 통계:\n"
            f"  승률: {wr:.0f}%\n"
            f"  평균 수익: +{avg_win:.1f}%\n"
            f"  평균 손실: {avg_loss:.1f}%\n"
            f"  PF: {pf:.2f}\n"
            f"  최대 낙폭: {mdd:.1f}%\n\n"
            f"\U0001f527 시스템: 정상\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
        )
