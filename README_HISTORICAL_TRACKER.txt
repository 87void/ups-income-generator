UPS COVERED CALL DASHBOARD — HISTORICAL TRACKER UPDATE

Files in this update:
  dashboard.py      Updated Streamlit dashboard with a Historical opportunity tracker section.
  history_store.py  New local SQLite snapshot storage module.

INSTALL
1. Stop Streamlit in the VS Code terminal with Control+C.
2. Copy dashboard.py into ~/ups-income-generator, replacing the existing file.
3. Copy history_store.py into ~/ups-income-generator (same folder as dashboard.py and schwab_client.py).
4. Add this line to .gitignore if it is not already present:
     data/
5. From ~/ups-income-generator run:
     ./.venv/bin/python -m py_compile dashboard.py history_store.py
6. Start the dashboard:
     ./.venv/bin/python -m streamlit run dashboard.py --server.port 8501

HOW HISTORY WORKS
- Every successful click on “Refresh Schwab data” records one snapshot in data/ups_history.db.
- It saves the complete standard 100-share CALL chain returned by the existing schwab_client.py, not just rows visible under the current dashboard strike/OI/spread filters.
- The existing Schwab client currently requests the 30–45 DTE window. This update does not change it.
- The dashboard can plot historical bid and underlying-price observations for an individual option contract and export the saved history to CSV.
- History is collected only when the user manually clicks Refresh. There is no background polling or automatic trading.
- Quote timestamps, quote age and Schwab's delayed-chain flag are saved for context. Stale or after-hours observations should not be treated as executable quotes.

SAFETY
- No account, balance, position, or order-placement API calls are added.
- No orders are submitted, replaced, or cancelled.
- Keep .env and schwab_tokens.json private. The SQLite history database contains captured market data, not Schwab credentials.
