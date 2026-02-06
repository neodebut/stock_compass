import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import pandas as pd
import json
import os
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Float, Integer, Date
from sqlalchemy.orm import sessionmaker, declarative_base
from contextlib import asynccontextmanager

# --- Configuration ---
MA_PERIODS = [17, 45, 117, 189, 305, 494]
MA_COLORS = ['#FF6B6B', '#4ECDC4', '#FFE66D', '#1A535C', '#FF9F1C', '#C2F970']

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

# --- Load seed data from JSON ---
def load_seed_data():
    """Load pre-fetched data from initial_data.json into database"""
    seed_file = os.path.join(os.path.dirname(__file__), 'initial_data.json')
    if not os.path.exists(seed_file):
        print("No initial_data.json found, skipping seed load")
        return
    
    print(">>> Loading seed data from initial_data.json...")
    db = SessionLocal()
    
    with open(seed_file, 'r') as f:
        all_data = json.load(f)
    
    for symbol, records in all_data.items():
        # Check if already loaded
        existing = db.query(StockData).filter(StockData.symbol == symbol).count()
        if existing > 0:
            print(f"  [{symbol}] Already has {existing} records, skipping")
            continue
        
        # Insert records
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
    
    db.close()
    print("<<< Seed data loaded successfully!")

# --- Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: load seed data
    load_seed_data()
    yield
    # Shutdown: nothing to clean up

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
        body { background-color: #1a1a1a; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; overflow: hidden; }
        .loader { border: 3px solid #333; border-top: 3px solid #3498db; border-radius: 50%; width: 24px; height: 24px; animation: spin 1s linear infinite; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        [v-cloak] { display: none; }
        .stock-item.active { background-color: #2563eb; color: white; }
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
        <!-- Chart -->
        <div class="flex-1 flex flex-col h-full bg-[#131722] relative">
            <div class="h-14 bg-[#1e222d] border-b border-[#2a2e39] flex items-center px-4 justify-between">
                <div class="flex items-center">
                    <button @click="isSidebarOpen = !isSidebarOpen" class="text-gray-400 mr-4"><i class="fas fa-bars"></i></button>
                    <div v-if="currentStock" class="text-lg font-bold text-white">{{ currentStock.symbol }} <span class="text-sm font-normal text-gray-400">{{ currentStock.name }}</span></div>
                </div>
                <div v-if="loading" class="loader"></div>
            </div>
            <div class="flex-1 relative w-full h-full">
                <div ref="chartContainer" class="absolute inset-0"></div>
                <div v-if="error" class="absolute inset-0 flex items-center justify-center bg-black/80 text-red-400 z-50">{{ error }}</div>
            </div>
        </div>
    </div>
    <script>
        const { createApp, ref, computed, onMounted } = Vue;
        createApp({
            setup() {
                const isSidebarOpen = ref(window.innerWidth > 768);
                const currentMarket = ref('TW');
                const currentStock = ref(__STOCK_LIST__[0]);
                const loading = ref(false);
                const error = ref(null);
                const chartContainer = ref(null);
                let chart = null, candleSeries = null, maLines = [];
                let abortController = null;

                const filteredStocks = computed(() => __STOCK_LIST__.filter(s => s.market === currentMarket.value));

                const initChart = () => {
                    chart = LightweightCharts.createChart(chartContainer.value, {
                        layout: { background: { type: 'solid', color: '#131722' }, textColor: '#d1d4dc' },
                        grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
                        timeScale: { borderColor: '#485c7b', timeVisible: true }
                    });
                    candleSeries = chart.addCandlestickSeries();
                    new ResizeObserver(e => {
                         if(e.length) chart.applyOptions({width: e[0].contentRect.width, height: e[0].contentRect.height});
                    }).observe(chartContainer.value);
                };

                const loadStockData = async (stock) => {
                    // Cancel previous request if it exists
                    if (abortController) abortController.abort();
                    abortController = new AbortController();
                    const signal = abortController.signal;

                    loading.value = true; error.value = null;
                    try {
                        const res = await fetch(`/api/stock/${stock.symbol}`, { signal });
                        if (!res.ok) throw new Error("API Error");
                        const data = await res.json();
                        
                        candleSeries.setData(data.candles);
                        
                        maLines.forEach(l => chart.removeSeries(l));
                        maLines = [];
                        data.ma.forEach((m, i) => {
                            if(m.length) {
                                const l = chart.addLineSeries({ color: __MA_COLORS__[i], lineWidth: 2, lastValueVisible: false });
                                l.setData(m);
                                maLines.push(l);
                            }
                        });
                        chart.timeScale().fitContent();
                        loading.value = false; // Only clear loading if successful
                    } catch (e) { 
                        if (e.name === 'AbortError') {
                            console.log('Fetch aborted for ' + stock.symbol);
                            // Do not clear loading or error, let the next request handle it
                        } else {
                            error.value = e.message; 
                            loading.value = false;
                        }
                    }
                };

                const selectStock = (s) => { currentStock.value = s; loadStockData(s); };

                onMounted(() => { initChart(); selectStock(currentStock.value); });

                return { isSidebarOpen, currentMarket, currentStock, filteredStocks, selectStock, chartContainer, loading, error };
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
    db = SessionLocal()
    rows = db.query(StockData).filter(StockData.symbol == symbol).order_by(StockData.date).all()
    db.close()
    
    if not rows:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    candles = []
    closes = []
    dates = []
    
    for r in rows:
        date_str = r.date.strftime('%Y-%m-%d')
        candles.append({
            "time": date_str,
            "open": r.open, "high": r.high, "low": r.low, "close": r.close
        })
        closes.append(r.close)
        dates.append(date_str)
        
    # Calculate MAs
    df = pd.DataFrame({"close": closes, "date": dates})
    ma_results = []
    for p in MA_PERIODS:
        if len(df) >= p:
            ma_col = df['close'].rolling(window=p).mean()
            ma_data = []
            for idx, val in ma_col.items():
                if not pd.isna(val):
                    ma_data.append({"time": df.loc[idx, "date"], "value": val})
            ma_results.append(ma_data)
        else:
            ma_results.append([])

    return {"symbol": symbol, "candles": candles, "ma": ma_results}
