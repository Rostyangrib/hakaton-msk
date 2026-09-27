"""Author a notebook analysing saved Docker PUBLIC decisions, never labels."""
from pathlib import Path
import nbformat as nbf


ROOT = Path(__file__).resolve().parents[1]
cells = []


def md(source):
    cells.append(nbf.v4.new_markdown_cell(source.strip()))


def code(source):
    cells.append(nbf.v4.new_code_cell(source.strip()))


md('''# Распределение рекомендаций на PUBLIC-101 и PUBLIC-102

Анализ сохранённых ответов представленного Docker-образа `corridor-solution:final`.
Алгоритм здесь не запускается повторно. TRAIN, labels и будущие входные события не используются.

**Единица подсчёта — одна рекомендация одному автомобилю в одном пакете.**
Один автомобиль может получить одинаковое действие сотни раз. Это не число поездок,
уникальных манёвров или физически исполненных команд. Поддержка — флаг назначения
текущего снимка, а не доказательство занятости физического оператора.

В ноутбуке: проверка исходных файлов, количества и доли шести действий,
распределение во времени, число автомобилей с каждой командой, поддержка,
связь с собственными оценками ODD и переключения рекомендаций.

Зависимости для повторного запуска: `python -m pip install pandas matplotlib jupyter`.
Пути задаются в следующей ячейке. Выполненная копия уже содержит таблицы и графики.''')

code(r'''
from pathlib import Path
from collections import Counter
import gzip, hashlib, json, os, zipfile
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
from IPython.display import display, Markdown

ACTION_ORDER = ['CONTINUE', 'LIMIT_SPEED', 'REROUTE', 'HOLD', 'SAFE_STOP', 'NO_ACTION']
COLORS = dict(zip(ACTION_ORDER, ['#2E7D32', '#F9A825', '#1565C0', '#D84315', '#7B1FA2', '#78909C']))
SCENARIOS = ['PUBLIC-101', 'PUBLIC-102']

working_directory = Path.cwd().resolve()
candidates = [candidate for parent in [working_directory, *working_directory.parents]
              for candidate in (parent, parent/'hakaton-msk')]
PROJECT_ROOT = next((p for p in candidates if (p/'corridor').is_dir() and (p/'Dockerfile').is_file()), None)
if PROJECT_ROOT is None and not os.environ.get('CORRIDOR_RESULTS_DIR'):
    raise FileNotFoundError('Откройте notebook из репозитория или задайте CORRIDOR_RESULTS_DIR.')
RESULTS_DIR = Path(os.environ.get('CORRIDOR_RESULTS_DIR', str(PROJECT_ROOT/'results/technical-submission') if PROJECT_ROOT else '.'))
FIGURE_DIR = (PROJECT_ROOT/'results/analysis-public') if PROJECT_ROOT else RESULTS_DIR/'analysis-public'
FIGURE_DIR.mkdir(parents=True, exist_ok=True)
SUBMISSION_ZIP = PROJECT_ROOT.parent/'Кариков Team.zip' if PROJECT_ROOT else None

plt.rcParams.update({'font.family':'DejaVu Sans', 'figure.dpi':110, 'axes.spines.top':False,
                     'axes.spines.right':False, 'axes.titlesize':12, 'axes.labelsize':10})
pd.set_option('display.max_columns', 20)
pd.set_option('display.precision', 3)

def save_figure(fig, name):
    fig.savefig(FIGURE_DIR/(name+'.png'), dpi=160, bbox_inches='tight')
    plt.show()
    plt.close(fig)

print('Файлы результатов:', RESULTS_DIR)
print('PNG-графики:', FIGURE_DIR)
''')

md('''## 1. Загрузка и проверка данных

Проверяем уникальность пакетов и автомобилей, возрастающее время, состав команд,
соответствие объектов сценария и числу оценок. Если рядом есть исходный ZIP сдачи,
сверяем SHA256 его PUBLIC-файлов с анализируемыми файлами. Это проверка происхождения,
не новая оценка правильности решений.''')

code(r'''
rows, packet_rows, provenance = [], [], []
expected_packets = {'PUBLIC-101':540, 'PUBLIC-102':720}
for scenario in SCENARIOS:
    choices = [RESULTS_DIR/(scenario+'.result.ndjson.gz'), RESULTS_DIR/(scenario+'.ndjson.gz')]
    source = next((p for p in choices if p.is_file()), None)
    if source is None:
        raise FileNotFoundError(f'Нет результата {scenario} в {RESULTS_DIR}')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    archive_match = None
    if SUBMISSION_ZIP and SUBMISSION_ZIP.is_file():
        with zipfile.ZipFile(SUBMISSION_ZIP) as archive:
            archive_digest = hashlib.sha256(archive.read(scenario+'.result.ndjson.gz')).hexdigest()
        archive_match = digest == archive_digest
        assert archive_match, 'Анализируемый результат отличается от архива сдачи'
    provenance.append({'scenario':scenario, 'file':source.name, 'sha256':digest, 'matches_submission_zip':archive_match})
    previous_time = None
    ids, vehicle_set = set(), None
    with gzip.open(source, 'rt', encoding='utf-8') as stream:
        for index, line in enumerate(stream):
            result = json.loads(line)
            assert result['scenario_id'] == scenario
            assert result['packet_id'] not in ids
            ids.add(result['packet_id'])
            moment = pd.Timestamp(result['decision_time'])
            assert moment.tzinfo is not None
            assert previous_time is None or moment > previous_time
            previous_time = moment
            actions = result['vehicle_actions']
            vehicles = {a['vehicle_id'] for a in actions}
            assert len(actions) == len(vehicles) == 72
            if vehicle_set is None: vehicle_set = vehicles
            assert vehicles == vehicle_set
            odd = {a['vehicle_id']:a for a in result['vehicle_assessments']}
            assert set(odd) == vehicles
            distribution = Counter(a['motion_action'] for a in actions)
            assert set(distribution) <= set(ACTION_ORDER)
            packet_rows.append({'scenario':scenario, 'packet_id':result['packet_id'], 'time':moment,
                                'step':index, 'elapsed_min':index*5/60,
                                'support_count':sum(a['remote_support_required'] for a in actions),
                                **{name:distribution[name] for name in ACTION_ORDER}})
            for action in actions:
                rows.append({'scenario':scenario, 'packet_id':result['packet_id'], 'time':moment,
                             'step':index, 'vehicle_id':action['vehicle_id'], 'action':action['motion_action'],
                             'remote_support':action['remote_support_required'], 'confidence':action['confidence'],
                             'speed_limit_kmh':action.get('speed_limit_kmh'), 'safe_stop_id':action.get('safe_stop_id'),
                             'route':tuple(action.get('route_segment_ids',[])),
                             'rationale_codes':tuple(action['rationale_codes']), 'odd_status':odd[action['vehicle_id']]['odd_status']})
    assert len(ids) == expected_packets[scenario]

decisions = pd.DataFrame(rows)
packets = pd.DataFrame(packet_rows)
assert (packets[ACTION_ORDER].sum(axis=1) == 72).all()
assert packets['support_count'].between(0,6).all()
assert len(decisions) == 72*(540+720)
display(pd.DataFrame(provenance))
overview = packets.groupby('scenario').agg(packets=('packet_id','size'), start=('time','min'), end=('time','max'))
overview['vehicle_recommendations'] = decisions.groupby('scenario').size()
overview['vehicles'] = decisions.groupby('scenario')['vehicle_id'].nunique()
display(overview)
print(f'Проверено {len(packets):,} пакетов и {len(decisions):,} рекомендаций.')
''')

md('''## 2. Количества и доли команд

В знаменателе долей — все 72 автомобиля во всех пакетах соответствующего сценария.
Нулевые классы сохраняются в таблице. Итог по двум сценариям взвешен числом рекомендаций,
а не простым средним процентов двух сценариев.''')

code(r'''
counts = pd.crosstab(decisions['action'], decisions['scenario']).reindex(index=ACTION_ORDER, columns=SCENARIOS, fill_value=0)
percentages = counts.div(counts.sum(axis=0), axis=1)*100
distribution = pd.DataFrame(index=ACTION_ORDER)
for scenario in SCENARIOS:
    distribution[scenario+' count'] = counts[scenario]
    distribution[scenario+' %'] = percentages[scenario].round(3)
distribution['TOTAL count'] = counts.sum(axis=1)
distribution['TOTAL %'] = (distribution['TOTAL count']/len(decisions)*100).round(3)
display(distribution.rename_axis('motion_action'))
assert int(distribution['TOTAL count'].sum()) == len(decisions)
fig, axes = plt.subplots(1,2,figsize=(14,5),sharey=True)
for ax, scenario in zip(axes,SCENARIOS):
    values = percentages[scenario]
    bars = ax.bar(ACTION_ORDER, values, color=[COLORS[a] for a in ACTION_ORDER])
    for bar, action in zip(bars, ACTION_ORDER):
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+1,
                f'{counts.loc[action,scenario]:,}\n{values[action]:.2f}%', ha='center', va='bottom', fontsize=8)
    ax.set(title=scenario, ylabel='Доля всех рекомендаций', ylim=(0,105))
    ax.yaxis.set_major_formatter(PercentFormatter(100))
    ax.tick_params(axis='x',labelrotation=35)
    ax.grid(axis='y',alpha=.2)
fig.suptitle('Распределение motion_action по сохранённым PUBLIC-ответам')
fig.tight_layout()
save_figure(fig,'01-action-distribution')
''')

md('''## 3. Подробно о командах кроме CONTINUE и NO_ACTION

Отдельный масштаб позволяет увидеть редкие действия. Проценты по-прежнему относятся
ко всем рекомендациям, а не только к показанным столбцам. Фильтр не определяет истинную
активность автомобиля и не классифицирует команды как опасные.''')

code(r'''
focus = ['LIMIT_SPEED','REROUTE','HOLD','SAFE_STOP']
fig, axes = plt.subplots(1,2,figsize=(13,4))
for ax, scenario in zip(axes,SCENARIOS):
    bars=ax.bar(focus,counts.loc[focus,scenario],color=[COLORS[a] for a in focus])
    for bar, action in zip(bars,focus):
        ax.text(bar.get_x()+bar.get_width()/2,bar.get_height(),
                f'{counts.loc[action,scenario]:,} ({percentages.loc[action,scenario]:.2f}%)',ha='center',va='bottom',fontsize=9)
    ax.set(title=scenario,ylabel='Число рекомендаций')
    ax.margins(y=.2); ax.grid(axis='y',alpha=.2)
fig.tight_layout(); save_figure(fig,'02-actions-detail')
''')

md('''## 4. Распределение команд во времени

Каждая вертикальная сумма равна 72: показаны команды текущего полного снимка.
Ось времени — минуты от первого пакета, рассчитанные по реальному decision_time.''')

code(r'''
fig, axes = plt.subplots(2,1,figsize=(14,8))
for ax, scenario in zip(axes,SCENARIOS):
    series=packets[packets['scenario']==scenario].sort_values('time')
    minutes=(series['time']-series['time'].iloc[0]).dt.total_seconds()/60
    ax.stackplot(minutes,*[series[a].to_numpy() for a in ACTION_ORDER],labels=ACTION_ORDER,
                 colors=[COLORS[a] for a in ACTION_ORDER],step='post',alpha=.9)
    ax.set(title=scenario,xlabel='Минуты от начала сценария',ylabel='Автомобили в пакете',ylim=(0,72))
    ax.grid(alpha=.2)
axes[0].legend(loc='upper center',bbox_to_anchor=(.5,1.30),ncol=6)
fig.tight_layout(); save_figure(fig,'03-actions-over-time')
''')

md('''## 5. Скольким разным автомобилям назначалось каждое действие

Автомобиль может попасть в несколько столбцов, поэтому сумма уникальных автомобилей
по действиям не обязана равняться 72. Рядом показаны суммарные назначения площадок и
значения LIMIT_SPEED: повторные назначения не являются физическим резервированием.''')

code(r'''
unique_vehicles=decisions.groupby(['action','scenario'])['vehicle_id'].nunique().unstack('scenario').reindex(index=ACTION_ORDER,columns=SCENARIOS).fillna(0).astype(int)
display(unique_vehicles.rename_axis('Действие: уникальные автомобили'))
display(pd.crosstab(decisions['speed_limit_kmh'],decisions['scenario']).rename_axis('LIMIT_SPEED, км/ч'))
stops=decisions[decisions['action']=='SAFE_STOP']
display(pd.crosstab(stops['safe_stop_id'],stops['scenario']).rename_axis('Назначения SAFE_STOP по площадкам'))
''')

md('''## 6. Поддержка и её предел

Поддержка — отдельный флаг remote_support_required, а не motion_action. Она может
сопровождать разные действия. Здесь считаем выставленные флаги и число назначений
в каждом пакете. Достижение шести назначений не означает, что остальных запросов не было.''')

code(r'''
support_summary=packets.groupby('scenario').agg(mean_per_packet=('support_count','mean'),max_per_packet=('support_count','max'),
                                               packets_at_capacity=('support_count',lambda s:int((s==6).sum())),
                                               packets_without_support=('support_count',lambda s:int((s==0).sum())))
support_summary['support_flags_total']=decisions.groupby('scenario')['remote_support'].sum()
display(support_summary)
display(pd.crosstab(decisions.loc[decisions['remote_support'],'action'],decisions.loc[decisions['remote_support'],'scenario']).reindex(index=ACTION_ORDER,columns=SCENARIOS,fill_value=0).fillna(0).astype(int))
fig,axes=plt.subplots(2,1,figsize=(14,6))
for ax,scenario in zip(axes,SCENARIOS):
    series=packets[packets['scenario']==scenario].sort_values('time')
    minutes=(series['time']-series['time'].iloc[0]).dt.total_seconds()/60
    ax.step(minutes,series['support_count'],where='post',color='#1565C0')
    ax.axhline(6,color='#D84315',linestyle='--',label='Лимит: 6')
    ax.set(title=scenario,xlabel='Минуты от начала',ylabel='Флаги поддержки',ylim=(-.2,6.5),yticks=range(7))
    ax.grid(alpha=.2); ax.legend(loc='upper right')
fig.tight_layout(); save_figure(fig,'04-support-over-time')
''')

md('''## 7. Команды и собственные оценки ODD

Это связь с vehicle_assessments самой программы, **не сравнение с истинным ODD**.
Можно увидеть, какие действия сопровождают COMPLIANT/VIOLATED/UNKNOWN, но нельзя
сделать вывод о правильности этих оценок без эталонов.''')

code(r'''
odd_tables={}
fig,axes=plt.subplots(1,2,figsize=(14,4))
for ax,scenario in zip(axes,SCENARIOS):
    subset=decisions[decisions['scenario']==scenario]
    table=pd.crosstab(subset['odd_status'],subset['action']).reindex(index=['COMPLIANT','VIOLATED','UNKNOWN'],columns=ACTION_ORDER,fill_value=0)
    odd_tables[scenario]=table
    display(Markdown('**'+scenario+'**')); display(table)
    values=table.to_numpy()
    im=ax.imshow(np.log1p(values),cmap='Blues',aspect='auto')
    for i in range(3):
        for j in range(6):
            ax.text(j,i,f'{values[i,j]:,}',ha='center',va='center',fontsize=9,
                    color='white' if np.log1p(values[i,j])>np.log1p(values.max())*.6 else 'black')
    ax.set(title=scenario,xticks=range(6),xticklabels=ACTION_ORDER,yticks=range(3),yticklabels=table.index)
    ax.tick_params(axis='x',labelrotation=35)
fig.suptitle('Числа в ячейках — рекомендации; цвет использует log(1 + count)')
fig.tight_layout(); save_figure(fig,'05-odd-action-matrix')
''')

md('''## 8. Переключения рекомендаций и частые коды причин

Смена команды считается между соседними пакетами одного автомобиля. Первое наблюдение
не считается переключением. Смена полного назначения также учитывает путь, скорость,
площадку и поддержку. Это **не число необоснованных переключений**: их критерий неизвестен.
Коды причин — объяснения программы, а не независимо установленные факты.''')

code(r'''
ordered=decisions.sort_values(['scenario','vehicle_id','time']).copy()
ordered['signature']=list(zip(ordered['action'],ordered['route'],ordered['speed_limit_kmh'].fillna(-1),
                              ordered['safe_stop_id'].fillna(''),ordered['remote_support']))
group=ordered.groupby(['scenario','vehicle_id'],sort=False)
first=group.cumcount()==0
ordered['action_switch']=(ordered['action']!=group['action'].shift()) & ~first
ordered['assignment_switch']=(ordered['signature']!=group['signature'].shift()) & ~first
switches=ordered.groupby('scenario')[['action_switch','assignment_switch']].sum()
switches['adjacent_vehicle_steps']=decisions.groupby('scenario').size()-72
switches['action_switch_pct']=100*switches['action_switch']/switches['adjacent_vehicle_steps']
display(switches)
for scenario in SCENARIOS:
    subset=decisions[decisions['scenario']==scenario]
    reasons=Counter(reason for codes in subset['rationale_codes'] for reason in codes)
    display(Markdown('**Частые коды причин: '+scenario+'**'))
    display(pd.DataFrame(reasons.most_common(12),columns=['code','recommendations_with_code']))
''')

md('''## 9. Краткий итог

Выводы ниже формируются из загруженных результатов. Частота команды сама по себе
не показывает её полезность или безопасность. Повторные SAFE_STOP/HOLD не означают,
что автомобиль выполнил остановку. PUBLIC не содержит здесь эталонных ответов.''')

code(r'''
lines=[]
for scenario in SCENARIOS:
    most_common=counts[scenario].idxmax()
    lines.append(f'- **{scenario}:** {int(counts[scenario].sum()):,} рекомендаций, '
                 f'наиболее частая {most_common}: {percentages.loc[most_common,scenario]:.2f}%. '
                 f'REROUTE: {int(counts.loc["REROUTE",scenario])}, '
                 f'HOLD: {int(counts.loc["HOLD",scenario])}, '
                 f'SAFE_STOP: {int(counts.loc["SAFE_STOP",scenario])}. '
                 f'Шесть флагов поддержки: {int(support_summary.loc[scenario,"packets_at_capacity"])} пакетов.')
lines.append('- NO_ACTION: '+str(int(counts.loc['NO_ACTION'].sum()))+' рекомендаций в двух сценариях.')
lines.append('- Суммы распределений и предел поддержки проверены утверждениями assert. '
             'Эти проверки не доказывают истинную безопасность решений.')
display(Markdown('\n'.join(lines)))
''')

notebook = nbf.v4.new_notebook(cells=cells, metadata={
    'kernelspec': {'display_name':'Python 3', 'language':'python', 'name':'python3'},
    'language_info': {'name':'python', 'version':'3.13'},
})
destination = ROOT/'notebooks/PUBLIC_ACTION_DISTRIBUTION.ipynb'
destination.parent.mkdir(parents=True, exist_ok=True)
nbf.write(notebook,destination)
nbf.validate(notebook)
print(destination)
