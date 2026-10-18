"""Generate fixtures and run reproducible inventory experiments."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import pandas as pd
from .data import Dataset
from .sample import write_sample
from .simulation import run_experiment, Experiment, SCENARIOS


def save_experiment(result,out):
    out.mkdir(parents=True,exist_ok=True)
    for name in ['summary','runs','facility_results']:
        getattr(result,name).to_csv(out/(name+'.csv'),index=False)
    result.daily_results.to_csv(out/'daily_results.csv.gz',index=False,compression='gzip')
    (out/'settings.json').write_text(json.dumps(result.settings,indent=2))
    lines=['# Stockout Shield: simulation results','',
        '**Synthetic demonstration only. Modelled inventory outcomes, not a clinical study.**','',
        f"{len(result.settings['seeds'])} seeds × {len(result.settings['scenarios'])} scenarios × 3 policies; {result.settings['days']} days per policy.",'',
        'Each policy receives identical hidden demand, initial stock, supplier conditions and an INR 20,000 procurement budget. Forecasts see only the policy’s past dispensing. The two forecast policies use the same reorder calculation; only the transfer option differs.','',
        'Repeated experiments use the deterministic FEFO-verified allocator. The interactive app also offers a MILP proposal with mandatory safety verification. Model choice is frozen from historical validation; predictions update daily.','',
        '| Scenario | Policy | Unmet units | Stockout days | Expired units | Procurement INR | Transfer INR |',
        '|---|---|---:|---:|---:|---:|---:|']
    for r in result.summary.to_dict('records'):
        lines.append(f"| {r['scenario']} | {r['policy']} | {r['unmet_units']:.1f} | {r['stockout_days']:.1f} | {r['expired_units']:.1f} | {r['procurement_cost']:.1f} | {r['transfer_cost']:.1f} |")
    lines+=['','Values are means across seeds. Stockout days count facility–medicine days with unmet demand. Prices are synthetic. Inspect runs.csv for paired seeds, facility_results.csv for local outcomes and daily_results.csv.gz for all dispensing traces.','',
        '## Limits','',
        '- Synthetic data does not establish performance at real facilities.',
        '- Corrections to stockout-affected consumption are estimates, not observed unmet demand.',
        '- Empirical uncertainty coverage uses few validation windows; ranges are not guarantees.',
        '- Inspect service, waste and cost trade-offs together. No improvement percentage is guaranteed.',
        '- Supplier capacity, cold-chain handling and approval delays are simplified.',
        '- An unforeseen surge becomes visible to policies only through subsequent observed dispensing.','']
    (out/'RESULTS.md').write_text('\n'.join(lines))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sample=sub.add_parser('sample');sample.add_argument('--output',default='data')
    sample.add_argument('--profile',choices=['compact','medium','expanded'],default='compact')
    bench=sub.add_parser('benchmark')
    bench.add_argument('--dataset',default='data/sample.json');bench.add_argument('--output',default='reports')
    bench.add_argument('--seeds',type=int,default=20);bench.add_argument('--seed-start',type=int,default=0)
    bench.add_argument('--scenario',action='append',choices=list(SCENARIOS))
    bench.add_argument('--days',type=int,default=30);bench.add_argument('--workers',type=int,default=1)
    args=parser.parse_args()
    if args.command=='sample':
        ds=write_sample(args.output,profile=args.profile);print(f'Wrote {len(ds.tables["usage"]):,} synthetic observations; version {ds.version}',flush=True);return
    if min(args.seeds,args.workers,args.days)<1 or args.days>30:parser.error('Positive seeds/workers; days between 1 and 30.')
    source=Path(args.dataset).resolve();ds=Dataset.from_payload(json.loads(source.read_text()))
    seeds=list(range(args.seed_start,args.seed_start+args.seeds));scenarios=args.scenario or list(SCENARIOS)
    start=time.perf_counter()
    if args.workers==1:
        result=run_experiment(ds,seeds=seeds,scenarios=scenarios,days=args.days,
            progress=lambda d,t,s,k:print(f'{d}/{t}: {s}, seed {k}',flush=True))
    else:
        tasks=[(s,k) for s in scenarios for k in seeds]
        with tempfile.TemporaryDirectory(prefix='stockout-benchmark-') as temp:
            def task(item):
                idx,(scenario,seed)=item;out=Path(temp)/str(idx)
                cmd=[sys.executable,'-m','stockout.cli','benchmark','--dataset',str(source),'--seeds','1',
                    '--seed-start',str(seed),'--scenario',scenario,'--days',str(args.days),'--workers','1','--output',str(out)]
                r=subprocess.run(cmd,capture_output=True,text=True,cwd=Path(__file__).resolve().parents[1])
                if r.returncode:raise RuntimeError(r.stderr or r.stdout)
                return out,scenario,seed
            outputs=[]
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for future in as_completed([pool.submit(task,x) for x in enumerate(tasks)]):
                    out,scenario,seed=future.result();outputs.append(out)
                    print(f'{len(outputs)}/{len(tasks)}: {scenario}, seed {seed}',flush=True)
            frames={n:pd.concat([pd.read_csv(o/(n+'.csv'+('.gz' if n=='daily_results' else ''))) for o in outputs],ignore_index=True)
                    for n in ['runs','facility_results','daily_results']}
            for n in frames:
                keys=['scenario','seed','policy']+(['date','facility_id','medicine_id'] if n=='daily_results' else ['facility_id','medicine_id'] if n=='facility_results' else [])
                frames[n]=frames[n].sort_values(keys).reset_index(drop=True)
            metrics=['unmet_units','stockout_days','expired_units','expiry_value','procurement_cost','transfer_cost']
            summary=frames['runs'].groupby(['scenario','policy'],sort=False)[metrics].mean().reset_index()
            settings=json.loads((outputs[0]/'settings.json').read_text());settings.update(seeds=seeds,scenarios=scenarios)
            result=Experiment(summary,frames['runs'],frames['facility_results'],frames['daily_results'],settings)
    result.settings['dataset_version']=ds.version
    save_experiment(result,Path(args.output))
    print(f'Wrote {len(result.runs)} policy runs in {time.perf_counter()-start:.1f}s to {args.output}',flush=True)

if __name__=='__main__':main()
