#!/usr/bin/env python3
"""
Incremental Stock Data Updater - 累積制資料更新
自動從資料庫最新日期開始拉取新資料
更新完成後自動觸發伺服器快取重新載入
"""

import sqlite3
import urllib.request
import urllib.error
import json
import time
import os
from datetime import datetime, date, timedelta

FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJkYXRlIjoiMjAyNi0wMi0xMiAxMjowNjozNCIsInVzZXJfaWQiOiJuZW9kZWJ1dCIsImVtYWlsIjoibmVvZGVidXRAZ21haWwuY29tIiwiaXAiOiIyMjMuMTQxLjIxNi4xMSJ9.kpmrDf9WLQWQicRescUReBW0-8EVmCnQmt2fttsftd4")

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

DB_PATH = os.getenv("DB_PATH", "/home/node/.openclaw/workspace/stock_compass/stocks.db")
API_BASE_URL = os.getenv("API_BASE_URL", "https://stockcompass.zeabur.app")

def get_db_latest_date(symbol):
    """取得資料庫中某股票的最後日期"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT MAX(date) FROM stock_history WHERE symbol = ?', (symbol,))
    result = cursor.fetchone()
    conn.close()
    if result and result[0]:
        return datetime.strptime(result[0], '%Y-%m-%d').date()
    return None

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
            return {
                "symbol": symbol,
                "date": date_str,
                "open": float(row.get("open", 0) or 0),
                "high": float(row.get("max", row.get("high", 0)) or 0),
                "low": float(row.get("min", row.get("low", 0)) or 0),
                "close": float(row.get("close", 0) or 0),
                "volume": int(row.get("Trading_Volume", 0) or 0)
            }
        else:
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

def trigger_cache_refresh():
    """觸發伺服器快取重新載入"""
    try:
        req = urllib.request.Request(f"{API_BASE_URL}/api/admin/update", method='POST')
        req.add_header('User-Agent', 'Mozilla/5.0 (compatible; StockBot/1.0)')
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode('utf-8'))
            print(f"  📡 快取重新載入狀態: {result.get('status', 'unknown')}")
            return True
    except Exception as e:
        print(f"  ⚠️ 快取重新載入失敗: {e}")
        return False

def update_stock(stock):
    """更新單一股票資料 - 累積制"""
    symbol = stock['symbol']
    data_id = stock['data_id']
    dataset = stock['dataset']
    name = stock['name']
    
    # 取得資料庫最後日期
    latest_date = get_db_latest_date(symbol)
    today = date.today()
    
    if latest_date:
        # 從最後日期的下一天開始
        start_date = (latest_date + timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        # 全新的股票，從 2020 年開始
        start_date = "2020-01-01"
    
    end_date = today.strftime('%Y-%m-%d')
    
    # 如果已是最新，跳過
    if start_date > end_date:
        print(f"📈 {symbol} ({name}): ✅ 已是最新 ({latest_date})")
        return 0
    
    print(f"📈 {symbol} ({name}): {start_date} ~ {end_date}", end="", flush=True)
    
    # 獲取資料
    data = fetch_finmind_data(symbol, data_id, dataset, start_date, end_date)
    if data is None:
        print(" ❌ API 錯誤")
        return 0
    if not data:
        print(" ✅ 無新資料")
        return 0
    
    # 解析並儲存
    records = []
    for row in data:
        rec = parse_record(row, symbol, dataset)
        if rec:
            records.append(rec)
    
    if records:
        inserted = save_to_db(records)
        new_latest = get_db_latest_date(symbol)
        print(f" ✅ 新增 {inserted} 筆 (最新: {new_latest})")
        return inserted
    else:
        print(" ⚠️ 無有效資料")
        return 0

def main():
    print("=" * 60)
    print("🚀 StockCompass 累積制資料更新")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    total_inserted = 0
    stocks_updated = 0
    
    for i, stock in enumerate(STOCK_LIST, 1):
        print(f"\n[{i}/{len(STOCK_LIST)}] ", end="")
        count = update_stock(stock)
        if count > 0:
            total_inserted += count
            stocks_updated += 1
        time.sleep(1)  # API 友善間隔
    
    print("\n" + "=" * 60)
    print(f"📊 更新摘要：{stocks_updated} 支股票, {total_inserted} 筆資料")
    
    # 如果有更新，觸發快取重新載入
    if total_inserted > 0:
        print("\n🔄 觸發伺服器快取重新載入...")
        time.sleep(2)
        trigger_cache_refresh()
    else:
        print("\n✅ 無新資料，跳過快取重新載入")
    
    print("=" * 60)

if __name__ == "__main__":
    main()
