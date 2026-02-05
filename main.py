import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import yfinance as yf
import pandas as pd
import json
import time
import requests_cache
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from fake_useragent import UserAgent

app = FastAPI()

# --- Configuration ---
CACHE = {}
CACHE_TTL = 3600
MA_PERIODS = [17, 45, 117, 189, 305, 494]
MA_COLORS = ['#FF6B6B', '#4ECDC4', '#FFE66D', '#1A535C', '#FF9F1C', '#C2F970']

# --- Enhanced Session for yfinance ---
def get_session():
    session = requests_cache.CachedSession('yfinance.cache')
    session.headers['User-Agent'] = UserAgent().random
    retry = Retry(connect=3, backoff_factor=0.5)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

STOCK_LIST = [
    {"symbol": "2330.TW", "name": "台積電", "market": "TW"},
    {"symbol": "2317.TW", "name": "鴻海", "market": "TW"},
    {"symbol": "2454.TW", "name": "聯發科", "market": "TW"},
    {"symbol": "2603.TW", "name": "長榮", "market": "TW"},
    {"symbol": "3231.TW", "name": "緯創", "market": "TW"},
    {"symbol": "NVDA", "name": "NVIDIA", "market": "US"},
    {"symbol": "AAPL", "name": "Apple", "market": "US"},
    {"symbol": "TSLA", "name": "Tesla", "market": "US"},
    {"symbol": "MSFT", "name": "Microsoft", "market": "US"},
    {"symbol": "AMD", "name": "AMD", "market": "US"},
]

# --- Frontend Template (Vue 3 + Tailwind) ---
# Using standard string to avoid f-string curly brace escaping issues
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-TW" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>StockView Pro</title>
    
    <!-- Stable CDNs -->
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
        
        /* Scrollbar */
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
            
            <!-- App Title -->
            <div class="p-4 border-b border-neutral-800">
                <h1 class="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-purple-500 flex items-center">
                    <i class="fas fa-chart-line mr-2 text-blue-400"></i> StockView
                </h1>
            </div>

            <!-- Market Tabs -->
            <div class="flex border-b border-neutral-800">
                <button @click="currentMarket = 'TW'" :class="['flex-1 py-3 text-sm font-medium transition-colors', currentMarket === 'TW' ? 'text-blue-400 border-b-2 border-blue-400 bg-neutral-800' : 'text-gray-400 hover:text-white hover:bg-neutral-800']">
                    🇹🇼 台股
                </button>
                <button @click="currentMarket = 'US'" :class="['flex-1 py-3 text-sm font-medium transition-colors', currentMarket === 'US' ? 'text-blue-400 border-b-2 border-blue-400 bg-neutral-800' : 'text-gray-400 hover:text-white hover:bg-neutral-800']">
                    🇺🇸 美股
                </button>
            </div>

            <!-- Search Box -->
            <div class="p-4">
                <div class="relative group">
                    <i class="fas fa-search absolute left-3 top-3 text-gray-500 group-focus-within:text-blue-400"></i>
                    <input v-model="searchQuery" @keyup.enter="searchCustomStock" 
                           type="text" placeholder="輸入代碼 (Enter搜尋)" 
                           class="w-full bg-neutral-800 text-white pl-10 pr-4 py-2 rounded-lg border border-neutral-700 focus:outline-none focus:border-blue-500 text-sm transition-colors">
                </div>
            </div>

            <!-- Stock List -->
            <div class="flex-1 overflow-y-auto">
                <div v-for="stock in filteredStocks" :key="stock.symbol" 
                     @click="selectStock(stock)"
                     :class="['p-3 mx-2 mb-1 rounded cursor-pointer transition-colors flex justify-between items-center', currentStock?.symbol === stock.symbol ? 'stock-item active' : 'hover:bg-neutral-800 text-gray-300']">
                    <div>
                        <div class="font-bold text-sm">{{ stock.symbol }}</div>
                        <div class="text-xs opacity-70">{{ stock.name }}</div>
                    </div>
                    <i v-if="currentStock?.symbol === stock.symbol" class="fas fa-chevron-right text-xs"></i>
                </div>
            </div>
            
            <div class="p-3 text-xs text-center text-gray-600 border-t border-neutral-800">
                OpenClaw v2026
            </div>
        </div>

        <!-- Main Chart Area -->
        <div class="flex-1 flex flex-col h-full bg-[#131722] relative w-0 min-w-0">
            
            <!-- Top Toolbar -->
            <div class="h-14 bg-[#1e222d] border-b border-[#2a2e39] flex items-center px-4 justify-between shrink-0">
                <div class="flex items-center">
                    <button @click="isSidebarOpen = !isSidebarOpen" class="text-gray-400 hover:text-white mr-4 focus:outline-none p-2 rounded hover:bg-[#2a2e39]">
                        <i class="fas fa-bars text-lg"></i>
                    </button>
                    
                    <div v-if="currentStock" class="flex flex-col">
                        <div class="flex items-baseline">
                            <span class="text-lg font-bold text-white mr-2">{{ currentStock.symbol }}</span>
                            <span class="text-sm text-gray-400">{{ currentStock.name }}</span>
                        </div>
                    </div>
                </div>

                <div class="flex items-center space-x-4">
                    <!-- MA Legend -->
                    <div class="hidden md:flex space-x-3 text-xs">
                        <div v-for="(p, i) in maPeriods" :key="p" class="flex items-center cursor-help" :title="'MA'+(i+1)">
                            <span class="w-2 h-2 rounded-full mr-1" :style="{backgroundColor: maColors[i]}"></span>
                            <span :style="{color: maColors[i]}">{{ p }}</span>
                        </div>
                    </div>
                    
                    <!-- Loading Indicator -->
                    <div v-if="loading" class="loader"></div>
                </div>
            </div>

            <!-- Chart Container -->
            <div class="flex-1 relative w-full h-full">
                <div ref="chartContainer" class="absolute inset-0"></div>
                
                <!-- Error Message -->
                <div v-if="error" class="absolute inset-0 flex flex-col items-center justify-center bg-black/80 z-50 text-red-400">
                    <i class="fas fa-bug text-4xl mb-3"></i>
                    <span class="text-lg font-medium">{{ error }}</span>
                    <button @click="loadStockData(currentStock)" class="mt-4 px-4 py-2 bg-neutral-800 rounded hover:bg-neutral-700 text-white">重試</button>
                </div>
            </div>

        </div>

    </div>

    <script>
        const { createApp, ref, computed, onMounted } = Vue;

        // Data Injection
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
                
                let chart = null;
                let candleSeries = null;
                let lineSeriesList = [];

                // Filter Logic
                const filteredStocks = computed(() => {
                    let list = RAW_STOCK_LIST.filter(s => s.market === currentMarket.value);
                    if (searchQuery.value) {
                        const q = searchQuery.value.toUpperCase();
                        list = list.filter(s => s.symbol.includes(q) || s.name.includes(q));
                    }
                    return list;
                });

                // Init Chart
                const initChart = () => {
                    if(!chartContainer.value) return;
                    
                    chart = LightweightCharts.createChart(chartContainer.value, {
                        layout: { background: { type: 'solid', color: '#131722' }, textColor: '#d1d4dc' },
                        grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
                        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
                        timeScale: { borderColor: '#485c7b', timeVisible: true }
                    });

                    candleSeries = chart.addCandlestickSeries({
                        upColor: '#ef5350', downColor: '#26a69a',
                        borderVisible: false, wickUpColor: '#ef5350', wickDownColor: '#26a69a'
                    });

                    // Resize Observer
                    new ResizeObserver(entries => {
                        if (entries.length === 0 || !entries[0].contentRect) return;
                        const { width, height } = entries[0].contentRect;
                        chart.applyOptions({ width, height });
                    }).observe(chartContainer.value);
                };

                // Fetch Data
                const loadStockData = async (stock) => {
                    if (!stock) return;
                    loading.value = true;
                    error.value = null;
                    
                    try {
                        // Apply colors based on market
                        if (stock.market === 'US') {
                            candleSeries.applyOptions({ upColor: '#26a69a', downColor: '#ef5350', wickUpColor: '#26a69a', wickDownColor: '#ef5350' });
                        } else {
                            candleSeries.applyOptions({ upColor: '#ef5350', downColor: '#26a69a', wickUpColor: '#ef5350', wickDownColor: '#26a69a' });
                        }

                        const res = await fetch(`/api/stock/${stock.symbol}`);
                        if (!res.ok) throw new Error(`API Error: ${res.status}`);
                        const data = await res.json();

                        // Sort candles (crucial for lightweight-charts)
                        const candles = data.candles.sort((a,b) => a.time.localeCompare(b.time));
                        if (candles.length === 0) throw new Error("無 K 線資料");
                        
                        candleSeries.setData(candles);

                        // Lines
                        lineSeriesList.forEach(s => chart.removeSeries(s));
                        lineSeriesList = [];

                        data.ma.forEach((maData, idx) => {
                            if (maData && maData.length > 0) {
                                maData.sort((a,b) => a.time.localeCompare(b.time));
                                const line = chart.addLineSeries({
                                    color: maColors[idx],
                                    lineWidth: 2,
                                    priceLineVisible: false,
                                    lastValueVisible: false,
                                    crosshairMarkerVisible: false
                                });
                                line.setData(maData);
                                lineSeriesList.push(line);
                            }
                        });

                        chart.timeScale().fitContent();

                    } catch (e) {
                        console.error(e);
                        error.value = e.message;
                    } finally {
                        loading.value = false;
                    }
                };

                const selectStock = (stock) => {
                    currentStock.value = stock;
                    if (window.innerWidth < 768) isSidebarOpen.value = false;
                    loadStockData(stock);
                };

                const searchCustomStock = () => {
                    if (!searchQuery.value) return;
                    const symbol = searchQuery.value.toUpperCase();
                    // Assume market based on input (simple heuristic or default to current)
                    const market = currentMarket.value;
                    const customStock = { symbol, name: symbol, market };
                    selectStock(customStock);
                };

                onMounted(() => {
                    initChart();
                    selectStock(currentStock.value);
                });

                return {
                    isSidebarOpen, currentMarket, searchQuery, currentStock,
                    filteredStocks, selectStock, searchCustomStock, loadStockData,
                    chartContainer, loading, error, maPeriods, maColors
                };
            }
        }).mount('#app');
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def read_root():
    # Inject JSON data into template
    # Avoids f-string/curly brace conflicts entirely
    content = HTML_TEMPLATE
    content = content.replace("__STOCK_LIST__", json.dumps(STOCK_LIST))
    content = content.replace("__MA_PERIODS__", json.dumps(MA_PERIODS))
    content = content.replace("__MA_COLORS__", json.dumps(MA_COLORS))
    return content

@app.get("/api/stock/{symbol}")
async def get_stock(symbol: str):
    try:
        current_time = time.time()
        if symbol in CACHE:
            timestamp, data = CACHE[symbol]
            if current_time - timestamp < CACHE_TTL:
                print(f"Serving {symbol} from cache")
                return data

        print(f"Fetching {symbol}...")
        
        # Use custom session with fake-useragent
        session = get_session()
        ticker = yf.Ticker(symbol, session=session)
        df = ticker.history(period="5y")
        
        if df.empty:
            # Fallback retry without session cache if empty (sometimes cache is bad)
            print("Empty dataframe, retrying without cache session...")
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="5y")
        
        if df.empty:
            raise HTTPException(status_code=404, detail="Stock not found or Rate Limited")

        df.columns = [c.lower() for c in df.columns]
        
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
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

import os

# ... (rest of imports)

# ... (app definition)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
