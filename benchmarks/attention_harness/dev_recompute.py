"""Recompute report/ledger from SHA-checked receipts, preserving all cells."""
import argparse,json
from pathlib import Path
from .dev_ledger import DevV2Store
from .dev_report import report,export

def main():
    p=argparse.ArgumentParser();p.add_argument('--store',required=True);p.add_argument('--experiment',required=True);p.add_argument('--output',required=True);p.add_argument('--compare');a=p.parse_args()
    value=export(DevV2Store(a.store),a.experiment,a.output)
    if not value['resource_ledger']['recompute_matches']:raise SystemExit('ledger does not match opening/job receipts')
    if a.compare:
        prior=json.loads(Path(a.compare).read_bytes())
        # Live goal wall clock may change; fixed metrics and receipt totals must agree.
        for k in ['groups','cells','manifest','planned']:
            if value[k]!=prior[k]:raise SystemExit('report mismatch: '+k)
    print('receipt ledger and evidence report recomputed successfully')
if __name__=='__main__':main()
