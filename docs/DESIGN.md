# Stockout Shield workspace design

Reference selected by the user: **Stockout Shield Workspace Design**, Stitch project `17190150116462211139`. All five reference screens were retrieved: Overview, Inventory & Data, Forecasts, Transfer & Reorder Plan, Scenario Analysis. The frontend-design skill at `/home/ray/.config/opencode/skills/frontend-design/SKILL.md` guides implementation.

## Design plan

Palette: paper `#FFFFFF`, clinical field `#F9F9FF`, ink `#111C2D`, teal `#005C55`, inset field `#F0F3FF`, structural rule `#DCE3EA`. Semantic red `#BA1A1A`, amber `#9B570D` and cyan `#006781` indicate shortages, caution and incoming stock.

Type: locally served Inter for navigation, headings and medicine names; JetBrains Mono for quantities, batch codes, forecast dates and before/after comparisons. Body text is 14–15px and tables remain readable at normal zoom.

Layout: a narrow white navigation rail, a network context panel, a slim workspace header, compact summary cells, then full-width operational tables. Overview puts the immediate action register first, the facility × medicine matrix second, and work queue / daily shortage exposure last. Forecasts put stock trajectory and depletion sequence before model diagnostics. Transfer recommendations use donor/recipient comparison panes followed by working review actions.

```
[logo             ] [network                    snapshot / workspace]
[network context  ] [page title                       primary action]
[navigation       ] [summary cells                                   ]
[                 ] [immediate action register                        ]
[                 ] [facility × medicine availability matrix          ]
[workspace/export ] [approval queue       ] [daily shortage exposure  ]
```

## Review against the brief

The selected design is the visual authority. Its clinical tables, small rectangular status tags, teal navigation, monospace stock comparisons, pale inset backgrounds and flat bordered panels replace the previous blue treatment. Uppercase labels are limited to compact table headers and operational metadata, as in the reference. Whitespace and font sizes are adjusted for normal browser viewing.

Reference content such as named regional hospitals, signed-in coordinators, cold-chain alerts and placeholder recommendations is not treated as real application data. The interface uses the saved workspace's facilities, medicines, projections and action state. HTML provides semantic presentation; native Streamlit controls retain actual filtering, validation, approvals, dispatch, receipt, downloads and scenarios.

## Verification

Screen rendering is checked through Streamlit AppTest and the core automated suite. Browser verification covers the five operational sections, sidebar collapse/reopen, a sticky accessible close control while the sidebar scrolls, and 390/785/1108 CSS-pixel content widths. No page-wide overflow or console errors were observed. The forecasting, inventory, allocation and storage algorithms are unchanged by this redesign.

## Sidebar regression fix

Hiding the entire Streamlit toolbar also hid its sidebar reopen button. Forcing sidebar minimum and maximum widths overrode the framework’s collapsed width and reserved a blank column. The fix hides only deployment chrome, leaves Streamlit in control of toolbar interaction and sidebar sizing, and keeps the sidebar header sticky while scrolling. Main content has safe spacing below the native header. Responsive selectors account for Streamlit’s layout wrappers; container queries adapt the main action and panels to the actual available content width. Scrollable tables support keyboard focus.

## Operational interface

Keep paper #FFFFFF, field #F9F9FF, ink #111C2D, teal #005C55, inset #F0F3FF and rule #DCE3EA. Inter carries instructions and larger status labels; mono remains limited to quantities and identifiers. The application opens on a network overview. The sidebar provides persistent navigation to Inventory & Data, Forecasts, Action Plan and Scenario Analysis. The overview surfaces the most urgent supply risks first; recommendation cards expose donor checks and recipient outcomes for review.
