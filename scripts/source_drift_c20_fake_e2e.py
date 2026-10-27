"""Synthetic C20 origin drift acceptance: no provider, simulator or sockets."""
import tempfile
from pathlib import Path
from benchmarks.attention_harness.tests.source_drift_fakes import scenario,complete

def main():
    with tempfile.TemporaryDirectory(prefix='source-drift-c20-') as tmp:
        with scenario(Path(tmp)) as (runner,path,old):
            decision=complete(runner)
            assert len(decision['source_drift'])==1
            assert decision['source_drift'][0]['files'][0]['path']==str(path)
            print('PASS: older origin lock → two current-code fake replays → 10 arms → sealed source_drift decision')

if __name__=='__main__':main()
