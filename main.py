import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
import pandas as pd
import numpy as np
import json
import os
import requests
import random
import time
from datetime import datetime, timedelta
from fake_useragent import UserAgent
from sqlalchemy import create_engine, Column, String, Float, Integer, Date
from sqlalchemy.orm import sessionmaker, declarative_base
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager

# --- Configuration ---
# Build Timestamp: 2026-02-08 16:19 UTC (Force Rebuild)
# EMA參數: EMA1~EMA6, EMA799, EMA1292 (依據波浪理論技術分析參數表)
MA_PERIODS = [17, 45, 117, 189, 305, 494, 799, 1292]
MA_COLORS = ['#FF6B6B', '#4ECDC4', '#FFE66D', '#1A535C', '#FF9F1C', '#C2F970', '#9B59B6', '#3498DB']

# 技術指標參數（日線）- 依據 PDF 技術分析參數表
RSI_PERIODS = [17, 44]  # RSI1, RSI2
KD_PARAMS = {'rsv': 17, 'k': 3, 'd': 3}  # RSV, K, D
BIAS_PERIODS = [117, 17, 45]  # BIAS1, BIASAV1, BIASAV2 (日線參數)
BIAS_COLORS = ['#E91E63', '#00BCD4', '#FFEB3B']  # 粉紅、青色、黃色
MACD_PARAMS = {'fast': 45, 'slow': 117, 'signal': 17}  # EMA1, EMA2, MACD

STOCK_LIST = [
    {"symbol": "2330", "google_symbol": "TPE:2330", "name": "台積電", "market": "TW"},
    {"symbol": "2317", "google_symbol": "TPE:2317", "name": "鴻海", "market": "TW"},
    {"symbol": "2454", "google_symbol": "TPE:2454", "name": "聯發科", "market": "TW"},
    {"symbol": "2603", "google_symbol": "TPE:2603", "name": "長榮", "market": "TW"},
    {"symbol": "3231", "google_symbol": "TPE:3231", "name": "緯創", "market": "TW"},
    {"symbol": "NVDA", "google_symbol": "NASDAQ:NVDA", "name": "NVIDIA", "market": "US"},
    {"symbol": "AAPL", "google_symbol": "NASDAQ:AAPL", "name": "Apple", "market": "US"},
    {"symbol": "TSLA", "google_symbol": "NASDAQ:TSLA", "name": "Tesla", "market": "US"},
    {"symbol": "MSFT", "google_symbol": "NASDAQ:MSFT", "name": "Microsoft", "market": "US"},
    {"symbol": "AMD", "google_symbol": "NASDAQ:AMD", "name": "AMD", "market": "US"},
]

# --- Database Setup (SQLite) ---
SQLALCHEMY_DATABASE_URL = "sqlite:///./stocks.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})

# Enable Write-Ahead Logging (WAL) for better concurrency
with engine.connect() as connection:
    connection.exec_driver_sql("PRAGMA journal_mode=WAL;")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class StockData(Base):
    __tablename__ = "stock_history"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True)
    date = Column(Date)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)

Base.metadata.create_all(bind=engine)

# --- Global Cache ---
STOCK_DATA_CACHE = {}

# --- Technical Indicator Calculations ---
def calc_ema(series, period):
    """Calculate EMA"""
    return series.ewm(span=period, adjust=False).mean()

def calc_rsi(closes, period):
    """Calculate RSI"""
    delta = closes.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calc_kd(high, low, close, rsv_period, k_period, d_period):
    """Calculate KD (Stochastic)"""
    lowest_low = low.rolling(window=rsv_period).min()
    highest_high = high.rolling(window=rsv_period).max()
    rsv = 100 * (close - lowest_low) / (highest_high - lowest_low)
    
    # K = RSV 的 EMA, D = K 的 EMA
    k = rsv.ewm(span=k_period, adjust=False).mean()
    d = k.ewm(span=d_period, adjust=False).mean()
    return k, d

def calc_bias(closes, period):
    """Calculate BIAS = (Close - MA) / MA * 100"""
    ma = closes.rolling(window=period).mean()
    bias = (closes - ma) / ma * 100
    return bias

def calc_macd(closes, fast, slow, signal):
    """Calculate MACD"""
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    dif = ema_fast - ema_slow  # DIF line
    dea = calc_ema(dif, signal)  # DEA/Signal line
    macd_hist = (dif - dea) * 2  # MACD Histogram
    return dif, dea, macd_hist

def calculate_all_indicators(df):
    """Calculate all technical indicators for a dataframe"""
    result = {}
    
    # MA Lines
    ma_results = []
    for p in MA_PERIODS:
        if len(df) >= p:
            ma_col = df['close'].rolling(window=p).mean()
            ma_data = []
            for idx, val in ma_col.items():
                if not pd.isna(val):
                    ma_data.append({"time": df.loc[idx, "date"], "value": float(val)})
            ma_results.append(ma_data)
        else:
            ma_results.append([])
    result['ma'] = ma_results
    
    # RSI
    rsi_results = []
    for p in RSI_PERIODS:
        rsi = calc_rsi(df['close'], p)
        rsi_data = []
        for idx, val in rsi.items():
            if not pd.isna(val):
                rsi_data.append({"time": df.loc[idx, "date"], "value": float(val)})
        rsi_results.append(rsi_data)
    result['rsi'] = rsi_results
    
    # KD
    k, d = calc_kd(df['high'], df['low'], df['close'], 
                   KD_PARAMS['rsv'], KD_PARAMS['k'], KD_PARAMS['d'])
    k_data = []
    d_data = []
    for idx in df.index:
        if not pd.isna(k.loc[idx]):
            k_data.append({"time": df.loc[idx, "date"], "value": float(k.loc[idx])})
        if not pd.isna(d.loc[idx]):
            d_data.append({"time": df.loc[idx, "date"], "value": float(d.loc[idx])})
    result['kd'] = {'k': k_data, 'd': d_data}
    
    # BIAS (三條線: BIAS1=117, BIASAV1=17, BIASAV2=45)
    bias_results = []
    for p in BIAS_PERIODS:
        bias = calc_bias(df['close'], p)
        bias_data = []
        for idx, val in bias.items():
            if not pd.isna(val):
                bias_data.append({"time": df.loc[idx, "date"], "value": float(val)})
        bias_results.append(bias_data)
    result['bias'] = bias_results
    
    # MACD
    dif, dea, macd_hist = calc_macd(df['close'], 
                                     MACD_PARAMS['fast'], MACD_PARAMS['slow'], MACD_PARAMS['signal'])
    dif_data = []
    dea_data = []
    hist_data = []
    for idx in df.index:
        time_str = df.loc[idx, "date"]
        if not pd.isna(dif.loc[idx]):
            dif_data.append({"time": time_str, "value": float(dif.loc[idx])})
        if not pd.isna(dea.loc[idx]):
            dea_data.append({"time": time_str, "value": float(dea.loc[idx])})
        if not pd.isna(macd_hist.loc[idx]):
            # Histogram with color based on value
            hist_data.append({
                "time": time_str, 
                "value": float(macd_hist.loc[idx]),
                "color": '#26A69A' if macd_hist.loc[idx] >= 0 else '#EF5350'
            })
    result['macd'] = {'dif': dif_data, 'dea': dea_data, 'histogram': hist_data}
    
    return result

# FinMind Token (from user)
FINMIND_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJkYXRlIjoiMjAyNi0wMi0xMiAwOTozMjoxNSIsInVzZXJfaWQiOiJuZW9kZWJ1dCIsImVtYWlsIjoibmVvZGVidXRAZ21haWwuY29tIiwiaXAiOiIyMjMuMTQxLjIxNi4xMSJ9.QJ_dj-AD03Ex14Ir7dUQiFfnJBZMrMpo-h6oNmqEo4M"

# --- Data Fetching Logic ---
def fetch_stock_data(symbol_info):
    """Fetch stock data using FinMind API"""
    symbol = symbol_info['symbol']
    google_symbol = symbol_info['google_symbol']
    
    # Determine dataset and data_id for FinMind
    if "TPE:" in google_symbol:
        dataset = "TaiwanStockPrice"
        data_id = symbol
    elif "NASDAQ:" in google_symbol or "NYSE:" in google_symbol:
        dataset = "USStockPrice"
        data_id = symbol
    else:
        print(f"[{symbol}] Unknown market type for FinMind")
        return None
    
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": dataset,
        "data_id": data_id,
        "token": FINMIND_TOKEN
    }
    
    print(f"[{symbol}] Fetching from FinMind ({dataset})...")
    
    try:
        res = requests.get(url, params=params, timeout=30)
        
        if res.status_code != 200:
            print(f"[{symbol}] FinMind Failed: HTTP {res.status_code}")
            return None
        
        data = res.json()
        
        if data.get("msg") != "success" or not data.get("data"):
            print(f"[{symbol}] FinMind API error: {data.get('msg')}")
            return None
        
        records = []
        for row in data["data"]:
            # FinMind date format: "2026-02-11"
            date_str = row.get("date", "")
            if not date_str:
                continue
                
            # Handle different field names for TW vs US stocks
            if dataset == "TaiwanStockPrice":
                # Taiwan: open, max, min, close, Trading_Volume
                records.append({
                    "symbol": symbol,
                    "date": datetime.strptime(date_str, "%Y-%m-%d").date(),
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("max", row.get("high", 0))),
                    "low": float(row.get("min", row.get("low", 0))),
                    "close": float(row.get("close", 0)),
                    "volume": int(row.get("Trading_Volume", 0))
                })
            else:
                # US: Open, High, Low, Close, Volume
                records.append({
                    "symbol": symbol,
                    "date": datetime.strptime(date_str, "%Y-%m-%d").date(),
                    "open": float(row.get("Open", row.get("open", 0))),
                    "high": float(row.get("High", row.get("high", 0))),
                    "low": float(row.get("Low", row.get("low", 0))),
                    "close": float(row.get("Close", row.get("close", 0))),
                    "volume": int(row.get("Volume", row.get("Trading_Volume", 0)))
                })
        
        if records:
            print(f"[{symbol}] FinMind success! Got {len(records)} records, latest: {records[-1]['date']}")
            return records
        else:
            print(f"[{symbol}] FinMind returned no records")
            return None
            
    except Exception as e:
        print(f"[{symbol}] FinMind ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return None
            
        if 'text/html' in res.headers.get('Content-Type', ''):
            print(f"[{symbol}] Blocked (HTML response)")
            return None

        from io import StringIO
        df = pd.read_csv(StringIO(res.text))
        
        if df.empty or 'Date' not in df.columns:
            return None
            
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.sort_values('Date').tail(1500)  # Increased for longer MA periods
        
        records = []
        for _, row in df.iterrows():
            records.append({
                "symbol": symbol,
                "date": row['Date'].date(),
                "open": float(row['Open']),
                "high": float(row['High']),
                "low": float(row['Low']),
                "close": float(row['Close']),
                "volume": int(row['Volume']) if 'Volume' in row else 0
            })
        return records

    except Exception as e:
        print(f"[{symbol}] Error: {e}")
        return None

def generate_fake_data(symbol):
    print(f"[{symbol}] Generating fallback data...")
    records = []
    price = 100 + random.randint(-20, 20)
    base_date = datetime.now() - timedelta(days=365)
    for i in range(250): 
        current_date = base_date + timedelta(days=i*1.5)
        change = random.uniform(-3, 3)
        price += change
        records.append({
            "symbol": symbol,
            "date": current_date.date(),
            "open": price,
            "high": price + random.uniform(0, 2),
            "low": price - random.uniform(0, 2),
            "close": price + random.uniform(-1, 1),
            "volume": random.randint(1000, 50000)
        })
    return records

def update_database():
    """增量更新：只新增資料庫中沒有的日期資料，保留所有歷史紀錄"""
    print(">>> Starting incremental database update job...")
    print(f">>> Current UTC time: {datetime.now()}")
    db = SessionLocal()
    
    for stock in STOCK_LIST:
        symbol = stock['symbol']
        
        # 查詢資料庫中該股票最新的日期
        from sqlalchemy import func
        latest_record = db.query(func.max(StockData.date)).filter(
            StockData.symbol == symbol
        ).scalar()
        
        print(f"[{symbol}] DB latest: {latest_record}, fetching new data...")
        
        # 抓取資料
        data = fetch_stock_data(stock)
        
        if not data:
            print(f"[{symbol}] Fetch returned NO DATA")
            continue
        
        # 過濾出新資料（日期比資料庫中最新日期還新的）
        if latest_record:
            new_data = [d for d in data if d['date'] > latest_record]
        else:
            new_data = data
        
        if not new_data:
            print(f"[{symbol}] Already up-to-date, no new data to add.")
            time.sleep(2)
            continue
        
        print(f"[{symbol}] Found {len(new_data)} new records to add")
        
        # 只插入新資料
        objects = [StockData(**d) for d in new_data]
        db.bulk_save_objects(objects)
        db.commit()
        print(f"[{symbol}] Successfully added {len(new_data)} records")
        
        time.sleep(5) 
    
    db.close()
    print("<<< Incremental database update complete.")

def refresh_cache():
    """Load stock data from DB into memory cache one by one to avoid OOM"""
    print(">>> Refreshing memory cache...")
    db = SessionLocal()
    try:
        # Get list of unique symbols first
        symbols = [r[0] for r in db.query(StockData.symbol).distinct()]
        
        for symbol in symbols:
            try:
                rows = db.query(StockData).filter(StockData.symbol == symbol).order_by(StockData.date).all()
                
                candles = []
                dates = []
                opens = []
                highs = []
                lows = []
                closes = []
                
                for r in rows:
                    date_str = r.date.strftime('%Y-%m-%d')
                    candles.append({
                        "time": date_str,
                        "open": r.open, "high": r.high, "low": r.low, "close": r.close
                    })
                    dates.append(date_str)
                    opens.append(r.open)
                    highs.append(r.high)
                    lows.append(r.low)
                    closes.append(r.close)
                    
                df = pd.DataFrame({
                    "date": dates,
                    "open": opens,
                    "high": highs,
                    "low": lows,
                    "close": closes
                })
                
                # Calculate all indicators
                indicators = calculate_all_indicators(df)
                
                # Optimize structure
                ma_values = []
                for ma_line in indicators['ma']:
                    ma_values.append([p['value'] for p in ma_line])
                    
                rsi_values = []
                for rsi_line in indicators['rsi']:
                    rsi_values.append([p['value'] for p in rsi_line])
                    
                k_values = [p['value'] for p in indicators['kd']['k']]
                d_values = [p['value'] for p in indicators['kd']['d']]
                
                bias_values = []
                for bias_line in indicators['bias']:
                    bias_values.append([p['value'] for p in bias_line])
                    
                dif_values = [p['value'] for p in indicators['macd']['dif']]
                dea_values = [p['value'] for p in indicators['macd']['dea']]
                hist_values = [p['value'] for p in indicators['macd']['histogram']]
                
                optimized_result = {
                    "symbol": symbol,
                    "dates": dates,
                    "opens": opens, "highs": highs, "lows": lows, "closes": closes,
                    "ma": ma_values,
                    "rsi": rsi_values,
                    "kd": {"k": k_values, "d": d_values},
                    "bias": bias_values,
                    "macd": {"dif": dif_values, "dea": dea_values, "histogram": hist_values}
                }
                
                STOCK_DATA_CACHE[symbol] = optimized_result
                print(f"  [{symbol}] Cached {len(rows)} records (Optimized)")
                
                # Sleep briefly to yield CPU
                time.sleep(0.1)
                
            except Exception as e:
                print(f"!!! Error caching {symbol}: {e}")
            
        print(f"<<< Cache refreshed. Loaded {len(STOCK_DATA_CACHE)} symbols.")
    except Exception as e:
        print(f"!!! Error refreshing cache: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

# --- Load seed data from JSON ---
def load_seed_data():
    """Load pre-fetched data from initial_data.json into database"""
    seed_file = os.path.join(os.path.dirname(__file__), 'initial_data.json')
    if not os.path.exists(seed_file):
        print("No initial_data.json found, skipping seed load")
        return
    
    print(">>> Loading seed data from initial_data.json...")
    db = SessionLocal()
    
    try:
        with open(seed_file, 'r') as f:
            all_data = json.load(f)
        
        for symbol, records in all_data.items():
            existing = db.query(StockData).filter(StockData.symbol == symbol).count()
            if existing > 0:
                print(f"  [{symbol}] Already has {existing} records, skipping")
                continue
            
            objects = []
            for r in records:
                objects.append(StockData(
                    symbol=r['symbol'],
                    date=datetime.strptime(r['date'], '%Y-%m-%d').date(),
                    open=r['open'],
                    high=r['high'],
                    low=r['low'],
                    close=r['close'],
                    volume=r['volume']
                ))
            
            db.bulk_save_objects(objects)
            db.commit()
            print(f"  [{symbol}] Loaded {len(objects)} records")
            
        print("<<< Seed data loaded successfully!")
    finally:
        db.close()

# --- Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    load_seed_data()
    
    # Update data on startup to ensure freshness after deploy
    print(">>> Running startup data update...")
    update_database()
    
    refresh_cache()
    
    scheduler = BackgroundScheduler()
    def job():
        update_database()
        refresh_cache()
        
    scheduler.add_job(job, 'cron', hour=22)
    scheduler.start()
    
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

# --- Frontend Template ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-TW" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>StockView Pro</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
    <script src="https://unpkg.com/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        body { background-color: #1a1a1a; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; }
        .loader { 
            border: 3px solid #333; 
            border-top: 3px solid #3498db; 
            border-radius: 50%; 
            width: 24px; 
            height: 24px; 
            min-width: 24px;
            min-height: 24px;
            flex-shrink: 0;
            animation: spin 1s linear infinite; 
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        [v-cloak] { display: none; }
        .stock-item.active { background-color: #2563eb; color: white; }
        .chart-section { border-top: 1px solid #2a2e39; }
        .chart-label { 
            position: absolute; 
            left: 8px; 
            top: 4px; 
            z-index: 10; 
            font-size: 11px; 
            color: #888; 
            background: rgba(19, 23, 34, 0.8);
            padding: 2px 6px;
            border-radius: 3px;
        }
        .debug-panel {
            position: fixed;
            bottom: 0;
            right: 0;
            width: 400px;
            max-height: 200px;
            background: rgba(0,0,0,0.9);
            color: #0f0;
            font-family: monospace;
            font-size: 11px;
            padding: 8px;
            overflow-y: auto;
            z-index: 9999;
            border-top: 1px solid #333;
            border-left: 1px solid #333;
        }
        .debug-panel .log-entry { margin: 2px 0; }
        .debug-panel .log-error { color: #f66; }
        .debug-panel .log-warn { color: #ff0; }
        .debug-panel .log-success { color: #0f0; }
        .debug-toggle {
            position: fixed;
            bottom: 10px;
            right: 10px;
            z-index: 10000;
            background: #333;
            color: #fff;
            border: none;
            padding: 5px 10px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
        }
    </style>
</head>
<body class="h-screen w-screen flex">
    <div id="app" class="flex w-full h-full">
        <!-- Sidebar -->
        <div :class="['bg-neutral-900 border-r border-neutral-800 flex flex-col z-20 h-full flex-shrink-0', isSidebarOpen ? 'w-80' : 'w-0 overflow-hidden']">
            <div class="p-4 border-b border-neutral-800"><h1 class="text-xl font-bold text-blue-400">StockView</h1></div>
            <div class="flex border-b border-neutral-800">
                <button @click="currentMarket = 'TW'" :class="['flex-1 py-3', currentMarket === 'TW' ? 'text-blue-400 border-b-2 border-blue-400' : 'text-gray-400']">🇹🇼 台股</button>
                <button @click="currentMarket = 'US'" :class="['flex-1 py-3', currentMarket === 'US' ? 'text-blue-400 border-b-2 border-blue-400' : 'text-gray-400']">🇺🇸 美股</button>
            </div>
            <div class="flex-1 overflow-y-auto">
                <div v-for="stock in filteredStocks" :key="stock.symbol" @click="selectStock(stock)" :class="['p-3 mx-2 mb-1 rounded cursor-pointer flex justify-between', currentStock?.symbol === stock.symbol ? 'stock-item active' : 'hover:bg-neutral-800 text-gray-300']">
                    <div><div class="font-bold text-sm">{{ stock.symbol }}</div><div class="text-xs opacity-70">{{ stock.name }}</div></div>
                </div>
            </div>
        </div>
        <!-- Chart Area -->
        <div class="flex-1 flex flex-col h-full bg-[#0a0e17] relative overflow-hidden">
            <!-- DEBUG: Always visible status -->
            <div style="position: absolute; top: 0; left: 0; right: 0; background: red; color: white; z-index: 99999; padding: 10px;">
                DEBUG: Vue is running | Stock: {{ currentStock?.symbol || 'NONE' }} | Market: {{ currentMarket }}
            </div>
            
            <!-- New Header - Yuanta Style -->
            <div class="bg-[#1a2332] border-b border-[#2a3a4a] px-4 py-3 flex-shrink-0">
                <!-- Top Row: Stock Name & Price -->
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-3">
                        <button @click="isSidebarOpen = !isSidebarOpen" class="text-gray-400 hover:text-white">
                            <i class="fas fa-bars text-lg"></i>
                        </button>
                        <div v-if="currentStock">
                            <div class="flex items-center gap-2">
                                <span class="text-white text-xl font-bold">{{ currentStock.name }}</span>
                                <span class="bg-purple-600 text-white text-xs px-2 py-0.5 rounded">市</span>
                                <span class="text-gray-400 text-lg">{{ currentStock.symbol }}</span>
                            </div>
                        </div>
                    </div>
                    <div v-if="currentCandle" class="text-right">
                        <div class="text-3xl font-bold" :class="priceChange >= 0 ? 'text-red-500' : 'text-green-500'">
                            {{ currentCandle.close.toFixed(2) }}
                        </div>
                        <div class="flex items-center justify-end gap-2 text-sm">
                            <span :class="priceChange >= 0 ? 'text-red-500' : 'text-green-500'">
                                <i v-if="priceChange >= 0" class="fas fa-caret-up"></i>
                                <i v-else class="fas fa-caret-down"></i>
                                {{ Math.abs(priceChange).toFixed(2) }}
                            </span>
                            <span :class="priceChange >= 0 ? 'text-red-500' : 'text-green-500'">
                                {{ priceChangePercent.toFixed(2) }}%
                            </span>
                        </div>
                    </div>
                </div>
                
                <!-- Info Dashboard - OHLCV -->
                <div v-if="currentCandle" class="bg-[#0f1419] rounded-lg p-3 mt-2">
                    <div class="grid grid-cols-6 gap-4 text-center">
                        <div>
                            <div class="text-gray-500 text-xs mb-1">日期</div>
                            <div class="text-white text-sm">{{ currentCandle.time }}</div>
                        </div>
                        <div>
                            <div class="text-gray-500 text-xs mb-1">開</div>
                            <div class="text-red-400 text-sm font-mono">{{ currentCandle.open.toFixed(2) }}</div>
                        </div>
                        <div>
                            <div class="text-gray-500 text-xs mb-1">高</div>
                            <div class="text-red-400 text-sm font-mono">{{ currentCandle.high.toFixed(2) }}</div>
                        </div>
                        <div>
                            <div class="text-gray-500 text-xs mb-1">低</div>
                            <div class="text-red-400 text-sm font-mono">{{ currentCandle.low.toFixed(2) }}</div>
                        </div>
                        <div>
                            <div class="text-gray-500 text-xs mb-1">收</div>
                            <div :class="currentCandle.close >= currentCandle.open ? 'text-red-400' : 'text-green-400'" class="text-sm font-mono font-bold">
                                {{ currentCandle.close.toFixed(2) }}
                            </div>
                        </div>
                        <div>
                            <div class="text-gray-500 text-xs mb-1">量</div>
                            <div class="text-yellow-400 text-sm font-mono">{{ formatVolume(currentCandle.volume) }}</div>
                        </div>
                    </div>
                </div>
                
                <!-- MA Values Row -->
                <div v-if="currentMAs.length > 0" class="flex flex-wrap gap-3 mt-2 px-2">
                    <div v-for="(ma, i) in currentMAs" :key="i" class="flex items-center gap-1 text-xs">
                        <span :style="{ color: __MA_COLORS__[i] }">MA{{ __MA_PERIODS__[i] }}</span>
                        <span class="text-white font-mono">{{ ma.value?.toFixed(2) || '--' }}</span>
                    </div>
                </div>
            </div>
            
            <!-- Loading Indicator -->
            <div v-if="loading" class="absolute top-20 right-4 z-50">
                <div class="loader"></div>
            </div>
            
            <!-- Charts Container (Scrollable) -->
            <div class="flex-1 overflow-y-auto bg-[#0a0e17]" ref="chartsScrollContainer">
                <!-- Main Candlestick Chart -->
                <div class="relative" style="height: 50%;">
                    <div ref="mainChartContainer" class="absolute inset-0"></div>
                </div>
                <!-- RSI Chart -->
                <div class="relative border-t border-[#1a2332]" style="height: 12.5%;">
                    <div ref="rsiChartContainer" class="absolute inset-0"></div>
                </div>
                <!-- KD Chart -->
                <div class="relative border-t border-[#1a2332]" style="height: 12.5%;">
                    <div ref="kdChartContainer" class="absolute inset-0"></div>
                </div>
                <!-- BIAS Chart -->
                <div class="relative border-t border-[#1a2332]" style="height: 12.5%;">
                    <div ref="biasChartContainer" class="absolute inset-0"></div>
                </div>
                <!-- MACD Chart -->
                <div class="relative border-t border-[#1a2332]" style="height: 12.5%;">
                    <div ref="macdChartContainer" class="absolute inset-0"></div>
                </div>
            </div>
            <!-- Error Overlay -->
            <div v-if="error" class="absolute inset-0 flex items-center justify-center bg-black/80 text-red-400 z-50">{{ error }}</div>
        </div>
        <!-- Debug Panel -->
        <button class="debug-toggle" @click="showDebug = !showDebug">{{ showDebug ? '隱藏' : '📋' }} Log</button>
        <div v-if="showDebug" class="debug-panel" ref="debugPanel">
            <div v-for="(log, i) in debugLogs" :key="i" :class="['log-entry', log.type]">
                {{ log.time }} {{ log.msg }}
            </div>
        </div>
    </div>
    <script>
        console.log('DEBUG: Script tag found, Vue available?', typeof Vue);
        const { createApp, ref, computed, onMounted, nextTick } = Vue;
        console.log('DEBUG: Destructuring Vue OK');
        createApp({
            setup() {
                console.log('DEBUG: Vue setup() started');
                const isSidebarOpen = ref(window.innerWidth > 768);
                const currentMarket = ref('TW');
                const currentStock = ref(__STOCK_LIST__[0]);
                const loading = ref(false);
                const error = ref(null);
                const showDebug = ref(false);
                const debugLogs = ref([]);
                const debugPanel = ref(null);
                
                // Yuanta-style new reactive variables
                const currentCandle = ref(null);
                const currentMAs = ref([]);
                const priceChange = ref(0);
                const priceChangePercent = ref(0);
                
                // Format volume (convert to K/M)
                const formatVolume = (vol) => {
                    if (!vol) return '--';
                    if (vol >= 1000000) return (vol / 1000000).toFixed(2) + 'M';
                    if (vol >= 1000) return (vol / 1000).toFixed(0) + 'K';
                    return vol.toString();
                };
                
                const addLog = (msg, type = '') => {
                    const now = new Date();
                    const time = now.toLocaleTimeString('zh-TW', {hour12: false}) + '.' + String(now.getMilliseconds()).padStart(3, '0');
                    debugLogs.value.push({ time, msg, type });
                    if (debugLogs.value.length > 50) debugLogs.value.shift();
                    // Auto scroll
                    nextTick(() => {
                        if (debugPanel.value) debugPanel.value.scrollTop = debugPanel.value.scrollHeight;
                    });
                    console.log(`[${time}] ${msg}`);
                };
                
                // Chart containers
                const chartsScrollContainer = ref(null);
                const mainChartContainer = ref(null);
                const rsiChartContainer = ref(null);
                const kdChartContainer = ref(null);
                const biasChartContainer = ref(null);
                const macdChartContainer = ref(null);
                
                // Chart instances
                let mainChart = null, candleSeries = null, maLines = [];
                let rsiChart = null, rsiLines = [];
                let kdChart = null, kLine = null, dLine = null;
                let biasChart = null, biasLines = [];
                let macdChart = null, difLine = null, deaLine = null, histSeries = null;
                
                let abortController = null;

                const filteredStocks = computed(() => __STOCK_LIST__.filter(s => s.market === currentMarket.value));

                const chartOptions = (container) => ({
                    layout: { background: { type: 'solid', color: '#131722' }, textColor: '#d1d4dc' },
                    grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
                    timeScale: { borderColor: '#485c7b', timeVisible: true, visible: true },
                    rightPriceScale: { borderColor: '#485c7b' },
                    crosshair: { mode: LightweightCharts.CrosshairMode.Normal }
                });

                const initCharts = () => {
                    // Main chart with candlesticks
                    mainChart = LightweightCharts.createChart(mainChartContainer.value, chartOptions());
                    candleSeries = mainChart.addCandlestickSeries();
                    
                    // RSI chart
                    rsiChart = LightweightCharts.createChart(rsiChartContainer.value, {
                        ...chartOptions(),
                        rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 } }
                    });
                    
                    // KD chart
                    kdChart = LightweightCharts.createChart(kdChartContainer.value, {
                        ...chartOptions(),
                        rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 } }
                    });
                    
                    // BIAS chart
                    biasChart = LightweightCharts.createChart(biasChartContainer.value, {
                        ...chartOptions(),
                        rightPriceScale: { scaleMargins: { top: 0.2, bottom: 0.2 } }
                    });
                    
                    // MACD chart
                    macdChart = LightweightCharts.createChart(macdChartContainer.value, {
                        ...chartOptions(),
                        rightPriceScale: { scaleMargins: { top: 0.2, bottom: 0.2 } }
                    });
                    
                    // Crosshair Move Handler - Yuanta Style
                    mainChart.subscribeCrosshairMove(param => {
                        if (!param.time) {
                            return;
                        }
                        
                        const candleData = param.seriesData.get(candleSeries);
                        if (candleData) {
                            currentCandle.value = candleData;
                            priceChange.value = candleData.close - candleData.open;
                            priceChangePercent.value = (priceChange.value / candleData.open) * 100;
                            
                            // Update MA values
                            const mas = [];
                            maLines.forEach((line, i) => {
                                const val = param.seriesData.get(line);
                                if (val !== undefined) {
                                    mas.push({ period: __MA_PERIODS__[i], value: val.value });
                                }
                            });
                            currentMAs.value = mas;
                        }
                    });
                    
                    // Sync all charts' time scales
                    const charts = [mainChart, rsiChart, kdChart, biasChart, macdChart];
                    charts.forEach(chart => {
                        chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
                            if (range) {
                                charts.forEach(c => {
                                    if (c !== chart) {
                                        c.timeScale().setVisibleLogicalRange(range);
                                    }
                                });
                            }
                        });
                    });
                    
                    // Resize observer
                    const resizeObserver = new ResizeObserver(() => {
                        [
                            [mainChartContainer, mainChart],
                            [rsiChartContainer, rsiChart],
                            [kdChartContainer, kdChart],
                            [biasChartContainer, biasChart],
                            [macdChartContainer, macdChart]
                        ].forEach(([container, chart]) => {
                            if (container.value && chart) {
                                chart.applyOptions({ 
                                    width: container.value.clientWidth, 
                                    height: container.value.clientHeight 
                                });
                            }
                        });
                    });
                    
                    [mainChartContainer, rsiChartContainer, kdChartContainer, biasChartContainer, macdChartContainer]
                        .forEach(c => { if (c.value) resizeObserver.observe(c.value); });
                };

                const loadStockData = async (stock) => {
                    const requestId = Date.now();
                    addLog(`🔵 SELECT: ${stock.symbol}`, 'log-success');
                    
                    if (abortController) {
                        addLog(`⚠️ Aborting previous request`, 'log-warn');
                        abortController.abort();
                    }
                    abortController = new AbortController();
                    const signal = abortController.signal;

                    loading.value = true; error.value = null;
                    const fetchStart = performance.now();
                    
                    try {
                        addLog(`📡 FETCH: /api/stock/${stock.symbol}`);
                        const res = await fetch(`/api/stock/${stock.symbol}`, { signal });
                        const fetchEnd = performance.now();
                        addLog(`✅ FETCH: ${(fetchEnd - fetchStart).toFixed(0)}ms`, 'log-success');
                        
                        if (!res.ok) throw new Error("API Error");
                        
                        const parseStart = performance.now();
                        const data = await res.json();
                        
                        // Reconstruct data if optimized format
                        if (data.dates) {
                            const dates = data.dates;
                            data.candles = dates.map((d, i) => ({
                                time: d,
                                open: data.opens[i], high: data.highs[i], low: data.lows[i], close: data.closes[i]
                            }));
                            
                            data.ma = data.ma.map(line => line.map((v, i) => ({ time: dates[i], value: v })));
                            data.rsi = data.rsi.map(line => line.map((v, i) => ({ time: dates[i], value: v })));
                            
                            data.kd.k = data.kd.k.map((v, i) => ({ time: dates[i], value: v }));
                            data.kd.d = data.kd.d.map((v, i) => ({ time: dates[i], value: v }));
                            
                            data.bias = data.bias.map(line => line.map((v, i) => ({ time: dates[i], value: v })));
                            
                            data.macd.dif = data.macd.dif.map((v, i) => ({ time: dates[i], value: v }));
                            data.macd.dea = data.macd.dea.map((v, i) => ({ time: dates[i], value: v }));
                            data.macd.histogram = data.macd.histogram.map((v, i) => ({ 
                                time: dates[i], value: v, color: v >= 0 ? '#26A69A' : '#EF5350'
                            }));
                        }
                        
                        addLog(`📊 JSON: candles=${data.candles?.length || 0}`);
                        
                        const renderStart = performance.now();
                        
                        // --- Main Chart: Candlesticks + MA ---
                        candleSeries.setData(data.candles);
                        
                        // Initialize current candle with the last one
                        if (data.candles.length > 0) {
                            const lastCandle = data.candles[data.candles.length - 1];
                            currentCandle.value = lastCandle;
                            priceChange.value = lastCandle.close - lastCandle.open;
                            priceChangePercent.value = (priceChange.value / lastCandle.open) * 100;
                        }
                        
                        maLines.forEach(l => mainChart.removeSeries(l));
                        maLines = [];
                        data.ma.forEach((m, i) => {
                            if(m.length) {
                                const l = mainChart.addLineSeries({ color: __MA_COLORS__[i], lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
                                l.setData(m);
                                maLines.push(l);
                            }
                        });
                        
                        maLines.forEach((l, i) => {
                            if (data.ma[i]?.length > 0) {
                                const lastMA = data.ma[i][data.ma[i].length - 1];
                                mas.push({ period: __MA_PERIODS__[i], value: lastMA.value });
                            }
                        });
                        currentMAs.value = mas;
                        
                        // --- RSI Chart ---
                        rsiLines.forEach(l => rsiChart.removeSeries(l));
                        rsiLines = [];
                        const rsiColors = ['#FF6B6B', '#4ECDC4'];
                        data.rsi.forEach((r, i) => {
                            if(r.length) {
                                const l = rsiChart.addLineSeries({ color: rsiColors[i], lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
                                l.setData(r);
                                rsiLines.push(l);
                            }
                        });
                        
                        // --- KD Chart ---
                        if (kLine) kdChart.removeSeries(kLine);
                        if (dLine) kdChart.removeSeries(dLine);
                        kLine = kdChart.addLineSeries({ color: '#FFEB3B', lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
                        dLine = kdChart.addLineSeries({ color: '#2196F3', lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
                        kLine.setData(data.kd.k);
                        dLine.setData(data.kd.d);
                        
                        // --- BIAS Chart (三條線: BIAS1=117, BIASAV1=17, BIASAV2=45) ---
                        biasLines.forEach(l => biasChart.removeSeries(l));
                        biasLines = [];
                        const biasColors = ['#E91E63', '#00BCD4', '#FFEB3B'];  // 粉紅、青色、黃色
                        data.bias.forEach((b, i) => {
                            if(b.length) {
                                const l = biasChart.addLineSeries({ color: biasColors[i], lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
                                l.setData(b);
                                biasLines.push(l);
                            }
                        });
                        
                        // --- MACD Chart ---
                        if (difLine) macdChart.removeSeries(difLine);
                        if (deaLine) macdChart.removeSeries(deaLine);
                        if (histSeries) macdChart.removeSeries(histSeries);
                        
                        difLine = macdChart.addLineSeries({ color: '#FFEB3B', lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
                        deaLine = macdChart.addLineSeries({ color: '#2196F3', lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
                        histSeries = macdChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
                        
                        difLine.setData(data.macd.dif);
                        deaLine.setData(data.macd.dea);
                        histSeries.setData(data.macd.histogram);
                        
                        addLog(`🎨 RENDER: ${(performance.now() - renderStart).toFixed(0)}ms`, 'log-success');
                        mainChart.timeScale().fitContent();
                        addLog(`✅ DONE: Total ${(performance.now() - fetchStart).toFixed(0)}ms`, 'log-success');
                    } catch (e) { 
                        if (e.name === 'AbortError') {
                            addLog(`🚫 ABORTED: ${stock.symbol}`, 'log-warn');
                            return;
                        }
                        addLog(`❌ ERROR: ${e.message}`, 'log-error');
                        error.value = e.message;
                    } finally {
                        if (!signal.aborted) {
                            loading.value = false;
                            addLog(`🏁 Loading=false`);
                        } else {
                            addLog(`⏭️ Skip loading=false (aborted)`, 'log-warn');
                        }
                    }
                };

                // Debounce helper
                const debounce = (fn, delay) => {
                    let timeout;
                    return (...args) => {
                        clearTimeout(timeout);
                        timeout = setTimeout(() => fn(...args), delay);
                    };
                };

                // Create a debounced version of loadStockData
                const _debouncedLoad = debounce((s) => {
                    // 確保只執行最後一次請求
                    if (currentStock.value.symbol === s.symbol) {
                        addLog(`🚀 Executing debounced load for ${s.symbol}`, 'log-success');
                        loadStockData(s);
                    } else {
                        addLog(`🚫 Skipping stale debounced load for ${s.symbol} (current=${currentStock.value.symbol})`, 'log-warn');
                    }
                }, 300);

                const selectStock = (s) => { 
                    addLog(`👆 CLICK: ${s.symbol}`, 'log-success');
                    currentStock.value = s; 
                    
                    if (loading.value) {
                        addLog(`⏳ Loading busy, debouncing ${s.symbol}...`, 'log-warn');
                        _debouncedLoad(s);
                    } else {
                        loadStockData(s);
                    }
                };

                onMounted(() => { 
                    nextTick(() => {
                        initCharts(); 
                        selectStock(currentStock.value); 
                    });
                });

                return { 
                    isSidebarOpen, currentMarket, currentStock, filteredStocks, selectStock, 
                    chartsScrollContainer, mainChartContainer, rsiChartContainer, kdChartContainer, biasChartContainer, macdChartContainer,
                    loading, error, showDebug, debugLogs, debugPanel,
                    currentCandle, currentMAs, priceChange, priceChangePercent, formatVolume
                };
            }
        }).mount('#app');
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def read_root():
    content = HTML_TEMPLATE.replace("__STOCK_LIST__", json.dumps(STOCK_LIST))
    content = content.replace("__MA_COLORS__", json.dumps(MA_COLORS))
    content = content.replace("__MA_PERIODS__", json.dumps(MA_PERIODS))
    return content

@app.get("/api/stock/{symbol}")
async def get_stock(symbol: str):
    # FAST PATH: Read from memory cache
    if symbol in STOCK_DATA_CACHE:
        return STOCK_DATA_CACHE[symbol]
        
    # Fallback: Run in background thread to avoid blocking
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, query_and_calculate, symbol)

@app.post("/api/admin/update")
async def manual_update():
    """Manual trigger for data update"""
    import asyncio
    loop = asyncio.get_event_loop()
    # Run update in background
    loop.run_in_executor(None, run_update_job)
    return {"status": "Update job started"}

def run_update_job():
    update_database()
    refresh_cache()

def query_and_calculate(symbol):
    print(f"[{symbol}] Cache miss, querying DB...")
    db = SessionLocal()
    try:
        rows = db.query(StockData).filter(StockData.symbol == symbol).order_by(StockData.date).all()
        
        if not rows:
            raise HTTPException(status_code=404, detail=f"No data for {symbol}")

        candles = []
        dates = []
        opens = []
        highs = []
        lows = []
        closes = []
        
        for r in rows:
            date_str = r.date.strftime('%Y-%m-%d')
            candles.append({
                "time": date_str,
                "open": r.open, "high": r.high, "low": r.low, "close": r.close
            })
            dates.append(date_str)
            opens.append(r.open)
            highs.append(r.high)
            lows.append(r.low)
            closes.append(r.close)
            
        df = pd.DataFrame({
            "date": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes
        })
        
        indicators = calculate_all_indicators(df)
        
        # Optimize structure
        ma_values = []
        for ma_line in indicators['ma']:
            ma_values.append([p['value'] for p in ma_line])
            
        rsi_values = []
        for rsi_line in indicators['rsi']:
            rsi_values.append([p['value'] for p in rsi_line])
            
        k_values = [p['value'] for p in indicators['kd']['k']]
        d_values = [p['value'] for p in indicators['kd']['d']]
        
        bias_values = []
        for bias_line in indicators['bias']:
            bias_values.append([p['value'] for p in bias_line])
            
        dif_values = [p['value'] for p in indicators['macd']['dif']]
        dea_values = [p['value'] for p in indicators['macd']['dea']]
        hist_values = [p['value'] for p in indicators['macd']['histogram']]
        
        optimized_result = {
            "symbol": symbol,
            "dates": dates,
            "opens": opens, "highs": highs, "lows": lows, "closes": closes,
            "ma": ma_values,
            "rsi": rsi_values,
            "kd": {"k": k_values, "d": d_values},
            "bias": bias_values,
            "macd": {"dif": dif_values, "dea": dea_values, "histogram": hist_values}
        }
        
        STOCK_DATA_CACHE[symbol] = optimized_result
        return JSONResponse(content=optimized_result)
    finally:
        db.close()
