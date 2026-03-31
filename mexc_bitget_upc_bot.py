import requests
import time
import hmac
import hashlib
import uuid
import json
import base64
import random
import logging
import sys
from datetime import datetime, timezone
from abc import ABC, abstractmethod

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

import configparser
import os

# === API Keys (Set your keys via config_temp.ini or env vars) ===
config_file_path = os.path.join(os.path.dirname(__file__), 'config_temp.ini')
config = configparser.ConfigParser()
if os.path.exists(config_file_path):
    config.read(config_file_path)

BITGET_API_KEY = os.getenv('BITGET_API_KEY', config.get('bitget', 'api_key', fallback=''))
BITGET_API_SECRET = os.getenv('BITGET_API_SECRET', config.get('bitget', 'api_secret', fallback=''))
BITGET_API_PASSPHRASE = os.getenv('BITGET_API_PASSPHRASE', config.get('bitget', 'api_passphrase', fallback=''))
MEXC_API_KEY = os.getenv('MEXC_API_KEY', config.get('mexc', 'api_key', fallback=''))
MEXC_API_SECRET = os.getenv('MEXC_API_SECRET', config.get('mexc', 'api_secret', fallback=''))

DRY_RUN = False  # Set to False for live trading

class OrderState:
    def __init__(self, order_id, price, quantity, side, exchange):
        self.order_id = order_id
        self.price = price
        self.quantity = quantity
        self.side = side
        self.exchange = exchange
        self.status = 'pending'  # pending, partial, filled, canceled
        self.filled_quantity = 0.0
        self.timestamp = time.time()

    def update_status(self, status, filled_quantity=0.0):
        self.status = status
        self.filled_quantity = filled_quantity

class Exchange(ABC):
    @abstractmethod
    def get_orderbook(self, symbol):
        pass

    @abstractmethod
    def place_order(self, symbol, side, price, quantity, order_type='limit', post_only=False):
        pass

    @abstractmethod
    def cancel_order(self, symbol, order_id):
        pass

    @abstractmethod
    def get_order_status(self, symbol, order_id):
        pass

class BitgetExchange(Exchange):
    BASE_URL = "https://api.bitget.com"

    def __init__(self, api_key, api_secret, api_passphrase):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase

    def _get_timestamp(self):
        return str(int(time.time_ns() // 1_000_000))

    def _sign(self, timestamp, method, path, query_string, body, secret_key):
        msg = timestamp + method.upper() + path
        if query_string:
            msg += "?" + query_string
        msg += body
        signature = hmac.new(secret_key.encode("utf-8"), msg=msg.encode("utf-8"), digestmod=hashlib.sha256).digest()
        return base64.b64encode(signature).decode()

    def get_orderbook(self, symbol):
        url = f"{self.BASE_URL}/api/v2/spot/market/orderbook?symbol={symbol}&type=step0&limit=100"
        response = requests.get(url)
        data = response.json()
        asks = data['data']['asks'][:2]
        return [{'price': float(ask[0]), 'quantity': float(ask[1])} for ask in asks]

    def place_order(self, symbol, side, price, quantity, order_type='limit', post_only=False):
        if DRY_RUN:
            order_id = f"dry-{uuid.uuid4()}"
            logging.info(f"[DRY RUN] Placed {side} order: {order_id} at {price} for {quantity}")
            sys.exit(0)  # Exit after first dry run order
            return order_id

        endpoint = "/api/v2/spot/trade/place-order"
        url = self.BASE_URL + endpoint
        method = "POST"
        query_string = ""

        # Bitget: price は 0.0001 刻み、size は 0.01 刻み（checkBDScale 2）
        price = max(price, 0.0001)
        price = round(price / 0.0001) * 0.0001
        quantity = max(quantity, 0.01)
        quantity = round(quantity, 2)

        body_dict = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "price": f"{price:.4f}",
            "size": f"{quantity:.2f}",
            "clientOid": str(uuid.uuid4()),
            "force": "postOnly" if post_only else "gtc"
        }

        body = json.dumps(body_dict, separators=(',', ':'))
        timestamp = self._get_timestamp()
        signature = self._sign(timestamp, method, endpoint, query_string, body, self.api_secret)
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-PASSPHRASE": self.api_passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
            "locale": "en-US"
        }
        response = requests.post(url, headers=headers, data=body)
        resp_json = response.json()
        if resp_json.get("code") == "00000":
            return resp_json['data']['orderId']
        else:
            logging.error(f"Order placement failed: {resp_json}")
            return None

    def cancel_order(self, symbol, order_id):
        if DRY_RUN:
            logging.info(f"[DRY RUN] Canceled order: {order_id}")
            return

        endpoint = "/api/v2/spot/trade/cancel-order"
        url = self.BASE_URL + endpoint
        method = "POST"
        query_string = ""
        body_dict = {"symbol": symbol, "orderId": order_id}
        body = json.dumps(body_dict, separators=(',', ':'))
        timestamp = self._get_timestamp()
        signature = self._sign(timestamp, method, endpoint, query_string, body, self.api_secret)
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-PASSPHRASE": self.api_passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
            "locale": "en-US"
        }
        response = requests.post(url, headers=headers, data=body)
        logging.info(f"Cancel response: {response.json()}")

    def get_order_status(self, symbol, order_id):
        if DRY_RUN:
            # Simulate fill after random time
            if random.random() > 0.5:
                return {'status': 'filled', 'filled_quantity': 1.0}  # Mock
            return {'status': 'pending'}

        # Implement actual API call for order status
        # For simplicity, assume filled if called
        return {'status': 'filled', 'filled_quantity': 1.0}

class MexcExchange(Exchange):
    BASE_URL = "https://api.mexc.com"

    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret

    def _generate_signature(self, query_string):
        """HMAC SHA256署名生成"""
        return hmac.new(self.api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

    def _sign_request(self, params):
        """パラメータをソートし署名を作成"""
        from urllib.parse import urlencode
        query_string = urlencode(sorted(params.items()))
        signature = self._generate_signature(query_string)
        return signature

    def _get_server_time(self):
        """サーバー時刻取得"""
        try:
            response = requests.get(f"{self.BASE_URL}/api/v3/time")
            response.raise_for_status()
            data = response.json()
            return data.get("serverTime", int(time.time() * 1000))
        except Exception as e:
            logging.error(f"Failed to get server time: {e}")
            return int(time.time() * 1000)

    def get_orderbook(self, symbol):
        url = f"{self.BASE_URL}/api/v3/depth?symbol={symbol}&limit=100"
        response = requests.get(url)
        data = response.json()
        asks = data['asks'][:2]
        return [{'price': float(ask[0]), 'quantity': float(ask[1])} for ask in asks]

    def place_order(self, symbol, side, price, quantity, order_type='limit', post_only=False):
        if DRY_RUN:
            order_id = f"dry-{uuid.uuid4()}"
            logging.info(f"[DRY RUN] Placed {side} order: {order_id} at {price} for {quantity}")
            sys.exit(0)  # Exit after first dry run order
            return order_id

        try:
            timestamp = int(time.time() * 1000)
            trade_type = "BUY" if side.lower() == 'buy' else "SELL"

            # 最小値で補正、かつtick size/lot sizeに合わせる
            tick_size = 0.0001  # 価格単位
            lot_size = 0.01     # 数量最小
            price = max(price, tick_size)
            price = round(price / tick_size) * tick_size  # tick sizeに丸め

            quantity = max(quantity, lot_size)
            quantity = round(quantity / lot_size) * lot_size  # lot sizeに丸め

            params = {
                "symbol": symbol,
                "price": f"{price:.6f}",
                "quantity": f"{quantity:.6f}",
                "side": trade_type,
                "type": "LIMIT",
                "timestamp": timestamp
            }

            from urllib.parse import urlencode
            query_string = urlencode(sorted(params.items()))
            signature = self._generate_signature(query_string)

            url = f"{self.BASE_URL}/api/v3/order?{query_string}&signature={signature}"
            headers = {
                "x-mexc-apikey": self.api_key,
                "Content-Type": "application/json"
            }

            response = requests.post(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            if "orderId" in data:
                return str(data["orderId"])
            else:
                logging.error(f"MEXC Order placement failed: {data}")
                return None

        except requests.RequestException as e:
            logging.error(f"Order creation failed: {e}")
            return None

    def cancel_order(self, symbol, order_id):
        if DRY_RUN:
            logging.info(f"[DRY RUN] Canceled order: {order_id}")
            return

        try:
            timestamp = int(time.time() * 1000)
            params = {
                "symbol": symbol,
                "orderId": order_id,
                "timestamp": timestamp
            }
            from urllib.parse import urlencode
            query_string = urlencode(sorted(params.items()))
            signature = self._generate_signature(query_string)
            url = f"{self.BASE_URL}/api/v3/order?{query_string}&signature={signature}"
            headers = {"x-mexc-apikey": self.api_key}
            response = requests.delete(url, headers=headers)
            response.raise_for_status()
            logging.info(f"Cancel response: {response.json()}")
        except requests.RequestException as e:
            logging.error(f"Failed to cancel order: {e}")

    def get_order_status(self, symbol, order_id):
        if DRY_RUN:
            if random.random() > 0.5:
                return {'status': 'filled', 'filled_quantity': 1.0}
            return {'status': 'pending'}

        try:
            timestamp = int(time.time() * 1000)
            params = {
                "symbol": symbol,
                "orderId": order_id,
                "timestamp": timestamp
            }
            from urllib.parse import urlencode
            query_string = urlencode(sorted(params.items()))
            signature = self._generate_signature(query_string)
            url = f"{self.BASE_URL}/api/v3/order?{query_string}&signature={signature}"
            headers = {"x-mexc-apikey": self.api_key}
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            status = data.get("status", "unknown").lower()
            filled_quantity = float(data.get("executedQty", 0))
            return {'status': status, 'filled_quantity': filled_quantity}
        except requests.RequestException as e:
            logging.error(f"Failed to get order status: {e}")
            return {'status': 'unknown', 'filled_quantity': 0}

class TradingBot:
    def __init__(self):
        self.bitget = BitgetExchange(BITGET_API_KEY, BITGET_API_SECRET, BITGET_API_PASSPHRASE)
        self.mexc = MexcExchange(MEXC_API_KEY, MEXC_API_SECRET)
        self.symbol = 'UPCUSDT'
        self.current_orders = []  # List of OrderState
        self.start_time = time.time()

    def get_cheaper_exchange(self):
        bitget_asks = self.bitget.get_orderbook(self.symbol)
        mexc_asks = self.mexc.get_orderbook(self.symbol)
        bitget_price = bitget_asks[0]['price']
        mexc_price = mexc_asks[0]['price']
        logging.info(f"Bitget price: {bitget_price}, MEXC price: {mexc_price}")
        if bitget_price < mexc_price:
            return self.bitget, bitget_asks, mexc_price - bitget_price
        else:
            return self.mexc, mexc_asks, bitget_price - mexc_price

    def check_conditions(self, exchange, asks, price_diff):
        first_ask_qty = asks[0]['quantity']
        return first_ask_qty < 1500 and price_diff >= 0.0000

    def place_initial_order(self, exchange, asks):
        price = asks[1]['price']
        quantity = asks[0]['quantity'] + 0.01

        # Ensure each order is at least $1 USD equivalent
        usd_value = price * quantity
        if usd_value < 1.0:
            min_qty = 1.0 / price
            quantity = round(max(min_qty, quantity), 6)
            # align to assumed lot size 0.01 quantity step
            quantity = round(quantity / 0.01) * 0.01

        order_id = exchange.place_order(self.symbol, 'buy', price, quantity)
        if order_id:
            self.current_orders.append(OrderState(order_id, price, quantity, 'buy', exchange))
        return order_id

    def place_postonly_order(self, exchange, asks):
        avg_price = (asks[0]['price'] + asks[1]['price']) / 2
        postonly_price = round(avg_price + 0.001, 3)  # Ceil to 3rd decimal
        usd_equivalent_qty = 1 / postonly_price
        quantity = usd_equivalent_qty + 0.01

        # Ensure each order is at least $1 USD equivalent
        usd_value = postonly_price * quantity
        if usd_value < 1.0:
            min_qty = 1.0 / postonly_price
            quantity = round(max(min_qty, quantity), 6)
            quantity = round(quantity / 0.01) * 0.01

        order_id = exchange.place_order(self.symbol, 'buy', postonly_price, quantity, post_only=True)
        if order_id:
            self.current_orders.append(OrderState(order_id, postonly_price, quantity, 'buy', exchange))
        return order_id

    def check_orders(self):
        for order in self.current_orders[:]:
            status = order.exchange.get_order_status(self.symbol, order.order_id)
            order.update_status(status['status'], status.get('filled_quantity', 0))
            if order.status in ['filled', 'partial']:
                logging.info(f"Order {order.order_id} {order.status}")
            if order.status == 'filled':
                self.current_orders.remove(order)

    def cancel_pending_orders(self):
        for order in self.current_orders:
            if order.status == 'pending':
                order.exchange.cancel_order(self.symbol, order.order_id)
                order.update_status('canceled')
        self.current_orders.clear()

    def run(self):
        while True:
            try:
                if time.time() - self.start_time > 600:  # 10 minutes
                    self.cancel_pending_orders()
                    self.start_time = time.time()

                self.check_orders()

                if not self.current_orders:  # No pending orders
                    exchange, asks, price_diff = self.get_cheaper_exchange()
                    if self.check_conditions(exchange, asks, price_diff):
                        self.place_initial_order(exchange, asks)
                        # Wait for fill, then place postonly (simplified)
                        time.sleep(5)  # Simulate wait
                        if not self.current_orders:  # If initial filled
                            self.place_postonly_order(exchange, asks)

                time.sleep(1)  # Polling interval
            except Exception as e:
                logging.error(f"Error: {e}")
                time.sleep(5)

if __name__ == "__main__":
    bot = TradingBot()
    bot.run()