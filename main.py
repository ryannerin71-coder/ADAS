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
    return "AI Adaptive Bot V4.2 Running"

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

def format_signal_card(symbol, action, price, rsi, tp, sl, chop):
    fmt = ",.2f" if any(x in symbol for x in ["JPY", "XAU", "BTC"]) else ",.5f"
    
    if action == "BUY":
        icon, side = "🟢", "LONG"
    elif action == "SELL":
        icon, side = "🔴", "SHORT"
    else:
        icon, side = "⚪", "NEUTRAL"

    market_state = "Trending" if chop < 50 else "Choppy"

    if action in ["BUY", "SELL"]:
        targets = f"🛑 <b>SL:</b> <code>{sl:{fmt}}</code>\n✅ <b>TP:</b> <code>{tp:{fmt}}</code>"
    else:
        targets = "⏳ <i>Waiting for clear trend crossover.</i>"

    msg = (
        f"🏛 <b>MARKET INTEL: {get_flags(symbol)} {symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>ACTION:</b> {icon} <b>{side}</b>\n"
        f"🏷 <b>PRICE:</b> <code>{price:{fmt}}</code>\n\n"
        f"📊 <b>TECHNICALS</b>\n"
        f"• RSI: {rsi:.1f}\n"
        f"• Condition: {market_state} (Chop: {chop:.1f})\n\n"
        f"🎯 <b>TRADE PLAN</b>\n"
        f"{targets}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
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

        # Determine bias for ALL pairs
        if price > ema_50 and ema_50 > ema_200:
            action = "BUY"
            sl = price - (atr * 1.5)
            tp = price + (atr * 2.0)
        elif price < ema_50 and ema_50 < ema_200:
            action = "SELL"
            sl = price + (atr * 1.5)
            tp = price - (atr * 2.0)
        else:
            action = "NEUTRAL"
            sl = 0
            tp = 0
            
        msg = format_signal_card(symbol, action, price, rsi, tp, sl, chop)
        send_telegram_message(msg)
        time.sleep(2) # Paused slightly longer to prevent Telegram spam limits

if __name__ == '__main__':
    startup_msg = "🟢 <b>SYSTEM ONLINE</b>\nAI Adaptive Bot V4.2 is active.\nBroadcasting all pairs every 30 minutes."
    send_telegram_message(startup_msg)
    
    analyze_markets()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=analyze_markets, trigger="interval", minutes=30)
    scheduler.start()
    
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
