# Demo Run Logging

The mathgent demo automatically captures logs for each search run in the `logs/` directory for debugging and analysis.

## Log Files

Each time a user performs a search via the demo UI, a new log file is created with the timestamp:
- **Location**: `demo/logs/run_YYYYMMDD_HHMMSS.log`
- **Format**: Plain text with timestamps and log levels
- **Levels**: DEBUG, INFO, WARNING, ERROR

Example log file: `demo/logs/run_20260513_203015.log`

## Log Contents

Each run log contains:
1. **Query parameters**
   - `query=<search query>`
   - `strictness=<threshold 0-1>`
   - `max_results=<max papers to process>`

2. **Search pipeline events** (from the SSE stream)
   - Discovery progress (OpenAlex, arXiv, zbMATH, etc.)
   - Metadata fetching
   - Theorem extraction and scoring
   - Worker status and completion

3. **Errors and warnings**
   - Provider timeouts or failures
   - Extraction issues
   - Score computation problems

## Analyzing Logs

To find logs from a specific query:
```bash
# Find all logs from today
ls -lt demo/logs/run_20260513_*.log

# Search for a specific error across all runs
grep -r "ERROR" demo/logs/

# Find all runs with a specific query
grep "query=" demo/logs/*.log
```

To analyze performance of a particular query:
```bash
grep -E "query=Banach|discovered|matched" demo/logs/run_*.log
```

## Log File Cleanup

Log files are **not automatically deleted** to preserve them for analysis. To clean up old logs:
```bash
# Remove logs older than 7 days
find demo/logs/ -name "run_*.log" -mtime +7 -delete

# Remove all logs
rm -f demo/logs/run_*.log
```

The `demo/logs/` directory is in `.gitignore` and will not be committed to the repository.
