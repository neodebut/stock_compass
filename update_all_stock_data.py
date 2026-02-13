#!/usr/bin/env python3
"""
Stock Data Updater - 使用 FinMind API 獲取 2000~現在的歷史股價資料
使用標準庫，無需 pandas/sqlalchemy
"""

import sqlite3
import urllib.request
import urllib.error
import json
import time
from datetime import datetime, date

# FinMind API Token
FINMIND_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJkYXRlIjoiMjAyNi0wMi0xMiAxMjowNjozNCIsInVzZXJfaWQiOiJuZW9kZWJ1dCIsImVtYWlsIjoibmVvZGVidXRAZ21haWwuY29tIiwiaXAiOiIyMjMuMTQxLjIxNi4xMSJ9.kpmrDf9WLQWQicRescUReBW0-8EVmCnQmt2fttsftd4"

# 股票列表
STOCK_LIST = [
    {"symbol": "2330", "data_id": "2330", "name": "台積電", "market": "TW", "dataset": "TaiwanStockPrice"},
    {"symbol": "2317", "data_id": "2317", "name": "鴻海", "market": "TW", "dataset": "TaiwanStockPrice"},
    {"symbol": "2454", "data_id": "2454", "name": "聯發科", "market": "TW", "dataset": "TaiwanStockPrice"},
    {"symbol": "2603", "data_id": "2603", "name": "長榮", "market": "TW", "dataset": "TaiwanStockPrice"},
    {"symbol": "3231", "data_id": "3231", "name": "緯創", "market": "TW", "dataset": "TaiwanStockPrice"},
    {"symbol": "NVDA", "data_id": "NVDA", "name": "NVIDIA", "market": "US", "dataset": "USStockPrice"},
    {"symbol": "AAPL", "data_id": "AAPL", "name": "Apple", "market": "US", "dataset": "USStockPrice"},
    {"symbol": "TSLA", "data_id": "TSLA", "name": "Tesla", "market": "US", "dataset": "USStockPrice"},
    {"symbol": "MSFT", "data_id": "MSFT", "name": "Microsoft", "market": "US", "dataset": "USStockPrice"},
    {"symbol": "AMD", "data_id": "AMD", "name": "AMD", "market": "US", "dataset": "USStockPrice"},
]

DB_PATH = "/home/node/.openclaw/workspace/projects/stock_compass/stocks.db"

def init_database():
    """初始化資料庫"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            date DATE NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            UNIQUE(symbol, date)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_symbol ON stock_history(symbol)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_date ON stock_history(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_symbol_date ON stock_history(symbol, date)')
    conn.commit()
    conn.close()
    print("✅ 資料庫初始化完成")

def fetch_finmind_data(symbol, data_id, dataset, start_date, end_date):
    """從 FinMind 獲取資料"""
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": dataset,
        "data_id": data_id,
        "start_date": start_date,
        "end_date": end_date,
        "token": FINMIND_TOKEN
    }
    
    # Build URL with params
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    full_url = f"{url}?{query_string}"
    
    try:
        req = urllib.request.Request(full_url, method='GET')
        req.add_header('User-Agent', 'Mozilla/5.0 (compatible; StockBot/1.0)')
        
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            if data.get("msg") != "success":
                print(f"  ⚠️ API 錯誤: {data.get('msg')}")
                return None
            
            return data.get("data", [])
    except urllib.error.HTTPError as e:
        print(f"  ❌ HTTP 錯誤: {e.code}")
        return None
    except Exception as e:
        print(f"  ❌ 錯誤: {e}")
        return None

def parse_record(row, symbol, dataset):
    """解析 FinMind 資料格式"""
    date_str = row.get("date", "")
    if not date_str:
        return None
    
    try:
        if dataset == "TaiwanStockPrice":
            # 台股格式
            return {
                "symbol": symbol,
                "date": date_str,  # Keep as string YYYY-MM-DD
                "open": float(row.get("open", 0) or 0),
                "high": float(row.get("max", row.get("high", 0)) or 0),
                "low": float(row.get("min", row.get("low", 0)) or 0),
                "close": float(row.get("close", 0) or 0),
                "volume": int(row.get("Trading_Volume", 0) or 0)
            }
        else:
            # 美股格式
            return {
                "symbol": symbol,
                "date": date_str,
                "open": float(row.get("Open", row.get("open", 0)) or 0),
                "high": float(row.get("High", row.get("high", 0)) or 0),
                "low": float(row.get("Low", row.get("low", 0)) or 0),
                "close": float(row.get("Close", row.get("close", 0)) or 0),
                "volume": int(row.get("Volume", row.get("Trading_Volume", 0)) or 0)
            }
    except Exception as e:
        print(f"  ⚠️ 解析錯誤: {e}")
        return None

def get_db_status():
    """取得資料庫目前狀態"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_history'")
    if not cursor.fetchone():
        conn.close()
        return {}
    
    cursor.execute('''
        SELECT symbol, MIN(date), MAX(date), COUNT(*) 
        FROM stock_history 
        GROUP BY symbol
    ''')
    
    result = {}
    for row in cursor.fetchall():
        result[row[0]] = {
            'min_date': row[1],
            'max_date': row[2],
            'count': row[3]
        }
    conn.close()
    return result

def save_to_db(records):
    """儲存資料到資料庫，使用 INSERT OR REPLACE 避免重複"""
    if not records:
        return 0
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    inserted = 0
    for rec in records:
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO stock_history (symbol, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (rec['symbol'], rec['date'], rec['open'], rec['high'], 
                  rec['low'], rec['close'], rec['volume']))
            inserted += 1
        except Exception as e:
            print(f"    ⚠️ 插入錯誤: {e}")
    
    conn.commit()
    conn.close()
    return inserted

def update_stock(stock, start_year=2000, end_year=2026):
    """更新單一股票資料，按年分批獲取"""
    symbol = stock['symbol']
    data_id = stock['data_id']
    dataset = stock['dataset']
    name = stock['name']
    
    print(f"\n📈 處理 {symbol} ({name})...")
    
    total_records = 0
    
    # 按年分批獲取
    for year in range(start_year, end_year + 1):
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"
        
        # 如果是今年，使用今天的日期
        if year == 2026:
            end_date = datetime.now().strftime("%Y-%m-%d")
        
        print(f"  📅 {year}年: ", end="", flush=True)
        
        data = fetch_finmind_data(symbol, data_id, dataset, start_date, end_date)
        
        if data is None:
            print("API 錯誤")
            time.sleep(2)
            continue
        
        if not data:
            print("無資料")
            time.sleep(0.5)
            continue
        
        # 解析資料
        records = []
        for row in data:
            rec = parse_record(row, symbol, dataset)
            if rec:
                records.append(rec)
        
        if records:
            inserted = save_to_db(records)
            total_records += inserted
            print(f"✅ {len(records)} 筆 (新增/更新 {inserted} 筆)")
        else:
            print("無有效資料")
        
        time.sleep(0.5)  # 避免 API 限制
    
    print(f"  📊 {symbol} 總計: {total_records} 筆資料")
    return total_records

def main():
    print("=" * 60)
    print("🚀 StockView Pro - 歷史股價資料更新程式")
    print("=" * 60)
    print(f"⏰ 開始時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 初始化資料庫
    init_database()
    
    # 檢查目前資料庫狀態
    print("\n📋 目前資料庫狀態:")
    status = get_db_status()
    if status:
        for sym, info in status.items():
            print(f"  {sym}: {info['count']} 筆 ({info['min_date']} ~ {info['max_date']})")
    else:
        print("  (資料庫為空)")
    
    # 更新所有股票
    print("\n" + "=" * 60)
    print("📥 開始獲取歷史資料 (2000-2026)")
    print("=" * 60)
    
    grand_total = 0
    for i, stock in enumerate(STOCK_LIST, 1):
        print(f"\n[{i}/{len(STOCK_LIST)}] ", end="")
        count = update_stock(stock, start_year=2000, end_year=2026)
        grand_total += count
        time.sleep(3)  # 股票之間間隔，避免 API 限制
    
    # 總結
    print("\n" + "=" * 60)
    print("✅ 更新完成!")
    print("=" * 60)
    print(f"📊 總計新增/更新: {grand_total} 筆資料")
    
    print("\n📋 更新後資料庫狀態:")
    status = get_db_status()
    for sym, info in status.items():
        print(f"  {sym}: {info['count']} 筆 ({info['min_date']} ~ {info['max_date']})")
    
    print(f"\n⏰ 結束時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    main()
