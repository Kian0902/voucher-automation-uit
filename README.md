# Voucher Automation UiT

AI-based automation for processing accounting vouchers submitted to the UiT finance department


## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create your env file from the example and add your keys:

```bash
cp .env.example .env.local
```

All data (example invoices, reports) is gitignored...


## Database (once, and after each new report export)

```bash
brew services start postgresql@17
createdb voucher_uit
python scripts/setup_database.py
python scripts/ingest_reports.py
```


## Running

Terminal 1, the lookup API (keep it running):

```bash
python api/app.py
```

Terminal 2, the agent (needs the UiT network for the LLM server):

```bash
python src/main.py
```


## Tests

```bash
python tests/test_lookup_api.py     # needs the ingested database
python tests/test_lookup_tools.py   # needs api/app.py running
```
