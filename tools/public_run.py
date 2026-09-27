"""Produce deterministic submission gzip outside Git; no labels are read."""
import argparse
import gzip
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from corridor.controller import Controller
from corridor.reference import Reference


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True); args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    for directory in sorted((args.data/'03_public').iterdir()):
        controller=Controller(Reference(args.data/'01_reference'))
        count=0
        with gzip.open(directory/'packets.ndjson.gz','rt',encoding='utf-8') as stream, (args.out/(directory.name+'.ndjson.gz')).open('wb') as raw:
            with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0,filename='') as output:
                for line in stream:
                    decision=controller.process(json.loads(line)); count+=1
                    output.write((json.dumps(decision,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8'))
                    if count%100==0: print(directory.name,count,flush=True)
        print(directory.name,'completed',count,flush=True)
