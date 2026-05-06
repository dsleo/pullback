#!/usr/bin/env python3
"""
Monitor benchmark execution in real-time, extracting key metrics and warnings.
"""
import subprocess
import time
import re
from pathlib import Path
from collections import defaultdict

LOG_FILE = Path("/private/tmp/claude-503/-Users-leodreyfusschmidt-Desktop-Repos-mathgent/b3a8de9b-2ec3-4f6f-ada2-84ef8080ded2/tasks/bcbhdrjd4.output")
REPORT_FILE = Path("/Users/leodreyfusschmidt/Desktop/Repos/mathgent/logs/MONITORING_REPORT.txt")

def tail_log(n=50):
    """Get last n lines of log"""
    try:
        with open(LOG_FILE) as f:
            lines = f.readlines()
        return lines[-n:]
    except:
        return []

def extract_metrics():
    """Extract current metrics from log"""
    lines = tail_log(1000)
    log_text = "".join(lines)

    metrics = {}

    # Count queries
    query_starts = len(re.findall(r'benchmark.query_start', log_text))
    query_dones = len(re.findall(r'benchmark.query_done', log_text))
    metrics['queries_started'] = query_starts
    metrics['queries_done'] = query_dones

    # Count errors
    errors = len(re.findall(r'\| ERROR', log_text))
    warnings = len(re.findall(r'\| WARNING', log_text))
    metrics['errors'] = errors
    metrics['warnings'] = warnings

    # Timeouts
    ss_timeouts = len(re.findall(r'semantic_scholar.*rate_limited', log_text))
    metrics['semantic_scholar_rate_limits'] = ss_timeouts

    # Provider timeouts
    e2b_timeouts = len(re.findall(r'sandbox.operation_timeout', log_text))
    metrics['e2b_timeouts'] = e2b_timeouts

    # Get last latency
    latency_match = re.findall(r'"latency_s":\s*([\d.]+)', log_text)
    if latency_match:
        metrics['last_latency'] = float(latency_match[-1])

    # Get last query
    query_match = re.findall(r'benchmark.query_start \[(\d+)/(\d+)\]', log_text)
    if query_match:
        metrics['current_query'] = int(query_match[-1][0])
        metrics['total_queries'] = int(query_match[-1][1])

    return metrics

def save_report(metrics):
    """Save monitoring report"""
    with open(REPORT_FILE, 'a') as f:
        f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}]\n")
        f.write(f"Progress: {metrics.get('current_query', '?')}/{metrics.get('total_queries', '?')}\n")
        f.write(f"Queries completed: {metrics.get('queries_done', 0)}\n")
        f.write(f"Errors: {metrics.get('errors', 0)} | Warnings: {metrics.get('warnings', 0)}\n")
        f.write(f"Semantic Scholar rate limits: {metrics.get('semantic_scholar_rate_limits', 0)}\n")
        f.write(f"E2B timeouts: {metrics.get('e2b_timeouts', 0)}\n")
        if 'last_latency' in metrics:
            f.write(f"Last query latency: {metrics['last_latency']:.1f}s\n")

def main():
    """Main monitoring loop"""
    # Clear report
    REPORT_FILE.write_text("=== BENCHMARK ITERATION 26 MONITORING ===\n")

    start_time = time.time()
    last_query_count = 0

    while True:
        metrics = extract_metrics()
        current_query = metrics.get('current_query', 0)

        # Check if done
        if current_query > 0 and current_query == last_query_count:
            # No progress in last check
            if metrics.get('e2b_timeouts', 0) > 0:
                print("❌ Benchmark appears stuck (E2B timeout likely)")
                save_report(metrics)
                break

        last_query_count = current_query

        # Print status
        elapsed = int(time.time() - start_time)
        print(f"\n[{time.strftime('%H:%M:%S')}] Progress: {current_query}/71 | Elapsed: {elapsed}s")
        print(f"  Errors: {metrics.get('errors', 0)} | Warnings: {metrics.get('warnings', 0)}")
        print(f"  SS rate limits: {metrics.get('semantic_scholar_rate_limits', 0)}")
        print(f"  E2B timeouts: {metrics.get('e2b_timeouts', 0)}")

        if metrics.get('last_latency'):
            print(f"  Last latency: {metrics['last_latency']:.1f}s")

        # Check if done (query_done count should indicate completion)
        if current_query >= 71 or (current_query > 0 and metrics.get('e2b_timeouts', 0) > 1):
            print("\n✅ Benchmark complete or stopped")
            save_report(metrics)
            break

        # Save report
        save_report(metrics)

        # Wait before next check
        time.sleep(30)

if __name__ == "__main__":
    main()
