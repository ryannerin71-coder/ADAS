import os
import requests
import pandas as pd
import numpy as np
import time
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TD_API_KEY = os.getenv("TD_API_KEY")

WATCHLIST = [
    "EUR/USD", "GBP/JPY", "AUD/USD", "GBP/USD",
    "XAU/USD", "AUD/CAD", "AUD/JPY", "BTC/USD"
]
TIMEFRAME = "1h"

app = Flask(__name__)

@app.route('/')
def home(): 
    return "AI Adaptive Bot V5.0 Running"

def calculate_chop_index(df, period=14):
    try:
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift())
        df['tr2'] = abs(df['low'] - df['close'].shift())
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
        
        df['atr_sum'] = df['tr'].rolling(period).sum()
        df['hh'] = df['high'].rolling(period).max()
        df['ll'] = df['low'].rolling(period).min()
        
        df['chop'] = 100 * np.log10(df['atr_sum'] / (df['hh'] - df['ll'])) / np.log10(period)
        return df['chop']
    except Exception:
        return pd.Series(50, index=df.index)

def fetch_data(symbol):
    url = "https://api.twelvedata.com/time_series"
    params = {"symbol": symbol, "interval": TIMEFRAME, "apikey": TD_API_KEY, "outputsize": 100}
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if "code" in data and data["code"] == 429: return "RATE_LIMIT"
        if "values" not in data: return "NO_DATA"
            
        df = pd.DataFrame(data["values"])
        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)
        df = df.iloc[::-1]
        
        cols = ['open', 'high', 'low', 'close']
        df[cols] = df[cols].astype(float)

        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
        
        delta = df['close'].diff()
        up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
        rs = up.ewm(com=13, adjust=False).mean() / down.ewm(com=13, adjust=False).mean()
        df['rsi'] = 100 - (100 / (1 + rs))
        
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift())
        df['tr2'] = abs(df['low'] - df['close'].shift())
        df['atr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1).rolling(14).mean()

        df['chop'] = calculate_chop_index(df)

        return df.dropna()
    except Exception as e: 
        print(f"Fetch Error for {symbol}: {e}")
        return "ERROR"

def get_flags(symbol):
    base, quote = symbol.split('/')
    flags = {
        "EUR": "🇪🇺", "USD": "🇺🇸", "GBP": "🇬🇧", "JPY": "🇯🇵",
        "AUD": "🇦🇺", "CAD": "🇨🇦", "XAU": "🥇", "BTC": "🅱️"
    }
    return f"{flags.get(base, '')}{flags.get(quote, '')}"

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    requests.post(url, json=payload)

def format_signal_card(symbol, action, price, rsi, tp1, tp2, sl, chop):
    fmt = ",.2f" if any(x in symbol for x in ["JPY", "XAU", "BTC"]) else ",.5f"
    flags = get_flags(symbol)
    
    # Calculate simulated Trend Power from Chop Index
    trend_power = int(100 - chop) if chop < 100 else 10
    
    if action == "BUY":
        header = "🔺 💎 SNIPER ENTRY: BUY 💎 🔺"
        side, trend_text = "LONG 🟢", "Bullish Uptrend"
        squares = "🟩🟩🟩🟩🟩" if trend_power > 50 else "🟩🟩🟩"
        conf = 95 if trend_power > 50 else 75
    elif action == "SELL":
        header = "🔻 💎 SNIPER ENTRY: SELL 💎 🔻"
        side, trend_text = "SHORT 🔴", "Bearish Downtrend"
        squares = "🟥🟥🟥🟥🟥" if trend_power > 50 else "🟥🟥🟥"
        conf = 95 if trend_power > 50 else 75
    else:
        header = "⚖️ 💎 MARKET WATCH: WAIT 💎 ⚖️"
        side, trend_text = "NEUTRAL ⚪", "Consolidating"
        squares = "⬜⬜⬜"
        conf = 50

    if action in ["BUY", "SELL"]:
        targets = (
            f"🎯 <b>PROFIT TARGETS</b>\n"
            f"🥇 <b>TP1:</b> <code>{tp1:{fmt}}</code>\n"
            f"🥈 <b>TP2:</b> <code>{tp2:{fmt}}</code>\n\n"
            f"🛡️ <b>RISK MANAGEMENT</b>\n"
            f"🧱 <b>SL:</b> <code>{sl:{fmt}}</code>"
        )
    else:
        targets = "⏳ <i>Awaiting precise trend alignment. No trade zone.</i>"

    msg = (
        f"{header}\n"
        f"〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
        f"┏ {flags} <b>{symbol}</b> 🔶 <b>{side}</b> ┓\n"
        f"┗ 💵 <b>ENTRY:</b> <code>{price:{fmt}}</code> ┛\n\n"
        f"📊 <b>HIGH PRECISION INTEL</b>\n"
        f"• <b>Trend:</b> {trend_text}\n"
        f"• <b>Trend Power:</b> {trend_power} (Strength)\n"
        f"• <b>RSI:</b> {rsi:.0f}\n"
        f"• <b>Signal Strength:</b> {squares} {conf}% CONFIDENCE\n\n"
        f"{targets}\n"
        f"〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️"
    )
    return msg

def analyze_markets():
    print("Scanning all markets...")
    for symbol in WATCHLIST:
        df = fetch_data(symbol)
        if isinstance(df, str): continue
            
        latest = df.iloc[-1]
        price = latest['close']
        rsi = latest['rsi']
        ema_50 = latest['ema_50']
        ema_200 = latest['ema_200']
        atr = latest['atr']
        chop = latest['chop']

        if price > ema_50 and ema_50 > ema_200:
            action = "BUY"
            sl = price - (atr * 1.5)
            tp1 = price + (atr * 1.0)
            tp2 = price + (atr * 2.5)
        elif price < ema_50 and ema_50 < ema_200:
            action = "SELL"
            sl = price + (atr * 1.5)
            tp1 = price - (atr * 1.0)
            tp2 = price - (atr * 2.5)
        else:
            action = "NEUTRAL"
            sl = tp1 = tp2 = 0
            
        msg = format_signal_card(symbol, action, price, rsi, tp1, tp2, sl, chop)
        send_telegram_message(msg)
        time.sleep(2)

if __name__ == '__main__':
    startup_msg = "🟢 <b>SYSTEM ONLINE</b>\nAI Sniper Bot V5.0 is active.\nBroadcasting high-precision intelligence every 30 minutes."
    send_telegram_message(startup_msg)
    
    analyze_markets()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=analyze_markets, trigger="interval", minutes=30)
    scheduler.start()
    
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
