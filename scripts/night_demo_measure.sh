#!/usr/bin/env bash
# Ночной замер демо-надёжности (docs/todo.md §91–93), строго последовательно:
#   A/B подрынков на шести доменах в одном окне кэша → судья покрытия на обеих папках →
#   4 ТЗ-запроса без подрынков ×1 (база) → 4 ТЗ-запроса ×3 без кэша (время, позиции, разброс).
# Запуск: nohup bash scripts/night_demo_measure.sh >> reports/night-demo.log 2>&1 &
# ⚠️ Один прогон за раз: перед стартом ps -W | grep -c ".venv/Scripts/python$" обязан дать 0.
set -u
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python
step() { echo; echo "===== $(date '+%d.%m %H:%M:%S') $*"; }

step "A: домены, кэш, без подрынков"
$PY scripts/measure_ask.py --domains --cached --no-submarkets --out reports/ask-A
step "B: домены, кэш, с подрынками"
$PY scripts/measure_ask.py --domains --cached --out reports/ask-B
step "судья покрытия A"
$PY scripts/judge_match.py --dir reports/ask-A
step "судья покрытия B"
$PY scripts/judge_match.py --dir reports/ask-B
step "ТЗ-запросы без подрынков, по одному, без кэша (база)"
$PY scripts/measure_ask.py --tz --repeat 1 --no-submarkets --out reports/ask-A
step "ТЗ-запросы с подрынками, по три, без кэша"
$PY scripts/measure_ask.py --tz --repeat 3
step "готово"
