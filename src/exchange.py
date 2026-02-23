"""Bybit V5 API 래퍼 모듈."""

from __future__ import annotations

import logging
import time
import pandas as pd
from pybit.unified_trading import HTTP

from src.config import Config

logger = logging.getLogger("xrp_bot")


class BybitExchange:
    """Bybit V5 API 인터페이스."""

    def __init__(self):
        self.client = HTTP(
            testnet=Config.BYBIT_TESTNET,
            api_key=Config.BYBIT_API_KEY,
            api_secret=Config.BYBIT_API_SECRET,
        )
        self.symbol = Config.SYMBOL
        self.category = Config.CATEGORY
        self._setup_leverage()

    def _setup_leverage(self):
        """레버리지 설정."""
        try:
            self.client.set_leverage(
                category=self.category,
                symbol=self.symbol,
                buyLeverage=str(Config.LEVERAGE),
                sellLeverage=str(Config.LEVERAGE),
            )
            logger.info(f"레버리지 설정: {Config.LEVERAGE}x")
        except Exception as e:
            # 이미 같은 레버리지면 에러 무시
            if "leverage not modified" not in str(e).lower() and "110043" not in str(e):
                logger.warning(f"레버리지 설정 실패: {e}")

    def get_klines(self, interval: str = None, limit: int = None) -> pd.DataFrame:
        """캔들스틱 데이터 조회.

        Returns:
            DataFrame with columns: open, high, low, close, volume, timestamp
        """
        interval = interval or Config.INTERVAL
        limit = limit or Config.KLINE_LIMIT

        result = self._api_call(
            self.client.get_kline,
            category=self.category,
            symbol=self.symbol,
            interval=interval,
            limit=limit,
        )

        rows = result.get("list", [])
        if not rows:
            logger.error("KLINE: 데이터 없음")
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"
        ])
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = df[col].astype(float)
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)

        logger.debug(f"KLINE: {len(df)}봉 조회 완료 (최신: {df['timestamp'].iloc[-1]})")
        return df

    def get_balance(self) -> dict:
        """USDT 잔고 조회.

        Returns:
            dict with totalEquity, availableBalance, etc.
        """
        result = self._api_call(
            self.client.get_wallet_balance,
            accountType="UNIFIED",
        )

        accounts = result.get("list", [])
        if not accounts:
            return {"totalEquity": 0, "availableBalance": 0}

        account = accounts[0]
        coins = {c["coin"]: c for c in account.get("coin", [])}
        usdt = coins.get("USDT", {})

        def _f(val):
            """빈 문자열 등 안전한 float 변환."""
            try:
                return float(val) if val else 0.0
            except (ValueError, TypeError):
                return 0.0

        return {
            "totalEquity": _f(account.get("totalEquity", 0)),
            "availableBalance": _f(usdt.get("availableToWithdraw", 0)),
            "totalMarginBalance": _f(account.get("totalMarginBalance", 0)),
            "totalWalletBalance": _f(account.get("totalWalletBalance", 0)),
        }

    def get_position(self) -> dict | None:
        """현재 포지션 조회.

        Returns:
            포지션 dict 또는 None (포지션 없음).
        """
        result = self._api_call(
            self.client.get_positions,
            category=self.category,
            symbol=self.symbol,
        )

        positions = result.get("list", [])
        for pos in positions:
            size = float(pos.get("size", 0))
            if size > 0:
                return {
                    "side": pos.get("side"),  # "Buy" or "Sell"
                    "size": size,
                    "entry_price": float(pos.get("avgPrice", 0)),
                    "unrealized_pnl": float(pos.get("unrealisedPnl", 0)),
                    "leverage": int(float(pos.get("leverage", 1))),
                    "position_value": float(pos.get("positionValue", 0)),
                    "liq_price": float(pos.get("liqPrice", 0)) if pos.get("liqPrice") else 0,
                    "created_time": pos.get("createdTime", ""),
                }
        return None

    def get_ticker(self) -> dict:
        """현재 티커 정보 (최종가, 스프레드 등)."""
        result = self._api_call(
            self.client.get_tickers,
            category=self.category,
            symbol=self.symbol,
        )
        tickers = result.get("list", [])
        if not tickers:
            return {}
        t = tickers[0]
        return {
            "last_price": float(t.get("lastPrice", 0)),
            "bid1": float(t.get("bid1Price", 0)),
            "ask1": float(t.get("ask1Price", 0)),
            "high_24h": float(t.get("highPrice24h", 0)),
            "low_24h": float(t.get("lowPrice24h", 0)),
            "volume_24h": float(t.get("volume24h", 0)),
            "turnover_24h": float(t.get("turnover24h", 0)),
            "price_change_24h_pct": float(t.get("price24hPcnt", 0)) * 100,
            "funding_rate": float(t.get("fundingRate", 0)),
            "open_interest": float(t.get("openInterest", 0)),
        }

    def place_order(self, side: str, qty: float, order_type: str = "Market",
                    price: float = None) -> dict | None:
        """주문 실행.

        Args:
            side: "Buy" 또는 "Sell"
            qty: 주문 수량
            order_type: "Market" 또는 "Limit"
            price: 지정가 (Limit 주문 시 필수)

        Returns:
            주문 결과 dict 또는 None.
        """
        logger.info(f"ORDER: {side} {qty} {self.symbol} ({order_type})"
                     + (f" @ {price}" if price else ""))
        try:
            params = dict(
                category=self.category,
                symbol=self.symbol,
                side=side,
                orderType=order_type,
                qty=str(qty),
            )
            if order_type == "Limit" and price is not None:
                params["price"] = str(price)
                params["timeInForce"] = "GTC"
            result = self._api_call(
                self.client.place_order,
                **params,
            )
            order_id = result.get("orderId", "")
            logger.info(f"ORDER_SUCCESS: {order_id}")
            return result
        except Exception as e:
            logger.critical(f"ORDER_FAILED: {side} {qty} {self.symbol} - {e}")
            return None

    def close_position(self, side: str, qty: float) -> dict | None:
        """포지션 청산 (반대 방향 시장가 + reduceOnly)."""
        close_side = "Sell" if side == "Buy" else "Buy"
        logger.info(f"CLOSE_POSITION: {close_side} {qty} {self.symbol} (reduceOnly)")
        try:
            result = self._api_call(
                self.client.place_order,
                category=self.category,
                symbol=self.symbol,
                side=close_side,
                orderType="Market",
                qty=str(qty),
                reduceOnly=True,
            )
            order_id = result.get("orderId", "")
            logger.info(f"CLOSE_SUCCESS: {order_id}")
            return result
        except Exception as e:
            logger.critical(f"CLOSE_FAILED: {close_side} {qty} {self.symbol} - {e}")
            return None

    def get_orderbook(self) -> dict:
        """호가창 조회."""
        result = self._api_call(
            self.client.get_orderbook,
            category=self.category,
            symbol=self.symbol,
            limit=5,
        )
        bids = [(float(b[0]), float(b[1])) for b in result.get("b", [])]
        asks = [(float(a[0]), float(a[1])) for a in result.get("a", [])]
        spread = asks[0][0] - bids[0][0] if bids and asks else 0
        return {"bids": bids, "asks": asks, "spread": spread}

    def _api_call(self, func, retries: int = 3, **kwargs):
        """API 호출 + 재시도 로직."""
        for attempt in range(1, retries + 1):
            try:
                resp = func(**kwargs)
                if resp.get("retCode") != 0:
                    raise Exception(f"API Error {resp.get('retCode')}: {resp.get('retMsg')}")
                return resp.get("result", {})
            except Exception as e:
                err_str = str(e)
                logger.error(f"API_ERROR: {err_str} - Retrying {attempt}/{retries}")
                if attempt == retries:
                    raise
                time.sleep(1 * attempt)
