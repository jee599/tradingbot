"""포지션 모드 감지 및 positionIdx 매핑 테스트."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from src.config import PositionMode


class TestPositionModeDetection(unittest.TestCase):
    """BybitExchange 포지션 모드 감지 테스트."""

    @patch("src.exchange.Config")
    def _make_exchange(self, positions_list: list, mock_config):
        """Helper: mock client로 BybitExchange 생성."""
        mock_config.BYBIT_TESTNET = True
        mock_config.BYBIT_API_KEY = "test"
        mock_config.BYBIT_API_SECRET = "test"
        mock_config.SYMBOL = "XRPUSDT"
        mock_config.SYMBOLS = ["XRPUSDT"]
        mock_config.CATEGORY = "linear"
        mock_config.LEVERAGE = 1

        with patch("src.exchange.HTTP") as mock_http_cls:
            mock_client = MagicMock()
            mock_http_cls.return_value = mock_client

            # get_positions → 포지션 모드 감지용
            mock_client.get_positions.return_value = {
                "retCode": 0,
                "retMsg": "OK",
                "result": {"list": positions_list},
            }
            # set_leverage → _setup_leverage용
            mock_client.set_leverage.return_value = {
                "retCode": 0,
                "retMsg": "OK",
                "result": {},
            }
            # get_instruments_info → instrument cache용
            mock_client.get_instruments_info.return_value = {
                "retCode": 0,
                "retMsg": "OK",
                "result": {"list": []},
            }

            from src.exchange import BybitExchange
            exc = BybitExchange()
            exc.client = mock_client  # 이후 테스트에서 사용
            return exc

    def test_detect_one_way_mode(self):
        """positionIdx=0 → ONE_WAY 감지."""
        positions = [{"positionIdx": "0", "symbol": "XRPUSDT", "size": "0"}]
        exc = self._make_exchange(positions)
        self.assertEqual(exc.position_mode, PositionMode.ONE_WAY)

    def test_detect_hedge_mode(self):
        """positionIdx=1,2 → HEDGE 감지."""
        positions = [
            {"positionIdx": "1", "symbol": "XRPUSDT", "size": "0"},
            {"positionIdx": "2", "symbol": "XRPUSDT", "size": "0"},
        ]
        exc = self._make_exchange(positions)
        self.assertEqual(exc.position_mode, PositionMode.HEDGE)

    def test_detect_empty_positions_defaults_one_way(self):
        """포지션 없으면 ONE_WAY 기본값."""
        exc = self._make_exchange([])
        self.assertEqual(exc.position_mode, PositionMode.ONE_WAY)


class TestPositionIdx(unittest.TestCase):
    """_get_position_idx 매핑 테스트."""

    @patch("src.exchange.Config")
    def _make_exchange_with_mode(self, mode: PositionMode, mock_config):
        mock_config.BYBIT_TESTNET = True
        mock_config.BYBIT_API_KEY = "test"
        mock_config.BYBIT_API_SECRET = "test"
        mock_config.SYMBOL = "XRPUSDT"
        mock_config.SYMBOLS = ["XRPUSDT"]
        mock_config.CATEGORY = "linear"
        mock_config.LEVERAGE = 1

        with patch("src.exchange.HTTP") as mock_http_cls:
            mock_client = MagicMock()
            mock_http_cls.return_value = mock_client
            mock_client.get_positions.return_value = {
                "retCode": 0, "retMsg": "OK", "result": {"list": []},
            }
            mock_client.set_leverage.return_value = {
                "retCode": 0, "retMsg": "OK", "result": {},
            }

            from src.exchange import BybitExchange
            exc = BybitExchange()
            exc._position_mode = mode
            exc.client = mock_client
            return exc

    def test_one_way_buy(self):
        exc = self._make_exchange_with_mode(PositionMode.ONE_WAY)
        self.assertEqual(exc._get_position_idx("Buy"), 0)

    def test_one_way_sell(self):
        exc = self._make_exchange_with_mode(PositionMode.ONE_WAY)
        self.assertEqual(exc._get_position_idx("Sell"), 0)

    def test_hedge_buy(self):
        exc = self._make_exchange_with_mode(PositionMode.HEDGE)
        self.assertEqual(exc._get_position_idx("Buy"), 1)

    def test_hedge_sell(self):
        exc = self._make_exchange_with_mode(PositionMode.HEDGE)
        self.assertEqual(exc._get_position_idx("Sell"), 2)


class TestOrderParamsMapping(unittest.TestCase):
    """place_order / close_position이 올바른 positionIdx를 전송하는지 테스트."""

    @patch("src.exchange.Config")
    def _make_exchange_with_mode(self, mode: PositionMode, mock_config):
        mock_config.BYBIT_TESTNET = True
        mock_config.BYBIT_API_KEY = "test"
        mock_config.BYBIT_API_SECRET = "test"
        mock_config.SYMBOL = "XRPUSDT"
        mock_config.SYMBOLS = ["XRPUSDT"]
        mock_config.CATEGORY = "linear"
        mock_config.LEVERAGE = 1

        with patch("src.exchange.HTTP") as mock_http_cls:
            mock_client = MagicMock()
            mock_http_cls.return_value = mock_client
            mock_client.get_positions.return_value = {
                "retCode": 0, "retMsg": "OK", "result": {"list": []},
            }
            mock_client.set_leverage.return_value = {
                "retCode": 0, "retMsg": "OK", "result": {},
            }
            mock_client.place_order.return_value = {
                "retCode": 0, "retMsg": "OK",
                "result": {"orderId": "test-order-123"},
            }

            from src.exchange import BybitExchange
            exc = BybitExchange()
            exc._position_mode = mode
            exc.client = mock_client
            return exc

    def test_place_order_one_way_buy(self):
        exc = self._make_exchange_with_mode(PositionMode.ONE_WAY)
        exc.place_order("Buy", 100, symbol="XRPUSDT")
        call_kwargs = exc.client.place_order.call_args[1]
        self.assertEqual(call_kwargs["positionIdx"], 0)
        self.assertEqual(call_kwargs["side"], "Buy")

    def test_place_order_hedge_sell(self):
        exc = self._make_exchange_with_mode(PositionMode.HEDGE)
        exc.place_order("Sell", 50, symbol="XRPUSDT")
        call_kwargs = exc.client.place_order.call_args[1]
        self.assertEqual(call_kwargs["positionIdx"], 2)
        self.assertEqual(call_kwargs["side"], "Sell")

    def test_place_order_hedge_buy(self):
        exc = self._make_exchange_with_mode(PositionMode.HEDGE)
        exc.place_order("Buy", 50, symbol="XRPUSDT")
        call_kwargs = exc.client.place_order.call_args[1]
        self.assertEqual(call_kwargs["positionIdx"], 1)

    def test_close_position_one_way(self):
        """ONE_WAY 청산: reduceOnly=True, positionIdx=0."""
        exc = self._make_exchange_with_mode(PositionMode.ONE_WAY)
        exc.close_position("Buy", 100, symbol="XRPUSDT")
        call_kwargs = exc.client.place_order.call_args[1]
        self.assertEqual(call_kwargs["positionIdx"], 0)
        self.assertEqual(call_kwargs["side"], "Sell")  # 반대
        self.assertTrue(call_kwargs["reduceOnly"])

    def test_close_position_hedge(self):
        """HEDGE 청산: reduceOnly 없음, positionIdx=원래 side 기준."""
        exc = self._make_exchange_with_mode(PositionMode.HEDGE)
        exc.close_position("Buy", 100, symbol="XRPUSDT")
        call_kwargs = exc.client.place_order.call_args[1]
        self.assertEqual(call_kwargs["positionIdx"], 1)  # 원래 Buy side
        self.assertEqual(call_kwargs["side"], "Sell")  # 반대 방향
        self.assertNotIn("reduceOnly", call_kwargs)

    def test_close_position_hedge_short(self):
        """HEDGE 숏 청산: positionIdx=2 (원래 Sell side)."""
        exc = self._make_exchange_with_mode(PositionMode.HEDGE)
        exc.close_position("Sell", 50, symbol="XRPUSDT")
        call_kwargs = exc.client.place_order.call_args[1]
        self.assertEqual(call_kwargs["positionIdx"], 2)
        self.assertEqual(call_kwargs["side"], "Buy")


class TestErrCode10001Retry(unittest.TestCase):
    """ErrCode 10001 발생 시 모드 재감지 + 재시도 테스트."""

    @patch("src.exchange.Config")
    def test_retry_on_position_idx_error(self, mock_config):
        """10001 에러 시 모드 재감지 후 재시도."""
        mock_config.BYBIT_TESTNET = True
        mock_config.BYBIT_API_KEY = "test"
        mock_config.BYBIT_API_SECRET = "test"
        mock_config.SYMBOL = "XRPUSDT"
        mock_config.SYMBOLS = ["XRPUSDT"]
        mock_config.CATEGORY = "linear"
        mock_config.LEVERAGE = 1

        with patch("src.exchange.HTTP") as mock_http_cls:
            mock_client = MagicMock()
            mock_http_cls.return_value = mock_client

            # 초기 감지: ONE_WAY
            mock_client.get_positions.return_value = {
                "retCode": 0, "retMsg": "OK",
                "result": {"list": [{"positionIdx": "0", "symbol": "XRPUSDT", "size": "0"}]},
            }
            mock_client.set_leverage.return_value = {
                "retCode": 0, "retMsg": "OK", "result": {},
            }

            from src.exchange import BybitExchange
            exc = BybitExchange()

            self.assertEqual(exc.position_mode, PositionMode.ONE_WAY)

            # place_order 첫 호출: 10001 에러
            # place_order 재시도 전 get_positions: HEDGE 반환
            # place_order 재시도: 성공
            call_count = [0]
            def side_effect_place_order(**kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return {"retCode": 10001, "retMsg": "position idx not match position mode"}
                return {"retCode": 0, "retMsg": "OK", "result": {"orderId": "retry-ok"}}

            mock_client.place_order.side_effect = side_effect_place_order

            # 재감지 시 HEDGE 반환
            def side_effect_get_positions(**kwargs):
                if call_count[0] >= 1:
                    return {
                        "retCode": 0, "retMsg": "OK",
                        "result": {"list": [
                            {"positionIdx": "1", "symbol": "XRPUSDT", "size": "0"},
                            {"positionIdx": "2", "symbol": "XRPUSDT", "size": "0"},
                        ]},
                    }
                return {
                    "retCode": 0, "retMsg": "OK",
                    "result": {"list": [{"positionIdx": "0", "symbol": "XRPUSDT", "size": "0"}]},
                }

            mock_client.get_positions.side_effect = side_effect_get_positions

            result = exc.place_order("Sell", 50, symbol="XRPUSDT")
            self.assertIsNotNone(result)
            self.assertEqual(exc.position_mode, PositionMode.HEDGE)
            # 재시도 시 positionIdx=2 (Sell in HEDGE)
            retry_kwargs = mock_client.place_order.call_args[1]
            self.assertEqual(retry_kwargs["positionIdx"], 2)

    def test_is_position_idx_error(self):
        from src.exchange import BybitExchange
        self.assertTrue(
            BybitExchange._is_position_idx_error(
                Exception("API Error 10001: position idx not match position mode")
            )
        )
        self.assertFalse(
            BybitExchange._is_position_idx_error(
                Exception("API Error 10004: sign error")
            )
        )
        self.assertFalse(
            BybitExchange._is_position_idx_error(
                Exception("API Error 10001: some other error")
            )
        )


if __name__ == "__main__":
    unittest.main()
