"""Exercise the real container with resource/network limits and EOF."""
import argparse
import gzip
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from corridor.contract import Contract
from corridor.reference import Reference
from evaluate import quantiles


def run(data,sid,out,limit=None):
    directory=data/('02_train' if sid.startswith('TRAIN') else '03_public')/sid
    command=['docker','run','--rm','-i','--platform','linux/amd64','--network','none','--cpus','8','--memory','16g',
             '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','256','corridor-solution:final']
    out.mkdir(parents=True,exist_ok=True)
    validator=Contract(Reference(data/'01_reference'))
    start=time.perf_counter()
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',bufsize=1)
    outputs=queue.Queue(); logs=[]
    def read_output():
        for line in process.stdout: outputs.put(line)
        outputs.put(None)
    def read_logs():
        for line in process.stderr:
            try: logs.append(json.loads(line))
            except json.JSONDecodeError: logs.append({'message':line.strip()})
    reader=threading.Thread(target=read_output,daemon=True); logger=threading.Thread(target=read_logs,daemon=True)
    reader.start(); logger.start()
    times=[]; count=0; first_response=None
    with gzip.open(directory/'packets.ndjson.gz','rt',encoding='utf-8') as stream, (out/(sid+'.ndjson.gz')).open('wb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0) as output:
            for line in stream:
                if limit is not None and count>=limit: break
                before=time.perf_counter(); process.stdin.write(line); process.stdin.flush()
                response=outputs.get(timeout=60 if count==0 else 10)
                if response is None: raise RuntimeError('Container terminated without response')
                times.append((time.perf_counter()-before)*1000)
                packet=json.loads(line); decision=json.loads(response)
                if first_response is None: first_response=time.perf_counter()-start
                validator.validate(decision)
                assert decision['packet_id']==packet['packet_id']
                output.write(response.encode('utf-8')); count+=1
                if count%100==0: print(json.dumps(dict(scenario=sid,packets=count,latest_roundtrip_ms=times[-1])),flush=True)
    process.stdin.close()
    code=process.wait(timeout=60); reader.join(timeout=5); logger.join(timeout=5)
    assert code==0, logs[-5:]
    assert outputs.get(timeout=5) is None, 'Extra stdout'
    elapsed=[r['elapsed_ms'] for r in logs if 'elapsed_ms' in r]
    assert len(elapsed)==count
    report=dict(scenario=sid,packets=count,exit_code=code,first_response_sec=first_response,
                roundtrip_ms=quantiles(times),processing_ms=quantiles(elapsed),
                processing_over_2000_ms=sum(t>2000 for t in elapsed),roundtrip_over_2000_ms=sum(t>2000 for t in times),
                complete_schema_valid=count,network='none',cpu_limit=8,memory_limit='16g',gpu=False,read_only=True)
    (out/(sid+'-container.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--data',type=Path,required=True); parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--scenario',required=True); parser.add_argument('--limit',type=int); args=parser.parse_args()
    run(args.data,args.scenario,args.out,args.limit)
