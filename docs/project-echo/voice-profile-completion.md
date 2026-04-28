# Voice Profile 補完流程

> 怎麼把每個社群的 voice_profile.md 從 bootstrap stub 變成 bot 動筆能用的真實人物設定。

最後更新：2026-04-29

---

## 概念

每個社群的 `customers/<id>/voice_profiles/openchat_NNN.md` 切成 6 塊：

| 區塊 | 誰填 | 怎麼補 |
|---|---|---|
| `## My nickname in this group` | **你必填** | Lark 講「我在 X 群暱稱叫 Y」 |
| `## My personality` | **你必填** | Lark 講「我在 X 群的個性是 …」 |
| `## Samples` | 你補 + 自動 | Lark 講「我在 X 群想讓 bot 學這幾句」 |
| `## Observed community lines` | **自動** | Lark 講「幫我抓 X 群的語氣樣本」→ harvest |
| `## Style anchors` | 通用 default | 通常不用改 |
| `## Off-limits` | 通用 default | 通常不用改 |

---

## 一鍵診斷：哪些群還缺什麼

對 bot 講：

> **「X 群還缺什麼」** 或 **「openchat_003 voice profile 完整了嗎」**

bot 會回類似：

```
openchat_003 voice profile 完成度 67%；還沒抓過真實語料；缺：nickname

下一步建議：
1. 執行 harvest_style_samples(openchat_003) 自動補入真實成員語句
2. 在 Lark 對 bot 講「我在 openchat_003 暱稱叫 XXX」
```

或者本機 CLI：

```bash
python3 -c "
from app.workflows.voice_profile_setup import check_voice_profile
import json
for cid in ('openchat_001', 'openchat_002', 'openchat_003', 'openchat_004'):
    r = check_voice_profile('customer_a', cid)
    print(f'{cid}: {r[\"summary_zh\"]}')"
```

---

## 三條補完管道

### ⭐ 0. 推薦起手式：匯入 LINE 對話紀錄（最完整，也最合規）

LINE 內建「**傳送對話紀錄 → 文字檔**」功能，匯出整段聊天歷史（不只 200 則），且**保留每則訊息的時間 + 發言者名字**——這是 UI 抓取做不到的。

**操作員端（30 秒）：**
1. 進到目標社群 → 右上選單 → 「**傳送對話紀錄**」 → 「**文字檔**」
2. 選 LINE Keep / 自己 email / AirDrop 把檔案傳到 Mac
3. 把檔案放在任何路徑（例 `~/Downloads/[LINE]xxx.txt`）

**對 bot 講：**
> 「我把 openchat_002 的匯出檔放在 /Users/bicometech/Downloads/[LINE]特殊支援群.txt，幫我 import」

bot 會：
- Parse 整個檔案（時間戳、發言者、多行訊息合併）
- 過濾系統廣播 / 連結 / 噪音
- 把自然語句去重 append 進 voice_profile.md（`top_n_new_samples=50`，比 UI 抓取的 30 更多）
- **匯出檔複製到** `customers/<id>/data/chat_exports/<community_id>__YYYYMMDD_HHMMSS.txt` 留檔
- 回報每位 sender 的訊息數、平均字數、樣本 — 你會立刻看到群裡誰最活躍

實測 openchat_002（小型測試群）：解析 9 則、辨識 3 個 sender、寫入 4 句新樣本。
若是 570 人活躍社群，一次匯入可以拿到數千則訊息，比 UI 抓取多 10-100 倍。

**為什麼這條最合規：**
- LINE 自家內建功能（不是自動化）
- 操作員手動觸發（不是 bot 主動）
- 你自己擁有的對話資料（不涉及他人隱私邊界）
- 完成後檔案在你 Mac 本機（沒上雲、沒外傳）

### A. 自動：harvest 真實成員語句（一句話即可）

對 bot 講：「**幫我抓 X 群的語氣樣本**」

工具會：
1. 自動 navigate 進 X 群
2. 讀 200 則最近訊息
3. 過濾掉公告 / 連結 / 時間戳 / 成員徽章
4. 自然度評分後選 top 30
5. **與既有樣本去重後 append 進**`## Observed community lines` 區塊（操作員手寫的其他區塊不動）

**累積式採集**（預設行為，建議週週跑一次）：

- 預設 `append_mode=True`：既有樣本保留、新樣本去重加進尾端
- `total_cap=200`：累積上限。超過時最舊樣本（list 最前）被淘汰
- 適合「一次抓一點、長期累積」——每次 LINE 操作維持短暫，但語料覆蓋會擴及不同時段、話題、成員

如果群風格大幅漂移想清盤重來，請明確告訴 bot 「**重抓 X 群樣本不要保留舊的**」，或直接帶 `append_mode=False`。

**為什麼不要一次讀更多？**

200 則對 570 人的群是幾小時的快照。要更厚的語料庫，**累積比一次讀更多更好**：
- 單次 session 短，自動化痕跡少
- 不同時段/話題自然輪替，避免單次抓到的偏向
- 隨群風格漂移自然汰舊換新

### B. 對話式：填暱稱 / 個性 / 樣本

對 bot 講以下任一：

| 你說 | bot 做的事 |
|---|---|
| 「我在 openchat_003 暱稱叫 小宇」 | `update_voice_profile_section(community_id='openchat_003', section='nickname', content='- 小宇')` |
| 「我在 openchat_003 的個性是平常觀察居多，看到有趣的會冒一句」 | `section='personality'`，自動轉成 `- ...` 列表 |
| 「我在 openchat_003 想讓 bot 學這幾句：『欸真的』『對啊就是這樣』」 | `section='samples'`，自動分行 |

bot 會回確認，並提示你下一步可以講「盤點 openchat_003」再 check 一次完成度。

支援的中文別名：`暱稱` / `個性` / `樣本` / `風格` / `底線`。

### C. 直接編輯檔案（進階）

```bash
$EDITOR customers/customer_a/voice_profiles/openchat_003.md
```

注意：**不要碰 `<!-- BEGIN auto-harvested -->` 到 `<!-- END auto-harvested -->` 之間的內容**——下次 harvest 會覆寫掉。其他區塊放心改。

---

## 推薦補完順序（每個新社群一遍）

```
1. 「幫我抓 openchat_004 的語氣樣本」     ← 自動填 Observed lines
   → 看 bot 回報抓到幾句、預覽 3 句確認群風

2. 「openchat_004 還缺什麼」              ← 確認進度
   → bot 回 missing=[nickname, personality, samples]

3. 「我在 openchat_004 暱稱叫 阿哲」       ← 填暱稱
4. 「我在 openchat_004 的個性是平常潛水，
    看到風水或道教話題會冒一兩句」          ← 填個性

5. 「openchat_004 還缺什麼」              ← 應該回 100%

6. （可選）「我在 openchat_004 想讓 bot 
    學這幾句：『...』『...』」              ← 累積 samples
```

每個社群大概 2-3 分鐘走完。

---

## 完成後會發生什麼

下次 bot 在那個社群動筆（不論你 Lark 觸發還是 watcher 自動），會：

1. 先呼叫 `get_persona_context(community_id)` 載入 (帳號 × 社群 × voice profile × 近期送過的句子)
2. **echo 一行 summary 給你確認**：
   > 「在『山納百景』(openchat_003)，你是 客戶 A — 暱稱『小宇』，個性『平常觀察居多』。最近 7 天送過 0 句。」
3. echo 完才動筆，且必須對齊：
   - 對話脈絡（有人在跟使用者講話 / 使用者已參與該話題）
   - 使用者的人物設定（暱稱口氣、個性、Off-limits）
   - 群內當前氛圍（中位字數、emoji 率、語氣詞）

任何一條沒對齊就退回略過——不會幫你編一個你從沒講過的立場。

---

## 與其他文件的關係

- [`services-startup.md`](services-startup.md) — 服務啟動指南
- [`CLAUDE.md`](../../CLAUDE.md) §0 — 專案合規前提（HIL 鐵則、操作員審核）
- [`change-log.md`](change-log.md) — 工具新增紀錄
