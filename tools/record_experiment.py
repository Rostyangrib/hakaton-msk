"""Bind complete offline TRAIN measurements to a verified Git runtime version."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

SCENARIOS=['TRAIN-001','TRAIN-002','TRAIN-003','TRAIN-004']


def record(repo,data,results,commit,label,description,registry,markdown,safety=None):
    def git(*args):
        return subprocess.check_output(['git','-c','safe.directory='+repo.resolve().as_posix(),*args],cwd=repo)
    commit=git('rev-parse',commit).decode().strip()
    reports=[json.loads((results/(sid+'.json')).read_text(encoding='utf-8')) for sid in SCENARIOS]
    runtime=reports[0]['runtime_sha256']
    for sid,r in zip(SCENARIOS,reports):
        expected=json.loads((data/'02_train'/sid/'scenario.json').read_text(encoding='utf-8'))['packet_count']
        assert r['scenario']==sid and r['packets']==expected,'Incomplete scenario '+sid
        assert r['runtime_sha256']==runtime,'Mixed runtime versions'
    for name,expected in runtime.items():
        content=git('show',commit+':'+name)
        lf=content.replace(b'\r\n',b'\n')
        variants={hashlib.sha256(v).hexdigest() for v in (content,lf,lf.replace(b'\n',b'\r\n'))}
        assert expected in variants,'Runtime does not match commit: '+name
    risks={r['scenario']:r for r in json.loads(safety.read_text(encoding='utf-8'))} if safety else {}
    entry=dict(code_commit=commit,label=label,description=description,runtime_sha256=runtime,
               git_content_verified='Exact bytes or Windows checkout line-ending translation',
               scenarios={sid:dict(input_sha256=r['input_sha256'],packets=r['packets'],metrics=r['metrics'],
                                   codes=r['codes'],confidence_mse=r['confidence_mse'],guard_failures=r['guard_failures'],
                                   action_counts=r['action_counts'],switches=r['switches'],latency_ms=r['latency_ms'],
                                   timing_environment=r.get('timing_environment'),
                                   moving_outside_odd_steps=risks.get(sid,{}).get('moving_outside_odd_steps'),
                                   report_sha256=hashlib.sha256((results/(sid+'.json')).read_bytes()).hexdigest()) for sid,r in zip(SCENARIOS,reports)})
    entries=json.loads(registry.read_text(encoding='utf-8')) if registry.exists() else []
    for previous in entries:
        for sid in SCENARIOS:
            assert previous['scenarios'][sid]['input_sha256']==entry['scenarios'][sid]['input_sha256'],'Input dataset changed: '+sid
    old=next((i for i,e in enumerate(entries) if e['code_commit']==commit),None)
    if old is None:entries.append(entry)
    else:entries[old]=entry
    registry.parent.mkdir(parents=True,exist_ok=True)
    registry.write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Журнал экспериментов','',
           'Коммит относится к исполняемому коду, а не к более позднему коммиту отчёта. Все четыре сценария должны быть обработаны полностью одной версией. Хеши файлов runtime проверяются против Git с учётом Windows-перевода окончаний строк; фактические SHA256 сохраняются в JSON. Метрики локальные, не официальный балл. Все TRAIN уже известны разработчикам: это регрессия, не независимый hold-out.','',
           '| Коммит кода | Версия | Метрика | TRAIN-001 | TRAIN-002 | TRAIN-003 | TRAIN-004 |','|---|---|---|---:|---:|---:|---:|']
    for e in entries:
        for key,title in [('road_final','Дороги macro F1'),('odd_active','ODD macro F1')]:
            lines.append('| '+e['code_commit'][:7]+' | '+e['label']+' | '+title+' | '+' | '.join(f"{e['scenarios'][s]['metrics'][key]['macro']:.6f}" for s in SCENARIOS)+' |')
        values=[e['scenarios'][s]['moving_outside_odd_steps'] for s in SCENARIOS]
        lines.append('| '+e['code_commit'][:7]+' | '+e['label']+' | Движение вне истинного ODD, шагов | '+' | '.join(str(v) if v is not None else 'не измерено' for v in values)+' |')
    for e in entries:
        lines+=['','## '+e['label']+' — '+e['code_commit'][:7],'',e['description'],'',
                'Пакеты: '+str(sum(e['scenarios'][s]['packets'] for s in SCENARIOS))+'. Guard-ошибки: '+str(sum(e['scenarios'][s]['guard_failures'] for s in SCENARIOS))+'.']
    lines+=['','Полные confusion, TP/FP/FN кодов, MSE уверенности, время и хеши входов/отчётов: [experiments.json](docs/experiments.json). Время локального Python под конкуренцией CPU не заменяет контроль Docker. Движение вне истинного ODD — диагностическая метрика, не число аварий.','']
    markdown.write_text('\n'.join(lines),encoding='utf-8')
    print(commit,label)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ['repo','data','results','registry','markdown']:p.add_argument('--'+key,type=Path,required=True)
    for key in ['commit','label','description']:p.add_argument('--'+key,required=True)
    p.add_argument('--safety',type=Path)
    a=p.parse_args();record(a.repo,a.data,a.results,a.commit,a.label,a.description,a.registry,a.markdown,a.safety)
