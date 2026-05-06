#!/bin/bash

echo "=== ITERATION 28 STATUS ==="
echo "Time: $(date)"
echo ""

LOG=$(find /private/tmp -name "b95pv0wcr.output" 2>/dev/null | head -1)

if [ ! -f "$LOG" ]; then
    echo "❌ Log file not found yet"
    exit 1
fi

QUERIES=$(grep -c "benchmark.query_start" "$LOG" 2>/dev/null || echo "0")
DONE=$(grep -c "benchmark.query_done" "$LOG" 2>/dev/null || echo "0")
ERRORS=$(grep -c "| ERROR" "$LOG" 2>/dev/null || echo "0")
WARNINGS=$(grep -c "| WARNING" "$LOG" 2>/dev/null || echo "0")

OA_FAILS=$(grep -c "OpenAlex.*failed\|semantic_failed_fallback" "$LOG" 2>/dev/null || echo "0")
SS_LIMITS=$(grep -c "semantic_scholar.*rate_limited" "$LOG" 2>/dev/null || echo "0")
E2B_TIMEOUTS=$(grep -c "sandbox.operation_timeout" "$LOG" 2>/dev/null || echo "0")

LAST_LATENCY=$(grep '"latency_s":' "$LOG" 2>/dev/null | tail -1 | grep -o '[0-9.]*' | tail -1)

echo "Progress: $QUERIES/71 (Done: $DONE)"
echo "Errors: $ERRORS | Warnings: $WARNINGS"
echo ""
echo "OpenAlex failures: $OA_FAILS ✓"
echo "Semantic Scholar limits: $SS_LIMITS"
echo "E2B timeouts: $E2B_TIMEOUTS"
echo ""
if [ ! -z "$LAST_LATENCY" ]; then
    echo "Last query latency: ${LAST_LATENCY}s"
fi

LINES=$(wc -l < "$LOG" 2>/dev/null)
echo "Log file: $LINES lines"
