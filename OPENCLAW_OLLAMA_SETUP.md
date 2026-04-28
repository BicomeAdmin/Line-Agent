# OpenClaw + Ollama 本機使用筆記

最後更新: 2026-04-24

## 目前這台電腦的條件

- Mac mini / Apple M4
- RAM: 32 GB
- Ollama 本機執行
- OpenClaw 透過 Feishu/Lark channel 使用

## 目前確認過的重點

- Ollama 服務正常
- OpenClaw Gateway 正常
- Feishu/Lark 憑證已修好，channel 可以連線
- OpenClaw 的 system prompt 很大，小模型就算能跑，也不一定會快

## 已測模型結論

### 最後決定

目前正式建議主模型:

- `qwen2.5-coder:3b`

原因:
- 支援 `tools`
- 和 OpenClaw 相容
- 本機實測可以正確回覆
- 比 `qwen2.5-coder:7b` 更快
- 比 `llama3.1:8b` 更穩

### 1. `qwen2.5-coder:3b`

目前最適合當主模型。

本機實測:
- Prompt: `Reply exactly: OK`
- 結果: 成功回 `OK`
- 耗時: 約 52 秒

優點:
- Ollama 顯示支援 `tools`
- 偏 coding / technical workflow
- 大小更小，約 1.9 GB
- 目前是已驗證過最穩的選項
- 互動等待感明顯比 7B 好

缺點:
- 品質理論上會比 7B 稍弱

### 2. `qwen2.5-coder:7b`

保留作備用模型。

本機實測:
- Prompt: `Reply exactly: OK`
- 結果: 成功回 `OK`
- 耗時: 約 79 秒到 108 秒

優點:
- Ollama 顯示支援 `tools`
- 偏 coding / technical workflow
- 理論上比 3B 更有餘裕

缺點:
- 明顯比較慢
- 更容易讓人覺得沒回應

### 3. `llama3.1:8b`

可當備選，但不是首選。

本機實測:
- Prompt: `Reply exactly: OK`
- 結果: 沒有照指令回 `OK`，而是誤觸工具後回 `No cron jobs are scheduled.`
- 耗時: 約 116 秒

優點:
- Ollama 顯示支援 `tools`
- OpenClaw 可以實際跑起來
- 對目前的 OpenClaw agent 流程相容性比 `deepseek-r1:8b` 好

缺點:
- 在重 prompt / 重工具 schema 下還是偏慢
- 偶爾會工具選錯，穩定度不如 `qwen2.5-coder:3b`

### 4. `qwen2.5:7b`

可用，但不推薦當主力。

原因:
- 本身能跑
- 但 OpenClaw 先前會踩到 `thinking` 能力不相容
- 回覆速度偏慢

### 5. `deepseek-r1:8b`

不建議當 OpenClaw 主模型。

原因:
- 模型本身可下載、可用 Ollama 跑
- 但 OpenClaw log 已確認它在目前這條路徑下會報:
  - `does not support tools`

## 平常啟動方式

### 啟動 / 重啟 OpenClaw

```bash
openclaw gateway restart
openclaw status --deep
```

### 查看 OpenClaw 狀態

```bash
openclaw status --deep
```

### 查看目前 session

```bash
openclaw sessions --json
```

### 查看 Ollama 載入中的模型

```bash
ollama ps
```

## 切換 OpenClaw 主模型

### 切成 `llama3.1:8b`

```bash
openclaw config set agents.defaults.model.primary 'ollama/llama3.1:8b'
openclaw gateway restart
```

### 切成 `qwen2.5-coder:3b`

```bash
openclaw config set agents.defaults.model.primary 'ollama/qwen2.5-coder:3b'
openclaw gateway restart
```

### 切成 `qwen2.5-coder:7b`

```bash
openclaw config set agents.defaults.model.primary 'ollama/qwen2.5-coder:7b'
openclaw gateway restart
```

### 切回 `qwen2.5:7b`

```bash
openclaw config set agents.defaults.model.primary 'ollama/qwen2.5:7b'
openclaw gateway restart
```

## 實際測試模型

用新的 session id 測，避免舊上下文污染:

```bash
openclaw agent --agent main --session-id test-$(date +%s) --message "Reply exactly: OK" --thinking off --json --timeout 120
```

## 清理舊 session

如果覺得越跑越慢，可以先做維護:

```bash
openclaw sessions cleanup
```

如果真的被舊上下文拖住，才考慮重置 session store。

## 目前建議保留的模型

建議保留:
- `qwen2.5-coder:3b`
- `qwen2.5-coder:7b`

建議刪除:
- `deepseek-r1:8b`
- `llama3.1:8b`
- `qwen2.5:7b`

## 刪除不用的模型

查看已安裝模型:

```bash
ollama list
```

刪除模型:

```bash
ollama rm deepseek-r1:8b
```

這次已實際刪除:

```bash
ollama rm deepseek-r1:8b llama3.1:8b qwen2.5:7b
```

## 目前最實際的建議

如果要先穩定用:

1. 主模型用 `qwen2.5-coder:3b`
2. 直接到 Feishu 實際對 bot 發短訊息測體感
3. 如果之後覺得品質不夠，再切回 `qwen2.5-coder:7b`

如果目標是:

- 比較快、比較適合日常互動: `qwen2.5-coder:3b`
- 比較穩、可當備用: `qwen2.5-coder:7b`
