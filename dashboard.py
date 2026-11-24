from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
from typing import Any

import pandas as pd
import streamlit as st

from schwab_client import SchwabClient
from history_store import record_snapshot, load_history, get_history_summary


# -------------------------------
# User's position and screen setup
# -------------------------------
SYMBOL = "UPS"
SHARES_OWNED = 100
COST_BASIS = 85.00
REFERENCE_STRIKE = 120.00
DEFAULT_MIN_STRIKE = COST_BASIS
DEFAULT_MAX_SPREAD_PCT = 30.0
DEFAULT_MIN_OPEN_INTEREST = 20
DEFAULT_MAX_QUOTE_AGE_MINUTES = 15
NEW_YORK = ZoneInfo("America/New_York")

st.set_page_config(
    page_title="UPS Covered Call Dashboard",
    page_icon="📈",
    layout="wide",
)


# -------------------------------
# Data helpers
# -------------------------------
def is_regular_session(now: datetime | None = None) -> bool:
    """Clock-based session check; does not know exchange holidays or early closes."""
    now = now or datetime.now(NEW_YORK)
    local = now.astimezone(NEW_YORK)
    return local.weekday() < 5 and time(9, 30) <= local.time().replace(tzinfo=None) < time(16, 0)


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def extract_underlying_quote(quote_response: dict, chain: dict) -> tuple[float, str | None]:
    """Best-effort extraction for Schwab's nested quote response."""
    chain_price = to_float(chain.get("underlyingPrice"), 0.0)
    quote_price = 0.0
    quote_time_label = None

    symbol_data = quote_response.get(SYMBOL, {}) if isinstance(quote_response, dict) else {}
    if isinstance(symbol_data, dict):
        quote_block = symbol_data.get("quote", {})
        if isinstance(quote_block, dict):
            quote_price = to_float(
                quote_block.get("lastPrice")
                or quote_block.get("mark")
                or quote_block.get("closePrice"),
                0.0,
            )
            raw_time = quote_block.get("quoteTime") or quote_block.get("quoteTimeInLong")
            if raw_time:
                try:
                    ts = float(raw_time)
                    if ts > 10_000_000_000:
                        ts /= 1000
                    quote_time_label = datetime.fromtimestamp(ts, timezone.utc).astimezone(NEW_YORK).strftime(
                        "%Y-%m-%d %I:%M:%S %p ET"
                    )
                except (ValueError, TypeError, OSError):
                    pass

    # Prefer the quote endpoint's last price, then the chain's underlying price.
    return (quote_price or chain_price, quote_time_label)


def build_contract_rows(chain: dict, min_strike: float, max_strike: float,
                        min_oi: int, max_spread_pct: float,
                        max_quote_age: int) -> list[dict]:
    rows: list[dict] = []
    underlying = to_float(chain.get("underlyingPrice"), 0.0)
    delayed = chain.get("isDelayed")
    now_utc = datetime.now(timezone.utc)

    expiration_map = chain.get("callExpDateMap", {}) or {}
    for expiration_key, strikes in expiration_map.items():
        for strike_text, contracts in (strikes or {}).items():
            for contract in contracts or []:
                strike = to_float(contract.get("strikePrice", strike_text), 0.0)
                dte = int(to_float(contract.get("daysToExpiration"), 0))
                if not (min_strike <= strike <= max_strike):
                    continue
                if contract.get("putCall") != "CALL" or contract.get("nonStandard", False):
                    continue
                multiplier = to_float(contract.get("multiplier"), 100)
                if multiplier != 100:
                    continue

                bid = to_float(contract.get("bid"), 0.0)
                ask = to_float(contract.get("ask"), 0.0)
                delta_raw = contract.get("delta")
                delta = to_float(delta_raw, float("nan")) if delta_raw is not None else float("nan")
                volume = int(to_float(contract.get("totalVolume"), 0))
                oi = int(to_float(contract.get("openInterest"), 0))
                midpoint = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
                spread_pct = ((ask - bid) / midpoint * 100) if midpoint > 0 and ask >= bid else None
                intrinsic = max(underlying - strike, 0.0)

                quote_ms = int(to_float(contract.get("quoteTimeInLong"), 0))
                quote_dt = None
                quote_age = None
                if quote_ms > 0:
                    try:
                        quote_dt = datetime.fromtimestamp(quote_ms / 1000, timezone.utc)
                        quote_age = (now_utc - quote_dt).total_seconds() / 60
                    except (ValueError, OSError, OverflowError):
                        quote_dt = None

                expiration_raw = contract.get("expirationDate", expiration_key)
                expiration = str(expiration_raw).split("T")[0]
                reasons: list[str] = []
                warnings: list[str] = []

                if bid <= 0:
                    reasons.append("No positive bid")
                if ask <= 0:
                    reasons.append("No positive ask")
                if ask > 0 and bid > ask:
                    reasons.append("Crossed market")
                if bid > 0 and bid < intrinsic - 0.01:
                    reasons.append("Bid below intrinsic value")
                if oi < min_oi:
                    reasons.append(f"Open interest below {min_oi}")
                if spread_pct is None:
                    reasons.append("Spread unavailable")
                elif spread_pct > max_spread_pct:
                    reasons.append("Bid/ask spread too wide")

                if quote_age is None:
                    warnings.append("Option quote timestamp unavailable")
                elif quote_age < -1:
                    warnings.append("Option quote timestamp is in the future")
                elif quote_age > max_quote_age:
                    warnings.append(f"Option quote older than {max_quote_age} minutes")
                if delayed is True:
                    warnings.append("Schwab marks chain as delayed")

                session_open = is_regular_session()
                if not session_open:
                    warnings.append("Outside regular-session clock; quotes may be stale")

                if reasons:
                    status = "REJECT"
                elif warnings:
                    status = "WATCH"
                else:
                    status = "FURTHER REVIEW"

                premium = bid * multiplier
                share_gain = (strike - COST_BASIS) * SHARES_OWNED
                assignment_profit = share_gain + premium
                basis_value = COST_BASIS * SHARES_OWNED
                premium_return = premium / basis_value * 100 if basis_value else 0.0
                otm_pct = ((strike / underlying) - 1) * 100 if underlying else float("nan")
                quote_time_et = quote_dt.astimezone(NEW_YORK).strftime("%Y-%m-%d %I:%M:%S %p ET") if quote_dt else "Unavailable"

                rows.append({
                    "Status": status,
                    "Expiration": expiration,
                    "DTE": dte,
                    "Strike": strike,
                    "Moneyness": "OTM" if strike >= underlying else "ITM",
                    "OTM distance %": otm_pct,
                    "Bid": bid,
                    "Ask": ask,
                    "Midpoint": midpoint if midpoint else None,
                    "Spread %": spread_pct,
                    "Delta": delta,
                    "Volume": volume,
                    "Open interest": oi,
                    "Premium at bid ($)": premium,
                    "Premium / cost basis %": premium_return,
                    "Share gain if assigned ($)": share_gain,
                    "Total gross profit if assigned ($)": assignment_profit,
                    "Quote time (ET)": quote_time_et,
                    "Quote age (min)": quote_age,
                    "Reasons": "; ".join(reasons + warnings),
                    "Symbol": contract.get("symbol", ""),
                })

    rows.sort(key=lambda r: (r["Status"] != "FURTHER REVIEW", r["Status"] != "WATCH", -r["Bid"], r["DTE"], r["Strike"]))
    return rows


def fmt_money(value: float) -> str:
    return f"${value:,.2f}"


# -------------------------------
# Page header and sidebar controls
# -------------------------------
st.title("UPS Covered Call Dashboard")
st.caption("Charles Schwab market data · Analysis only · No account access or order placement")

with st.sidebar:
    st.header("Position & screening")
    st.write(f"**Position:** {SHARES_OWNED} UPS shares")
    st.write(f"**Cost basis:** {fmt_money(COST_BASIS)} per share")
    st.write(f"**Reference strike:** {fmt_money(REFERENCE_STRIKE)}")
    st.divider()
    st.write("The $120 strike is a reference point, not a fixed filter.")
    min_strike = st.number_input(
        "Minimum strike ($)", min_value=85.0, max_value=300.0,
        value=float(DEFAULT_MIN_STRIKE), step=1.0,
        help="Defaults to your $85 cost basis so the screen does not include calls below that strike.",
    )
    max_strike = st.number_input(
        "Maximum strike ($)", min_value=float(min_strike), max_value=500.0,
        value=150.0, step=1.0,
    )
    min_oi = st.number_input("Minimum open interest", min_value=0, max_value=10000, value=DEFAULT_MIN_OPEN_INTEREST, step=5)
    max_spread = st.slider("Maximum bid/ask spread (% of midpoint)", min_value=5, max_value=200, value=int(DEFAULT_MAX_SPREAD_PCT), step=5)
    max_quote_age = st.number_input("Maximum quote age (minutes)", min_value=1, max_value=1440, value=DEFAULT_MAX_QUOTE_AGE_MINUTES, step=1)
    st.divider()
    st.caption("Expiration window remains 30–45 DTE in this version.")


# -------------------------------
# Fetch / refresh market data
# -------------------------------
col_refresh, col_status = st.columns([1, 3])
with col_refresh:
    refresh = st.button("Refresh Schwab data", type="primary", use_container_width=True)
with col_status:
    session_now = datetime.now(NEW_YORK)
    if is_regular_session(session_now):
        st.info("Regular-session clock is open. This check does not account for exchange holidays or early closes.")
    else:
        st.warning("Outside regular-session hours by the New York clock. Option quotes may be stale or indicative.")

if refresh:
    try:
        with st.spinner("Requesting UPS quote and 30–45 DTE call chain from Schwab..."):
            client = SchwabClient()
            quote_response = client.get_quote(SYMBOL)
            chain_response = client.get_option_chain(SYMBOL)
            price, quote_time_label = extract_underlying_quote(quote_response, chain_response)
            st.session_state["ups_chain"] = chain_response
            st.session_state["ups_underlying_price"] = price
            st.session_state["ups_quote_time"] = quote_time_label
            st.session_state["ups_fetched_at"] = datetime.now(NEW_YORK).strftime("%Y-%m-%d %I:%M:%S %p ET")
            history_result = record_snapshot(
                chain=chain_response,
                underlying_price=price,
                underlying_quote_time=quote_time_label,
                regular_session_clock=is_regular_session(),
                symbol=SYMBOL,
            )
        st.success(
            "Schwab market data retrieved successfully. "
            f"Saved {history_result['contracts_saved']} contracts to local history."
        )
    except Exception as exc:
        # Avoid printing credentials, tokens, or complete HTTP headers in the UI.
        st.error(f"Schwab request or local history save failed: {type(exc).__name__}: {str(exc)[:250]}")

chain = st.session_state.get("ups_chain")
if not chain:
    st.info("Select **Refresh Schwab data** to load the latest UPS quote and options chain.")
    st.stop()

underlying = to_float(st.session_state.get("ups_underlying_price"), to_float(chain.get("underlyingPrice"), 0.0))
quote_time_label = st.session_state.get("ups_quote_time")
fetched_at = st.session_state.get("ups_fetched_at", "Unknown")
rows = build_contract_rows(chain, float(min_strike), float(max_strike), int(min_oi), float(max_spread), int(max_quote_age))
df = pd.DataFrame(rows)

# -------------------------------
# Main metrics
# -------------------------------
if df.empty:
    st.warning("No standard UPS calls matched the selected strike range and the Schwab client's 30–45 DTE request.")
    st.write(f"Underlying price from chain: {fmt_money(to_float(chain.get('underlyingPrice'), 0))}")
    st.stop()

acceptable_count = int((df["Status"] == "FURTHER REVIEW").sum())
watch_count = int((df["Status"] == "WATCH").sum())
reject_count = int((df["Status"] == "REJECT").sum())
contracts_count = len(df)

m1, m2, m3, m4 = st.columns(4)
m1.metric("UPS underlying", fmt_money(underlying))
m2.metric("Contracts in selected strike range", f"{contracts_count}")
m3.metric("Further review", f"{acceptable_count}")
m4.metric("Watch / Reject", f"{watch_count} / {reject_count}")

st.caption(f"Last dashboard fetch: {fetched_at} · Chain delayed flag: {chain.get('isDelayed')} · Underlying quote timestamp: {quote_time_label or 'Not provided'}")

# -------------------------------
# Guidance / context
# -------------------------------
st.subheader("Strike-range comparison")
st.write(
    f"Showing standard calls from **{fmt_money(float(min_strike))} to {fmt_money(float(max_strike))}**. "
    f"Your $120 reference strike is highlighted in the table; it is not the only strike evaluated. "
    f"The $85 floor respects your stated preference not to write a call below your cost basis."
)

# -------------------------------
# Contract table and filters
# -------------------------------
status_options = ["FURTHER REVIEW", "WATCH", "REJECT"]
selected_statuses = st.multiselect("Show screening statuses", options=status_options, default=status_options)
show_otm_only = st.checkbox("Show only out-of-the-money calls (strike at or above UPS price)", value=True)
view = df[df["Status"].isin(selected_statuses)].copy()
if show_otm_only:
    view = view[view["Moneyness"] == "OTM"]

# Highlight the reference strike and format the main table.
if not view.empty:
    view["$120 reference"] = view["Strike"].apply(lambda x: "REFERENCE" if abs(float(x) - REFERENCE_STRIKE) < 0.001 else "")
    preferred_columns = [
        "Status", "$120 reference", "Expiration", "DTE", "Strike", "Moneyness",
        "Bid", "Ask", "Spread %", "Delta", "Volume", "Open interest",
        "Premium at bid ($)", "Premium / cost basis %",
        "Total gross profit if assigned ($)", "Quote age (min)", "Reasons", "Symbol",
    ]
    st.dataframe(
        view[preferred_columns],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Bid": st.column_config.NumberColumn(format="$%.2f"),
            "Ask": st.column_config.NumberColumn(format="$%.2f"),
            "Strike": st.column_config.NumberColumn(format="$%.2f"),
            "Spread %": st.column_config.NumberColumn(format="%.1f%%"),
            "Premium at bid ($)": st.column_config.NumberColumn(format="$%.2f"),
            "Premium / cost basis %": st.column_config.NumberColumn(format="%.3f%%"),
            "Total gross profit if assigned ($)": st.column_config.NumberColumn(format="$%.2f"),
            "Quote age (min)": st.column_config.NumberColumn(format="%.1f"),
            "Delta": st.column_config.NumberColumn(format="%.3f"),
        },
    )
else:
    st.info("No contracts match the selected display filters.")

# -------------------------------
# Candidate detail panel
# -------------------------------
st.subheader("Contract details")
contract_symbols = [str(x) for x in view["Symbol"].tolist()] if not view.empty else []
if contract_symbols:
    selected_symbol = st.selectbox("Select a contract", contract_symbols)
    selected = next((r for r in rows if r["Symbol"] == selected_symbol), None)
    if selected:
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Bid premium for 1 contract", fmt_money(selected["Premium at bid ($)"]))
        d2.metric("Share gain if assigned", fmt_money(selected["Share gain if assigned ($)"]))
        d3.metric("Total gross profit if assigned", fmt_money(selected["Total gross profit if assigned ($)"]))
        d4.metric("Premium / original cost", f"{selected['Premium / cost basis %']:.3f}%")
        st.write(f"**Screen result:** {selected['Status']}")
        st.write(f"**Quote notes:** {selected['Reasons'] or 'No automated warning triggered.'}")
        st.caption("Assignment scenario assumes 100 shares are sold at the strike and adds the gross option premium. It excludes fees, taxes, dividends, and any early-assignment effects. Premium at bid is an estimate, not a guaranteed fill.")


# -------------------------------
# Historical opportunity tracker
# -------------------------------
st.divider()
st.subheader("Historical opportunity tracker")
st.caption(
    "Each successful manual refresh saves the complete standard 100-share UPS call chain "
    "returned by schwab_client.py to a local SQLite database. The history is not limited "
    "to the current strike, OI, or spread filters."
)

try:
    history_summary = get_history_summary(SYMBOL)
    history_records = load_history(SYMBOL)
    history_df = pd.DataFrame(history_records)
except Exception as history_exc:
    history_summary = {}
    history_df = pd.DataFrame()
    st.error(f"Could not read local history database: {type(history_exc).__name__}: {str(history_exc)[:200]}")

if history_df.empty:
    st.info(
        "No historical snapshots have been saved yet. Click **Refresh Schwab data** "
        "during a future market session to begin building your local history. "
        "Each manual refresh records another observation."
    )
else:
    captured_utc = pd.to_datetime(history_df["captured_at_utc"], utc=True, errors="coerce")
    history_df["Captured at ET"] = captured_utc.dt.tz_convert(NEW_YORK).dt.strftime("%Y-%m-%d %I:%M:%S %p ET")
    history_df["Quote age (min)"] = pd.to_numeric(history_df["quote_age_minutes"], errors="coerce")
    history_df["Spread %"] = pd.to_numeric(history_df["spread_pct"], errors="coerce")

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Saved refreshes", int(history_summary.get("snapshot_count") or 0))
    h2.metric("Distinct contracts", int(history_summary.get("distinct_contracts") or 0))
    h3.metric("Saved observations", int(history_summary.get("total_contract_observations") or 0))
    h4.metric("Latest underlying", fmt_money(float(history_df["underlying_price"].dropna().iloc[-1])) if history_df["underlying_price"].notna().any() else "Unavailable")

    available_symbols = sorted(history_df["contract_symbol"].dropna().unique().tolist())
    if available_symbols:
        default_symbol = available_symbols[0]
        current_selected_symbol = st.session_state.get("history_selected_contract")
        if current_selected_symbol in available_symbols:
            default_index = available_symbols.index(current_selected_symbol)
        else:
            default_index = 0
        selected_history_symbol = st.selectbox(
            "Track a specific option contract",
            available_symbols,
            index=default_index,
            key="history_selected_contract",
        )
        contract_history = history_df[
            history_df["contract_symbol"] == selected_history_symbol
        ].sort_values("captured_at_utc").copy()

        if len(contract_history) >= 2:
            st.markdown("**Bid premium history (per share)**")
            bid_chart = contract_history.set_index("Captured at ET")[["bid"]].rename(columns={"bid": "Bid ($/share)"})
            st.line_chart(bid_chart)
            st.markdown("**Underlying UPS price history**")
            underlying_chart = contract_history.set_index("Captured at ET")[["underlying_price"]].rename(columns={"underlying_price": "UPS ($/share)"})
            st.line_chart(underlying_chart)
        else:
            st.info(
                "This contract has one saved observation. Run another refresh later "
                "to start seeing how its bid and UPS share price change over time."
            )

        display_history = contract_history[[
            "Captured at ET", "underlying_price", "expiration", "dte", "strike",
            "bid", "ask", "delta", "volume", "open_interest", "Spread %",
            "Quote age (min)", "chain_is_delayed", "regular_session_clock",
        ]].rename(columns={
            "underlying_price": "UPS price",
            "expiration": "Expiration",
            "dte": "DTE",
            "strike": "Strike",
            "bid": "Bid",
            "ask": "Ask",
            "delta": "Delta",
            "volume": "Volume",
            "open_interest": "Open interest",
            "chain_is_delayed": "Chain delayed flag",
            "regular_session_clock": "Regular-session clock",
        }).sort_values("Captured at ET", ascending=False)
        st.dataframe(display_history, use_container_width=True, hide_index=True)

    history_csv = history_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download complete option history (CSV)",
        data=history_csv,
        file_name="ups_option_history.csv",
        mime="text/csv",
    )
    st.caption(
        "History is stored locally in data/ups_history.db. Snapshots are created only "
        "when you click Refresh Schwab data; this version does not run a background poller. "
        "After-hours and stale quotes are stored with their timestamps so they can be identified, "
        "not mistaken for executable prices."
    )

# -------------------------------
# Data export
# -------------------------------
st.subheader("Export")
csv_bytes = df.to_csv(index=False).encode("utf-8")
st.download_button(
    "Download all screened contracts (CSV)",
    data=csv_bytes,
    file_name="ups_covered_call_screen.csv",
    mime="text/csv",
)

with st.expander("Important limitations"):
    st.markdown(
        "- This dashboard uses Schwab market-data endpoints through your existing local `SchwabClient`.\n"
        "- It does not access brokerage positions, balances, or order-entry endpoints and cannot place trades.\n"
        "- The 30–45 DTE window is currently set in `schwab_client.py`; this dashboard adjusts strike and liquidity filters.\n"
        "- Open interest is not a real-time measure. Quote timestamps and the delayed flag should be checked for every refresh.\n"
        "- The regular-session indicator uses weekday clock hours only; it does not include a market-holiday or early-close calendar.\n"
        "- A contract passing these rules is only a candidate for further review, not a trade recommendation."
    )
