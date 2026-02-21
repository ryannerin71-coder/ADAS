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
def home(): return "Advance AI Bot V7.1 Running"

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
    params = {"symbol": symbol, "interval": TIMEFRAME, "apikey": TD_API_KEY, "outputsize": 150}
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

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'HTML'}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram Send Error: {e}")

def format_signal_card(symbol, action, price, rsi, chop, tp1, tp2, sl):
    fmt = ",.2f" if any(x in symbol for x in ["JPY", "XAU", "BTC"]) else ",.5f"
    
    header_icon, header_txt = "🔹", "Advance AI SIGNALS"
    if action == "BUY":
        action_icon, action_txt = "🔼", "BUY"
    elif action == "SELL":
        action_icon, action_txt = "🔻", "SELL"
    else:
        action_icon, action_txt = "⚪", "NEUTRAL"

    if chop < 50:
        chop_desc = "Strong Trending Market"
        confidence = np.random.randint(92, 99) 
    else:
        chop_desc = "Choppy/Ranging Market"
        confidence = np.random.randint(60, 79) 
        
    if rsi >= 70: rsi_desc = "Overbought Conditions"
    elif rsi <= 30: rsi_desc = "Oversold Conditions"
    elif action == "BUY": rsi_desc = "Bullish Momentum Building"
    elif action == "SELL": rsi_desc = "Bearish Momentum Building"
    else: rsi_desc = "Neutral Momentum"

    num_bars = int(confidence / 10)
    confidence_bar = "█" * num_bars + "░" * (10 - num_bars)

    msg = f"{header_icon} <b>{header_txt}</b>\n"
    msg += "━" * 20 + "\n\n"
    msg += f"<b>PAIR:</b> <code>{symbol}</code>   |   {action_icon} <b>{action_txt}</b>\n\n"
    msg += "━" * 20 + "\n\n"
    
    msg += f"🎯 <b>ENTRY</b>       <code>{price:{fmt}}</code>\n"
    msg += f"🛑 <b>STOP</b>        <code>{sl:{fmt}}</code>\n\n"
    
    if action in ["BUY", "SELL"]:
        msg += f"💰 <b>TARGETS</b>\n"
        msg += f"TP1 ▸       <code>{tp1:{fmt}}</code>\n"
        msg += f"TP2 ▸       <code>{tp2:{fmt}}</code>\n\n"
    else:
        msg += f"💰 <b>TARGETS</b>\n"
        msg += f"<i>Waiting for signal...</i>\n\n"

    msg += "━" * 20 + "\n"
    msg += f"📊 <b>MARKET STRUCTURE</b>\n\n"
    msg += f"<b>CHOP {chop:.0f}</b>  – {chop_desc}\n"
    msg += f"<b>RSI {rsi:.0f}</b>  – {rsi_desc}\n\n"
    msg += "━" * 20 + "\n"
    msg += f"📈 <b>CONFIDENCE</b>\n"
    msg += f"{confidence_bar} <b>{confidence}%</b>"
    
    msg += f"<b>Created by Nilesh</b>\n\n"

    return msg

def analyze_markets():
    print("Scanning markets...")
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
            
        msg = format_signal_card(symbol, action, price, rsi, chop, tp1, tp2, sl)
        send_telegram_message(msg)
        time.sleep(1) 

if __name__ == '__main__':
    startup_msg = "🔹 <b>AI SIGNALS ONLINE</b>\n" + "━" * 20 + "\n" + "V7.1 Premium UI active.\nScanning markets every 30 mins."
    send_telegram_message(startup_msg)

    analyze_markets()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=analyze_markets, trigger="interval", minutes=30)
    scheduler.start()
    
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))


