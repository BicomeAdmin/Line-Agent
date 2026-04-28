# Project Echo 服務啟動指南

> 這份文件說明 Project Echo **常駐服務**怎麼啟動、停止、檢查狀態。
> 操作員平常只需記一行指令，其他都交給啟動腳本。

最後更新：2026-04-29

---

## 一句話啟動全部

```bash
bash scripts/start_services.sh
```

啟動腳本會幫你跑起以下三個服務（已在跑的會自動略過，不重複）：

| 服務 | 中文標籤 | 用途 |
|---|---|---|
| `scheduler_daemon` | 排程引擎 | 巡邏 / 排程貼文 / Watcher tick / 每日 09:00 摘要 / 逾期 review 提醒 |
| `lark_bridge` | Lark 長連線 | 接 Lark 對 bot 的訊息、卡片按鈕回呼（[通過/修改/忽略]） |
| `web_dashboard` | 本機儀表板 | http://localhost:8080 即時監控頁面 |

啟動完會印一張表格顯示三個服務的 PID + log 路徑，最後一行給你儀表板網址。

---

## 啟動腳本完整用法

```bash
bash scripts/start_services.sh           # 啟動全部（已在跑的略過）
bash scripts/start_services.sh start     # 同上
bash scripts/start_services.sh restart   # 全部停掉後重啟（改了代碼後用這個）
bash scripts/start_services.sh stop      # 全部停掉
bash scripts/start_services.sh status    # 看現在誰在跑、誰沒跑、PID 是多少
```

特性：

- 全部用 `nohup` 跑，**關掉終端機也不會中斷**
- Log 檔在 `/tmp/`：
  - `/tmp/scheduler_daemon.log`
  - `/tmp/lark_bridge.log`
  - `/tmp/web_dashboard.log`
- 啟動前自動 `source .env`，所以 `OPERATOR_*` / `LARK_*` 環境變數子程序都拿得到

---

## 儀表板

啟動後直接打開瀏覽器：

```
http://localhost:8080
```

頁面每 5 秒自動刷新，看得到：

- 系統健康（三個服務的紅綠燈）
- 24 小時送發統計（drafts / sent / ignored / pending）
- 4 個社群一覽（voice profile 健康度、active watch 倒數、pending 數）
- 待審 inbox（按等待時間色碼：>4h 紅、>2h 黃）
- Active watches（倒數 + 上次 check 時間）
- 最近 5 次 auto-fire（codex 開稿摘要）
- **即時事件流**（audit log 最近 80 則，色碼分類）

預設只 bind `127.0.0.1`（loopback 只給本機），同網段別人連不到。要從手機 / 別台電腦看的話：

```bash
python3 scripts/web_dashboard.py --host 0.0.0.0 --port 8080
```

但這條別在咖啡店做。

---

## 各服務獨立啟動（debug 用）

```bash
# 排程引擎（前景，看即時 log）
python3 scripts/scheduler_daemon.py --interval-seconds 30

# Lark 長連線
python3 scripts/start_lark_long_connection.py

# 本機儀表板（前景）
python3 scripts/web_dashboard.py
```

---

## 改了代碼後怎麼讓它生效

| 改動類型 | 怎麼處理 |
|---|---|
| 改 `app/workflows/scheduler.py`、`app/workflows/job_processor.py`、`app/workflows/watch_tick.py`、`app/workflows/dashboard.py` 等 daemon-side 邏輯 | `bash scripts/start_services.sh restart`（daemon 模組是 boot-time 載入，必須重啟才拿到新代碼） |
| 改 `app/lark/`、`scripts/start_lark_long_connection.py`、bridge framing | 同上，`restart` |
| 改 `app/mcp/project_echo_server.py`（MCP tool） | **不需要重啟** — codex 每次 spawn MCP 都重新載入 |
| 改 `app/web/dashboard_server.py`、HTML | 只重啟 web_dashboard 即可：`pgrep -f web_dashboard.py \| xargs kill && bash scripts/start_services.sh` |
| 改 `.env` | 三個服務都要重啟，才能讀到新環境變數：`bash scripts/start_services.sh restart` |

---

## 常見故障排解

### 啟動腳本說「已在跑」但功能怪怪的

→ 大概率是舊代碼還沒被踢掉。直接 `restart`：

```bash
bash scripts/start_services.sh restart
```

### 儀表板打不開（`localhost:8080` 連線拒絕）

```bash
bash scripts/start_services.sh status   # 看 web_dashboard 是不是 ❌ 未跑
tail -20 /tmp/web_dashboard.log         # 看為什麼掛了
```

最常見原因：port 8080 被別的程式佔走。改 port：

```bash
python3 scripts/web_dashboard.py --port 8081
```

### Lark 點卡片沒反應

```bash
tail -20 /tmp/lark_bridge.log           # 看有沒有 "card action trigger"
```

如果有印 `card action trigger` 但後面是 `unsupported_action`，代表 payload schema 又變了（lark-oapi SDK 升版）—— 開個 issue 回報。

### 系統健康紅燈，scheduler_daemon 顯示 ❌ 未跑

```bash
tail -20 /tmp/scheduler_daemon.log      # 看是不是 import error / port 衝突
bash scripts/start_services.sh restart
```

---

## 啟動時序建議（冷啟動完整流程）

如果你是從**整台機器重開**或**新環境**：

1. **確認 emulator 在跑**
   ```bash
   adb devices                                # 看到 emulator-5554 device
   python3 scripts/start_emulator.py --avd project-echo-api35 --no-snapshot   # 沒在跑就起
   ```

2. **確認 LINE 已 logged in 且在前景**（一次性手動）
   ```bash
   python3 scripts/openchat_validation.py --community-id openchat_001
   ```

3. **啟動三個服務**
   ```bash
   bash scripts/start_services.sh
   ```

4. **打開儀表板**
   ```
   http://localhost:8080
   ```

5. **驗一下 Lark 通**
   - 在 Lark 對 bot 講「狀態」應該回一份摘要
   - 或者讓 daemon 自然跑，9:00 自動推每日摘要到你 DM

---

## 與其他文件的關係

- [`CLAUDE.md`](../../CLAUDE.md) §4.3 daemon 段落 — 規範了「改完 scheduler / job_processor / workflow 後**必須重啟**」的鐵則
- [`operator-runbook.md`](operator-runbook.md) — 診斷腳本（snapshot / readiness / acceptance）；這份文件補的是「服務怎麼啟動」的空缺
- [`daily-operator-checklist.md`](daily-operator-checklist.md) — 每天上工的健康檢查清單
- [`incident-recovery-runbook.md`](incident-recovery-runbook.md) — 真的炸鍋時走的修復流程
