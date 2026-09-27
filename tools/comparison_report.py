"""Publish measured old/new results, charts and limitations from completed runs."""
import argparse
import json
import shutil
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def load(path): return json.loads(path.read_text(encoding='utf-8'))


def build(repo,root,document):
    analysis=root/'analysis';plots=analysis/'plots';plots.mkdir(exist_ok=True)
    train=[(load(repo/'results/train-current'/(s+'.json')),load(repo/'results/train-new'/(s+'.json'))) for s in ['TRAIN-001','TRAIN-002','TRAIN-003','TRAIN-004']]
    safety_old=load(repo.parent/'output/analysis/train-current/safety_analysis.json')
    safety_new=load(analysis/'train-new/safety_analysis.json')
    public=load(analysis/'public_comparison.json');synthetic=load(analysis/'synthetic_comparison.json')
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10})
    fig,axes=plt.subplots(1,3,figsize=(14,4))
    for axis,(metric,title) in zip(axes,[('road_final','Дороги'),('odd_active','ODD'),('source_status_proxy','Источники (proxy)')]):
        x=np.arange(4)
        for version,label,offset in [(0,'Старая',-.19),(1,'Новая',.19)]: axis.bar(x+offset,[r[version]['metrics'][metric]['macro'] for r in train],.38,label=label)
        axis.set(xticks=x,xticklabels=['001','002','003','004'],xlabel='TRAIN',ylabel='Macro F1',title=title,ylim=(0,1.05));axis.grid(axis='y',alpha=.2)
    axes[0].legend();fig.tight_layout();fig.savefig(plots/'train_f1.png',dpi=160);plt.close(fig)
    actions=['CONTINUE','LIMIT_SPEED','REROUTE','HOLD','SAFE_STOP','NO_ACTION']
    fig,axes=plt.subplots(1,2,figsize=(12,4))
    for axis,r in zip(axes,public):
        x=np.arange(6)
        axis.bar(x-.2,[r['old_actions'].get(a,0) for a in actions],.4,label='Старая')
        axis.bar(x+.2,[r['new_actions'].get(a,0) for a in actions],.4,label='Новая')
        axis.set(xticks=x,xticklabels=actions,title=r['scenario'],ylabel='Рекомендаций');axis.tick_params(axis='x',rotation=35);axis.legend();axis.grid(axis='y',alpha=.2)
    fig.tight_layout();fig.savefig(plots/'public_actions.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(12,5))
    for axis,r in zip(axes,public):
        matrix=np.array([[r['transitions'].get(a+' → '+b,0) for b in actions] for a in actions]);axis.imshow(np.log1p(matrix),cmap='Blues')
        for y in range(6):
            for x in range(6):
                if matrix[y,x]: axis.text(x,y,str(matrix[y,x]),ha='center',va='center',fontsize=8,color='white' if matrix[y,x]>2000 else 'black')
        axis.set(xticks=range(6),xticklabels=actions,yticks=range(6),yticklabels=actions,title=r['scenario'],xlabel='Новая',ylabel='Старая');axis.tick_params(axis='x',rotation=35)
    fig.tight_layout();fig.savefig(plots/'public_transitions.png',dpi=160);plt.close(fig)
    fig,axis=plt.subplots(figsize=(8,4));x=np.arange(4)
    axis.bar(x-.2,[r['moving_outside_odd_steps'] for r in safety_old],.4,label='Старая')
    axis.bar(x+.2,[r['moving_outside_odd_steps'] for r in safety_new],.4,label='Новая')
    axis.set(xticks=x,xticklabels=['TRAIN-001','TRAIN-002','TRAIN-003','TRAIN-004'],ylabel='Рекомендаций',title='Движущие рекомендации при истинном нарушении ODD');axis.legend();axis.grid(axis='y',alpha=.2)
    fig.tight_layout();fig.savefig(plots/'train_odd_risk.png',dpi=160);plt.close(fig)
    lines=['# Сравнение старого и нового алгоритма','',
           'Дата: 28.09.2026. Старая версия: ca971a4 (runtime технической сдачи); новая: 6486a44. Восемь предложенных направлений реализованы без изменения правила CONTINUE/REROUTE. ML и обучаемая калибровка не используются.','',
           '## Что изменилось','',
           '| Направление | Реализация и смысл |','|---|---|',
           '| Доверие по показателям | `corridor/trust.py`, `fusion.py`: отдельные Beta-окна для доступности, скорости, очереди, видимости, дождя и ветра. OPEN не подтверждает скорость. Числовые отказы не выключают автоматически корректную доступность. При отсутствии сопоставимого независимого подтверждения положительный голос не начисляется. |',
           '| Доступность и загрузка | `basic.py`, `fusion.py`: закрытие/полосы отдельно от очереди. Три разных сообщения очереди >=100 м за 60 с либо два источника допускают CONGESTED без использования рекомендованной скорости RSU как измеренной. Прямое подтверждение частичной блокировки сохраняет приоритет. |',
           '| Память погоды | `memory.py`, `odd.py`: предупреждение ограничено 90 с и 5 км; исчезновение старой станции не доказывает восстановление. До трёх новых подтверждений прежних источников — UNKNOWN, а не вымышленное нарушение. |',
           '| Качество V2X | `fusion.py`, `odd.py`: учитывается цикл публикаций по сегментам, явный отказ/потеря >=20%, устойчивые задержки и пропуски обновления. Свежая посылка сама по себе не отменяет явный сбой. |',
           '| Устойчивость | `memory.py`: опасность принимается сразу; восстановление дороги требует трёх новых сигнатур. Дубли не продлевают число подтверждений. Переходная оценка UNKNOWN ограничена 60 с. Это не cooldown маршрутизации. |',
           '| Стоимость маршрута | `limits.py`, `routing.py`: общие caps WET=60, WATER_FILM/PARTIAL=40, очередь/затор/пограничная видимость=30 и надёжная рекомендация RSU. FLOODED исключён; положительное время и штраф неопределённости сохранены. |',
           '| Поддержка | `resources.py`: риск → груз → время до опасного продолжения → прежнее назначение → ID. ETA не перебивает более высокий риск или приоритет груза. Ограничение шести операторов сохранено. |',
           '| Подъезд к площадке | `limits.py`, `resources.py`, `guard.py`: проверяются текущий фрагмент, весь направленный путь и цель, масса/av_allowed/сооружения, COMPLIANT и не FLOODED. UNKNOWN недостаточно. Нет доказанного подъезда — диагностический HOLD и доступная поддержка. |','',
           'Дополнительно возраст карты увеличивается на возраст телеметрии. Последовательность цифрового двойника и задержанные/замороженные последовательности не используются как доказательство PACKET_LOSS; явные сообщения о потерях всё ещё дают этот код, поэтому совпадение с единственным эталонным типом сбоя не гарантируется. Восстановление доставки/отключённого показателя после трёх новых своевременных обновлений отделено от положительных Beta-голосов. Справочник площадок задаёт только сегмент, а не точный offset точки остановки: время подъезда остаётся приближением.','',
           '## TRAIN: измерения качества','',
           'Все четыре TRAIN уже участвовали в анализе ошибок. Это регрессия на известных сценариях, не независимый hold-out и не прогноз приватного балла. Метрика источников — proxy статуса по интервалам отказов, а не полный эталон.','',
           '| Сценарий | Дороги F1: старый → новый | ODD F1: старый → новый | Источники proxy F1: старый → новый | Движение вне истинного ODD: старый → новый |','|---|---:|---:|---:|---:|']
    for (a,b),sa,sb in zip(train,safety_old,safety_new):
        lines.append('| '+a['scenario']+' | '+' | '.join(f"{a['metrics'][m]['macro']:.4f} → {b['metrics'][m]['macro']:.4f}" for m in ['road_final','odd_active','source_status_proxy'])+f" | {sa['moving_outside_odd_steps']} → {sb['moving_outside_odd_steps']} |")
    lines+=['','Строгие трёхшаговые ODD-эпизоды по опубликованному gate: '+str(sum(s['gate_odd_episodes'] for s in safety_old))+' → '+str(sum(s['gate_odd_episodes'] for s in safety_new))+'. Это не проверка всех физических аварий.','',
            '| Сценарий | Road MSE: старый → новый | ODD MSE: старый → новый | Переключения motion | Проверки guard: старый → новый |','|---|---:|---:|---:|---:|']
    for a,b in train:
        lines.append(f"| {a['scenario']} | {a['confidence_mse']['road']:.5f} → {b['confidence_mse']['road']:.5f} | {a['confidence_mse']['odd_active']:.5f} → {b['confidence_mse']['odd_active']:.5f} | {a['switches']['motion']} → {b['switches']['motion']} | {a['guard_failures']} → {b['guard_failures']} |")
    lines+=['','### Отдельные коды и задержки обнаружения','',
            '| Сценарий | Группа / код | TP: старый → новый | FP | FN |','|---|---|---:|---:|---:|']
    for a,b in train:
        for group in ('source_fault_codes','odd_codes'):
            left=a['codes'].get(group,{});right=b['codes'].get(group,{})
            for code in sorted(set(left)|set(right)):
                x=left.get(code,dict(tp=0,fp=0,fn=0));y=right.get(code,dict(tp=0,fp=0,fn=0))
                lines.append(f"| {a['scenario']} | {group} / {code} | {x['tp']} → {y['tp']} | {x['fp']} → {y['fp']} | {x['fn']} → {y['fn']} |")
    lines+=['','Задержки обнаружения и TP/FP/FN каждого кода сохранены в JSON TRAIN. Детальные ошибки ODD/дорог/источников, временные окна и эпизоды: `analysis/train-new/` в архиве аналитики.','',
            '## PUBLIC: различия фактических ответов','',
            'Labels отсутствуют: различие команд не доказывает улучшение. Старый образ повторно обработал оба сценария; все 1260 JSON-ответов побайтово совпали с файлами ранее сданного архива. Новые ответы получены финальным образом `corridor-solution:final` для архива Кариков Team2.zip; прежний образ сохранён как `corridor-solution:previous`.','',
            '| Сценарий | Пакеты | Другая motion_action | Другое назначение поддержки | Другой путь | Другой speed limit | Другая площадка |','|---|---:|---:|---:|---:|---:|---:|']
    for r in public:
        c=r['counts'];lines.append(f"| {r['scenario']} | {c['packets']} | {c['motion_action_changed']} | {c['remote_support_required_changed']} | {c['route_segment_ids_changed']} | {c['speed_limit_kmh_changed']} | {c['safe_stop_id_changed']} |")
    lines+=['','| Сценарий | Команда | Старая | Новая | Разница |','|---|---|---:|---:|---:|']
    for r in public:
        for a in actions:
            x=r['old_actions'].get(a,0);y=r['new_actions'].get(a,0);lines.append(f"| {r['scenario']} | {a} | {x} | {y} | {y-x:+} |")
    lines+=['','Все смены команд с временем, автомобилем, путём, скоростью, площадкой и поддержкой — `PUBLIC-*-action-changes.csv`; все отличия оценок и confidence — `PUBLIC-*-changes.ndjson.gz`.','',
            '## Синтетические критические случаи','',
            f"Проверены {len(synthetic)} сценария, по 48 пакетов, обеими версиями: {sum(x['old']['packets'] for x in synthetic)} + {sum(x['new']['packets'] for x in synthetic)} ответов. Это производные первых 48 пакетов TRAIN-001, а не независимые новые дорожные ситуации. Использованы отдельные справочники случаев массы, вместимости и тоннелей. SHA входов сверены с manifest. Каждый ответ проверен схемой и отдельными проверками топологии/ресурсов/времени.",'',
            'Проверяются частичные свойства известных мутаций; полный F1/официальный балл вычислить нельзя. Отсутствие ошибки у случая означает только прохождение перечисленных машинных проверок. Пустые/поздние данные, повторения, границы ODD и явные закрытия не приравниваются к физической модели аварий.','',
            '| Версия | Случаев с нарушенным проверенным свойством | Случаев с guard-ошибкой | Ответов >2 с в локальном прогоне |','|---|---:|---:|---:|']
    for v,title in [('old','Старая'),('new','Новая')]:
        lines.append(f"| {title} | {sum(bool(x[v]['failures']) for x in synthetic)} | {sum(x[v]['replay']['guard_failure_packets']>0 for x in synthetic)} | {sum(x[v]['replay']['timing_over_2000_ms'] for x in synthetic)} |")
    lines+=['','| Случай | Свойство/ограничение | Старая: нарушения | Новая: нарушения |','|---|---|---|---|']
    for x in synthetic:
        if x['old']['failures'] or x['new']['failures']:
            lines.append(f"| {x['scenario']} | {x['name']} | {json.dumps(x['old']['failures'],ensure_ascii=False)} | {json.dumps(x['new']['failures'],ensure_ascii=False)} |")
    lines+=['','### Риски до появления подтверждающих событий','',
            'Отдельно считаются ситуации, когда генератор уже изменил физическое состояние, но соответствующее измерение ещё не доставлено. Они не объявляются нарушением известного алгоритму факта.','',
            '| Случай | Риск | Старая | Новая |','|---|---|---:|---:|']
    for x in synthetic:
        a=x['old'].get('risk_indicators',{});b=x['new'].get('risk_indicators',{})
        for name in sorted(set(a)|set(b)):
            lines.append(f"| {x['scenario']} | {name} | {a.get(name,0)} | {b.get(name,0)} |")
    lines+=['','Генератор проверяет равенство raw map_age_min порогу, но измерение доставлено на секунду позже. Значение на момент решения уже больше на 1/60 минуты; это касается и исходного значения всего на 0.01 минуты ниже порога. Новая трактовка явно отражена в проверке. Общий wind_mps выше бокового предела означает UNKNOWN без направления, а не выдуманный код WIND. Окно восстановления после шага 36 равно лишь 60 с: последняя HOLD не доказывает бесконечное удержание.','',
            '## Скорость и воспроизводимость','',
            'TRAIN и синтетика измеряют controller.process локально; параллельные сценарии создают конкуренцию за CPU. Эти времена не являются изолированным тестом Docker. Первый финальный PUBLIC начат одновременно с двумя завершающимися TRAIN: по одному roundtrip >2 с в каждом сценарии. Сохранён в public-final-first-run. Затем PUBLIC полностью повторён без параллельных сценариев; ниже приведены контрольные измерения. Docker ограничен 8 CPU и 16 ГБ, сеть отключена. Промежуточные версии сохранены отдельно в pilot-public.','',
            '| PUBLIC | Образ | p50 ответа, мс | p95 | p99 | максимум | >2 с | Первый ответ, с |','|---|---|---:|---:|---:|---:|---:|---:|']
    for r in public:
        for v in ['old','new']:
            c=r[v+'_container'];q=c['roundtrip_ms'];lines.append(f"| {r['scenario']} | {v} | {q['p50']:.1f} | {q['p95']:.1f} | {q['p99']:.1f} | {q['max']:.1f} | {c['roundtrip_over_2000_ms']} | {c['first_response_sec']:.3f} |")
    lines+=['','## Корректность двух Dijkstra','',
            'Каждый Dijkstra корректно находит кратчайший направленный путь при положительных стоимостях. Пересчёт выполняется от конца текущего сегмента до хаба; путь полный, а не короткий локальный отрезок. Остаток сегмента определяет момент планового выбора, а не длину поиска. Статический граф моделирует свободное движение; динамический учитывает известные ограничения, скорость и штраф неопределённости.','',
            'Зафиксированное правило оставлено неизменным: при допустимой текущей безопасности и remaining <= min(500,length) совпадение полных списков даёт CONTINUE, различие — REROUTE. Нет порога выгоды/cooldown. Даже отличие только дальнейшей части полного пути может дать REROUTE; при равных стоимостях выбор по ID.','',
            'Модель имеет ограничения: исходный фактический маршрут неизвестен, поэтому статический путь — допущение. Сравнение не доказывает, что водитель уже выбрал статический путь или исполнил прошлую команду. Два кратчайших пути не обеспечивают оптимальное управление всем парком и не доказывают физическую безопасность при ошибочных наблюдениях. На обычном маршруте по прежней политике будущий ODD UNKNOWN сам по себе не исключает ребро; исключается подтверждённое VIOLATED. Для подъезда к площадке теперь требуется COMPLIANT. Средняя погода в середине сегмента не является точным прогнозом всего пути.','',
            'Протокол допускает одну motion_action: REROUTE не может одновременно быть LIMIT_SPEED. Учёт скорости в стоимости пути не равен выдаче числовой команды снижения скорости текущей машине. При таком конфликте сохранён приоритет согласованного маршрутного правила.','',
            '## Что остаётся неизвестным','',
            '- Причины четырёх приватных эпизодов без трасс/разметки не установлены. Проверки TRAIN/синтетики не гарантируют их исчезновение.','- Полная полезность действий и определение необоснованных переключений не опубликованы.','- Измерения метеостанции могут расходиться с истинной погодой сегмента; память лишь сохраняет уже наблюдавшуюся опасность.','- Репутация не определяет виновника при двух противоречащих источниках без независимого подтверждения; небольшой сдвиг ниже порога не объявляется отказом.','- SAFE_STOP не содержит полный путь в выходном контракте; достижимость и допустимость проверяются внутри. HOLD вне подтверждённой зоны — протокольный последний вариант.','- Старый архив сохранён. Тег final обновлён по запросу пользователя для повторной сдачи; новая версия ещё требует внешнего приватного прогона.','',
            '## Воспроизведение','',
            '```powershell','python tools/evaluate.py --data <данные> --out results/train-new --details','python tools/train_analysis.py --data <данные> --results results/train-new --out <comparison>/analysis/train-new','python tools/replay_suite.py --repo . --synthetic <распакованная-синтетика> --out <comparison>/synthetic-new --workers 3 --resume','docker build --platform linux/amd64 -t corridor-solution:comparison-new .','python tools/container_check.py --data <данные> --scenario PUBLIC-101 --out <comparison>/public-new --image corridor-solution:comparison-new','python tools/container_check.py --data <данные> --scenario PUBLIC-102 --out <comparison>/public-new --image corridor-solution:comparison-new','python tools/compare_versions.py --root <comparison> --synthetic <распакованная-синтетика>','python tools/comparison_report.py --repo . --root <comparison> --document docs/ALGORITHM_COMPARISON.md','```','']
    for name,title in [('train_f1.png','Сравнение F1'),('train_odd_risk.png','Движение вне ODD'),('public_actions.png','Команды PUBLIC'),('public_transitions.png','Переходы команд')]:
        lines+=['## '+title,'',f'![{title}](comparison_plots/{name})','']
    document.parent.mkdir(exist_ok=True);document.write_text('\n'.join(lines),encoding='utf-8')
    shutil.copytree(plots,document.parent/'comparison_plots',dirs_exist_ok=True)
    shutil.copytree(plots,analysis/'comparison_plots',dirs_exist_ok=True)
    (analysis/'report.md').write_text('\n'.join(lines),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--repo',type=Path,required=True);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--document',type=Path,required=True)
    args=parser.parse_args();build(args.repo,args.root,args.document)
