
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from schwab_client import SchwabClient


# ==========================================
# YOUR UPS POSITION
# ==========================================

SHARES_OWNED = 100
COST_BASIS = 85.00

MIN_STRIKE = 120.00
MIN_DTE = 30
MAX_DTE = 45

MIN_OPEN_INTEREST = 20
MAX_SPREAD_PERCENT = 30.0
MAX_QUOTE_AGE_MINUTES = 15

MULTIPLIER_REQUIRED = 100


def money(value):
    return f"${value:,.2f}"


def analyze_options():

    client = SchwabClient()
    chain = client.get_option_chain("UPS")

    underlying = float(chain.get("underlyingPrice", 0))
    delayed = chain.get("isDelayed")

    candidates = []

    expiration_map = chain.get("callExpDateMap", {})

    now = datetime.now(timezone.utc)

    print("\n" + "=" * 65)
    print("             UPS COVERED CALL ANALYZER")
    print("=" * 65)

    print(f"UPS Price:             {money(underlying)}")
    print(f"Delayed feed flag:     {delayed}")
    print(f"Shares owned:          {SHARES_OWNED}")
    print(f"Cost basis:            {money(COST_BASIS)}")
    print(f"Minimum strike:        {money(MIN_STRIKE)}")
    print(f"Expiration:            {MIN_DTE}-{MAX_DTE} DTE")
    print(f"Minimum open interest: {MIN_OPEN_INTEREST}")
    print(f"Maximum spread:        {MAX_SPREAD_PERCENT}%")

    # ======================================
    # PROCESS CONTRACTS
    # ======================================

    for expiration_key, strike_map in expiration_map.items():

        for strike_text, contracts in strike_map.items():

            for contract in contracts:

                strike = float(
                    contract.get("strikePrice", strike_text)
                )

                dte = int(
                    contract.get("daysToExpiration", 0)
                )

                if strike < MIN_STRIKE:
                    continue

                if not MIN_DTE <= dte <= MAX_DTE:
                    continue

                if contract.get("putCall") != "CALL":
                    continue

                if contract.get("nonStandard", False):
                    continue

                multiplier = float(
                    contract.get("multiplier", 100)
                )

                if multiplier != MULTIPLIER_REQUIRED:
                    continue

                bid = float(contract.get("bid") or 0)
                ask = float(contract.get("ask") or 0)

                delta = contract.get("delta")

                volume = int(
                    contract.get("totalVolume") or 0
                )

                oi = int(
                    contract.get("openInterest") or 0
                )

                intrinsic = max(underlying - strike, 0)

                midpoint = (bid + ask) / 2

                spread_pct = (
                    ((ask - bid) / midpoint) * 100
                    if midpoint > 0
                    else None
                )

                expiration = contract.get(
                    "expirationDate", expiration_key
                )[:10]

                # ==================================
                # QUOTE TIMESTAMP
                # Schwab quote timestamp is ms.
                # ==================================

                quote_ms = int(
                    contract.get("quoteTimeInLong") or 0
                )

                quote_time = None
                quote_age = None

                if quote_ms > 0:

                    quote_time = datetime.fromtimestamp(
                        quote_ms / 1000,
                        tz=timezone.utc
                    )

                    quote_age = (
                        now - quote_time
                    ).total_seconds() / 60

                # ==================================
                # QUALITY CHECKS
                # ==================================

                rejection_reasons = []
                watch_reasons = []

                if bid <= 0:
                    rejection_reasons.append("No positive bid")

                if ask <= 0:
                    rejection_reasons.append("No positive ask")

                if bid > ask and ask > 0:
                    rejection_reasons.append("Crossed market")

                if bid > 0 and bid < intrinsic - 0.01:
                    rejection_reasons.append(
                        "Bid below intrinsic value"
                    )

                if oi < MIN_OPEN_INTEREST:
                    rejection_reasons.append(
                        "Open interest below minimum"
                    )

                if spread_pct is None:
                    rejection_reasons.append(
                        "Spread cannot be calculated"
                    )

                elif spread_pct > MAX_SPREAD_PERCENT:
                    rejection_reasons.append(
                        "Wide bid/ask spread"
                    )

                if quote_age is None:
                    watch_reasons.append(
                        "Quote timestamp unavailable"
                    )

                elif quote_age > MAX_QUOTE_AGE_MINUTES:
                    watch_reasons.append(
                        "Quote older than 15 minutes"
                    )

                elif quote_age < -1:
                    watch_reasons.append(
                        "Quote timestamp is in the future"
                    )

                if delayed is True:
                    watch_reasons.append(
                        "Chain marked delayed by Schwab"
                    )

                # ==================================
                # TRADE CLASSIFICATION
                # ==================================

                if rejection_reasons:

                    status = "REJECT"

                elif watch_reasons:

                    status = "WATCH"

                else:

                    status = "ACCEPTABLE FOR FURTHER REVIEW"

                # ==================================
                # FINANCIAL CALCULATIONS
                # ==================================

                premium = bid * multiplier

                share_gain = (
                    strike - COST_BASIS
                ) * SHARES_OWNED

                assignment_profit = share_gain + premium

                premium_return = (
                    premium / (COST_BASIS * SHARES_OWNED)
                ) * 100

                candidates.append({

                    "symbol": contract.get("symbol", ""),
                    "expiration": expiration,
                    "dte": dte,
                    "strike": strike,

                    "bid": bid,
                    "ask": ask,
                    "midpoint": midpoint,

                    "delta": delta,
                    "volume": volume,
                    "oi": oi,

                    "spread_pct": spread_pct,

                    "quote_time": quote_time,
                    "quote_age": quote_age,

                    "premium": premium,
                    "share_gain": share_gain,
                    "assignment_profit": assignment_profit,
                    "premium_return": premium_return,

                    "status": status,

                    "rejection_reasons": rejection_reasons,
                    "watch_reasons": watch_reasons

                })

    # ==========================================
    # SORT RESULTS
    # ==========================================

    order = {
        "ACCEPTABLE FOR FURTHER REVIEW": 0,
        "WATCH": 1,
        "REJECT": 2
    }

    candidates.sort(
        key=lambda x: (
            order[x["status"]],
            -x["bid"]
        )
    )

    # ==========================================
    # DISPLAY RESULTS
    # ==========================================

    print("\n" + "=" * 65)
    print("                 SCREENING RESULTS")
    print("=" * 65)

    if not candidates:
        print("No contracts matched your strike and DTE filters.")
        return

    for number, option in enumerate(candidates, 1):

        print(f"\nCONTRACT {number}: {option['symbol']}")

        print(
            f"Expiration: {option['expiration']} | "
            f"DTE: {option['dte']}"
        )

        print(
            f"Strike: {money(option['strike'])} | "
            f"Bid: {money(option['bid'])} | "
            f"Ask: {money(option['ask'])}"
        )

        print(
            f"Delta: {option['delta']} | "
            f"Volume: {option['volume']} | "
            f"OI: {option['oi']}"
        )

        if option["spread_pct"] is not None:
            print(
                f"Spread: {option['spread_pct']:.1f}%"
            )

        if option["quote_time"]:

            eastern_time = option["quote_time"].astimezone(
                ZoneInfo("America/New_York")
            )

            print(
                f"Quote time: {eastern_time.strftime('%Y-%m-%d %I:%M:%S %p ET')}"
            )

            print(
                f"Quote age: {option['quote_age']:.1f} minutes"
            )

        else:
            print("Quote time: UNAVAILABLE")

        print(f"Gross premium: {money(option['premium'])}")

        print(
            f"Premium return on cost basis: "
            f"{option['premium_return']:.3f}%"
        )

        print(
            f"Profit if assigned: "
            f"{money(option['assignment_profit'])}"
        )

        print(f"DECISION: {option['status']}")

        for reason in option["rejection_reasons"]:
            print(f"  REJECT REASON: {reason}")

        for reason in option["watch_reasons"]:
            print(f"  WATCH REASON: {reason}")

    # ==========================================
    # SUMMARY
    # ==========================================

    accepted = sum(
        1 for x in candidates
        if x["status"] == "ACCEPTABLE FOR FURTHER REVIEW"
    )

    watch = sum(
        1 for x in candidates
        if x["status"] == "WATCH"
    )

    rejected = sum(
        1 for x in candidates
        if x["status"] == "REJECT"
    )

    print("\n" + "=" * 65)
    print("                    SUMMARY")
    print("=" * 65)

    print(f"Contracts analyzed: {len(candidates)}")
    print(f"Acceptable for further review: {accepted}")
    print(f"Watch: {watch}")
    print(f"Rejected: {rejected}")

    print("\nPremium calculations use the displayed bid.")
    print("All amounts are gross, before fees and taxes.")
    print("Assignment profit assumes all 100 shares are sold.")
    print("A passing screen is not a trade recommendation.")
    print("No orders have been placed.")

    print("\nAnalysis completed.")


if __name__ == "__main__":
    analyze_options()
