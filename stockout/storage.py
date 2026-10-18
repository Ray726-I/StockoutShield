from __future__ import annotations
from contextlib import contextmanager
from datetime import date, datetime, timezone, timedelta
import json
from pathlib import Path
import uuid
import math
from sqlalchemy import create_engine, MetaData, Table, Column, String, Integer, Text, select, update, insert, literal_column
from .data import Dataset, Batch, Usage

metadata = MetaData()
workspaces = Table('workspaces', metadata, Column('id', String, primary_key=True),
    Column('label', String), Column('payload', Text), Column('revision', Integer))
snapshots = Table('snapshots', metadata, Column('id', String, primary_key=True),
    Column('workspace_id', String, index=True), Column('version', String), Column('payload', Text))
plans = Table('plans', metadata, Column('id', String, primary_key=True),
    Column('workspace_id', String, index=True), Column('version', String), Column('payload', Text))
actions = Table('actions', metadata, Column('id', String, primary_key=True),
    Column('workspace_id', String, index=True), Column('plan_id', String), Column('status', String),
    Column('payload', Text), Column('comment', Text))
ledger = Table('ledger', metadata, Column('id', String, primary_key=True),
    Column('workspace_id', String, index=True), Column('date', String), Column('event', String), Column('payload', Text))


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine('sqlite:///'+str(path.resolve()), connect_args={'timeout':30})
        metadata.create_all(self.engine)
        with self.engine.connect() as c:
            c.exec_driver_sql('PRAGMA journal_mode=WAL')

    @contextmanager
    def transaction(self):
        with self.engine.connect() as c:
            c.exec_driver_sql('BEGIN IMMEDIATE')
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise

    def _workspace(self, c, wid):
        row = c.execute(select(workspaces).where(workspaces.c.id==wid)).mappings().first()
        if row is None: raise ValueError('Workspace not found.')
        return row

    def _dataset(self, c, wid):
        return Dataset.from_payload(json.loads(self._workspace(c,wid)['payload']))

    def _actions(self, c, wid):
        return [dict(r, payload=json.loads(r['payload'])) for r in
                c.execute(select(actions).where(actions.c.workspace_id==wid)).mappings()]

    def _event(self,c,wid,today,event,payload,event_id=None):
        event_id=event_id or uuid.uuid4().hex
        if c.execute(select(ledger.c.id).where(ledger.c.id==event_id)).first(): return
        payload=dict(payload,_recorded_at=datetime.now(timezone.utc).isoformat())
        c.execute(insert(ledger).values(id=event_id,workspace_id=wid,date=str(today),event=event,payload=json.dumps(payload)))

    def _save(self,c,wid,ds):
        row=self._workspace(c,wid)
        c.execute(update(workspaces).where(workspaces.c.id==wid).values(payload=json.dumps(ds.payload()),revision=row['revision']+1))
        sid=f'{wid}:{ds.version}'
        if not c.execute(select(snapshots.c.id).where(snapshots.c.id==sid)).first():
            c.execute(insert(snapshots).values(id=sid,workspace_id=wid,version=ds.version,payload=json.dumps(ds.payload())))

    def create_workspace(self, ds, label=None):
        wid=uuid.uuid4().hex
        with self.transaction() as c:
            c.execute(insert(workspaces).values(id=wid,label=label or ds.label,payload=json.dumps(ds.payload()),revision=0))
            self._save(c,wid,ds)
        return wid

    def exists(self,wid):
        with self.engine.connect() as c:
            return c.execute(select(workspaces.c.id).where(workspaces.c.id==wid)).first() is not None

    def load(self,wid):
        with self.engine.connect() as c: return self._dataset(c,wid)

    def _planning_dataset(self,c,wid):
        ds=self._dataset(c,wid)
        extra=[]
        for a in self._actions(c,wid):
            p=a['payload']
            if a['status']=='approved' and p['kind']=='transfer':
                mask=ds.tables['batches'].batch_id.eq(p['batch_id'])
                ds.tables['batches'].loc[mask,'quantity']-=p['quantity']
            if a['status'] in ['approved','dispatched'] and p['kind']=='transfer':
                arrival=max(str(ds.as_of),p['arrival'])
                extra.append(dict(order_id='TRANSFER-'+a['id'],facility_id=p['to_facility'],
                    medicine_id=p['medicine_id'],quantity=p['quantity'],arrival=arrival,expiry=p['expiry']))
            elif a['status']=='approved' and p['kind']=='reorder':
                extra.append(dict(order_id='APPROVED-'+a['id'],facility_id=p['facility_id'],medicine_id=p['medicine_id'],
                                  quantity=p['quantity'],arrival=max(str(ds.as_of),p['arrival']),expiry=p['expiry']))
        if extra:
            import pandas as pd
            ds.tables['orders']=pd.concat([ds.tables['orders'],pd.DataFrame(extra)],ignore_index=True)
        return ds

    def planning_dataset(self,wid):
        with self.engine.connect() as c: return self._planning_dataset(c,wid)

    def replace(self,wid,ds):
        with self.transaction() as c:
            pending=[a for a in self._actions(c,wid) if a['status'] in ['approved','dispatched','ordered']]
            if pending: raise ValueError('Finish pending simulated actions before replacing this workspace, or create a new workspace.')
            self._save(c,wid,ds)
            self._event(c,wid,ds.as_of,'dataset_imported',{'version':ds.version})

    def save_plan(self,wid,plan):
        with self.transaction() as c:
            if self._planning_dataset(c,wid).version!=plan.dataset_version:
                raise ValueError('Inventory changed. Regenerate the plan.')
            if not c.execute(select(plans.c.id).where(plans.c.id==plan.plan_id)).first():
                c.execute(insert(plans).values(id=plan.plan_id,workspace_id=wid,version=plan.dataset_version,payload=json.dumps(plan.payload())))
        return plan.plan_id

    def list_plans(self,wid):
        with self.engine.connect() as c:
            return [json.loads(r.payload) for r in c.execute(select(plans).where(plans.c.workspace_id==wid))]

    def decide(self,wid,plan_id,recommendation_id,approve=True,comment=''):
        with self.transaction() as c:
            existing=c.execute(select(actions).where(actions.c.id==recommendation_id)).mappings().first()
            if existing:
                if existing['workspace_id']!=wid: raise ValueError('Action belongs to another workspace.')
                return existing['status'] # Idempotent before stale-plan checks.
            row=c.execute(select(plans).where((plans.c.id==plan_id)&(plans.c.workspace_id==wid))).mappings().first()
            if row is None: raise ValueError('Saved plan not found.')
            if row['version']!=self._planning_dataset(c,wid).version:
                raise ValueError('This plan is stale. Regenerate before deciding another action.')
            plan=json.loads(row['payload'])
            p=next((r for r in plan['recommendations'] if r['recommendation_id']==recommendation_id),None)
            if p is None: raise ValueError('Recommendation not found.')
            if approve and p['kind']=='reorder':
                dependencies=[r['recommendation_id'] for r in plan['recommendations'] if r['kind']=='transfer']
                states={a['id']:a['status'] for a in self._actions(c,wid)}
                if any(states.get(r) not in ['approved','dispatched','received'] for r in dependencies):
                    raise ValueError('Reorders depend on proposed transfers. Decide transfers and regenerate first.')
            state='approved' if approve else 'rejected'
            c.execute(insert(actions).values(id=recommendation_id,workspace_id=wid,plan_id=plan_id,
                status=state,payload=json.dumps(p),comment=comment))
            ds=self._dataset(c,wid)
            self._save(c,wid,ds)
            self._event(c,wid,ds.as_of,state,{'recommendation_id':recommendation_id,'comment':comment})
            # A rejection must invalidate this plan too, even though stock did not change.
            if not approve:
                c.execute(update(plans).where(plans.c.id==plan_id).values(version='rejected:'+row['version']))
            return state

    def advance_action(self,wid,aid):
        with self.transaction() as c:
            row=c.execute(select(actions).where((actions.c.id==aid)&(actions.c.workspace_id==wid))).mappings().first()
            if row is None: raise ValueError('Action not found.')
            p=json.loads(row['payload']); state=row['status']; ds=self._dataset(c,wid)
            if state in ['received','rejected','expired']: return state
            if state=='approved' and p['kind']=='transfer':
                mask=ds.tables['batches'].batch_id.eq(p['batch_id'])
                if not mask.any() or int(ds.tables['batches'].loc[mask,'quantity'].iloc[0])<p['quantity']:
                    raise ValueError('Reserved stock is no longer available; reconcile inventory.')
                batch=ds.tables['batches'].loc[mask].iloc[0]
                if batch.status!='available' or batch.facility_id!=p['from_facility'] or batch.medicine_id!=p['medicine_id']:
                    raise ValueError('The reserved batch is no longer eligible for dispatch.')
                if p['expiry']<=str(ds.as_of): raise ValueError('Batch expired before dispatch.')
                if p['expiry']<=str(ds.as_of+timedelta(days=p['travel_days'])):
                    raise ValueError('The batch would expire before arrival. Review another supply option.')
                ds.tables['batches'].loc[mask,'quantity']-=p['quantity']
                # Delivery lead time begins at actual simulated dispatch, not proposal creation.
                p['arrival']=str(ds.as_of+timedelta(days=p['travel_days']))
                newstate='dispatched'
            elif state=='approved' and p['kind']=='reorder':
                import pandas as pd
                p['arrival']=str(ds.as_of+timedelta(days=p['travel_days']))
                new=dict(order_id='ACTION-'+aid,facility_id=p['facility_id'],medicine_id=p['medicine_id'],
                         quantity=p['quantity'],arrival=p['arrival'],expiry=p['expiry'])
                ds.tables['orders']=pd.concat([ds.tables['orders'],pd.DataFrame([new])],ignore_index=True)
                newstate='ordered'
            elif state in ['dispatched','ordered']:
                if str(ds.as_of)<p['arrival']: raise ValueError('Advance the simulation clock to the arrival date before receiving.')
                import pandas as pd
                if p['expiry']<=str(ds.as_of):
                    self._event(c,wid,ds.as_of,'expired_in_transit',{'action_id':aid,'quantity':p['quantity']})
                else:
                    b=dict(batch_id='RECEIVED-'+aid,facility_id=p.get('to_facility',p['facility_id']),
                           medicine_id=p['medicine_id'],quantity=p['quantity'],expiry=p['expiry'],status='available')
                    ds.tables['batches']=pd.concat([ds.tables['batches'],pd.DataFrame([b])],ignore_index=True)
                ds.tables['orders']=ds.tables['orders'][~ds.tables['orders'].order_id.eq('ACTION-'+aid)].copy()
                newstate='expired' if p['expiry']<=str(ds.as_of) else 'received'
            else: raise ValueError('Invalid action transition.')
            c.execute(update(actions).where(actions.c.id==aid).values(status=newstate,payload=json.dumps(p)))
            self._save(c,wid,ds)
            self._event(c,wid,ds.as_of,newstate,{'action_id':aid,'quantity':p['quantity']},event_id=f'{aid}:{newstate}')
            return newstate

    def advance_clock(self,wid,days,predictions):
        """Consume forecast demand as an explicit deterministic simulation; never claim real dispensing."""
        import pandas as pd
        with self.transaction() as c:
            ds=self._dataset(c,wid)
            # An undisbursed reservation must not be consumed by the simulated facility.
            if any(a['status']=='approved' for a in self._actions(c,wid)):
                raise ValueError('Dispatch/place approved actions before advancing the clock.')
            for offset in range(days):
                today=str(ds.as_of)
                for idx,b in ds.tables['batches'].iterrows():
                    if b['status']=='available' and b['expiry']<=today and b['quantity']:
                        self._event(c,wid,today,'expiry_writeoff',{'batch_id':b['batch_id'],'quantity':int(b['quantity'])})
                        ds.tables['batches'].loc[idx,'quantity']=0
                # Supplier fixture deliveries arrive automatically; action receipts remain explicit.
                due=ds.tables['orders'][(ds.tables['orders'].arrival<=today)&~ds.tables['orders'].order_id.str.startswith('ACTION-')]
                for o in due.to_dict('records'):
                    b=dict(batch_id='SUPPLY-'+o['order_id'],facility_id=o['facility_id'],medicine_id=o['medicine_id'],
                           quantity=o['quantity'] if o['expiry']>today else 0,expiry=o['expiry'],status='available')
                    ds.tables['batches']=pd.concat([ds.tables['batches'],pd.DataFrame([b])],ignore_index=True)
                    self._event(c,wid,today,'supplier_received',o)
                ds.tables['orders']=ds.tables['orders'][~ds.tables['orders'].order_id.isin(due.order_id)].copy()
                usage=[]
                for key,pred in predictions.items():
                    f,m=key; demand=int(math.ceil(pred.mean[min(offset,len(pred.mean)-1)])); remaining=demand
                    eligible=ds.tables['batches'][(ds.tables['batches'].facility_id==f)&(ds.tables['batches'].medicine_id==m)
                        &(ds.tables['batches'].status=='available')&(ds.tables['batches'].expiry>today)].sort_values(['expiry','batch_id'])
                    for idx,b in eligible.iterrows():
                        taken=min(int(b['quantity']),remaining)
                        ds.tables['batches'].loc[idx,'quantity']-=taken
                        remaining-=taken
                    usage.append(dict(date=today,facility_id=f,medicine_id=m,dispensed=demand-remaining,
                                      availability='stockout' if remaining else 'available'))
                ds.tables['usage']=pd.concat([ds.tables['usage'],pd.DataFrame(usage)],ignore_index=True)
                ds.as_of+=timedelta(days=1)
            self._save(c,wid,ds)
            self._event(c,wid,ds.as_of,'clock_advanced',{'days':days,'demand':'Rounded central forecast; simulated, not patient data.'})

    def record_inventory(self,wid,operation,payload,*,expected_version,request_id):
        """Audit an explicit stock/history change, without touching reserved quantities.

        Usage is a completed-day total already reflected in the snapshot count.
        It updates forecasting history, never deducts stock a second time.
        """
        import pandas as pd
        event_id=f'inventory:{wid}:{request_id}'
        with self.transaction() as c:
            if c.execute(select(ledger.c.id).where(ledger.c.id==event_id)).first():
                return 'already_recorded'
            ds=self._dataset(c,wid)
            if ds.version!=expected_version:
                raise ValueError('Inventory changed in another session. Refresh and review the current quantities.')
            reason=str(payload.get('reason','')).strip()
            if len(reason)<3: raise ValueError('Enter a reason or delivery reference (at least 3 characters).')
            details=dict(payload)
            if operation in ['receive','usage','dispense']:
                if payload.get('facility_id') not in set(ds.tables['facilities'].facility_id):
                    raise ValueError('Choose a facility in this workspace.')
                if payload.get('medicine_id') not in set(ds.tables['medicines'].medicine_id):
                    raise ValueError('Choose a medicine from this catalogue.')
            if operation=='receive':
                batch=Batch.model_validate(payload).model_dump(mode='json')
                if batch['quantity']<=0: raise ValueError('Received quantity must be greater than zero.')
                if batch['expiry']<=str(ds.as_of): raise ValueError('Received stock must expire after the inventory date.')
                if batch['status']!='available': raise ValueError('Receive stock first, then record any quarantine separately.')
                if ds.tables['batches'].batch_id.eq(batch['batch_id']).any():
                    raise ValueError('This batch reference already exists. Use an adjustment for an existing batch.')
                ds.tables['batches']=pd.concat([ds.tables['batches'],pd.DataFrame([batch])],ignore_index=True)
                details.update(batch)
            elif operation in ['adjust','quarantine','release']:
                mask=ds.tables['batches'].batch_id.eq(payload.get('batch_id'))
                if not mask.any(): raise ValueError('Choose an existing batch.')
                batch=ds.tables['batches'].loc[mask].iloc[0].to_dict()
                reserved=sum(a['payload']['quantity'] for a in self._actions(c,wid)
                    if a['status']=='approved' and a['payload']['kind']=='transfer'
                    and a['payload']['batch_id']==batch['batch_id'])
                details.update(facility_id=batch['facility_id'],medicine_id=batch['medicine_id'],previous_quantity=int(batch['quantity']),previous_status=batch['status'])
                if operation=='adjust':
                    updated=Batch.model_validate(dict(batch,quantity=payload.get('quantity')))
                    if updated.quantity<reserved: raise ValueError(f'{reserved} units are reserved for an approved transfer. The count cannot be lower than this reservation.')
                    ds.tables['batches'].loc[mask,'quantity']=updated.quantity
                    details['quantity']=updated.quantity
                else:
                    if operation=='quarantine' and reserved:
                        raise ValueError('This batch is reserved for an approved transfer. Resolve the reservation before quarantining it.')
                    if operation=='release' and batch['expiry']<=str(ds.as_of):
                        raise ValueError('An expired batch cannot be released for use.')
                    ds.tables['batches'].loc[mask,'status']='quarantined' if operation=='quarantine' else 'available'
            elif operation=='dispense':
                usage=Usage.model_validate(dict(payload,date=str(ds.as_of),availability='available')).model_dump(mode='json')
                if usage['dispensed']<1: raise ValueError('Dispensed quantity must be greater than zero.')
                frame=ds.tables['batches']
                reserved={}
                for action in self._actions(c,wid):
                    if action['status']=='approved' and action['payload']['kind']=='transfer':
                        p=action['payload'];reserved[p['batch_id']]=reserved.get(p['batch_id'],0)+p['quantity']
                eligible=frame[frame.facility_id.eq(usage['facility_id'])&frame.medicine_id.eq(usage['medicine_id'])&frame.status.eq('available')&frame.expiry.gt(str(ds.as_of))].sort_values(['expiry','batch_id'])
                free=sum(max(0,int(b['quantity'])-reserved.get(b['batch_id'],0)) for b in eligible.to_dict('records'))
                if usage['dispensed']>free: raise ValueError(f'Only {free} usable, unreserved units are available. Check the count or resolve a reservation before dispensing.')
                remaining=usage['dispensed'];movements=[]
                for idx,b in eligible.iterrows():
                    take=min(remaining,max(0,int(b['quantity'])-reserved.get(b['batch_id'],0)))
                    if take:
                        frame.loc[idx,'quantity']-=take
                        movements.append(dict(batch_id=b['batch_id'],quantity=take))
                        remaining-=take
                    if not remaining:break
                details.update(usage,movements=movements,quantity=usage['dispensed'])
            elif operation=='usage':
                usage=Usage.model_validate(payload).model_dump(mode='json')
                if usage['date']>=str(ds.as_of): raise ValueError('Usage history must precede the inventory date. Choose a completed reporting day.')
                frame=ds.tables['usage']
                mask=frame.date.eq(usage['date'])&frame.facility_id.eq(usage['facility_id'])&frame.medicine_id.eq(usage['medicine_id'])
                details['previous_record']=frame.loc[mask].to_dict('records')
                ds.tables['usage']=pd.concat([frame.loc[~mask],pd.DataFrame([usage])],ignore_index=True)
                details.update(usage,stock_change=0)
            else: raise ValueError('Unknown inventory operation.')
            self._save(c,wid,ds)
            self._event(c,wid,ds.as_of,'inventory_'+operation,details,event_id=event_id)
            return 'recorded'

    def cancel_action(self,wid,aid,reason):
        with self.transaction() as c:
            action=next((a for a in self._actions(c,wid) if a['id']==aid),None)
            if action is None: raise ValueError('Action not found.')
            if action['status']=='cancelled': return
            if action['status']!='approved': raise ValueError('Only an action awaiting dispatch or order recording can be cancelled.')
            if len(reason.strip())<3: raise ValueError('Enter a reason for cancelling this action.')
            c.execute(update(actions).where(actions.c.id==aid).values(status='cancelled',comment=reason.strip()))
            ds=self._dataset(c,wid)
            self._event(c,wid,ds.as_of,'cancelled',dict(action_id=aid,reason=reason.strip()),event_id=f'{aid}:cancelled')

    def receive_supply(self,wid,order_id,*,expected_version,reason='Recorded delivery'):
        """Receive a listed supplier order once; action orders use advance_action."""
        import pandas as pd
        event_id=f'supply:{wid}:{order_id}'
        with self.transaction() as c:
            if c.execute(select(ledger.c.id).where(ledger.c.id==event_id)).first(): return 'received'
            ds=self._dataset(c,wid)
            if ds.version!=expected_version: raise ValueError('Inventory changed. Refresh before receiving this delivery.')
            orders=ds.tables['orders']; selected=orders[orders.order_id.eq(order_id)]
            if selected.empty: raise ValueError('Incoming supply not found.')
            if order_id.startswith(('ACTION-','TRANSFER-','APPROVED-')):
                raise ValueError('Receive this delivery from action tracking.')
            item=selected.iloc[0].to_dict()
            if item['arrival']>str(ds.as_of): raise ValueError('This delivery is not due yet. Update its expected arrival if it arrived earlier.')
            if item['expiry']<=str(ds.as_of): raise ValueError('This supply has expired. Do not receive it as usable stock.')
            bid='SUPPLY-'+order_id
            if ds.tables['batches'].batch_id.eq(bid).any(): raise ValueError('This delivery already has a stock entry. Reconcile the incoming order.')
            batch=dict(batch_id=bid,facility_id=item['facility_id'],medicine_id=item['medicine_id'],quantity=item['quantity'],expiry=item['expiry'],status='available')
            ds.tables['batches']=pd.concat([ds.tables['batches'],pd.DataFrame([batch])],ignore_index=True)
            ds.tables['orders']=orders[~orders.order_id.eq(order_id)].copy()
            self._save(c,wid,ds)
            self._event(c,wid,ds.as_of,'supplier_received',dict(item,reason=reason),event_id)
            return 'received'

    def update_arrival(self,wid,reference,arrival,reason,*,expected_version,request_id):
        event_id=f'arrival:{wid}:{request_id}'
        with self.transaction() as c:
            if c.execute(select(ledger.c.id).where(ledger.c.id==event_id)).first(): return
            ds=self._dataset(c,wid)
            if self._planning_dataset(c,wid).version!=expected_version:
                raise ValueError('Supply commitments changed. Refresh before updating the delivery.')
            arrival=date.fromisoformat(str(arrival)).isoformat()
            if arrival<str(ds.as_of): raise ValueError('Expected arrival cannot precede the current inventory date.')
            if len(reason.strip())<3: raise ValueError('Explain why the delivery date changed.')
            action=next((a for a in self._actions(c,wid) if a['id']==reference),None)
            if action:
                if action['status'] not in ['dispatched','ordered']:
                    raise ValueError('Update arrival after dispatch or order recording.')
                p=action['payload']; previous=p['arrival']
                if arrival>=p['expiry']: raise ValueError('The medicine would expire before arrival. Resolve this delivery before replanning.')
                p['arrival']=arrival
                c.execute(update(actions).where(actions.c.id==reference).values(payload=json.dumps(p)))
                mask=ds.tables['orders'].order_id.eq('ACTION-'+reference)
                ds.tables['orders'].loc[mask,'arrival']=arrival
                mid=p['medicine_id']; facility=p['facility_id']
            else:
                mask=ds.tables['orders'].order_id.eq(reference)
                if not mask.any(): raise ValueError('Delivery not found in this workspace.')
                p=ds.tables['orders'].loc[mask].iloc[0]
                if arrival>=p.expiry: raise ValueError('The medicine would expire before arrival.')
                previous=p.arrival;mid=p.medicine_id;facility=p.facility_id
                ds.tables['orders'].loc[mask,'arrival']=arrival
            self._save(c,wid,ds)
            self._event(c,wid,ds.as_of,'arrival_updated',dict(reference=reference,previous_arrival=previous,arrival=arrival,reason=reason,medicine_id=mid,facility_id=facility),event_id)

    def action_history(self,wid):
        with self.engine.connect() as c: return self._actions(c,wid)

    def events(self,wid):
        with self.engine.connect() as c:
            return [dict(r, payload=json.loads(r['payload'])) for r in
                c.execute(select(ledger).where(ledger.c.workspace_id==wid).order_by(literal_column('rowid'))).mappings()]
