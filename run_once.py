#!/usr/bin/env python3
"""봇 1회 사이클 테스트 실행."""

from __future__ import annotations

from src.config import Config
from src.exchange import BybitExchange
from src.indicators import calc_all_indicators
from src.strategy import generate_signals
from src.risk_manager import RiskManager
from src.position import PositionManager
from src.logger import BotLogger
from src.telegram_bot import TelegramNotifier
from src.utils import pct_change

print("🚀 XRP 자동매매 봇 - 테스트넷 1회 실행")
print("=" * 50)

# 초기화
bot_logger = BotLogger()
notifier = TelegramNotifier()
exchange = BybitExchange()
risk_mgr = RiskManager(bot_logger)
pos_mgr = PositionManager(exchange, risk_mgr, bot_logger, notifier)

# 1. 잔고
bal = exchange.get_balance()
print(f"💰 잔고: ${bal['totalEquity']:.2f} USDT")
print()

# 2. 티커
ticker = exchange.get_ticker()
price = ticker["last_price"]
print(f"📊 XRP 현재가: ${price}")
print(f"   24h 변동: {ticker['price_change_24h_pct']:.2f}%")
print()

# 3. 캔들 + 지표 계산
print("📈 300봉 데이터 조회 + 지표 계산 중...")
df = exchange.get_klines()
df = calc_all_indicators(df)
row = df.iloc[-1]
print(f"   EMA20: {row['ema20']:.4f} | EMA50: {row['ema50']:.4f}")
print(f"   RSI: {row['rsi']:.1f} | ADX: {row['adx']:.1f}")
print(f"   BB%: {row['bb_pct']:.2f} | BB폭: {row['bb_width']:.4f}")
print(f"   4H EMA20: {row['ema20_4h']:.4f} | 4H EMA50: {row['ema50_4h']:.4f}")
print(f"   거래량 비율: {row['volume_ratio']:.2f}")
print()

# 4. 시그널 생성
signals = generate_signals(df)
sig_icons = {1: "✅ 롱", -1: "❌ 숏", 0: "⬜ 중립"}
print("📡 시그널:")
for name in ["MA", "RSI", "BB", "MTF"]:
    s = signals[name]
    print(f"   {name}: {sig_icons[s['value']]} - {s['reason']}")
print(f"   ▶ 종합: {signals['signal_detail']}")
print()

# 5. 필터 체크
filters = risk_mgr.check_entry_filters(df, pos_mgr.has_position())
passed_str = "통과 ✅" if filters["passed"] else "차단 ❌"
print(f"🛡️ 필터: {passed_str}")
print(f"   최근 손절: {filters['recent_sl']} | 저거래량: {filters['low_volume']} | 포지션: {filters['already_in_position']}")
print()

# 6. 매매 실행
combined = signals["combined_signal"]
confidence = signals["confidence"]

if combined != 0 and filters["passed"]:
    can_trade, reason = risk_mgr.can_trade()
    if can_trade:
        equity = bal["totalEquity"]
        margin = risk_mgr.calc_position_size(equity, confidence)
        side = "Buy" if combined == 1 else "Sell"
        direction = "🟢 LONG" if combined == 1 else "🔴 SHORT"

        print(f"⚡ {direction} 진입!")
        print(f"   마진: ${margin:.2f} x {Config.LEVERAGE}x = ${margin * Config.LEVERAGE:.2f} 포지션")
        print(f"   확신도: {confidence}/4")

        success = pos_mgr.open_position(
            side=side,
            margin_usdt=margin,
            current_price=price,
            signals=signals,
            indicators={
                "ema20": round(row["ema20"], 6),
                "ema50": round(row["ema50"], 6),
                "rsi": round(row["rsi"], 2),
                "bb_pct": round(row["bb_pct"], 4),
                "adx": round(row["adx"], 2),
            },
        )

        if success:
            print("   ✅ 진입 성공!")
            pos = exchange.get_position()
            if pos:
                print(f"   포지션: {pos['side']} {pos['size']} XRP @ ${pos['entry_price']}")
        else:
            print("   ❌ 진입 실패")
    else:
        print(f"⏸️ 매매 차단: {reason}")
elif combined == 0:
    print("⏸️ 시그널 없음 → 대기")
else:
    print("⏸️ 필터 미통과 → 대기")

print()
print("=" * 50)

# 최종 잔고
bal2 = exchange.get_balance()
print(f"💰 최종 잔고: ${bal2['totalEquity']:.2f} USDT")
pos2 = exchange.get_position()
if pos2:
    upnl = pct_change(pos2["entry_price"], price, pos2["side"])
    print(f"📍 포지션: {pos2['side']} {pos2['size']} XRP | 미실현: {upnl:+.2f}%")
else:
    print("📍 포지션: 없음")
