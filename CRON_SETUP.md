# StockCompass 自動更新設定

## 📅 排程說明

預設設定：**週一到週六每天早上 07:00 (UTC+8)** 執行股票資料更新

## 🚀 設定方式（三選一）

### 方式一：GitHub Actions（推薦）

已建立 `.github/workflows/daily-update.yml`，會自動：
1. 每日 UTC 23:00 (台灣時間 07:00+1) 執行
2. 從 FinMind API 拉取最新資料
3. 更新 SQLite 資料庫
4. 自動推送到 GitHub
5. Zeabur 會自動重新部署

**需要設定 Secrets:**
```bash
# 在 GitHub Repository Settings > Secrets > Actions 中設定：
FINMIND_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
```

### 方式二：系統 Cron（Linux/macOS）

在本地開發環境設定：

```bash
# 編輯 crontab
crontab -e

# 新增以下行（每天早上 07:00 執行）
0 7 * * 1-6 cd /home/node/.openclaw/workspace/stock_compass && python3 update_incremental.py >> /tmp/stock_update.log 2>&1
```

### 方式三：OpenClaw Cron

如果 OpenClaw Gateway 支援 cron，建立以下配置：

```json
{
  "name": "stockcompass-daily",
  "schedule": "0 7 * * 1-6",
  "command": "python3 /home/node/.openclaw/workspace/stock_compass/update_incremental.py"
}
```

## 📝 更新腳本說明

### `update_incremental.py` - 累積制更新

- 自動檢測資料庫中每支股票的最後日期
- 只拉取「最後日期+1天」到「今天」的新資料
- 使用 `INSERT OR REPLACE` 避免重複資料
- 更新完成後自動觸發伺服器快取重新載入

## 🔧 環境變數

```bash
FINMIND_TOKEN      # FinMind API Token（必需）
DB_PATH            # 資料庫檔案路徑（選填，預設 ./stocks.db）
API_BASE_URL       # API 基底 URL（選填，預設 https://stockcompass.zeabur.app）
```

## 📊 執行紀錄

執行後會輸出類似：

```
==================================================
🚀 StockCompass 累積制資料更新
⏰ 2026-03-01 07:00:00
==================================================

[1/10] 📈 2330 (台積電): 2026-02-27 ~ 2026-03-01 ✅ 新增 0 筆 (最新: 2026-02-26)
[2/10] 📈 2317 (鴻海): 2026-02-27 ~ 2026-03-01 ✅ 新增 0 筆 (最新: 2026-02-26)
...

==================================================
📊 更新摘要：0 支股票, 0 筆資料

✅ 無新資料，跳過快取重新載入
==================================================
```

## ⚠️ 注意事項

1. **台股假日**：週日無交易，但腳本會自動檢測，無資料時不會出錯
2. **美股時間**：美股收盤時間對應台灣時間凌晨，隔天早上才能取得
3. **速率限制**：腳本內建 1 秒間隔，避免觸發 FinMind API 限制
4. **資料庫對齊**：
   - 更新的是 `stocks.db`
   - 需要同步複製到 `stocks_20250211.db`（後端使用）

## 🔄 手動更新

```bash
cd /home/node/.openclaw/workspace/stock_compass
python3 update_incremental.py
```
