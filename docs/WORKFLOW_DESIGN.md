# Operational workflow improvements

The primary task is to spot a medicine shortage, inspect a response and follow stock through receipt. The existing teal identity remains; the new attention list and shipment tracker carry the interface.

- Palette: ink #172d38, muted #526776, teal #005c55, workspace #f4f7f8, amber #9b570d, red #ba1a1a.
- Type: locally hosted Inter for interface text, tabular numerals for quantities; increase small labels and remove uppercase tracking from table headings.
- Layout: left-aligned attention queue above supporting network detail; medicine context persists across operational sections. Tracking opens independently of recommendation calculation.
- Interaction: direct links from shortages, explicit next actions, a real inventory ledger and progress stages. Loading and confirmations respond to user actions. No tutorial in the interface.

The design review rejected adding more summary cards and decorative motion. Existing KPIs stay secondary to the attention queue. The implementation retains native Streamlit widgets for keyboard navigation and sidebar accessibility.

Inventory changes use optimistic version checks, idempotency keys and transactions. Reserved stock cannot be reduced or quarantined. Usage entries correct completed-day history without deducting already-counted snapshot stock. Receipts associated with approved actions must use action tracking. Existing SQLite records are preserved; no schema migration is needed.
