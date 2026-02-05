import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import pandas as pd
import json
import time
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from datetime import datetime

app = FastAPI()

# --- Configuration ---
CACHE = {}
CACHE_TTL = 3600  # 1 hour cache
MA_PERIODS = [17, 45, 117, 189, 305, 494]
MA_COLORS = ['#FF6B6B', '#4ECDC4', '#FFE66D', '#1A535C', '#FF9F1C', '#C2F970']

# --- Stock List (Google Finance Format) ---
# TPE = Taiwan, NASDAQ/NYSE = US
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

# --- Helper: Fetch from Google Finance ---
def fetch_google_data(google_symbol: str):
    # Google Finance doesn't have a clean history API publicly.
    # We will use a fallback: 'stooq.com' for CSV data which is very reliable for pandas.
    # Stooq symbols: 2330.TW, NVDA.US
    
    # Map Google symbol to Stooq symbol
    if "TPE:" in google_symbol:
        stooq_code = google_symbol.split(":")[1] + ".TW"
    elif "NASDAQ:" in google_symbol:
        stooq_code = google_symbol.split(":")[1] + ".US"
    elif "NYSE:" in google_symbol:
        stooq_code = google_symbol.split(":")[1] + ".US"
    else:
        stooq_code = google_symbol # Try direct

    url = f"https://stooq.com/q/d/l/?s={stooq_code}&i=d"
    print(f"Fetching from Stooq: {url}")
    
    try:
        df = pd.read_csv(url)
        # Stooq columns: Date, Open, High, Low, Close, Volume
        if df.empty or 'Date' not in df.columns:
            raise ValueError("Empty data")
            
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.sort_values('Date')
        
        # Rename cols to lowercase for compatibility
        df.columns = [c.lower() for c in df.columns]
        return df.tail(1200) # Last ~5 years approx
        
    except Exception as e:
        print(f"Stooq fetch error: {e}")
        raise HTTPException(status_code=404, detail="Data source unavailable")


# --- Frontend Template (Vue 3 + Tailwind) ---
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
        .sidebar-transition { transition: all 0.3s ease; }
        .loader { border: 3px solid #333; border-top: 3px solid #3498db; border-radius: 50%; width: 24px; height: 24px; animation: spin 1s linear infinite; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        [v-cloak] { display: none; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: #1a1a1a; }
        ::-webkit-scrollbar-thumb { background: #444; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #555; }
        .stock-item.active { background-color: #2563eb; color: white; }
    </style>
</head>
<body class="h-screen w-screen flex">
    <div id="app" v-cloak class="flex w-full h-full">
        <!-- Sidebar -->
        <div :class="['bg-neutral-900 border-r border-neutral-800 flex flex-col z-20 sidebar-transition h-full flex-shrink-0', isSidebarOpen ? 'w-80' : 'w-0 overflow-hidden']">
            <div class="p-4 border-b border-neutral-800">
                <h1 class="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-purple-500 flex items-center">
                    <i class="fas fa-chart-line mr-2 text-blue-400"></i> StockView
                </h1>
            </div>
            <div class="flex border-b border-neutral-800">
                <button @click="currentMarket = 'TW'" :class="['flex-1 py-3 text-sm font-medium transition-colors', currentMarket === 'TW' ? 'text-blue-400 border-b-2 border-blue-400 bg-neutral-800' : 'text-gray-400 hover:text-white hover:bg-neutral-800']">🇹🇼 台股</button>
                <button @click="currentMarket = 'US'" :class="['flex-1 py-3 text-sm font-medium transition-colors', currentMarket === 'US' ? 'text-blue-400 border-b-2 border-blue-400 bg-neutral-800' : 'text-gray-400 hover:text-white hover:bg-neutral-800']">🇺🇸 美股</button>
            </div>
            <div class="p-4">
                <div class="relative group">
                    <i class="fas fa-search absolute left-3 top-3 text-gray-500 group-focus-within:text-blue-400"></i>
                    <input v-model="searchQuery" type="text" placeholder="搜尋代碼..." class="w-full bg-neutral-800 text-white pl-10 pr-4 py-2 rounded-lg border border-neutral-700 focus:outline-none focus:border-blue-500 text-sm transition-colors">
                </div>
            </div>
            <div class="flex-1 overflow-y-auto">
                <div v-for="stock in filteredStocks" :key="stock.symbol" @click="selectStock(stock)" :class="['p-3 mx-2 mb-1 rounded cursor-pointer transition-colors flex justify-between items-center', currentStock?.symbol === stock.symbol ? 'stock-item active' : 'hover:bg-neutral-800 text-gray-300']">
                    <div>
                        <div class="font-bold text-sm">{{ stock.symbol }}</div>
                        <div class="text-xs opacity-70">{{ stock.name }}</div>
                    </div>
                    <i v-if="currentStock?.symbol === stock.symbol" class="fas fa-chevron-right text-xs"></i>
                </div>
            </div>
            <div class="p-3 text-xs text-center text-gray-600 border-t border-neutral-800">Source: Stooq</div>
        </div>

        <!-- Main Chart -->
        <div class="flex-1 flex flex-col h-full bg-[#131722] relative w-0 min-w-0">
            <div class="h-14 bg-[#1e222d] border-b border-[#2a2e39] flex items-center px-4 justify-between shrink-0">
                <div class="flex items-center">
                    <button @click="isSidebarOpen = !isSidebarOpen" class="text-gray-400 hover:text-white mr-4 focus:outline-none p-2 rounded hover:bg-[#2a2e39]"><i class="fas fa-bars text-lg"></i></button>
                    <div v-if="currentStock" class="flex flex-col">
                        <div class="flex items-baseline"><span class="text-lg font-bold text-white mr-2">{{ currentStock.symbol }}</span><span class="text-sm text-gray-400">{{ currentStock.name }}</span></div>
                    </div>
                </div>
                <div class="flex items-center space-x-4">
                    <div class="hidden md:flex space-x-3 text-xs">
                        <div v-for="(p, i) in maPeriods" :key="p" class="flex items-center"><span class="w-2 h-2 rounded-full mr-1" :style="{backgroundColor: maColors[i]}"></span><span :style="{color: maColors[i]}">{{ p }}</span></div>
                    </div>
                    <div v-if="loading" class="loader"></div>
                </div>
            </div>
            <div class="flex-1 relative w-full h-full">
                <div ref="chartContainer" class="absolute inset-0"></div>
                <div v-if="error" class="absolute inset-0 flex flex-col items-center justify-center bg-black/80 z-50 text-red-400">
                    <i class="fas fa-bug text-4xl mb-3"></i><span class="text-lg font-medium">{{ error }}</span>
                    <button @click="loadStockData(currentStock)" class="mt-4 px-4 py-2 bg-neutral-800 rounded hover:bg-neutral-700 text-white">重試</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        const { createApp, ref, computed, onMounted } = Vue;
        const RAW_STOCK_LIST = __STOCK_LIST__;
        const RAW_MA_PERIODS = __MA_PERIODS__;
        const RAW_MA_COLORS = __MA_COLORS__;

        createApp({
            setup() {
                const isSidebarOpen = ref(window.innerWidth > 768);
                const currentMarket = ref('TW');
                const searchQuery = ref('');
                const currentStock = ref(RAW_STOCK_LIST[0]);
                const loading = ref(false);
                const error = ref(null);
                const chartContainer = ref(null);
                const maPeriods = RAW_MA_PERIODS;
                const maColors = RAW_MA_COLORS;
                let chart = null, candleSeries = null, lineSeriesList = [];

                const filteredStocks = computed(() => {
                    let list = RAW_STOCK_LIST.filter(s => s.market === currentMarket.value);
                    if (searchQuery.value) {
                        const q = searchQuery.value.toUpperCase();
                        list = list.filter(s => s.symbol.includes(q) || s.name.includes(q));
                    }
                    return list;
                });

                const initChart = () => {
                    if(!chartContainer.value) return;
                    chart = LightweightCharts.createChart(chartContainer.value, {
                        layout: { background: { type: 'solid', color: '#131722' }, textColor: '#d1d4dc' },
                        grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
                        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
                        timeScale: { borderColor: '#485c7b', timeVisible: true }
                    });
                    candleSeries = chart.addCandlestickSeries({ upColor: '#ef5350', downColor: '#26a69a', borderVisible: false, wickUpColor: '#ef5350', wickDownColor: '#26a69a' });
                    new ResizeObserver(entries => {
                        if (entries.length === 0 || !entries[0].contentRect) return;
                        const { width, height } = entries[0].contentRect;
                        chart.applyOptions({ width, height });
                    }).observe(chartContainer.value);
                };

                const loadStockData = async (stock) => {
                    if (!stock) return;
                    loading.value = true;
                    error.value = null;
                    try {
                        const res = await fetch(`/api/stock/${stock.symbol}`);
                        if (!res.ok) throw new Error("API Error");
                        const data = await res.json();
                        const candles = data.candles.sort((a,b) => a.time.localeCompare(b.time));
                        if (candles.length === 0) throw new Error("No Data");
                        candleSeries.setData(candles);
                        
                        lineSeriesList.forEach(s => chart.removeSeries(s));
                        lineSeriesList = [];
                        data.ma.forEach((maData, idx) => {
                            if (maData && maData.length > 0) {
                                const line = chart.addLineSeries({ color: maColors[idx], lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
                                line.setData(maData);
                                lineSeriesList.push(line);
                            }
                        });
                        chart.timeScale().fitContent();
                    } catch (e) { error.value = e.message; } finally { loading.value = false; }
                };

                const selectStock = (stock) => {
                    currentStock.value = stock;
                    if (window.innerWidth < 768) isSidebarOpen.value = false;
                    loadStockData(stock);
                };

                onMounted(() => { initChart(); selectStock(currentStock.value); });

                return { isSidebarOpen, currentMarket, searchQuery, currentStock, filteredStocks, selectStock, loadStockData, chartContainer, loading, error, maPeriods, maColors };
            }
        }).mount('#app');
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def read_root():
    content = HTML_TEMPLATE
    content = content.replace("__STOCK_LIST__", json.dumps(STOCK_LIST))
    content = content.replace("__MA_PERIODS__", json.dumps(MA_PERIODS))
    content = content.replace("__MA_COLORS__", json.dumps(MA_COLORS))
    return content

@app.get("/api/stock/{symbol}")
async def get_stock(symbol: str):
    # Find full google symbol info if possible
    target_stock = next((s for s in STOCK_LIST if s["symbol"] == symbol), None)
    google_symbol = target_stock["google_symbol"] if target_stock else symbol # fallback

    # Check cache
    current_time = time.time()
    if symbol in CACHE:
        timestamp, data = CACHE[symbol]
        if current_time - timestamp < CACHE_TTL:
            print(f"Serving {symbol} from cache")
            return data

    try:
        # Use Stooq
        df = fetch_google_data(google_symbol)
        
        # Calculate MAs
        ma_results = []
        for p in MA_PERIODS:
            ma_col = df['close'].rolling(window=p).mean()
            ma_data = []
            for date, val in ma_col.items():
                if not pd.isna(val):
                    ma_data.append({"time": date.strftime('%Y-%m-%d'), "value": val})
            ma_results.append(ma_data)

        # Candles
        candles = []
        for date, row in df.iterrows():
            candles.append({
                "time": date.strftime('%Y-%m-%d'),
                "open": row['open'], "high": row['high'],
                "low": row['low'], "close": row['close']
            })

        response_data = {"symbol": symbol, "candles": candles, "ma": ma_results}
        CACHE[symbol] = (current_time, response_data)
        return response_data

    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
