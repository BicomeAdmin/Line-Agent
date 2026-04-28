#!/usr/bin/env bash
# Project Echo 服務啟動器
#
# 用法：
#   bash scripts/start_services.sh           # 啟動全部（已在跑的略過）
#   bash scripts/start_services.sh restart   # 全部停掉後重啟
#   bash scripts/start_services.sh stop      # 全部停掉
#   bash scripts/start_services.sh status    # 看現在誰在跑
#
# 管理三個常駐服務：
#   1. scheduler_daemon  排程引擎（巡邏 / 排程貼文 / Watcher / 每日摘要 / 逾期提醒）
#   2. lark_bridge       Lark 長連線（接訊息、卡片按鈕回呼）
#   3. web_dashboard     本機監控網頁 http://localhost:8080
#
# 全部用 nohup 跑，關掉終端機也不會中斷。Log 檔在 /tmp/。
# .env 會自動 source 進來，OPERATOR_* / LARK_* 變數子程序都拿得到。

set -eo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON="${PYTHON:-python3}"

# 載入 .env
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# 服務定義（macOS 內建 bash 3.2 不支援 associative array，用平行陣列代替）
NAMES=(scheduler_daemon lark_bridge web_dashboard)

CMD_scheduler_daemon="$PYTHON scripts/scheduler_daemon.py --interval-seconds 30"
CMD_lark_bridge="$PYTHON scripts/start_lark_long_connection.py"
CMD_web_dashboard="$PYTHON scripts/web_dashboard.py"

PAT_scheduler_daemon="scripts/scheduler_daemon.py"
PAT_lark_bridge="scripts/start_lark_long_connection.py"
PAT_web_dashboard="scripts/web_dashboard.py"

LOG_scheduler_daemon="/tmp/scheduler_daemon.log"
LOG_lark_bridge="/tmp/lark_bridge.log"
LOG_web_dashboard="/tmp/web_dashboard.log"

LABEL_scheduler_daemon="排程引擎"
LABEL_lark_bridge="Lark 長連線"
LABEL_web_dashboard="本機儀表板"

get_var() {
  # 用法：get_var PREFIX_NAME → 回傳對應變數值
  eval "echo \${$1}"
}

print_status() {
  printf "\n  %-18s %-12s %-6s %-8s %s\n" "服務" "中文標籤" "狀態" "PID" "log"
  printf "  %-18s %-12s %-6s %-8s %s\n"   "──────" "────────" "────" "───" "──────"
  for name in "${NAMES[@]}"; do
    pat=$(get_var "PAT_$name")
    log=$(get_var "LOG_$name")
    label=$(get_var "LABEL_$name")
    pid=$(pgrep -f "$pat" 2>/dev/null | head -1 || true)
    if [ -n "$pid" ]; then
      printf "  %-18s %-12s ✅ 在跑 %-8s %s\n" "$name" "$label" "$pid" "$log"
    else
      printf "  %-18s %-12s ❌ 未跑 %-8s %s\n" "$name" "$label" "-" "$log"
    fi
  done
  printf "\n"
}

start_one() {
  local name="$1"
  local cmd
  local log
  local pat
  local label
  cmd=$(get_var "CMD_$name")
  log=$(get_var "LOG_$name")
  pat=$(get_var "PAT_$name")
  label=$(get_var "LABEL_$name")

  if pgrep -f "$pat" > /dev/null 2>&1; then
    local existing
    existing=$(pgrep -f "$pat" | head -1)
    echo "  [略過] $label 已在跑（PID $existing）"
    return 0
  fi

  echo "  [啟動] $label → $log"
  nohup $cmd > "$log" 2>&1 &
  sleep 1
}

stop_all() {
  for name in "${NAMES[@]}"; do
    pat=$(get_var "PAT_$name")
    label=$(get_var "LABEL_$name")
    pids=$(pgrep -f "$pat" 2>/dev/null || true)
    if [ -n "$pids" ]; then
      echo "  [停止] $label（PIDs: $pids）"
      echo "$pids" | xargs kill 2>/dev/null || true
    fi
  done
  sleep 2
}

cmd="${1:-start}"

case "$cmd" in
  status|--status)
    print_status
    ;;

  restart|--restart)
    echo "重啟所有服務..."
    stop_all
    for name in "${NAMES[@]}"; do
      start_one "$name"
    done
    sleep 2
    print_status
    echo "  → 儀表板：http://localhost:8080"
    echo
    ;;

  stop|--stop)
    echo "停止所有服務..."
    stop_all
    print_status
    ;;

  start|"")
    echo "啟動 Project Echo 服務（已在跑的略過）..."
    for name in "${NAMES[@]}"; do
      start_one "$name"
    done
    sleep 2
    print_status
    echo "  → 儀表板：http://localhost:8080"
    echo
    ;;

  *)
    echo "錯誤：未知指令「$cmd」" >&2
    echo "用法：$0 [start|restart|stop|status]" >&2
    exit 2
    ;;
esac
