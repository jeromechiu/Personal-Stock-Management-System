# AI 投資組合分析與模擬交易系統

這是一套以 Python、CrewAI 與 Google Gemini 建立的本機投資分析程式。程式從 MariaDB 讀取持倉，參考歷史價格與市場指標產生繁體中文分析，並可將報告傳送到 Telegram。

> **交易模式：**本程式只在本機資料庫模擬成交，不連接券商，也不會送出真實委託。模擬損益未扣手續費、交易稅、滑價或流動性影響，不保證獲利。

## 功能摘要

- 從 MariaDB 動態讀取持倉、成本與模擬現金。
- 以 15 年日線 OHLC 資料計算日線、週線、月線、年線指標；保留三年報酬統計。
- 各週期提供 KD（9 期 RSV、K/D 三期平滑）、5/20/60 期均線及 OHLC 趨勢。
- 納入聯準會目標利率（FRED）、國際油價（Yahoo Finance `CL=F`）與費半指數（`^SOX`）。
- 以程式計算資產數字，避免交由語言模型心算。
- 經理人可採納或否決市場訊號，選擇加碼、減碼或持平，並列出決策理由。
- 每筆模擬委託以 1 張（1,000 股）為單位；每個台灣日期最多 3 筆。
- 持平不新增 `settlements` 或 `simulated_orders` 記錄；模擬成交記錄在 `simulated_orders`。

## 資產計算方式

```text
持倉總市值 = 各持倉股數 × 最新價格後加總
總資產     = 持倉總市值 + 模擬帳戶可用現金
```

持倉成本與未實現損益會另外列示，不會再加到總資產中。若持倉報價缺漏或幣別不一致，程式不會將不完整的數字加總。

## 安裝與設定

以下指令請在專案根目錄執行。

1. 建立環境檔。若 `.env` 已存在，請勿覆蓋：

   ```bash
   cp -n .env.example .env
   ```

2. 編輯 `.env`，設定 Gemini、MariaDB 與模擬帳戶。Telegram 欄位只有在使用 `--telegram` 時才需要；此選項會同時啟動命令服務並傳送分析報告。

   ```dotenv
   GEMINI_API_KEY=your_gemini_api_key
   GEMINI_MODEL=gemini/gemini-2.5-flash

   MARIADB_HOST=127.0.0.1
   MARIADB_PORT=3306
   MARIADB_DATABASE=portfolio
   MARIADB_USER=portfolio
   MARIADB_PASSWORD=your_mariadb_password

   SIMULATION_INITIAL_CASH_TWD=150000
   TRACK_INTERVAL_SECONDS=900

   TELEGRAM_BOT_TOKEN=your_telegram_bot_token
   TELEGRAM_CHAT_ID=your_telegram_chat_id
   TELEGRAM_ALLOWED_USER_IDS=123456789
   ```

   `TELEGRAM_ALLOWED_USER_IDS` 是可使用 Telegram 指令的使用者 ID 清單，以逗號分隔；只接受私訊及清單中的使用者。`.env` 含有密鑰與密碼，已由 `.gitignore` 排除，請勿提交至版本庫。

3. 安裝相依套件：

   ```bash
   ./env/bin/python -m pip install -r requirements.txt
   ```

4. 初始化資料庫與預設持倉：

   ```bash
   ./env/bin/python init_portfolio.py
   ```

   首次執行會建立所需資料表，並在標的尚未存在時加入 0050.TW 與
   0056.TW 各 10 張（每檔 10,000 股），以最新可取得的收盤價作為成本。
   模擬現金預設為新台幣 150,000 元，亦可由
   `SIMULATION_INITIAL_CASH_TWD` 設定。

   `--reset` 會清除**所有**持倉、結算與模擬委託，並重設模擬現金；執行前請先備份。若舊版曾將預設持股建成每檔 1,000 股，需使用此選項重新初始化。

## 排程與執行

啟動台股交易時段排程：

```bash
./env/bin/python main.py --telegram
```

程式使用台北時間，預設在平日 09:00 至 13:30，每 15 分鐘分析一次，時段錨定開盤時間：09:00、09:15、09:30，依此類推至 13:30。

- 開盤前啟動：等待至當日 09:00。
- 交易時段內啟動：等待至下一個排程時間；若剛好在排程點啟動，立即執行。
- 收盤後或週末啟動：等待至下一個平日 09:00。
- 若分析超過一個排程間隔，會略過已錯過的時段，不會連續補跑。
- 使用 `--telegram` 時，主程式會在背景啟動 Telegram 指令服務，與排程共用資料庫中的暫停狀態，不需另外啟動程序。
- 若 Yahoo Finance 沒有所有持倉標的的當日日線資料（例如休市或資料延遲），會略過該時段的 AI 分析。程式以報價新鮮度判斷，未另外維護交易所假日行事曆。
- 排程程式會持續執行；保持終端機開啟，按 `Ctrl+C` 可停止。

常用選項：

```bash
./env/bin/python main.py --once             # 交易時段內立即執行一輪
./env/bin/python main.py --interval 900     # 指定排程間隔（秒）
./env/bin/python main.py --analyze-only     # 只分析，不模擬成交
./env/bin/python main.py                    # 不傳送 Telegram
./env/bin/python main.py --telegram         # 單一指令啟動排程、Telegram 指令與報告
```

`--no-settle` 仍可作為 `--analyze-only` 的舊版別名。模擬成交只允許在平日 09:00–13:30；每筆必須是整數張，且受可用現金、持股數量與每日最多 3 筆限制。資料不完整、幣別不符或價格未更新時，不會模擬成交。

Telegram agent 支援自然語言對話，例如「目前資產狀況如何？」、「幫我畫資產分布圖」、「今天賣出幾股？」、「請暫停交易兩小時」、「恢復經理人」或「現在還在暫停嗎？」；也保留 `/assets`、`/chart`、`/orders`、`/pause 2h`、`/resume`、`/status` 等指令。自然語言由 Gemini 判斷意圖，程式只執行資產查詢、圖表、今日交易紀錄查詢、暫停、恢復與暫停狀態等白名單操作，不會下單或修改持倉；需要 `GEMINI_API_KEY`。暫停會略過新的排程分析，且資料庫會在每筆模擬成交前再次檢查暫停狀態；目前進行中的分析可能完成，但暫停後不會新增模擬成交。`--once --telegram` 只執行單次分析並傳送報告，不啟動常駐指令服務。系統只做模擬交易，不連接券商。

`GEMINI_MODEL` 是所有 CrewAI agents 共用的模型設定，分析與 Telegram 對話都由 [`.env`](./.env) 讀取；未設定時程式會明確報錯，不會使用程式內的預設模型。

### Telegram Agent 操作說明

Telegram agent 是一個常駐的私訊指令服務，由 `--telegram` 與排程主程式在同一個執行緒程序中啟動。設定完成後，使用 `/help` 可在 Telegram 中查看指令；`TELEGRAM_CHAT_ID` 是排程分析報告的收件帳號，`TELEGRAM_ALLOWED_USER_IDS` 則是可送出指令與對話的使用者白名單。

啟動前請確認：

- 已在 Telegram 建立 bot 並取得 `TELEGRAM_BOT_TOKEN`。
- 已取得允許操作 bot 的 Telegram numeric user ID，並以逗號分隔寫入 `TELEGRAM_ALLOWED_USER_IDS`。
- 已設定 `GEMINI_API_KEY` 與 `GEMINI_MODEL`，自然語言意圖判斷需要 Gemini。
- 若要接收排程報告，另須設定 `TELEGRAM_CHAT_ID`。
- bot 只接受私訊及白名單中的使用者，群組或未授權使用者的訊息會忽略。

建議啟動方式：

```bash
./env/bin/python main.py --telegram
```

這會同時啟動排程、Telegram 指令輪詢、自然語言對話與分析報告傳送。
只執行一次分析並傳送報告時，可使用：

```bash
./env/bin/python main.py --once --telegram
```

此模式不啟動常駐 Telegram 指令服務。

支援的指令：

| 指令 | 語法與效果 |
| --- | --- |
| `/help` | 顯示可用指令。 |
| `/assets` | 即時讀取 MariaDB 持倉、模擬現金並計算資產、市值、成本與未實現損益。 |
| `/chart` | 產生並傳送持倉市值與可用現金的資產分布圖。 |
| `/orders` | 查詢台北時間今天的模擬買賣紀錄，並列出買入與賣出的股數。 |
| `/pause` | 暫停排程分析與模擬交易，例如 `/pause 30m`、`/pause 2h`、`/pause 1d`；時間可為 1 分鐘至 7 天。 |
| `/resume` | 清除暫停狀態，下一輪排程即可恢復分析與模擬交易。 |
| `/status` | 顯示是否暫停，以及台北時間的剩餘時間與預計恢復時間。 |

自然語言的處理規則：

- Gemini 只負責判斷意圖及產生簡短繁體中文回覆；實際資料由程式重算或從資料庫讀取。
- 支援資產查詢、圖表、今日模擬買賣紀錄、暫停、恢復、暫停狀態與一般問候。
- 暫停時間必須是正整數分鐘，最多 7 天；未提供時間時會詢問使用者，不會自行猜測。
- 不支援下單、修改持倉、讀取真實券商帳戶或任何真實交易操作。
- 當報價或幣別資料不完整時，資產報告會明確顯示「無法計算」或「報價不完整」，不會捏造總額。

## 資料表

| 資料表 | 用途 |
| --- | --- |
| `holdings` | 持倉股數、平均成本與幣別 |
| `simulation_account` | 模擬帳戶現金餘額 |
| `simulated_orders` | 模擬成交價格、股數、方向、損益、時間與決策理由 |
| `manager_control` | 經理人暫停到期時間 |
| `settlements` | 舊版結算資料表；目前程式不新增結算快照 |

## 調整指南

| 調整項目 | 檔案或設定 |
| --- | --- |
| 開盤、收盤與排程時間 | `scheduler.py`：`MARKET_OPEN`、`MARKET_CLOSE`；`.env`：`TRACK_INTERVAL_SECONDS` |
| 初始模擬現金 | `.env`：`SIMULATION_INITIAL_CASH_TWD` |
| 歷史資料年數、KD 與時間週期 | `market_data.py`：`HISTORY_YEARS`、`TIMEFRAME_RULES`、`_kd_values` |
| 市場分析與持倉分析提示 | `agents/market_analyst.py`、`agents/portfolio_agent.py` |
| 最終加減碼決策提示 | `agents/portfolio_manager.py` |
| 每日委託上限、每張股數與模擬成交規則 | `database.py`：`MAX_DAILY_ORDERS`、`SHARES_PER_LOT`、`execute_simulated_order` |
| 資產總額與主流程 | `main.py`：`summarize_portfolio`、`run_cycle` |
| 預設持倉初始化 | `init_portfolio.py` |

## MCP 伺服器（選用）

排程程式 `main.py` 不會連接 MCP 伺服器，而是直接透過專案模組讀取市場資料與 MariaDB。只有在使用其他 MCP client 時，才需要另外啟動：

```bash
./env/bin/python finance_mcp_server.py
```

## 專案結構

```text
agents/
  crew.py                   CrewAI Agent 與任務組裝
  telegram_agent.py         Telegram 資產查詢、圖表與經理人控制指令
  decision_models.py        結構化市場訊號與經理人決策
  market_analyst.py         市場分析師
  portfolio_agent.py        投資組合分析師
  portfolio_manager.py      投資組合經理人
database.py                 MariaDB schema 與模擬帳戶／委託操作
finance_mcp_server.py       選用的 MCP stdio server
init_portfolio.py           初始化資料庫與預設持倉
main.py                     排程、分析、資產計算與報告
market_data.py              歷史價格、KD、均線與宏觀資料
scheduler.py                台股交易時段排程
tests/                      模擬流程與指標測試
```
