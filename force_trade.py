#!/usr/bin/env python3
"""강제 매매 테스트 (BTC, 텔레그램 알림 포함)."""

from __future__ import annotations

import time
from pybit.unified_trading import HTTP

from src.config import Config
from src.exchange import BybitExchange
from src.logger import BotLogger
from src.telegram_bot import TelegramNotifier

print("⚡ 강제 매매 테스트 (텔레그램 알림 포함)")
print("=" * 50)

bot_logger = BotLogger()
notifier = TelegramNotifier()
exchange = BybitExchange()

client = HTTP(
    testnet=True,
    api_key=Config.BYBIT_API_KEY,
    api_secret=Config.BYBIT_API_SECRET,
)

# 레버리지
try:
    client.set_leverage(category="linear", symbol="BTCUSDT", buyLeverage="1", sellLeverage="1")
except Exception:
    pass

# 잔고 + 시세
bal = exchange.get_balance()
ticker = client.get_tickers(category="linear", symbol="BTCUSDT")
btc_price = float(ticker["result"]["list"][0]["lastPrice"])
print(f"잔고: ${bal['totalEquity']:.2f}")
print(f"BTC 현재가: ${btc_price:,.0f}")
print()

# --- 숏 진입 ---
qty = 0.001
print(f"🔴 SHORT 진입: Sell {qty} BTC")
result = client.place_order(
    category="linear", symbol="BTCUSDT",
    side="Sell", orderType="Market", qty=str(qty),
)

if result["retCode"] != 0:
    print(f"❌ 주문 실패: {result['retMsg']}")
    exit()

oid = result["result"]["orderId"]
print(f"   주문 접수: {oid}")
time.sleep(2)

# 체결 확인
order = client.get_order_history(category="linear", symbol="BTCUSDT", orderId=oid)
o = order["result"]["list"][0]
entry_price = float(o["avgPrice"])
print(f"   ✅ 체결: Sell {qty} BTC @ ${entry_price:,.2f}")

# 텔레그램 진입 알림
sl = entry_price * 1.02
tp = entry_price * 0.96
notifier.notify_entry(
    side="Sell", price=entry_price, qty=qty * entry_price,
    leverage=1, sl=sl, tp=tp, sl_pct=2.0, tp_pct=4.0,
    signals={"MA": 0, "RSI": -1, "BB": -1, "MTF": 0},
    confidence=2,
)
print("   📱 텔레그램 진입 알림 발송!")
print()

# --- 5초 대기 ---
print("⏳ 5초 대기 후 청산...")
time.sleep(5)

# --- 청산 ---
print("🟢 청산: Buy 0.001 BTC (reduceOnly)")
close = client.place_order(
    category="linear", symbol="BTCUSDT",
    side="Buy", orderType="Market", qty=str(qty),
    reduceOnly=True,
)

if close["retCode"] != 0:
    print(f"❌ 청산 실패: {close['retMsg']}")
    exit()

coid = close["result"]["orderId"]
time.sleep(2)

co = client.get_order_history(category="linear", symbol="BTCUSDT", orderId=coid)
corder = co["result"]["list"][0]
exit_price = float(corder["avgPrice"])

pnl_pct = ((entry_price - exit_price) / entry_price) * 100
pnl_usdt = (entry_price - exit_price) * qty
fee = (entry_price + exit_price) * qty * 0.00055
net_pnl = pnl_usdt - fee

print(f"   ✅ 체결: Buy {qty} BTC @ ${exit_price:,.2f}")
print(f"   PnL: {pnl_pct:+.3f}% (${net_pnl:+.4f})")

# 텔레그램 청산 알림
notifier.notify_exit(
    exit_reason="TEST_CLOSE",
    pnl_pct=pnl_pct,
    net_pnl=net_pnl,
    fee_total=fee,
    holding_hours=0.0,
)
print("   📱 텔레그램 청산 알림 발송!")
print()

# 최종 잔고
bal2 = exchange.get_balance()
diff = bal2["totalEquity"] - bal["totalEquity"]
print("=" * 50)
print(f"💰 최종 잔고: ${bal2['totalEquity']:.2f} (변동: ${diff:+.4f})")
