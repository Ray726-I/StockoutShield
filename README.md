# Stockout Shield

Stockout Shield is a medicine supply management application for reviewing inventory, forecasting demand, identifying shortages and expiry risk, and planning transfers and replenishment.

## Open the running app

On this computer, open **http://127.0.0.1:8501** in Chrome, Firefox or another browser. No login is required. A new URL gets a separate saved workspace; keep the `?workspace=...` URL to resume that workspace.

**There are no authenticated roles or permissions in this MVP.** A saved workspace link is not a secure account. This version is intended for judges and teammates, not real clinical operations.

## Use the workspace

The application opens on **Overview** and provides these working sections:

1. **Overview:** identify medicines that may run short in the next seven days.
2. **Inventory:** inspect physical batches and reservations; record stock receipts, dispensing, counts and quarantine; receive listed supplier deliveries; import a validated workbook or six CSV files into a separate workspace.
3. **Forecasts:** select a facility and medicine. Read the forecast, record-quality flags and uncertainty limits. Low-quality pairs require an explicitly reviewed estimate.
4. **Actions:** open Recommendations, Tracking or History. Inspect the proposed quantity and donor checks; approve and dispatch a transfer, update its expected arrival and confirm receipt. Cancel an approved action before dispatch or order recording to release its reservation. Tracking runs independently of recommendation calculation.
5. **Scenario Analysis:** test a demand increase or delay, then compare inventory policies. One seed is a quick preview; 20 seeds are a broader comparison.

The included workspace contains **8 facilities, 14 medicines, 540 days and 59,557 dispensing records** so forecasts and action plans open quickly. Import a current inventory snapshot to create a workspace for your own data. The larger 12-facility, 20-medicine profile remains available for analysis.

Facility and medicine selections persist between the operational sections. Overview opens a selected shortage directly in its supply options. The inventory register shows quantities, units, expiry eligibility and reservations, with search and stock-status filters. Imports expose medicine mappings in dropdowns and retain the current workspace.

**Stock and usage semantics:** Record dispensing reduces usable, unreserved stock in expiry order and records exact batch movements in the ledger on the inventory date. Correct daily usage history replaces a completed-day total without deducting snapshot stock again. Current-day dispensing events do not enter the forecast until included in a completed-day usage record. Advancing the planning date remains an explicit forecast simulation under Advanced planning tools; it is not actual dispensing or an automatic day-close workflow.

Inventory changes and delivery-date updates use database transactions, version checks and idempotent event identifiers. Existing workspace links and records remain valid. Login and role enforcement are deferred by request.

## Local setup

Use Python 3.12.

On this machine, `./start-stockout-shield.sh` starts a detached server so closing a terminal or finishing a coding session does not stop the app. It checks the local `.venv` first and uses the existing development environment when needed. Logs are in `runtime/server.log`. Use `./start-stockout-shield.sh --stop` to stop its managed process or `--foreground` to keep server output in the terminal. Running the launcher again does not start a duplicate server.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

The SQLite database is stored in `runtime/stockout.db`. Set `STOCKOUT_DB` to use a different persistent path. Keep the database and its WAL files together when backing up a running server, or stop the server before copying it.

## Docker

```sh
docker compose up --build
```

Open http://127.0.0.1:8501. The named volume preserves SQLite data. The Docker configuration is supplied but has not been built or checked on this machine.

## Data, experiments and checks

```sh
python -m stockout.cli sample
python -m stockout.cli sample --profile medium --output data/medium
python -m stockout.cli benchmark --seeds 20 --workers 4
python -m pytest -q
```

The benchmark evaluates six scenarios, 20 common random seeds, three policies and 30 daily decisions: 360 policy runs. It uses the deterministic FEFO-verified allocator in repeated experiments. The interactive planner also offers SciPy MILP allocation followed by mandatory timing/reserve/expiry validation. A trimmed proposal is labelled and does not claim global optimality.

`data/` includes an example Excel workbook, CSVs and versioned JSON. `data/medium/` contains the starter workspace; `data/regional/` retains the larger profile. The stock CSV export is immediate; complete workbook generation is explicitly requested in Inventory & Data so it does not slow every navigation or approval. `reports/` contains completed benchmark outputs when the command finishes. Detailed daily results are gzip-compressed CSV. See [manual checks](docs/MANUAL_CHECKS.md).

## Interface design

The interface follows the user's selected **Stockout Shield Workspace Design** in Stitch (project `17190150116462211139`), using the local OpenCode frontend-design skill. Teal navigation, locally bundled Inter and JetBrains Mono fonts, alert-led registers, a facility–medicine availability matrix, a combined forecast trajectory and donor/recipient comparison panes carry the design across the operational screens. Font licenses are included in `static/fonts/`. See [design decisions](docs/DESIGN.md).

The automated suite covers forecasting, stock projection, allocation constraints and saved action transitions. Browser checks cover the five operational sections, sidebar open/close and scrolling, and content widths of 390, 785 and 1108 CSS pixels without page-wide overflow. No account or role is implied by the workspace header.

## Architecture

`stockout/data.py` validates Pydantic records and spreadsheet imports. `sample.py` creates reproducible fixtures. `forecasting.py` handles causal corrections, historical model selection and empirical seven-day ranges. `inventory.py` projects daily FEFO consumption. `planning.py` allocates transfer packs, checks donor reserves under central/upper demand assumptions and calculates reorders. `storage.py` persists datasets, plans, reservations, state transitions and events through SQLAlchemy/SQLite. `simulation.py` separates hidden future demand from policy observations. `cli.py` reproduces sample data and experiments. `app.py` provides the operational screens and main navigation.

Forecasts compare 7/28-day averages, weekly and eligible annual seasonal naïve methods, and Croston-SBA. Method selection uses earlier rolling validation windows; final windows assess interval coverage. Uncertainty is an empirical estimate from few windows, not a guarantee. With insufficient calibration evidence, the upper-demand scenario is explicitly +25% stress. Missing/censored values are estimated only from earlier reliable records and remain unchanged in the source data.

Planning uses a 30-day horizon, exact medicine identifiers/specifications, integer pack quantities, a minimum two days of remaining shelf life on arrival, facility reserves, and procurement lead times of 1–21 days. Existing approved actions reserve inventory. A changed dataset invalidates earlier recommendations. Reorder suggestions must be regenerated after transfer decisions.

## Evidence and limits

- [C-DAC e-Aushadhi](https://cdac.gov.in/index.aspx?id=achieve_health_informatics): existing inventory and distribution infrastructure.
- [Mabizela et al., 2023](https://doi.org/10.1186/s12913-023-10096-4): relationships between shortages, surplus, expiry and redistribution in eight South African facilities.
- [Redistribution of short-dated emergency medications](https://pubmed.ncbi.nlm.nih.gov/32761055/): a practical redistribution process based on historical utilization.
- [Decision-Aware Learning for Optimizing Health Supply Chains](https://arxiv.org/abs/2211.08507): evaluating forecasts through allocation outcomes.
- [USAID Logistics Handbook](https://pdf.usaid.gov/pdf_docs/pnaeb974.pdf): consumption corrections for shortages and incomplete records.
- [Forecasting: Principles and Practice](https://otexts.com/fpptr/counts.html): intermittent demand and Croston uncertainty limitations.
- [WHO inventory guidance](https://emanual.who.int/p17new/s09/Pages/9-3-4-Warehouse-and-Stock-Management.aspx): expiry-based stock rotation.

These sources motivate the approach; they do not validate this application. Real data, pharmacist review, transport/storage rules, facility authorization, and operational evaluation would be required before operational use. Cold-chain and controlled medicines are outside the current scope.

## Possible pilot roles — not implemented

- **Facility staff:** manage their facility's stock and review proposed actions.
- **Network coordinator:** plan transfers across participating facilities.
- **Administrator:** manage facilities, catalogue and permissions.
- **Viewer/judge:** inspect synthetic scenarios without changing operational records.

Authentication and enforcement of these roles would be a separate implementation.
# StockoutShield
