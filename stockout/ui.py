"""Clinical workspace presentation. Escape imported labels; keep decisions in domain modules."""
from html import escape
from datetime import date
import math
import streamlit as st


def render(html):
    st.markdown(html, unsafe_allow_html=True)


def badge(label, tone='stable'):
    return f'<span class="ss-status ss-status-{tone}"><i aria-hidden="true"></i>{escape(str(label))}</span>'


def brand():
    render('<div class="ss-brand"><svg viewBox="0 0 36 40" width="32" height="36" aria-hidden="true"><path d="M18 2 33 8v12c0 9-9 15-15 18C12 35 3 29 3 20V8Z" fill="#005c55"/><path d="M18 7 28 11v9c0 6-6 11-10 13-4-2-10-7-10-13v-9Z" fill="none" stroke="#c7f2e9" stroke-width="1.5"/><path d="M15 12h6v5h5v6h-5v5h-6v-5h-5v-6h5Z" fill="white"/></svg><div><strong>Stockout Shield</strong><span>Medicine supply management</span></div></div>')


def network_context(name, facilities, medicines, as_of):
    render(f'<section class="ss-network"><span class="ss-overline">Current supply network</span><strong>{escape(name)}</strong><div class="ss-network-meta"><span>{facilities} facilities</span><span>{medicines} medicines</span></div><div class="ss-network-date">Inventory date <b>{as_of:%d %b %Y}</b></div></section>')


def masthead(page, as_of, network='Medicine supply operations'):
    render(f'<header class="ss-masthead"><div><strong>{escape(network)}</strong><span>Medicine supply operations <i>/</i> {escape(page)}</span></div><div class="ss-header-meta"><span class="ss-snapshot">Snapshot <b>{as_of:%d %b %Y}</b></span><span class="ss-workspace-state"><i></i>Saved workspace</span></div></header>')


def summary(items):
    cells=[]
    for label, value, note, tone in items:
        cells.append(f'<div class="ss-kpi"><span class="ss-overline">{escape(label)}</span><strong class="ss-kpi-value ss-text-{tone}">{escape(str(value))}</strong><span class="ss-kpi-note">{escape(note)}</span></div>')
    render('<section class="ss-summary" aria-label="Workspace summary">'+''.join(cells)+'</section>')


def section_title(title, subtitle='', tag=''):
    render(f'<div class="ss-section-title"><div><h2>{escape(title)}</h2>{"<p>"+escape(subtitle)+"</p>" if subtitle else ""}</div>{"<span class=ss-section-tag>"+escape(tag)+"</span>" if tag else ""}</div>')


def attention_item(medicine, title, detail, tone):
    render(f'<div class="ss-attention ss-attention-{tone}"><span class="ss-attention-icon" aria-hidden="true">{"!" if tone in ["urgent","watch"] else "↗"}</span><div><strong>{escape(medicine)}</strong><p>{escape(title)}</p><span>{escape(detail)}</span></div></div>')


def import_steps(current):
    labels=['Upload','Resolve issues','Review','Import']
    steps=''.join(f'<li class="ss-step-{"complete" if i<current else "current" if i==current else "upcoming"}" {"aria-current=step" if i==current else ""}><span>{"✓" if i<current else i+1}</span><b>{label}</b></li>' for i,label in enumerate(labels))
    render('<ol class="ss-progress" aria-label="Import progress">'+steps+'</ol>')


def watchlist(risks, facilities, medicines, units):
    ordered=risks[risks.shortage_day<999].sort_values(['shortage_day','unmet7'], ascending=[True,False]).head(8)
    rows=[]
    for r in ordered.to_dict('records'):
        urgent=r['shortage_day']<7
        tone='urgent' if urgent else 'watch'
        due=date.fromisoformat(r['first_shortage']).strftime('%d %b')
        timing='Today' if r['shortage_day']==0 else f"In {int(r['shortage_day'])} {'day' if r['shortage_day']==1 else 'days'}"
        amount=r['unmet7'] if urgent else r['unmet30']
        horizon='7-day gap' if urgent else '30-day gap'
        m=r['medicine_id']
        rows.append(f'<tr><td>{badge("Critical shortage" if urgent else "Supply watch",tone)}</td><td><strong>{escape(medicines[m])}</strong><span class="ss-table-sub">{escape(m)} · {escape(units[m])}</span></td><td>{escape(facilities[r["facility_id"]])}</td><td><b class="ss-data">{timing}</b><span class="ss-table-sub ss-data">{due}</span></td><td class="ss-number"><b class="ss-diff ss-diff-{tone}">{math.ceil(amount):,} {escape(units[m])}</b><span class="ss-table-sub">{horizon}</span></td><td><span class="ss-table-sub">{"Ready for planning" if r["actionable"] else "Demand review needed"}</span></td></tr>')
    empty='<tr><td colspan="6" class="ss-empty">No shortages projected in the next 30 days. Review expiry risk and incoming supply in Inventory &amp; Data.</td></tr>'
    render('<section class="ss-panel ss-watchlist"><div class="ss-section-title"><div><h2>Medicines that need attention</h2><p>Forecast stock gaps, ordered by first shortage. Review a plan before moving stock.</p></div><span class="ss-section-tag">Next 30 days</span></div><div class="ss-table-scroll" tabindex="0" role="region" aria-label="Scrollable supply table"><table class="ss-table"><thead><tr><th>Priority</th><th>Medicine</th><th>Facility</th><th>First shortage</th><th class="ss-number">Uncovered demand</th><th>Planning status</th></tr></thead><tbody>'+(''.join(rows) or empty)+'</tbody></table></div></section>')


def facility_board(risks, facilities, medicines):
    ranked=risks.groupby('medicine_id').agg(first=('shortage_day','min'),gap=('unmet7','sum')).sort_values(['first','gap'],ascending=[True,False]).head(6).index.tolist()
    headers=''.join(f'<th>{escape(medicines[m])}</th>' for m in ranked)
    rows=[]
    lookup=risks.set_index(['facility_id','medicine_id'])
    for f,name in facilities.items():
        cells=[]
        for m in ranked:
            r=lookup.loc[(f,m)]
            day=int(r.shortage_day)
            tone='urgent' if day<7 else 'watch' if day<999 else 'stable'
            label='30d+' if day==999 else 'Today' if day==0 else f'{day}d'
            detail='No forecast gap' if day==999 else 'To first stock gap'
            cells.append(f'<td><div class="ss-matrix-cell ss-matrix-{tone}"><b>{label}</b><span>{detail}</span></div></td>')
        rows.append(f'<tr><th scope="row"><strong>{escape(name)}</strong><span class="ss-table-sub ss-data">{escape(f)}</span></th>'+''.join(cells)+'</tr>')
    render(f'<section class="ss-panel ss-board"><div class="ss-section-title"><div><h2>Availability across facilities</h2><p>The {len(ranked)} medicines with the earliest projected gaps across {len(facilities)} facilities.</p></div><span class="ss-section-tag">Central forecast</span></div><div class="ss-table-scroll" tabindex="0" role="region" aria-label="Scrollable supply table"><table class="ss-table ss-matrix"><thead><tr><th>Facility / medicine</th>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table></div><div class="ss-legend"><span><i class="ss-stable"></i>Available through 30 days</span><span><i class="ss-watch"></i>Later shortage</span><span><i class="ss-urgent"></i>Shortage within 7 days</span></div></section>')


def activity_queue(actions, facilities, medicines, units):
    active=[a for a in actions if a['status'] not in ['rejected','received']]
    rows=[]
    for a in active[:4]:
        p=a['payload']; tone='transit' if a['status'] in ['dispatched','ordered'] else 'stable'
        rows.append(f'<div class="ss-activity-row"><div><strong>{escape(medicines[p["medicine_id"]])}</strong><span>{escape(facilities[p["facility_id"]])}</span><b class="ss-data">{p["quantity"]:,} {escape(units[p["medicine_id"]])}</b></div>{badge(a["status"].title(),tone)}</div>')
    if not rows:rows=['<div class="ss-empty"><strong>No approved actions waiting</strong><p>Review a transfer or reorder recommendation to start tracking it here.</p></div>']
    render(f'<section class="ss-panel"><div class="ss-section-title"><div><h2>Action tracking queue</h2><p>Approved stock movements and purchase orders.</p></div><span class="ss-count">{len(active)}</span></div>{"".join(rows)}</section>')


def forecast_risk(risk, facility, medicine, unit):
    day=int(risk.shortage_day)
    tone='urgent' if day<7 else 'watch' if day<999 else 'stable'
    title=('Forecast shortage today' if day==0 else f'First stock gap in {day} days') if day<999 else 'Stock available through the forecast horizon'
    render(f'<section class="ss-risk-banner ss-risk-{tone}"><div class="ss-risk-icon" aria-hidden="true">{"!" if day<999 else "✓"}</div><div><strong>{title}</strong><p>{escape(facility)} / {escape(medicine)}</p><span class="ss-data">Opening stock {risk.stock_now:,.0f} {escape(unit)} &nbsp; | &nbsp; Expected 7-day demand {risk.demand7:,.0f} {escape(unit)}</span></div>{badge("Review shortage" if day<999 else "No stock gap",tone)}</section>')


def depletion_table(curve, unit, reserve_days):
    records=curve.sort_values('day').to_dict('records')
    rows=[]
    for i,r in enumerate(records[:7]):
        reserve=sum(x['demand'] for x in records[i+1:i+1+reserve_days])
        label='Stock gap' if r['unmet']>1e-6 else 'Below reserve target' if r['stock']<reserve else 'Covered'
        tone='urgent' if r['unmet']>1e-6 else 'watch' if r['stock']<reserve else 'stable'
        rows.append(f'<tr><td class="ss-data">{date.fromisoformat(r["date"]):%d %b}</td><td class="ss-number ss-data">{r["opening"]:,.1f}</td><td class="ss-number ss-data">{r["demand"]:,.1f}</td><td class="ss-number ss-data">{r["stock"]:,.1f}</td><td class="ss-number ss-data ss-text-urgent">{r["unmet"]:,.1f}</td><td>{badge(label,tone)}</td></tr>')
    render(f'<section class="ss-panel"><div class="ss-section-title"><div><h2>Stock over the next seven days</h2><p>Daily FEFO projection. Quantities in {escape(unit)}; reserve target uses the facility setting.</p></div><span class="ss-section-tag">Next 7 days</span></div><div class="ss-table-scroll" tabindex="0" role="region" aria-label="Scrollable supply table"><table class="ss-table"><thead><tr><th>Date</th><th class="ss-number">Opening</th><th class="ss-number">Demand</th><th class="ss-number">Closing stock</th><th class="ss-number">Unmet</th><th>Position</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')


def action_header(rec, facilities, medicines, units):
    transfer=rec['kind']=='transfer'
    tone='watch' if rec.get('arrives_too_late') else 'stable' if transfer else 'transit'
    status='Arrival after first shortage' if rec.get('arrives_too_late') else 'Expiry and donor safety checks passed' if transfer else 'Procurement recommendation'
    route=f'{facilities[rec["from_facility"]]} → {facilities[rec["to_facility"]]}' if transfer else facilities[rec['facility_id']]
    details=f'Batch {rec["batch_id"]} / expires {rec["expiry"]}' if transfer else f'Lead time {rec["travel_days"]} days / 7-day review period'
    render(f'<div class="ss-action-strip ss-action-strip-{tone}"><span>{status}</span><span class="ss-data">{escape(rec["recommendation_id"][:8])}</span></div><div class="ss-action-header"><div><h3>{"Transfer" if transfer else "Reorder"} {rec["quantity"]:,} {escape(units[rec["medicine_id"]])}</h3><p><strong>{escape(medicines[rec["medicine_id"]])}</strong></p><p>{escape(route)}</p></div><div class="ss-action-quantity"><span>Expected arrival</span><strong>{date.fromisoformat(rec["arrival"]):%d %b}</strong></div></div><div class="ss-action-meta"><span>{escape(details)}</span></div>')


def allocation_comparison(rec, facilities, units):
    u=escape(units[rec['medicine_id']])
    if rec['kind']=='transfer':
        before=min(rec['donor_before']); after=min(rec['donor_after'])
        render(f'<div class="ss-allocation-grid"><section><span class="ss-overline">Donor position</span><strong>{escape(facilities[rec["from_facility"]])}</strong><dl><dt>Lowest projected stock</dt><dd>{before:,.0f} → {after:,.0f} {u}</dd><dt>Reserve check</dt><dd class="ss-text-stable">Central + upper demand passed</dd></dl></section><section class="ss-recipient-pane"><span class="ss-overline">Recipient position</span><strong>{escape(facilities[rec["to_facility"]])}</strong><dl><dt>Seven-day unmet demand</dt><dd>{rec["recipient_before_unmet7"]:,.0f} → {rec["recipient_after_unmet7"]:,.0f} {u}</dd><dt>Estimated transport cost</dt><dd>INR {rec["transfer_cost"]:,.2f}</dd></dl></section></div><p class="ss-comparison-note">Stock and demand comparisons include all transfers in this plan. Donor figures use the central forecast.</p>')
    else:
        render(f'<div class="ss-order-facts"><div><span class="ss-overline">Supplier lead time</span><strong>{rec["travel_days"]} days</strong></div><div><span class="ss-overline">Review period</span><strong>7 days</strong></div><div><span class="ss-overline">Reference cost</span><strong>INR {rec["procurement_cost"]:,.2f}</strong></div></div>')
