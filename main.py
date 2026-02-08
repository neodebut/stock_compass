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

# --- Data Fetching Logic (Restored) ---
def fetch_stooq_data(symbol_info):
    symbol = symbol_info['symbol']
    google_symbol = symbol_info['google_symbol']
    
    # Map to Stooq format
    if "TPE:" in google_symbol:
        stooq_code = google_symbol.split(":")[1] + ".TW"
    elif "NASDAQ:" in google_symbol:
        stooq_code = google_symbol.split(":")[1] + ".US"
    elif "NYSE:" in google_symbol:
        stooq_code = google_symbol.split(":")[1] + ".US"
    else:
        stooq_code = google_symbol

    url = f"https://stooq.com/q/d/l/?s={stooq_code}&i=d"
    print(f"[{symbol}] Fetching from {url}...")
    
    try:
        headers = {'User-Agent': UserAgent().random}
        res = requests.get(url, headers=headers, timeout=15)
        
        if res.status_code != 200:
            print(f"[{symbol}] Failed: HTTP {res.status_code}")
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
    db = SessionLocal()
    
    for stock in STOCK_LIST:
        symbol = stock['symbol']
        
        # 查詢資料庫中該股票最新的日期
        from sqlalchemy import func
        latest_record = db.query(func.max(StockData.date)).filter(
            StockData.symbol == symbol
        ).scalar()
        
        if latest_record:
            print(f"[{symbol}] Latest date in DB: {latest_record}")
        else:
            print(f"[{symbol}] No existing data, will fetch all")
        
        # 抓取資料
        data = fetch_stooq_data(stock)
        
        if not data:
            print(f"[{symbol}] Fetch failed, keeping existing data.")
            continue
        
        # 過濾出新資料（日期比資料庫中最新日期還新的）
        if latest_record:
            new_data = [d for d in data if d['date'] > latest_record]
        else:
            new_data = data
        
        if not new_data:
            print(f"[{symbol}] Already up-to-date, no new data.")
            time.sleep(2)
            continue
        
        # 只插入新資料
        objects = [StockData(**d) for d in new_data]
        db.bulk_save_objects(objects)
        db.commit()
        print(f"[{symbol}] Added {len(new_data)} new records. (Total fetched: {len(data)})")
        
        time.sleep(5) 
    
    db.close()
    print("<<< Incremental database update complete.")

def refresh_cache():
    """Load all stock data from DB into memory cache with all indicators"""
    print(">>> Refreshing memory cache...")
    db = SessionLocal()
    try:
        stocks = db.query(StockData).all()
        
        data_by_symbol = {}
        for row in stocks:
            if row.symbol not in data_by_symbol:
                data_by_symbol[row.symbol] = []
            data_by_symbol[row.symbol].append(row)
            
        for symbol, rows in data_by_symbol.items():
            rows.sort(key=lambda x: x.date)
            
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
            
            STOCK_DATA_CACHE[symbol] = {
                "symbol": symbol, 
                "candles": candles,
                **indicators
            }
            
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
    <div id="app" v-cloak class="flex w-full h-full">
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
        <div class="flex-1 flex flex-col h-full bg-[#131722] relative overflow-hidden">
            <!-- Header -->
            <div class="h-14 bg-[#1e222d] border-b border-[#2a2e39] flex items-center px-4 justify-between flex-shrink-0">
                <div class="flex items-center">
                    <button @click="isSidebarOpen = !isSidebarOpen" class="text-gray-400 mr-4"><i class="fas fa-bars"></i></button>
                    <div v-if="currentStock" class="text-lg font-bold text-white">{{ currentStock.symbol }} <span class="text-sm font-normal text-gray-400">{{ currentStock.name }}</span></div>
                </div>
                <div v-if="loading" class="loader"></div>
            </div>
            <!-- Charts Container (Scrollable) -->
            <div class="flex-1 overflow-y-auto" ref="chartsScrollContainer">
                <!-- Main Candlestick Chart -->
                <div class="relative" style="height: 55%;">
                    <span class="chart-label">K線 + MA</span>
                    <div ref="mainChartContainer" class="absolute inset-0"></div>
                </div>
                <!-- RSI Chart -->
                <div class="relative chart-section" style="height: 12%;">
                    <span class="chart-label">RSI (17, 44)</span>
                    <div ref="rsiChartContainer" class="absolute inset-0"></div>
                </div>
                <!-- KD Chart -->
                <div class="relative chart-section" style="height: 12%;">
                    <span class="chart-label">KD (17, 3, 3)</span>
                    <div ref="kdChartContainer" class="absolute inset-0"></div>
                </div>
                <!-- BIAS Chart -->
                <div class="relative chart-section" style="height: 10%;">
                    <span class="chart-label">BIAS (117, 17, 45)</span>
                    <div ref="biasChartContainer" class="absolute inset-0"></div>
                </div>
                <!-- MACD Chart -->
                <div class="relative chart-section" style="height: 15%;">
                    <span class="chart-label">MACD (45, 117, 17)</span>
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
        const { createApp, ref, computed, onMounted, nextTick } = Vue;
        createApp({
            setup() {
                const isSidebarOpen = ref(window.innerWidth > 768);
                const currentMarket = ref('TW');
                const currentStock = ref(__STOCK_LIST__[0]);
                const loading = ref(false);
                const error = ref(null);
                const showDebug = ref(false);
                const debugLogs = ref([]);
                const debugPanel = ref(null);
                
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
                        addLog(`📊 JSON: candles=${data.candles?.length || 0}`);
                        
                        const renderStart = performance.now();
                        
                        // --- Main Chart: Candlesticks + MA ---
                        candleSeries.setData(data.candles);
                        maLines.forEach(l => mainChart.removeSeries(l));
                        maLines = [];
                        data.ma.forEach((m, i) => {
                            if(m.length) {
                                const l = mainChart.addLineSeries({ color: __MA_COLORS__[i], lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
                                l.setData(m);
                                maLines.push(l);
                            }
                        });
                        
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
                    loading, error, showDebug, debugLogs, debugPanel
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
        
        result = {"symbol": symbol, "candles": candles, **indicators}
        STOCK_DATA_CACHE[symbol] = result
        return JSONResponse(content=result)
    finally:
        db.close()
