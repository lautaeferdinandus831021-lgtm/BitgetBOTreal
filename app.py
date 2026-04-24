
#!/usr/bin/env python3
"""
BG-BOT v5 — Full-Stack Trading Bot
Backend: Flask + Bitget API + Dual Timeframe Engine
Run: python app.py
Open: http://localhost:5000
"""

import json, os, time, hmac, hashlib, base64, threading
from datetime import datetime
from flask import Flask, render_template, jsonify, request

try:
    import requests as http_requests
    import pandas as pd
    import numpy as np
except ImportError:
    print("="*50)
    print("Install dependencies first:")
    print("  pip install flask pandas numpy requests")
    print("="*50)
    exit(1)

# ═══════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "api_key": "",
    "api_secret": "",
    "api_passphrase": "",
    "demo": True,
    "market_mode": "spot",
    "symbol": "BTCUSDT",
    "order_size": 50,
    "max_positions": 3,
    "tp_percent": 2.5,
    "sl_percent": 1.5,
    "leverage": 3,
    "margin_mode": "crossed",
    "order_type": "market",
    "limit_offset": 0.2,
    "limit_expiry": 300,
    "strategy": "multi_confirm",
    "indicators": {
        "rsi":   {"enabled": True,  "period": 14, "overbought": 70, "oversold": 30},
        "macd":  {"enabled": True,  "fast": 12, "slow": 26, "signal": 9},
        "bb":    {"enabled": False, "period": 20, "std_dev": 2},
        "ema":   {"enabled": False, "fast": 9, "slow": 21},
        "stoch": {"enabled": False, "k_period": 14, "d_period": 3, "smooth": 3},
        "atr":   {"enabled": False, "period": 14, "multiplier": 1.5}
    }
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        # Merge with defaults for any missing keys
        for k, v in DEFAULT_CONFIG.items():
            if k not in cfg:
                cfg[k] = v
            elif isinstance(v, dict):
                for k2, v2 in v.items():
                    if k2 not in cfg[k]:
                        cfg[k][k2] = v2
        return cfg
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ═══════════════════════════════════════════════════════
#  BITGET API CLIENT
# ═══════════════════════════════════════════════════════
class BitgetClient:
    BASE = "https://api.bitget.com"

    def __init__(self, key, secret, passphrase, demo=True):
        self.key = key
        self.secret = secret
        self.passphrase = passphrase
        self.demo = demo
        self.sess = http_requests.Session()

    def _sign(self, ts, method, path, body=""):
        msg = ts + method.upper() + path + body
        mac = hmac.new(
            self.secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode()

    def _headers(self, method, path, body=""):
        ts = str(int(time.time()))
        h = {
            "ACCESS-KEY": self.key,
            "ACCESS-SIGN": self._sign(ts, method, path, body),
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "locale": "en-US"
        }
        if self.demo:
            h["paptrading"] = "1"
        return h

    def _request(self, method, path, params=None, data=None):
        url = self.BASE + path
        body = json.dumps(data) if data else ""
        h = self._headers(method, path, body)
        try:
            r = self.sess.request(
                method, url, headers=h,
                params=params,
                data=body if body else None,
                timeout=10
            )
            return r.json()
        except Exception as e:
            return {"code": "99999", "msg": str(e), "data": None}

    # ─── Market Data ──────────────────────────────
    def get_klines(self, symbol, gran, market="spot", limit=200):
        if market == "spot":
            path = "/api/v2/spot/market/candles"
            params = {"symbol": symbol, "granularity": gran,
                      "limit": str(limit)}
        else:
            path = "/api/v2/mix/market/candles"
            params = {"productType": "USDT-FUTURES",
                      "symbol": symbol, "granularity": gran,
                      "limit": str(limit)}
        result = self._request("GET", path, params)
        if not result or result.get("code") != "00000":
            return None
        try:
            df = pd.DataFrame(result["data"], columns=[
                "timestamp", "open", "high", "low",
                "close", "volume", "quote_volume"
            ])
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = pd.to_numeric(df[c])
            df["timestamp"] = pd.to_datetime(
                df["timestamp"].astype(int), unit="ms"
            )
            return df.sort_values("timestamp").reset_index(drop=True)
        except:
            return None

    # ─── Account ──────────────────────────────────
    def get_balance(self, market="spot"):
        if market == "spot":
            r = self._request("GET", "/api/v2/spot/account/assets",
                              {"coin": "USDT"})
            if r and r.get("data") and r["data"]:
                return float(r["data"][0].get("available", 0))
        else:
            r = self._request("GET",
                              "/api/v2/account/get-account-balance",
                              {"productType": "USDT-FUTURES"})
            if r and r.get("data"):
                return float(r["data"][0].get("available", 0))
        return 0

    # ─── Spot Trading ─────────────────────────────
    def spot_market(self, symbol, side, size):
        data = {"symbol": symbol, "side": side,
                "orderType": "market", "force": "gtc",
                "size": str(size)}
        return self._request("POST",
                             "/api/v2/spot/trade/place-order",
                             data=data)

    def spot_limit(self, symbol, side, price, size):
        data = {"symbol": symbol, "side": side,
                "orderType": "limit", "force": "gtc",
                "price": str(price), "size": str(size)}
        return self._request("POST",
                             "/api/v2/spot/trade/place-order",
                             data=data)

    # ─── Perp Trading ─────────────────────────────
    def set_leverage(self, symbol, lev, hold_side):
        data = {"productType": "USDT-FUTURES", "symbol": symbol,
                "leverage": str(lev), "holdSide": hold_side}
        return self._request("POST",
                             "/api/v2/mix/account/set-leverage",
                             data=data)

    def perp_market(self, symbol, side, size, tp=None, sl=None):
        data = {"productType": "USDT-FUTURES", "symbol": symbol,
                "marginMode": "crossed", "marginCoin": "USDT",
                "size": str(size), "side": side,
                "orderType": "market"}
        if tp: data["presetStopSurplusPrice"] = str(tp)
        if sl: data["presetStopLossPrice"] = str(sl)
        return self._request("POST",
                             "/api/v2/mix/order/place-order",
                             data=data)

    def perp_limit(self, symbol, side, price, size,
                   tp=None, sl=None):
        data = {"productType": "USDT-FUTURES", "symbol": symbol,
                "marginMode": "crossed", "marginCoin": "USDT",
                "size": str(size), "side": side,
                "orderType": "limit", "price": str(price)}
        if tp: data["presetStopSurplusPrice"] = str(tp)
        if sl: data["presetStopLossPrice"] = str(sl)
        return self._request("POST",
                             "/api/v2/mix/order/place-order",
                             data=data)

    def get_positions(self, symbol=None):
        params = {"productType": "USDT-FUTURES"}
        if symbol:
            params["symbol"] = symbol
        r = self._request("GET",
                          "/api/v2/mix/position/get-all-position",
                          params)
        if r and r.get("data"):
            return [p for p in r["data"]
                    if float(p.get("total", 0)) > 0]
        return []

    def close_position(self, symbol, hold_side):
        data = {"productType": "USDT-FUTURES", "symbol": symbol,
                "holdSide": hold_side}
        return self._request("POST",
                             "/api/v2/mix/order/close-positions",
                             data=data)

    # ─── Test Connection ──────────────────────────
    def test(self):
        bal = self.get_balance("spot")
        return {"ok": bal >= 0, "balance": bal}


# ═══════════════════════════════════════════════════════
#  INDICATOR ENGINE
# ═══════════════════════════════════════════════════════
class Indicators:
    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = [k for k, v in cfg.items()
                        if v.get("enabled")]

    def evaluate(self, df):
        signals = {}
        for name in self.enabled:
            try:
                fn = getattr(self, f"calc_{name}")
                signals[name] = fn(df, self.cfg[name])
            except:
                signals[name] = "NEUTRAL"
        return signals

    def calc_rsi(self, df, c):
        p = c.get("period", 14)
        delta = df["close"].diff()
        g = delta.where(delta > 0, 0).rolling(p).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(p).mean()
        rs = g / loss
        rsi = 100 - (100 / (1 + rs))
        v = rsi.iloc[-1]
        prev = rsi.iloc[-2]
        if v < c.get("oversold", 30): return "LONG"
        if v > c.get("overbought", 70): return "SHORT"
        if prev < c.get("oversold", 30) and v >= c.get("oversold", 30):
            return "LONG"
        if prev > c.get("overbought", 70) and v <= c.get("overbought", 70):
            return "SHORT"
        return "NEUTRAL"

    def calc_macd(self, df, c):
        ef = df["close"].ewm(span=c.get("fast", 12)).mean()
        es = df["close"].ewm(span=c.get("slow", 26)).mean()
        ml = ef - es
        sl = ml.ewm(span=c.get("signal", 9)).mean()
        h = ml - sl
        if h.iloc[-1] > 0 and h.iloc[-2] <= 0: return "LONG"
        if h.iloc[-1] < 0 and h.iloc[-2] >= 0: return "SHORT"
        if h.iloc[-1] > 0: return "LONG"
        if h.iloc[-1] < 0: return "SHORT"
        return "NEUTRAL"

    def calc_bb(self, df, c):
        p = c.get("period", 20)
        sd = c.get("std_dev", 2)
        m = df["close"].rolling(p).mean()
        std = df["close"].rolling(p).std()
        pr = df["close"].iloc[-1]
        if pr <= (m - std * sd).iloc[-1]: return "LONG"
        if pr >= (m + std * sd).iloc[-1]: return "SHORT"
        return "NEUTRAL"

    def calc_ema(self, df, c):
        ef = df["close"].ewm(span=c.get("fast", 9)).mean()
        es = df["close"].ewm(span=c.get("slow", 21)).mean()
        if (ef.iloc[-1] > es.iloc[-1]
                and ef.iloc[-2] <= es.iloc[-2]): return "LONG"
        if (ef.iloc[-1] < es.iloc[-1]
                and ef.iloc[-2] >= es.iloc[-2]): return "SHORT"
        if ef.iloc[-1] > es.iloc[-1]: return "LONG"
        return "SHORT"

    def calc_stoch(self, df, c):
        kp = c.get("k_period", 14)
        dp = c.get("d_period", 3)
        sm = c.get("smooth", 3)
        lo = df["low"].rolling(kp).min()
        hi = df["high"].rolling(kp).max()
        k = (100 * (df["close"] - lo) / (hi - lo)).rolling(sm).mean()
        d = k.rolling(dp).mean()
        if k.iloc[-1] < 20 and d.iloc[-1] < 20: return "LONG"
        if k.iloc[-1] > 80 and d.iloc[-1] > 80: return "SHORT"
        if (k.iloc[-1] > d.iloc[-1]
                and k.iloc[-2] <= d.iloc[-2]): return "LONG"
        if (k.iloc[-1] < d.iloc[-1]
                and k.iloc[-2] >= d.iloc[-2]): return "SHORT"
        return "NEUTRAL"

    def calc_atr(self, df, c):
        p = c.get("period", 14)
        m = c.get("multiplier", 1.5)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(p).mean()
        avg = atr.rolling(50).mean().iloc[-1]
        if atr.iloc[-1] > avg * m:
            return ("LONG" if df["close"].iloc[-1]
                    > df["close"].iloc[-5] else "SHORT")
        return "NEUTRAL"


# ═══════════════════════════════════════════════════════
#  TRADING BOT ENGINE
# ═══════════════════════════════════════════════════════
class TradingBot:
    def __init__(self):
        self.running = False
        self.config = load_config()
        self.client = None
        self.indicators = None
        self.thread = None

        # Live state (read by frontend)
        self.state = {
            "running": False,
            "mode": "spot",
            "symbol": "BTCUSDT",
            "balance": 0,
            "m1": {"signal": "WAITING", "price": 0,
                   "indicators": {}},
            "m5": {"signal": "WAITING", "price": 0,
                   "indicators": {}},
            "aligned": False,
            "last_order": None,
            "trades": [],
            "stats": {"total": 0, "wins": 0, "losses": 0,
                      "spot": 0, "perp": 0,
                      "alignments": 0, "checks": 0},
            "logs": [],
            "positions": []
        }

    def _log(self, level, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = {"time": ts, "level": level, "msg": msg}
        self.state["logs"].append(entry)
        # Keep last 200 logs
        if len(self.state["logs"]) > 200:
            self.state["logs"] = self.state["logs"][-200:]
        # Also print to terminal
        colors = {"info": "\033[36m", "success": "\033[32m",
                  "warn": "\033[33m", "error": "\033[31m",
                  "tf": "\033[38;5;141m"}
        c = colors.get(level, "")
        print(f"{c}[{ts}] [{level.upper()}] {msg}\033[0m")

    def _init_client(self):
        cfg = self.config
        if not cfg.get("api_key"):
            self._log("error", "API Key not configured!")
            return False
        self.client = BitgetClient(
            cfg["api_key"], cfg["api_secret"],
            cfg["api_passphrase"], cfg.get("demo", True)
        )
        self.indicators = Indicators(cfg["indicators"])
        return True

    def _resolve(self, signals):
        strat = self.config.get("strategy", "multi_confirm")
        if strat == "multi_confirm":
            bull = sum(1 for s in signals.values() if s == "LONG")
            bear = sum(1 for s in signals.values() if s == "SHORT")
            thr = max(2, len(signals) // 2)
            if bull >= thr: return "LONG"
            if bear >= thr: return "SHORT"
        elif strat == "primary_secondary":
            primary = signals.get("rsi", "NEUTRAL")
            others = [v for k, v in signals.items() if k != "rsi"]
            if primary == "LONG" and "SHORT" not in others:
                return "LONG"
            if primary == "SHORT" and "LONG" not in others:
                return "SHORT"
        elif strat == "weighted_score":
            w = {"rsi": 2, "macd": 2, "bb": 1.5,
                 "ema": 1.5, "stoch": 1, "atr": 1}
            score = 0
            for k, v in signals.items():
                if v == "LONG": score += w.get(k, 1)
                elif v == "SHORT": score -= w.get(k, 1)
            if score >= 3: return "LONG"
            if score <= -3: return "SHORT"
        return "NEUTRAL"

    def _calc_tp_sl(self, entry, side):
        tp_p = self.config.get("tp_percent", 2.5) / 100
        sl_p = self.config.get("sl_percent", 1.5) / 100
        if side == "buy":
            return (round(entry * (1 + tp_p), 2),
                    round(entry * (1 - sl_p), 2))
        return (round(entry * (1 - tp_p), 2),
                round(entry * (1 + sl_p), 2))

    def _execute(self, signal, price):
        cfg = self.config
        mode = cfg.get("market_mode", "spot")
        otype = cfg.get("order_type", "market")
        symbol = cfg.get("symbol", "BTCUSDT")
        size = cfg.get("order_size", 50)

        if signal == "NEUTRAL":
            return

        if mode == "spot":
            if signal == "LONG":
                if otype == "market":
                    r = self.client.spot_market(symbol, "buy", size)
                else:
                    offset = cfg.get("limit_offset", 0.2) / 100
                    lp = round(price * (1 - offset), 2)
                    r = self.client.spot_limit(symbol, "buy", lp,
                                               round(size / lp, 6))
                if r and r.get("code") == "00000":
                    self._log("success",
                              f"SPOT {otype.upper()} BUY filled!")
                    self.state["stats"]["total"] += 1
                    self.state["stats"]["spot"] += 1
                    self.state["trades"].insert(0, {
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "mode": "spot", "side": "buy",
                        "pair": symbol, "price": price,
                        "type": otype, "pnl": None
                    })
                else:
                    self._log("error",
                              f"SPOT BUY failed: {r}")

            elif signal == "SHORT":
                coin = symbol.replace("USDT", "")
                # Get spot balance
                bal_r = self.client._request(
                    "GET", "/api/v2/spot/account/assets",
                    {"coin": coin})
                bal = 0
                if bal_r and bal_r.get("data") and bal_r["data"]:
                    bal = float(bal_r["data"][0].get("available", 0))
                if bal > 0:
                    if otype == "market":
                        r = self.client.spot_market(symbol, "sell",
                                                    bal)
                    else:
                        offset = cfg.get("limit_offset", 0.2) / 100
                        lp = round(price * (1 + offset), 2)
                        r = self.client.spot_limit(symbol, "sell",
                                                   lp, bal)
                    if r and r.get("code") == "00000":
                        self._log("success",
                                  f"SPOT {otype.upper()} SELL filled!")
                        self.state["stats"]["total"] += 1
                        self.state["stats"]["spot"] += 1
                    else:
                        self._log("error",
                                  f"SPOT SELL failed: {r}")
                else:
                    self._log("warn",
                              f"No {coin} balance to sell.")

        else:  # perp
            side = "buy" if signal == "LONG" else "sell"
            hs = "long" if side == "buy" else "short"
            lev = cfg.get("leverage", 3)
            contracts = round(size * lev / price, 3)
            tp, sl = self._calc_tp_sl(price, side)

            # Close opposing
            positions = self.client.get_positions(symbol)
            for p in positions:
                p_hs = p.get("holdSide", "")
                if ((signal == "LONG" and p_hs == "short")
                        or (signal == "SHORT" and p_hs == "long")):
                    self._log("info",
                              f"Closing opposing {p_hs}...")
                    self.client.close_position(symbol, p_hs)

            self.client.set_leverage(symbol, lev, hs)

            if otype == "market":
                r = self.client.perp_market(symbol, side,
                                            contracts, tp, sl)
            else:
                offset = cfg.get("limit_offset", 0.2) / 100
                if side == "buy":
                    lp = round(price * (1 - offset), 2)
                else:
                    lp = round(price * (1 + offset), 2)
                r = self.client.perp_limit(symbol, side, lp,
                                           contracts, tp, sl)

            if r and r.get("code") == "00000":
                oid = r.get("data", {}).get("orderId", "?")
                self._log("success",
                          f"PERP {otype.upper()} {signal} filled! "
                          f"ID: {oid}")
                self.state["stats"]["total"] += 1
                self.state["stats"]["perp"] += 1
                self.state["trades"].insert(0, {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "mode": "perp",
                    "side": side,
                    "pair": symbol,
                    "price": price,
                    "type": otype,
                    "pnl": None
                })
            else:
                self._log("error",
                          f"PERP order failed: {r}")

        self.state["last_order"] = {
            "signal": signal, "price": price,
            "type": otype, "time": datetime.now().strftime("%H:%M:%S")
        }

    def _loop(self):
        self._log("info", "═══ Bot Started ═══")
        cfg = self.config
        symbol = cfg.get("symbol", "BTCUSDT")
        mode = cfg.get("market_mode", "spot")
        self.state["mode"] = mode
        self.state["symbol"] = symbol
        self._log("info",
                  f"Pair: {symbol} | Mode: {mode.upper()} | "
                  f"Strategy: {cfg.get('strategy')} | "
                  f"Order: {cfg.get('order_type', 'market').upper()}")

        while self.running:
            try:
                # 1. Fetch M1 data
                df_m1 = self.client.get_klines(symbol, "1m", mode)
                df_m5 = self.client.get_klines(symbol, "5m", mode)

                if df_m1 is None or df_m5 is None:
                    self._log("warn", "No data received. Retrying...")
                    time.sleep(5)
                    continue

                # 2. Run indicators on both timeframes
                sig_m1 = self.indicators.evaluate(df_m1)
                sig_m5 = self.indicators.evaluate(df_m5)

                m1_signal = self._resolve(sig_m1)
                m5_signal = self._resolve(sig_m5)

                price_m1 = float(df_m1["close"].iloc[-1])
                price_m5 = float(df_m5["close"].iloc[-1])

                # 3. Update state
                self.state["m1"] = {
                    "signal": m1_signal,
                    "price": price_m1,
                    "indicators": sig_m1
                }
                self.state["m5"] = {
                    "signal": m5_signal,
                    "price": price_m5,
                    "indicators": sig_m5
                }

                # 4. Alignment check
                aligned = (m1_signal == m5_signal
                           and m1_signal != "NEUTRAL")
                self.state["aligned"] = aligned
                self.state["stats"]["checks"] += 1

                self._log("tf",
                          f"[M1] {m1_signal} ${price_m1:,.2f} | "
                          f"[M5] {m5_signal} ${price_m5:,.2f} "
                          f"{'✓ ALIGNED' if aligned else '✗'}")

                # 5. Execute if aligned
                if aligned:
                    self.state["stats"]["alignments"] += 1
                    self._log("success",
                              f"✓ ALIGNED: M1={m1_signal} == "
                              f"M5={m5_signal} → EXECUTE")
                    self._execute(m1_signal, price_m5)

                # 6. Update balance
                try:
                    bal = self.client.get_balance(mode)
                    self.state["balance"] = bal
                except:
                    pass

                # 7. Update positions (perp only)
                if mode == "perp":
                    try:
                        pos = self.client.get_positions(symbol)
                        self.state["positions"] = pos
                    except:
                        pass

                # 8. Keep trades list trimmed
                if len(self.state["trades"]) > 100:
                    self.state["trades"] = \
                        self.state["trades"][:100]

                # 9. Wait for next M5 candle
                self._log("info", "Next check in 300s (M5)...")
                # Sleep in small chunks so we can stop quickly
                for _ in range(60):
                    if not self.running:
                        break
                    time.sleep(5)

            except Exception as e:
                self._log("error", f"Loop error: {e}")
                time.sleep(10)

        self._log("warn", "Bot stopped.")
        self.state["running"] = False

    def start(self):
        if self.running:
            return {"ok": False, "msg": "Already running"}
        if not self._init_client():
            return {"ok": False, "msg": "API not configured"}
        self.running = True
        self.state["running"] = True
        self.thread = threading.Thread(target=self._loop,
                                       daemon=True)
        self.thread.start()
        return {"ok": True, "msg": "Bot started"}

    def stop(self):
        self.running = False
        self.state["running"] = False
        return {"ok": True, "msg": "Bot stopped"}

    def update_config(self, new_cfg):
        self.config = new_cfg
        save_config(new_cfg)
        self._log("info", "Configuration updated.")


# ═══════════════════════════════════════════════════════
#  FLASK APP
# ═══════════════════════════════════════════════════════
app = Flask(__name__)
bot = TradingBot()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    """Frontend polls this every 2 seconds."""
    return jsonify(bot.state)


@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(bot.config)


@app.route("/api/config", methods=["POST"])
def api_set_config():
    new_cfg = request.json
    bot.update_config(new_cfg)
    return jsonify({"ok": True})


@app.route("/api/connect", methods=["POST"])
def api_connect():
    """Test API connection."""
    cfg = request.json or bot.config
    try:
        client = BitgetClient(
            cfg.get("api_key", ""),
            cfg.get("api_secret", ""),
            cfg.get("api_passphrase", ""),
            cfg.get("demo", True)
        )
        result = client.test()
        return jsonify({"ok": result["ok"],
                        "balance": result["balance"]})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route("/api/bot/start", methods=["POST"])
def api_start():
    return jsonify(bot.start())


@app.route("/api/bot/stop", methods=["POST"])
def api_stop():
    return jsonify(bot.stop())


@app.route("/api/bot/status")
def api_status():
    return jsonify({
        "running": bot.running,
        "state": bot.state
    })


# ═══════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"""
\033[36m╔══════════════════════════════════════════╗
║  BG-BOT v5 — Full-Stack Trading Bot      ║
║                                          ║
║  Server: http://0.0.0.0:{port:<17s}║
║                                          ║
║  Android Chrome Desktop Mode:            ║
║  → Open http://YOUR_IP:{port:<17s}║
║  → ⋮ Menu → Desktop site ✓              ║
║                                          ║
║  Press Ctrl+C to stop                    ║
╚══════════════════════════════════════════╝\033[0m
    """)
    app.run(host="0.0.0.0", port=port, debug=False,
            threaded=True)
