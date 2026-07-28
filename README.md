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


## Employee index

Invoices often identify a person instead of a koststed. `scripts/scrape_employees.py` builds a local index from UiT's public employee pages (name, unit, campus, title, email, profile URL) so any of those can be used to find the corresponding koststed.

```bash
python scripts/scrape_employees.py --fresh        # look up every campus again
python scripts/scrape_employees.py                # build/refresh data/employees.json
python scripts/scrape_employees.py --check <URL>  # try one page, write nothing
python scripts/scrape_employees.py --check-all    # try every configured page
```

To add a unit, run `--check` on its list page first. The layouts are not always all the same, and a page that does not parse cleanly stops the run rather than writing a short index.

Example with employee list from "Det helsevitenskapelige fakultet":
```bash
python scripts/scrape_employees.py --check "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=88108"
```

If it parses cleanly, add the URL and unit name to `UNIT_PAGES` in the script.

