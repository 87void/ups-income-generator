# UPS Income Generator

A Python and Streamlit application for analyzing UPS covered-call opportunities using market data from Charles Schwab.

The application retrieves UPS quotes and listed call-option data, screens contracts using configurable liquidity and quote-quality criteria, records historical observations locally, and provides CSV exports for further analysis.

> **Important:** This project is an analytical tool. It does not access brokerage positions, account balances, or order-entry endpoints, and it cannot place trades.

<img width="1408" height="768" alt="image" src="https://github.com/user-attachments/assets/a5579e96-6fd4-45e0-9f89-35e22b1aaa5e" />


## Features

- **Schwab market data:** Retrieve the UPS underlying quote and options chain.
- **Covered-call screening:** Evaluate standard 100-share call contracts.
- **Contract filters:** Filter by strike range, open interest, bid/ask spread, and quote age.
- **Expiration window:** Request options approximately 30–45 days to expiration.
- **Contract analysis:** Review bid, ask, delta, volume, open interest, moneyness, premium, and assignment scenarios.
- **Quote-quality checks:** Identify stale quotes, delayed chains, missing timestamps, and other warnings.
- **Historical tracking:** Save option-chain observations in a local SQLite database.
- **CSV exports:** Download screened contracts and historical observations.
- **Interactive dashboard:** Explore results using Streamlit.

## Technology Stack

- Python
- Streamlit
- Pandas
- SQLite
- Charles Schwab market data API
- Git and GitHub

## Project Structure

```text
ups-income-generator/
├── dashboard.py
├── schwab_client.py
├── schwab_auth.py
├── history_store.py
├── options_analyzer.py
├── requirements.txt
├── streamlit_test.py
├── README.md
├── ARCHITECTURE.md
├── .gitignore
└── data/
    └── ups_history.db       # Created locally
```

## Installation

Clone the repository:

```bash
git clone https://github.com/87void/ups-income-generator.git
cd ups-income-generator
```

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## Schwab API Configuration

The application uses a local Schwab authentication module.

Before starting:

1. Configure your Schwab developer application.
2. Complete the required OAuth authorization process.
3. Configure the local credentials and authentication files expected by the application.
4. Keep credentials, tokens, and environment files outside version control.

Never commit API secrets, access tokens, refresh tokens, or brokerage credentials.

## Run the Dashboard

```bash
streamlit run dashboard.py
```

Open the local Streamlit URL displayed in your terminal.

Select **Refresh Schwab data** to retrieve the latest available quote and options chain.

## Historical Data

The application stores historical observations locally in:

```text
data/ups_history.db
```

Snapshots are recorded when the user manually refreshes market data. The current implementation does not run a background polling service.

## Analysis Methodology

The dashboard evaluates contracts using:

- Strike range
- Days to expiration
- Bid and ask prices
- Bid/ask spread
- Delta
- Trading volume
- Open interest
- Quote timestamps and quote age
- Delayed-market-data indicators

Results are categorized as:

- `FURTHER REVIEW`
- `WATCH`
- `REJECT`

A contract passing the screening rules is only a candidate for further investigation, not a trade recommendation.

## Limitations

- Market data availability and freshness depend on Schwab.
- Open interest is not a real-time measure.
- After-hours quotes may be stale or indicative.
- The regular-session indicator uses weekday clock hours and does not account for exchange holidays or early closes.
- Assignment calculations are gross estimates and exclude fees, taxes, dividends, and early-assignment effects.
- No orders are submitted.

## Testing

Run the available tests with:

```bash
python -m unittest discover -s tests -v
```

Check Python syntax with:

```bash
python -m compileall -q .
```

## Disclaimer

This software is provided for educational and analytical purposes only. It is not investment advice, a recommendation to buy or sell securities, or a guarantee of investment results.

Users are responsible for independently verifying market data, option contract specifications, liquidity, execution prices, and risks.

## Author

Developed as a Python-based market-data and options-analysis project.

GitHub: [87void](https://github.com/87void)
