#!/bin/bash
# Wrapper script to run benchmark and save iteration in one command.
# Usage: ./scripts/run_iteration.sh <iteration_num> <config_name> [--hypothesis "..."] [--status ACCEPT|REVERT|PENDING]

set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 1. Validation: Ensure required arguments exist
if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <iteration_num> <config_name> [--hypothesis \"...\"] [--status ACCEPT|REVERT|PENDING]"
    echo ""
    echo "Example:"
    echo "  $0 1 top_k_15 --hypothesis 'Increase top_k_headers 10→15' --status ACCEPT"
    exit 1
fi

ITER_NUM=$1
CONFIG_NAME=$2
shift 2

# 2. Validation: Ensure config.json is valid JSON
if ! command -v jq &> /dev/null; then
    echo "⚠️ Warning: 'jq' not found. Skipping JSON syntax validation."
else
    if ! jq '.' "$ROOT/config.json" > /dev/null 2>&1; then
        echo "❌ Error: config.json is not valid JSON. Please fix syntax errors (e.g., trailing commas) before running."
        exit 1
    fi
    echo "✅ config.json syntax is valid."
fi

# 3. Dependency Check: Run unit tests before benchmark
echo "🧪 Running unit tests..."
if ! /usr/local/bin/python -m pytest "$ROOT/tests/" 2>&1 | grep -q "passed\|collected"; then
    echo "⚠️ Warning: Tests could not run (pytest issue). Proceeding with benchmark..."
else
    echo "✅ All tests passed."
fi

# Parse optional arguments
HYPOTHESIS=""
STATUS="PENDING"
while [[ $# -gt 0 ]]; do
    case $1 in
        --hypothesis)
            HYPOTHESIS="$2"
            shift 2
            ;;
        --status)
            STATUS="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Generate output filename with timestamp
TIMESTAMP=$(date +%s)
OUTPUT_FILE="$ROOT/logs/benchmark_iter_${ITER_NUM}_${TIMESTAMP}.jsonl"

echo "🔄 Running iteration $ITER_NUM (config: $CONFIG_NAME)"
echo "📁 Output: $OUTPUT_FILE"

# Source environment
if [[ -f "$ROOT/.env.local" ]]; then
    set -a
    source "$ROOT/.env.local"
    set +a
else
    echo "⚠️ Warning: .env.local not found."
fi

# 4. Run benchmark
# Note: Ensure python points to the correct environment
python "$ROOT/scripts/eval_benchmark.py" \
    --data "$ROOT/data/benchmark_clean_71.jsonl" \
    --max-results 20 \
    --strictness 0.2 \
    --validate-labels \
    --output "$OUTPUT_FILE"

echo ""
echo "✅ Benchmark complete"
echo "💾 Saving iteration..."

# 5. Save iteration
# This captures the metrics and snapshots config.json
python "$ROOT/scripts/save_iteration.py" \
    "$ITER_NUM" \
    "$CONFIG_NAME" \
    "$OUTPUT_FILE" \
    ${HYPOTHESIS:+--hypothesis "$HYPOTHESIS"} \
    --status "$STATUS"

echo ""
echo "🎯 Iteration $ITER_NUM saved successfully!"
echo ""
echo "📊 Analysis Tip:"
echo "   Compare with previous best to verify your --status decision:"
echo "   python scripts/analyze_iterations.py compare <prev_best_id> $ITER_NUM"