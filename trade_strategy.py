import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
import os
import re
from datetime import datetime, date

# ─── Global RSI state ─────────────────────────────────────────────────────────
avg_gain = 0.0
avg_loss = 0.0

# ─── Indicator Functions ───────────────────────────────────────────────────────
def calculate_rsi(delta: pd.Series, window: int = 14, smooth: bool = False) -> float:
    global avg_gain, avg_loss
    gains  = delta.where(delta > 0, 0)
    losses = -delta.where(delta < 0, 0)

    if not smooth:
        avg_gain = gains.rolling(window=window).mean().iloc[-1]
        avg_loss = losses.rolling(window=window).mean().iloc[-1]
    else:
        curr_gain = delta.iloc[-1] if delta.iloc[-1] > 0 else 0
        curr_loss = -delta.iloc[-1] if delta.iloc[-1] < 0 else 0
        avg_gain  = ((avg_gain * 13) + curr_gain) / window
        avg_loss  = ((avg_loss * 13) + curr_loss) / window

    rs  = avg_gain / avg_loss if avg_loss != 0 else 0
    rsi = 100 - (100 / (1 + rs)) if rs != 0 else 0
    return rsi


def calculate_macd(data: pd.Series, short_window: int = 12, long_window: int = 26, signal_window: int = 9):
    short_ema = data.ewm(span=short_window, adjust=False).mean()
    long_ema  = data.ewm(span=long_window,  adjust=False).mean()
    macd      = short_ema - long_ema
    signal    = macd.ewm(span=signal_window, adjust=False).mean()
    return macd, signal

# ─── User Inputs ───────────────────────────────────────────────────────────────
def get_ticker():
    while True:
        ticker = input("Enter ticker symbol (e.g. SOFI): ").strip().upper()
        if re.match(r'^[A-Z]{1,5}$', ticker):
            return ticker
        print("  Invalid ticker. Use 1-5 letters only (e.g. SOFI, AAPL).")

def get_date():
    while True:
        day = input("Enter date (YYYY-MM-DD): ").strip()
        try:
            parsed = datetime.strptime(day, "%Y-%m-%d").date()
            if parsed > date.today():
                print("  Date cannot be in the future.")
                continue
            if parsed.weekday() >= 5:
                print("  That's a weekend. Markets are closed — please enter a weekday.")
                continue
            return day
        except ValueError:
            print("  Invalid format. Use YYYY-MM-DD (e.g. 2025-12-09).")

def get_capital():
    while True:
        raw = input("Enter starting capital (e.g. 10000): ").strip().replace(",", "").replace("$", "")
        try:
            amount = float(raw)
            if amount <= 0:
                print("  Capital must be greater than 0.")
                continue
            return amount
        except ValueError:
            print("  Invalid amount. Enter a number (e.g. 10000 or 5000.50).")

def get_max_trades():
    while True:
        raw = input("Enter max trades per day (press Enter for unlimited): ").strip()
        if raw == "":
            return float("inf")
        try:
            val = int(raw)
            if val <= 0:
                print("  Must be at least 1.")
                continue
            return val
        except ValueError:
            print("  Invalid input. Enter a whole number or press Enter to skip.")

ticker      = get_ticker()
day         = get_date()
capital     = get_capital()
max_trades  = get_max_trades()

# ─── Download 1-min OHLCV ─────────────────────────────────────────────────────
start = pd.Timestamp(f"{day} 09:25", tz="America/New_York")
end   = pd.Timestamp(f"{day} 16:00", tz="America/New_York")

raw = yf.download(ticker, start=start, end=end, interval="1m", auto_adjust=True)
raw = raw.tz_convert("America/New_York")
raw.dropna(inplace=True)

if isinstance(raw.columns, pd.MultiIndex):
    raw = raw.xs(ticker, level="Ticker", axis=1)

if raw.empty:
    print(f"\nNo data found for '{ticker}' on {day}.")
    print("The market may have been closed, or the ticker may be invalid.")
    exit(1)

df = raw.copy()

# ─── Indicators ────────────────────────────────────────────────────────────────
# EMA20
df["EMA20"]      = df["Close"].ewm(span=20, adjust=False).mean()
df["EMA20_prev"] = df["EMA20"].shift(1)
df["EMARising"]  = df["EMA20"] > df["EMA20_prev"]

# Volume
df["VolMA20"] = df["Volume"].rolling(20).mean()
df["HighVol"] = df["Volume"] > df["VolMA20"]

# VWAP (resets each day)
df["VWAP"] = (df["Close"] * df["Volume"]).cumsum() / df["Volume"].cumsum()

# Price change
df["PriceChange"] = df["Close"].pct_change()

# RSI
rsi_window = 14
delta      = df["Close"].diff()
df["RSI"]  = 0.0
for i in range(rsi_window, len(df)):
    df.at[df.index[i], "RSI"] = calculate_rsi(delta.iloc[:i+1], window=rsi_window, smooth=(i > rsi_window))

# MACD
macd_short, macd_long, macd_sig = 12, 26, 9
macd_line, signal_line = calculate_macd(df["Close"], macd_short, macd_long, macd_sig)
df["MACD"]        = macd_line
df["MACD_Signal"] = signal_line
df["MACD_Hist"]   = df["MACD"] - df["MACD_Signal"]

# ─── Entry Signal ──────────────────────────────────────────────────────────────
df["LongSignal"] = (
    (df["Close"] > df["EMA20"])      &   # Price above rising EMA
    df["EMARising"]                  &   # EMA trending up
    df["HighVol"]                    &   # Above average volume
    (df["Close"] > df["VWAP"])       &   # Price above VWAP
    (df["PriceChange"] > 0.001)      &   # Minimum 0.1% price move
    (df["RSI"] > 50) & (df["RSI"] < 65) &  # RSI in momentum zone (tightened)
    (df["MACD"] > df["MACD_Signal"])     # MACD bullish crossover active
)

# ─── Strategy Settings ────────────────────────────────────────────────────────
risk_pct       = 0.01        # Risk 1% of capital per trade
stop_pct       = 0.005       # 0.5% stop distance
take_profit    = 1.01        # 1% take profit
# max_trades set by user input above
market_open    = pd.Timestamp("09:45").time()
market_close   = pd.Timestamp("15:45").time()
eod_flatten    = pd.Timestamp("15:55").time()

# ─── Backtest ──────────────────────────────────────────────────────────────────
position    = 0
entry_price = 0.0
trail_stop  = 0.0
pnl         = []
buys        = []
sells       = []
trade_count = 0

for i in range(1, len(df)):
    t   = df.index[i]
    row = df.iloc[i]

    # Skip first and last 15 minutes
    if t.time() < market_open or t.time() > market_close:
        # Still flatten at EOD
        if position != 0 and t.time() >= eod_flatten:
            pnl.append((row["Close"] - entry_price) * position)
            sells.append((t, row["Close"], "EOD Exit"))
            position = 0; entry_price = 0.0; trail_stop = 0.0
        continue

    # EOD flatten
    if position != 0 and t.time() >= eod_flatten:
        pnl.append((row["Close"] - entry_price) * position)
        sells.append((t, row["Close"], "EOD Exit"))
        position = 0; entry_price = 0.0; trail_stop = 0.0
        continue

    # Entry — risk-based sizing, max trades cap
    if position == 0 and row["LongSignal"] and trade_count < max_trades:
        entry_price = row["Close"]
        risk_amount = capital * risk_pct
        stop_dist   = entry_price * stop_pct
        position    = max(1, int(risk_amount / stop_dist))
        trail_stop  = entry_price * (1 - stop_pct)
        buys.append((t, entry_price))
        trade_count += 1
        continue

    # Exits
    if position != 0:
        # Update trailing stop
        trail_stop = max(trail_stop, row["High"] * (1 - stop_pct))

        # Take profit
        if row["High"] >= entry_price * take_profit:
            exit_price = entry_price * take_profit
            pnl.append((exit_price - entry_price) * position)
            sells.append((t, exit_price, "Take Profit"))
            position = 0; entry_price = 0.0; trail_stop = 0.0
            continue

        # Trailing stop
        if row["Low"] <= trail_stop:
            exit_price = trail_stop
            pnl.append((exit_price - entry_price) * position)
            sells.append((t, exit_price, "Trail Stop"))
            position = 0; entry_price = 0.0; trail_stop = 0.0
            continue

        # RSI overbought exit
        if row["RSI"] > 70:
            pnl.append((row["Close"] - entry_price) * position)
            sells.append((t, row["Close"], "RSI Exit"))
            position = 0; entry_price = 0.0; trail_stop = 0.0
            continue

        # MACD crossover exit
        if row["MACD"] < row["MACD_Signal"]:
            pnl.append((row["Close"] - entry_price) * position)
            sells.append((t, row["Close"], "MACD Exit"))
            position = 0; entry_price = 0.0; trail_stop = 0.0
            continue

        # EMA breakdown
        if row["Close"] < row["EMA20"]:
            pnl.append((row["Close"] - entry_price) * position)
            sells.append((t, row["Close"], "EMA Exit"))
            position = 0; entry_price = 0.0; trail_stop = 0.0

total_pnl = sum(pnl)
print(f"\nTotal PnL:   ${total_pnl:.2f}")
print(f"Total Trades: {trade_count}")
print(f"Wins:         {sum(1 for p in pnl if p > 0)}")
print(f"Losses:       {sum(1 for p in pnl if p <= 0)}")

# ─── Plot ──────────────────────────────────────────────────────────────────────
fig = make_subplots(
    rows=3, cols=1,
    shared_xaxes=True,
    row_heights=[0.6, 0.2, 0.2],
    vertical_spacing=0.04,
    subplot_titles=(
        f"{ticker}  —  {day}  |  Candlestick + EMA20 + VWAP",
        f"RSI ({rsi_window})  |  Entry zone: 50–65  |  Exit above 70",
        f"MACD  ({macd_short} / {macd_long} / {macd_sig})  |  Line  ·  Signal  ·  Histogram"
    )
)

# Row 1 — Candlestick
fig.add_trace(go.Candlestick(
    x=df.index, open=df["Open"], high=df["High"],
    low=df["Low"], close=df["Close"], name="OHLC",
    increasing_line_color="#26a69a", decreasing_line_color="#ef5350"
), row=1, col=1)

fig.add_trace(go.Scatter(
    x=df.index, y=df["EMA20"], mode="lines", name="EMA20",
    line=dict(color="#f0a500", width=1.5, dash="dot")
), row=1, col=1)

fig.add_trace(go.Scatter(
    x=df.index, y=df["VWAP"], mode="lines", name="VWAP",
    line=dict(color="#29b6f6", width=1.5, dash="dash")
), row=1, col=1)

if buys:
    bx, by = zip(*buys)
    fig.add_trace(go.Scatter(
        x=list(bx), y=[p * 0.999 for p in by],
        mode="markers", name="Buy",
        marker=dict(symbol="triangle-up", size=13, color="#00e676")
    ), row=1, col=1)

if sells:
    sx   = [s[0] for s in sells]
    sy   = [s[1] for s in sells]
    slbl = [s[2] for s in sells]
    fig.add_trace(go.Scatter(
        x=sx, y=[p * 1.001 for p in sy],
        mode="markers+text", name="Sell",
        marker=dict(symbol="triangle-down", size=13, color="#ff1744"),
        text=slbl, textposition="top center",
        textfont=dict(size=9, color="#ff1744")
    ), row=1, col=1)

# Row 2 — RSI
fig.add_trace(go.Scatter(
    x=df.index, y=df["RSI"], mode="lines", name=f"RSI ({rsi_window})",
    line=dict(color="#ab47bc", width=1.5)
), row=2, col=1)
fig.add_hline(y=70, line=dict(color="red",    width=1, dash="dash"), row=2, col=1)
fig.add_hline(y=65, line=dict(color="orange", width=1, dash="dot"),  row=2, col=1)
fig.add_hline(y=50, line=dict(color="green",  width=1, dash="dot"),  row=2, col=1)

# Row 3 — MACD
fig.add_trace(go.Scatter(
    x=df.index, y=df["MACD"], mode="lines", name="MACD Line",
    line=dict(color="#42a5f5", width=1.5)
), row=3, col=1)
fig.add_trace(go.Scatter(
    x=df.index, y=df["MACD_Signal"], mode="lines", name="Signal Line",
    line=dict(color="#ff7043", width=1.5)
), row=3, col=1)
colors = ["#26a69a" if v >= 0 else "#ef5350" for v in df["MACD_Hist"]]
fig.add_trace(go.Bar(
    x=df.index, y=df["MACD_Hist"], name="Histogram",
    marker_color=colors, opacity=0.6
), row=3, col=1)

win_count  = sum(1 for p in pnl if p > 0)
loss_count = sum(1 for p in pnl if p <= 0)
pnl_color  = "#00e676" if total_pnl >= 0 else "#ff1744"

fig.update_layout(
    title=dict(
        text=(
            f"{ticker}  |  {day}  |  Momentum Strategy  |  "
            f"PnL: <span style='color:{pnl_color}'>${total_pnl:.2f}</span>  |  "
            f"Trades: {trade_count}  |  W: {win_count}  L: {loss_count}"
        ),
        font=dict(size=15)
    ),
    xaxis_rangeslider_visible=False,
    template="plotly_dark",
    legend=dict(orientation="h", y=1.02, x=0),
    margin=dict(l=40, r=20, t=100, b=40),
    height=900,
)
fig.update_yaxes(title_text="Price ($)", row=1, col=1)
fig.update_yaxes(title_text="RSI",       row=2, col=1)
fig.update_yaxes(title_text="MACD",      row=3, col=1)

# ─── Save ────────────────────────────────────────────────────────────────────
html_file = f"{ticker}_{day}.html"
fig.write_html(html_file)
print(f"\nHTML chart saved to: {os.path.abspath(html_file)}")