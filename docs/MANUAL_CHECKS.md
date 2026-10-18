# Manual checks for Stockout Shield

## Automated checks already completed

The core pytest suite passes 27 tests covering workbook round trips, explicit medicine mappings, conflicting specifications, duplicate and future records, causal missing-data corrections, forecast selection and uncertainty, quality gates, intermittent demand, FEFO, donor reserves, pack sizes, rejected transfers, approval/receipt idempotence, stale plans, saved-state restoration, workspace isolation, and reproducible experiment demand.

The benchmark covers six scenarios, 20 seeds and 360 policy runs. Browser checks confirmed that all five operational sections render, the sidebar collapses and reopens, and the layout works at narrow and desktop widths without page-wide overflow.

## Current workflow to check manually

1. **Overview:** inspect Shortages, Expiring stock and Deliveries. Open Review options, then confirm the selected facility and medicine persist across Inventory, Forecasts and Actions.
2. **Inventory:** use Update inventory for receipt, dispensing, stock counts, quarantine and completed-day history corrections. Confirm that dispensing uses expiry order and preserves reservations, while a history correction does not deduct stock. Receive listed incoming supplies once. Import a workbook into a separate workspace; map unknown medicine codes using the dropdowns and reject conflicting specifications.
3. **Forecasts:** change facility and medicine. Check the seven-day total, selected method, uncertainty limits, and record-quality warnings. Review an estimate and confirm the plan becomes stale or refreshes appropriately.
4. **Action Plan:** filter by destination, inspect the donor reserve and recipient benefit, read the recommendation explanation, and test an arrival-time warning.
5. **Actions:** approve and dispatch a transfer. Inspect its progress tracker and unavailable receipt explanation. Update the expected arrival, then confirm that projections change. In an isolated workspace only, use Advanced planning tools to reach arrival and receive once. Cancel another approved action and confirm its reservation is released. Reject a recommendation and confirm the plan refreshes.
6. **Scenario Analysis:** introduce a surge, delay, or route closure. Confirm previews do not change saved physical inventory; download and inspect the results.
7. **Persistence:** refresh the same workspace URL and confirm decisions persist. Open the base URL separately and confirm it starts a separate workspace.
8. **Readability and responsive layout:** check text before hover, keyboard focus, disabled controls, sidebar controls, dropdowns and tabs at narrow and desktop widths.

## Browser workflow verified on 18 October 2026

In a separate workspace, browser checks completed shortage review, approval, dispatch, a one-day arrival delay, explicit planning-date advancement, and receipt of exactly 30 units. The original saved workspace was compared with the backup: its inventory version and action history were unchanged. Mobile tracking was checked at 390 CSS pixels without page-wide overflow, and desktop navigation at 1280 pixels. The final regression suite also verifies automatic navigation to the completed transfer after receipt and selected-facility dispensing.

The launcher now starts a detached local process, checks its health, and avoids duplicate servers. Logs and its PID live in runtime/.

## Remaining validation

- Extended browser interaction for imports, forecast overrides, decisions and downloads, including a second browser engine.
- Docker image build and restart checks.
- Review benchmark trade-offs in `reports/RESULTS.md`.
- Evaluate with real inventory data and clinical supply staff before operational use.
