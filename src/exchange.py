"""Bybit V5 API 래퍼 모듈 (멀티심볼 지원)."""

from __future__ import annotations

import logging
import time
import pandas as pd
from pybit.unified_trading import HTTP

from src.config import Config, PositionMode
from src.utils import round_price

logger = logging.getLogger("xrp_bot")


class BybitExchange:
    """Bybit V5 API 인터페이스 (멀티심볼)."""

    def __init__(self):
        self.client = HTTP(
            testnet=Config.BYBIT_TESTNET,
            api_key=Config.BYBIT_API_KEY,
            api_secret=Config.BYBIT_API_SECRET,
        )
        self.symbol = Config.SYMBOL  # 하위 호환
        self.category = Config.CATEGORY
        self._instrument_cache: dict[str, dict] = {}
        self._position_mode: PositionMode | None = None
        self._detect_position_mode()
        self._setup_leverage()

    def _detect_position_mode(self):
        """Bybit 포지션 모드 감지 (One-Way vs Hedge).

        get_positions 응답의 positionIdx 필드로 판별:
          - positionIdx=0 → ONE_WAY
          - positionIdx=1 or 2 → HEDGE
        """
        try:
            result = self._api_call(
                self.client.get_positions,
                category=self.category,
                symbol=Config.SYMBOLS[0],
            )
            positions = result.get("list", [])
            for pos in positions:
                idx = int(pos.get("positionIdx", 0))
                if idx in (1, 2):
                    self._position_mode = PositionMode.HEDGE
                    logger.info(f"POSITION_MODE: Hedge (양방향) 감지")
                    return
            self._position_mode = PositionMode.ONE_WAY
            logger.info(f"POSITION_MODE: One-Way (단방향) 감지")
        except Exception as e:
            self._position_mode = PositionMode.ONE_WAY
            logger.warning(f"POSITION_MODE: 감지 실패, One-Way로 기본 설정 - {e}")

    @property
    def position_mode(self) -> PositionMode:
        if self._position_mode is None:
            self._detect_position_mode()
        return self._position_mode

    def _get_position_idx(self, side: str) -> int:
        """주문 side에 맞는 positionIdx 반환.

        ONE_WAY: 0
        HEDGE: Buy(Long)=1, Sell(Short)=2
        """
        if self.position_mode == PositionMode.HEDGE:
            return 1 if side == "Buy" else 2
        return 0

    def _setup_leverage(self):
        """모든 심볼에 레버리지 설정."""
        for sym in Config.SYMBOLS:
            self.setup_leverage(sym)

    def setup_leverage(self, symbol: str = None, leverage: int = None):
        """개별 심볼 레버리지 설정."""
        symbol = symbol or self.symbol
        leverage = leverage or Config.LEVERAGE
        try:
            self.client.set_leverage(
                category=self.category,
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
            logger.info(f"레버리지 설정: {symbol} {leverage}x")
        except Exception as e:
            if "leverage not modified" not in str(e).lower() and "110043" not in str(e):
                logger.warning(f"레버리지 설정 실패 [{symbol}]: {e}")

    def get_instrument_info(self, symbol: str = None) -> dict:
        """심볼의 수량/가격 정밀도 조회 (캐시).

        Returns:
            {"qty_step": float, "min_qty": float, "tick_size": float}
        """
        symbol = symbol or self.symbol
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        try:
            result = self._api_call(
                self.client.get_instruments_info,
                category=self.category,
                symbol=symbol,
            )
            instruments = result.get("list", [])
            if instruments:
                inst = instruments[0]
                lot_filter = inst.get("lotSizeFilter", {})
                price_filter = inst.get("priceFilter", {})
                info = {
                    "qty_step": float(lot_filter.get("qtyStep", "1")),
                    "min_qty": float(lot_filter.get("minOrderQty", "1")),
                    "tick_size": float(price_filter.get("tickSize", "0.0001")),
                }
                self._instrument_cache[symbol] = info
                logger.info(f"INSTRUMENT [{symbol}]: qty_step={info['qty_step']}, tick={info['tick_size']}")
                return info
        except Exception as e:
            logger.error(f"INSTRUMENT_INFO_ERROR [{symbol}]: {e}")

        fallback = {"qty_step": 1.0, "min_qty": 1.0, "tick_size": 0.0001}
        self._instrument_cache[symbol] = fallback
        return fallback

    def get_klines(self, interval: str = None, limit: int = None,
                   symbol: str = None) -> pd.DataFrame:
        """캔들스틱 데이터 조회."""
        symbol = symbol or self.symbol
        interval = interval or Config.INTERVAL
        limit = limit or Config.KLINE_LIMIT

        result = self._api_call(
            self.client.get_kline,
            category=self.category,
            symbol=symbol,
            interval=interval,
            limit=limit,
        )

        rows = result.get("list", [])
        if not rows:
            logger.error(f"KLINE [{symbol}]: 데이터 없음")
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"
        ])
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = df[col].astype(float)
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)

        logger.debug(f"KLINE [{symbol}]: {len(df)}봉 조회 완료")
        return df

    def get_balance(self) -> dict:
        """USDT 잔고 조회."""
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

    def get_position(self, symbol: str = None) -> dict | None:
        """현재 포지션 조회."""
        symbol = symbol or self.symbol
        result = self._api_call(
            self.client.get_positions,
            category=self.category,
            symbol=symbol,
        )

        positions = result.get("list", [])
        for pos in positions:
            size = float(pos.get("size", 0))
            if size > 0:
                return {
                    "symbol": symbol,
                    "side": pos.get("side"),
                    "size": size,
                    "entry_price": float(pos.get("avgPrice", 0)),
                    "unrealized_pnl": float(pos.get("unrealisedPnl", 0)),
                    "leverage": int(float(pos.get("leverage", 1))),
                    "position_value": float(pos.get("positionValue", 0)),
                    "liq_price": float(pos.get("liqPrice", 0)) if pos.get("liqPrice") else 0,
                    "created_time": pos.get("createdTime", ""),
                }
        return None

    def get_ticker(self, symbol: str = None) -> dict:
        """현재 티커 정보."""
        symbol = symbol or self.symbol
        result = self._api_call(
            self.client.get_tickers,
            category=self.category,
            symbol=symbol,
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
                    price: float = None, symbol: str = None) -> dict | None:
        """주문 실행 (positionIdx 자동 설정)."""
        symbol = symbol or self.symbol
        logger.info(f"ORDER [{symbol}]: {side} {qty} ({order_type})"
                     + (f" @ {price}" if price else ""))
        try:
            params = dict(
                category=self.category,
                symbol=symbol,
                side=side,
                orderType=order_type,
                qty=str(qty),
                positionIdx=self._get_position_idx(side),
            )
            if order_type == "Limit" and price is not None:
                params["price"] = str(price)
                params["timeInForce"] = "GTC"
            result = self._api_call(
                self.client.place_order,
                **params,
            )
            order_id = result.get("orderId", "")
            logger.info(f"ORDER_SUCCESS [{symbol}]: {order_id}")
            return result
        except Exception as e:
            if self._is_position_idx_error(e):
                return self._retry_with_refreshed_mode(
                    side=side, qty=qty, order_type=order_type,
                    price=price, symbol=symbol,
                )
            logger.critical(f"ORDER_FAILED [{symbol}]: {side} {qty} - {e}")
            return None

    def close_position(self, side: str, qty: float, symbol: str = None) -> dict | None:
        """포지션 청산 (반대 방향 시장가).

        ONE_WAY: reduceOnly=True, positionIdx=0
        HEDGE: reduceOnly 불필요 (positionIdx가 포지션을 특정), positionIdx=원래 side 기준
        """
        symbol = symbol or self.symbol
        close_side = "Sell" if side == "Buy" else "Buy"
        # Hedge 모드: positionIdx는 청산할 포지션의 방향 (원래 side 기준)
        pos_idx = self._get_position_idx(side)
        logger.info(f"CLOSE [{symbol}]: {close_side} {qty} (positionIdx={pos_idx})")
        try:
            params = dict(
                category=self.category,
                symbol=symbol,
                side=close_side,
                orderType="Market",
                qty=str(qty),
                positionIdx=pos_idx,
            )
            # ONE_WAY 모드에서만 reduceOnly 사용
            if self.position_mode == PositionMode.ONE_WAY:
                params["reduceOnly"] = True
            result = self._api_call(
                self.client.place_order,
                **params,
            )
            order_id = result.get("orderId", "")
            logger.info(f"CLOSE_SUCCESS [{symbol}]: {order_id}")
            return result
        except Exception as e:
            if self._is_position_idx_error(e):
                # 모드 재감지 후 재시도
                self._detect_position_mode()
                close_side2 = "Sell" if side == "Buy" else "Buy"
                pos_idx2 = self._get_position_idx(side)
                logger.info(f"CLOSE_RETRY [{symbol}]: {close_side2} {qty} (positionIdx={pos_idx2})")
                try:
                    params2 = dict(
                        category=self.category,
                        symbol=symbol,
                        side=close_side2,
                        orderType="Market",
                        qty=str(qty),
                        positionIdx=pos_idx2,
                    )
                    if self.position_mode == PositionMode.ONE_WAY:
                        params2["reduceOnly"] = True
                    result = self._api_call(self.client.place_order, **params2)
                    logger.info(f"CLOSE_RETRY_SUCCESS [{symbol}]: {result.get('orderId', '')}")
                    return result
                except Exception as e2:
                    logger.critical(f"CLOSE_RETRY_FAILED [{symbol}]: {e2}")
                    return None
            logger.critical(f"CLOSE_FAILED [{symbol}]: {close_side} {qty} - {e}")
            return None

    def set_trading_stop(self, sl_price: float, tp_price: float,
                         symbol: str = None, side: str = None) -> bool:
        """서버사이드 SL/TP 설정."""
        symbol = symbol or self.symbol
        info = self.get_instrument_info(symbol)
        tick = info["tick_size"]
        pos_idx = self._get_position_idx(side) if side else 0
        try:
            self._api_call(
                self.client.set_trading_stop,
                category=self.category,
                symbol=symbol,
                stopLoss=str(round_price(sl_price, tick)),
                takeProfit=str(round_price(tp_price, tick)),
                tpslMode="Full",
                slTriggerBy="LastPrice",
                tpTriggerBy="LastPrice",
                positionIdx=pos_idx,
            )
            logger.info(f"TRADING_STOP [{symbol}]: SL=${sl_price:.4f} TP=${tp_price:.4f}")
            return True
        except Exception as e:
            logger.error(f"TRADING_STOP_ERROR [{symbol}]: {e}")
            return False

    def update_stop_loss(self, sl_price: float, symbol: str = None,
                         side: str = None) -> bool:
        """서버사이드 SL만 업데이트."""
        symbol = symbol or self.symbol
        info = self.get_instrument_info(symbol)
        tick = info["tick_size"]
        pos_idx = self._get_position_idx(side) if side else 0
        try:
            self._api_call(
                self.client.set_trading_stop,
                category=self.category,
                symbol=symbol,
                stopLoss=str(round_price(sl_price, tick)),
                tpslMode="Full",
                slTriggerBy="LastPrice",
                positionIdx=pos_idx,
            )
            logger.debug(f"SL_UPDATE [{symbol}]: SL=${sl_price:.4f}")
            return True
        except Exception as e:
            logger.error(f"SL_UPDATE_ERROR [{symbol}]: {e}")
            return False

    def get_orderbook(self, symbol: str = None) -> dict:
        """호가창 조회."""
        symbol = symbol or self.symbol
        result = self._api_call(
            self.client.get_orderbook,
            category=self.category,
            symbol=symbol,
            limit=5,
        )
        bids = [(float(b[0]), float(b[1])) for b in result.get("b", [])]
        asks = [(float(a[0]), float(a[1])) for a in result.get("a", [])]
        spread = asks[0][0] - bids[0][0] if bids and asks else 0
        return {"bids": bids, "asks": asks, "spread": spread}

    @staticmethod
    def _is_position_idx_error(exc: Exception) -> bool:
        """ErrCode 10001 (position idx not match) 여부 확인."""
        msg = str(exc)
        return "10001" in msg and "position" in msg.lower()

    def _retry_with_refreshed_mode(self, side: str, qty: float,
                                   order_type: str, price: float | None,
                                   symbol: str) -> dict | None:
        """positionIdx 에러 시 모드 재감지 후 1회 재시도."""
        logger.warning(
            f"ORDER_RETRY [{symbol}]: ErrCode 10001 감지 → 포지션 모드 재확인 중..."
        )
        logger.warning(
            "TIP: Bybit 앱/웹 > 파생상품 > 설정 > 포지션 모드에서 "
            "One-Way 또는 Hedge 모드를 확인/변경할 수 있습니다."
        )
        self._detect_position_mode()
        try:
            params = dict(
                category=self.category,
                symbol=symbol,
                side=side,
                orderType=order_type,
                qty=str(qty),
                positionIdx=self._get_position_idx(side),
            )
            if order_type == "Limit" and price is not None:
                params["price"] = str(price)
                params["timeInForce"] = "GTC"
            result = self._api_call(self.client.place_order, **params)
            logger.info(f"ORDER_RETRY_SUCCESS [{symbol}]: {result.get('orderId', '')}")
            return result
        except Exception as e2:
            logger.critical(f"ORDER_RETRY_FAILED [{symbol}]: {side} {qty} - {e2}")
            return None

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
                # positionIdx 에러는 재시도해도 동일 → 즉시 상위로 전파
                if self._is_position_idx_error(e):
                    raise
                logger.error(f"API_ERROR: {err_str} - Retrying {attempt}/{retries}")
                if attempt == retries:
                    raise
                time.sleep(1 * attempt)
