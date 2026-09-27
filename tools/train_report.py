"""Generate current TRAIN report and figures from completed offline runs."""
import argparse
import csv
import json
import os
from collections import Counter
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as stream: return list(csv.DictReader(stream))


def metrics(counts):
    tp,fp,fn=counts['tp'],counts['fp'],counts['fn']
    return tp/(tp+fp) if tp+fp else None,tp/(tp+fn) if tp+fn else None


def build(results, analysis, document, previous=None):
    reports=[json.loads((results/(sid+'.json')).read_text(encoding='utf-8')) for sid in ['TRAIN-001','TRAIN-002','TRAIN-003','TRAIN-004']]
    safety=json.loads((analysis/'safety_analysis.json').read_text(encoding='utf-8'))
    vehicles=read_csv(analysis/'vehicle_steps.csv')
    episodes=read_csv(analysis/'critical_odd_episodes.csv')
    segments=read_csv(analysis/'road_errors_by_segment.csv')
    windows=read_csv(analysis/'evaluation_windows.csv')
    images=analysis/'plots'; images.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10})
    ids=[r['scenario'] for r in reports]
    x=np.arange(4); fig,axis=plt.subplots(figsize=(10,4.5))
    for i,(metric,title) in enumerate([('road_final','Дороги'),('odd_active','ODD активных'),('source_status_proxy','Источники (proxy)')]):
        axis.bar(x+(i-1)*.24,[r['metrics'][metric]['macro'] for r in reports],.24,label=title)
    axis.set(xticks=x,xticklabels=ids,ylim=(0,1.08),ylabel='Macro F1',title='Текущая версия: качество по сценариям')
    axis.legend(loc='lower left'); axis.grid(axis='y',alpha=.2); fig.tight_layout(); fig.savefig(images/'01_f1.png',dpi=160); plt.close(fig)
    for metric,name,labels in [('road_final','02_road_confusion',['OPEN','CONGESTED','PARTIAL_BLOCK','CLOSED','UNKNOWN']),('odd_active','03_odd_confusion',['COMPLIANT','VIOLATED','UNKNOWN'])]:
        fig,axes=plt.subplots(1,4,figsize=(17,4))
        for axis,r in zip(axes,reports):
            pairs={tuple(k.split(' → ')):v for k,v in r['metrics'][metric]['confusion'].items()}
            truth=[label for label in labels if any(t==label for t,p in pairs)]
            pred=[label for label in labels if any(p==label for t,p in pairs)]
            matrix=np.array([[pairs.get((t,p),0) for p in pred] for t in truth])
            norm=matrix/np.maximum(matrix.sum(axis=1,keepdims=True),1)
            axis.imshow(norm,vmin=0,vmax=1,cmap='Blues',aspect='auto')
            for iy in range(len(truth)):
                for ix in range(len(pred)):
                    axis.text(ix,iy,str(matrix[iy,ix]),ha='center',va='center',color='white' if norm[iy,ix]>.6 else 'black',fontsize=9)
            axis.set(xticks=range(len(pred)),xticklabels=pred,yticks=range(len(truth)),yticklabels=truth,title=r['scenario'],xlabel='Прогноз')
            axis.tick_params(axis='x',rotation=35)
        axes[0].set_ylabel('Истина'); fig.suptitle('Матрицы ошибок: '+('дороги' if metric=='road_final' else 'ODD активных'))
        fig.tight_layout(); fig.savefig(images/(name+'.png'),dpi=160); plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(13,4))
    for axis,metric in zip(axes,['road','source_status_proxy','odd_active']):
        axis.plot([0,1],[0,1],'--',color='gray',label='Идеальное соответствие')
        for r in reports:
            bins=r['calibration_bins'][metric]
            axis.plot([b['mean_confidence'] for b in bins],[b['accuracy'] for b in bins],'o-',label=r['scenario'])
        axis.set(title=metric,xlim=(0,1),ylim=(0,1.03),xlabel='Заявленная уверенность',ylabel='Фактическая правильность'); axis.grid(alpha=.2)
    axes[-1].legend(fontsize=8); fig.tight_layout(); fig.savefig(images/'04_confidence.png',dpi=160); plt.close(fig)
    actions=['CONTINUE','LIMIT_SPEED','REROUTE','HOLD','SAFE_STOP','NO_ACTION']
    fig,axis=plt.subplots(figsize=(10,4)); bottom=np.zeros(4)
    for action in actions:
        values=np.array([100*r['action_counts'].get(action,0)/(72*r['packets']) for r in reports])
        axis.bar(ids,values,bottom=bottom,label=action); bottom+=values
    axis.set(ylabel='Рекомендации, %',title='Распределение команд (не исполненные действия)',ylim=(0,100)); axis.legend(bbox_to_anchor=(1.02,1),loc='upper left')
    fig.tight_layout(); fig.savefig(images/'05_actions.png',dpi=160); plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(13,7))
    for axis,sid in zip(axes.flat,ids):
        selected=[r for r in vehicles if r['scenario']==sid]
        by_step={}
        for row in selected:
            c=by_step.setdefault(int(row['step']),Counter())
            if row['active']=='True' and row['true_odd']=='VIOLATED': c['Истинное нарушение ODD']+=1
            if row['gate_odd']=='True': c['CONTINUE/NO_ACTION без поддержки']+=1
            if row['moving_outside_odd']=='True': c['Любая движущая команда вне ODD']+=1
        for name in ['Истинное нарушение ODD','CONTINUE/NO_ACTION без поддержки','Любая движущая команда вне ODD']:
            axis.plot([k*5/60 for k in sorted(by_step)],[by_step[k][name] for k in sorted(by_step)],label=name)
        axis.set_ylim(0,max(1,max((c['Истинное нарушение ODD'] for c in by_step.values()),default=0)*1.08))
        axis.set(title=sid,xlabel='Минуты от первого пакета',ylabel='Автомобилей на шаге'); axis.grid(alpha=.2)
    axes[0,0].legend(fontsize=8); fig.tight_layout(); fig.savefig(images/'06_odd_risk_timeline.png',dpi=160); plt.close(fig)
    selected=[r for r in vehicles if r['scenario']=='TRAIN-003' and r['vehicle_id']=='AV-061' and 265<=int(r['step'])<=285]
    fig,axis=plt.subplots(figsize=(10,4))
    axis.plot([int(r['step'])*5/60 for r in selected],[float(r['true_visibility']) for r in selected],'o-',label='Истинная видимость сегмента')
    axis.plot([int(r['step'])*5/60 for r in selected],[float(r['fused_visibility']) if r['fused_visibility'] else np.nan for r in selected],'o-',label='Объединённые показания станций')
    axis.axhline(70,color='red',linestyle='--',label='Предел ODD-A: 70 м')
    axis.axvspan(22.75,23+5/60,color='red',alpha=.12,label='5 шагов LIMIT_SPEED вне истинного ODD')
    axis.set(title='TRAIN-003, AV-061: пространственное расхождение погоды',xlabel='Минуты от первого пакета',ylabel='Видимость, м')
    axis.legend(fontsize=9); axis.grid(alpha=.2); fig.tight_layout(); fig.savefig(images/'07_weather_case.png',dpi=160); plt.close(fig)
    def image(name):
        return os.path.relpath(images/name,document.parent).replace('\\','/')
    lines=['# Подробный анализ текущей версии на всех TRAIN','',
        'Дата проверки: 28.09.2026. Прогон выполнен последовательно локальным Python на Windows. Алгоритм не изменялся; добавлены только офлайн-экспорт и анализ. Labels доступны исключительно инструментам оценки. Это не официальный балл и не результат скрытых тестов.', '',
        '## Что было проверено','',
        f"Обработаны все {sum(r['packets'] for r in reports)} пакетов, {sum(r['packets']*72 for r in reports)} рекомендаций автомобилям. Все ответы прошли runtime-контракт; количество строк и повторный независимый подсчёт эпизодов сверены с отчётами. SHA256 runtime-файлов и входных пакетов сохранены в JSON каждого сценария. Последний технический Docker-пакет этим анализом не пересобирался.",'',
        'Предварительные прогоны ранее выполнялись на всех четырёх TRAIN, но относились к версии до последних исправлений. Этот отчёт закрывает повтор текущей версии, а не первое знакомство с TRAIN-002/003/004.', '',
        '### Главные результаты','',
        '- Трёхшаговых критических ODD-эпизодов по опубликованному условию не найдено. Независимые проверки маршрутов, площадок и ресурсов также не нашли нарушений в этих TRAIN. Это не гарантия безопасности скрытых сценариев.',
        '- Найдены 98 движущих рекомендаций при истинном нарушении ODD: 80 в TRAIN-003 и 18 в TRAIN-004. Среди них 19 CONTINUE без поддержки; остальные случаи анализируются отдельно от строгого safety gate.',
        '- TRAIN-003: F1 класса VIOLATED равен нулю. Все 2430 истинных нарушений оценены как UNKNOWN либо COMPLIANT.',
        '- TRAIN-004: все 1259 истинных CONGESTED пропущены; macro F1 дорог около 0,6595.',
        '- Не распознаны DRIFT скорости, BIAS погоды и BYZANTINE ложной скорости. В TRAIN-002/003/004 сохраняются ложные PACKET_LOSS.', '',
        '## Сводные метрики','',
        '| Сценарий | Пакеты | F1 дорог | F1 ODD активных | F1 статуса источника (proxy) | MSE дорог | MSE ODD | MSE источников |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in reports:
        m,c=r['metrics'],r['confidence_mse']
        lines.append(f"| {r['scenario']} | {r['packets']} | {m['road_final']['macro']:.4f} | {m['odd_active']['macro']:.4f} | {m['source_status_proxy']['macro']:.4f} | {c['road']:.4f} | {c['odd_active']:.4f} | {c['source_status_proxy']:.4f} |")
    lines+=['',f"![F1 по сценариям]({image('01_f1.png')})",'',
        'Macro F1 усредняется по классам, присутствующим в истине конкретного сценария. Сценарий без нарушений не проверяет полноту VIOLATED. Статус источника — proxy: OUTAGE → FAILED, другие размеченные отказы → DEGRADED, отсутствие отказа → OK. Интервалы отказов полуоткрытые. Эта метрика не заменяет качество конкретных типов отказа.', '',
        '## Проверка критических эпизодов','',
        'В 06_evaluation_rules.json указано: CONTINUE или NO_ACTION при истинном нарушении ODD без поддержки минимум три последовательных шага образуют критический эпизод. При 1–2 эпизодах потолок 45, при ≥3 — 25. Это объясняет потолок приватного результата, но найденные ниже TRAIN-эпизоды не отождествляются с четырьмя приватными эпизодами.', '',
        '| Сценарий | Истинных нарушений, автошагов | CONTINUE/NO_ACTION вне ODD без поддержки | Эпизодов ≥3 шагов | Движущих команд вне ODD, включая LIMIT_SPEED/REROUTE | Эпизодов таких команд ≥3 |',
        '|---|---:|---:|---:|---:|---:|']
    for s in safety:
        lines.append(f"| {s['scenario']} | {s['violated_steps']} | {s['gate_odd_steps']} | {s['gate_odd_episodes']} | {s['moving_outside_odd_steps']} | {s['moving_outside_odd_episodes']} |")
    lines+=['',
        'Последние два столбца — дополнительный консервативный индикатор риска, не определение аварии оценщика. LIMIT_SPEED не устраняет ODD. SAFE_STOP считается реакцией, а не обычным продолжением; фактическая безопасность подъезда здесь не моделируется. Длина эпизода — число пакетов, span_sec — расстояние между первым и последним временем, поэтому три шага имеют span_sec=10 при интервале 5 секунд.', '',
        f"![Риск во времени]({image('06_odd_risk_timeline.png')})",'',
        '| Сценарий | Внутренних ошибок guard | Ошибок маршрутов/площадок против labels и справочников | Нарушений вместимости/поддержки |',
        '|---|---:|---:|---:|']
    for r,s in zip(reports,safety): lines.append(f"| {r['scenario']} | {r['guard_failures']} | {len(s['route_stop_truth_issues'])} | {len(s['snapshot_issues'])} |")
    lines+=['','Нулевой guard проверяет только собственные оценки. Для маршрутов отдельно проверены связность, известность, оценочный UNKNOWN, масса, разрешение ВАТС, назначенный хаб и истинный CLOSED. Для площадок — известность, масса, совместимость сегмента, истинный CLOSED и вместимость. Истинный будущий ODD всего маршрута и физические манёвры не включены в эту проверку.', '',
        '### Все обнаруженные критические ODD-эпизоды','',
        '| Сценарий | Автомобиль | Начало UTC | Конец UTC | Шаги | Истинные коды | Оценки ODD |',
        '|---|---|---|---|---:|---|---|']
    for e in episodes:
        lines.append(f"| {e['scenario']} | {e['vehicle_id']} | {e['start']} | {e['end']} | {e['steps']} | {e['codes']} | {e['predicted_statuses']} |")
    if not episodes: lines.append('| — | — | — | — | 0 | Эпизодов не найдено | — |')
    lines+=['','### Примеры отдельных опасных шагов','',
        'Ниже показаны первые CONTINUE/NO_ACTION вне истинного ODD без поддержки для каждого сценария и профиля. Это наблюдаемые факты для разбора, не автоматически установленная причина приватной аварии.', '',
        '| Сценарий | Пакет / автомобиль | Профиль | Истинные коды | Видимость: истина / объединение, м | Потери бортовой связи, % | Оценка ODD | Поддержек всего |',
        '|---|---|---|---|---|---:|---|---:|']
    sampled=set()
    for row in vehicles:
        key=row['scenario'],row['profile']
        if row['gate_odd']=='True' and key not in sampled:
            sampled.add(key)
            lines.append(f"| {row['scenario']} | {row['packet_id']} / {row['vehicle_id']} | {row['profile']} | {row['true_codes']} | {row['true_visibility']} / {row['fused_visibility']} | {row['packet_loss_pct_10s']} | {row['pred_odd']} | {row['support_count']} |")
    lines+=['','### Подтверждённые случаи для исправлений','',
        '1. **Погода, AV-061, TRAIN-003:** 11:22:45–11:23:05 UTC, пять последовательных LIMIT_SPEED при истинном VISIBILITY и предсказанном COMPLIANT. Это 20 секунд между крайними пакетами. Эпизод не попадает в буквальное условие CONTINUE/NO_ACTION safety gate, но снижение скорости не снимает нарушение ODD.', '',
        f"![Разбор погодного случая]({image('07_weather_case.png')})",'',
        '2. **Погода, AV-002, TRAIN-003-P0266:** S010, профиль ODD-B, истинная видимость 96,2 м при минимуме 100 м; объединённая видимость 943,4 м, диапазон [943,4;943,4], LIMIT_SPEED без поддержки. Это пространственная ошибка применимости измерений, а не конфликт по разные стороны порога внутри fusion.', '',
        '3. **Возраст карты, TRAIN-004, 14:45:00 UTC:** 18 автомобилей ODD-D получили LIMIT_SPEED при истинном MAP_AGE. Во всех случаях последняя телеметрия имеет map_age_min=60,0 и возраст 1 секунду. Проверка сравнивает значение в момент измерения с пределом 60 и не увеличивает возраст к моменту решения. Кандидат исправления: возраст карты на момент решения = переданный возраст + возраст телеметрии / 60; отдельно учесть округление и обновление карты. Приёмка требует отсутствия новых ложных MAP_AGE на всех сценариях и сохранения корректной семантики равенства порогу.', '',
        '4. **V2X, начало отказа TRAIN-003:** первые опасные CONTINUE появляются в 11:13:40 UTC — на границе начала отказа RSU-04. Например, AV-004 на S028: истинный V2X нарушен, телеметрия ещё показывает 0% потерь и нормальную задержку. Нельзя обещать обнаружение отказа до первого наблюдаемого признака. Нужно проверять время реакции и отсутствие перехода в три последовательных критических шага, а не подставлять известный интервал labels в runtime.', '']
    lines+=['','## Дороги: ошибки по классам','',f"![Матрицы дорог]({image('02_road_confusion.png')})",'',
        '| Сценарий | Истинный класс | Прогноз | Число |','|---|---|---|---:|']
    for r in reports:
        for pair,n in r['metrics']['road_final']['confusion'].items():
            t,p=pair.split(' → ')
            if t!=p: lines.append(f"| {r['scenario']} | {t} | {p} | {n} |")
    lines+=['','### Сегменты с наибольшим числом ошибок','','| Сценарий | Сегмент | Ошибок | Доля |','|---|---|---:|---:|']
    for row in segments[:15]: lines.append(f"| {row['scenario']} | {row['segment_id']} | {row['errors']} | {float(row['error_share']):.2%} |")
    lines+=['','## ODD: пропуски и неопределённость','',f"![Матрицы ODD]({image('03_odd_confusion.png')})",'',
        '| Сценарий | Истинный статус | Прогноз | Число |','|---|---|---|---:|']
    for r in reports:
        for pair,n in r['metrics']['odd_active']['confusion'].items():
            t,p=pair.split(' → ')
            if t!=p: lines.append(f"| {r['scenario']} | {t} | {p} | {n} |")
    lines+=['','### Коды нарушений ODD','','| Сценарий | Код | Precision | Recall | F1 | TP | FP | FN |','|---|---|---:|---:|---:|---:|---:|---:|']
    def fmt(x): return '—' if x is None else f'{x:.4f}'
    for r in reports:
        for code,value in r['codes'].get('odd_codes',{}).items():
            precision,recall=metrics(value)
            lines.append(f"| {r['scenario']} | {code} | {fmt(precision)} | {fmt(recall)} | {fmt(value['f1'])} | {value['tp']} | {value['fp']} | {value['fn']} |")
    lines+=['','### Команды при истинном нарушении ODD','','| Сценарий | Оценки ODD | Команды | Шагов без поддержки |','|---|---|---|---:|']
    for s in safety: lines.append(f"| {s['scenario']} | {s['violation_pred_status']} | {s['violation_actions']} | {s['violation_without_support']} |")
    lines+=['','## Источники: типы отказов и задержки','','| Сценарий | Тип | Precision | Recall | F1 | TP | FP | FN |','|---|---|---:|---:|---:|---:|---:|---:|']
    for r in reports:
        for code,value in r['codes'].get('source_fault_codes',{}).items():
            precision,recall=metrics(value)
            lines.append(f"| {r['scenario']} | {code} | {fmt(precision)} | {fmt(recall)} | {fmt(value['f1'])} | {value['tp']} | {value['fp']} | {value['fn']} |")
    lines+=['','| Сценарий | Отказ | Задержка правильного типа, с |','|---|---|---:|']
    for r in reports:
        for fault,delay in r['detection_delay_sec'].items(): lines.append(f"| {r['scenario']} | {fault} | {'Не обнаружен в интервале' if delay is None else delay} |")
    lines+=['','Нераспознанный тип не получает credit задержки. Даже обнаружение спустя более 120 секунд не приносит балла задержки по опубликованным правилам. Линейная интерполяция credit не предполагается: точная функция между 0 и 120 здесь не рассчитывается.', '',
        '## Уверенность','',f"![Уверенность и фактическая правильность]({image('04_confidence.png')})",'',
        'Диагональ соответствует совпадению уверенности и доли правильных ответов. Сравнение выполнено без обучаемого калибратора. Для UNKNOWN проверяется правильность выданного статуса, поэтому неизвестный ответ при истинном OPEN/COMPLIANT считается неверным. Высокий MSE может означать как неправильные выводы, так и недоуверенность правильных выводов. Proxy источников остаётся ограничением.', '',
        '## Команды, поддержка и переключения','',f"![Команды]({image('05_actions.png')})",'',
        '| Сценарий | Частоты команд | Изменений motion_action | Изменений полного назначения | Пакетов с 6 поддержками |','|---|---|---:|---:|---:|']
    for r,s in zip(reports,safety): lines.append(f"| {r['scenario']} | {r['action_counts']} | {r['switches'].get('motion',0)} | {r['switches'].get('full_recommendation',0)} | {s['support_histogram'].get('6',0)} |")
    lines+=['','| Сценарий | Шагов маршрутного выбора | Различий путей | Итоговых REROUTE в этих шагах |','|---|---:|---:|---:|']
    for r in reports:
        q=r['route_selection']; lines.append(f"| {r['scenario']} | {q.get('eligible_steps',0)} | {q.get('different_paths',0)} | {q.get('emitted_reroute',0)} |")
    lines+=['','Политика безопасности и ресурсов может заменить предварительный REROUTE. Число переключений не равно числу необоснованных переключений. REMOTE_SUPPORT из labels не сравнивается как motion_action: это отдельный признак поддержки. Точные полезности действий не вычисляются.', '',
        '| Сценарий | REMOTE_SUPPORT в эталоне, шагов | Из них с назначенной поддержкой | Неактивных шагов | Команды на неактивных шагах |','|---|---:|---:|---:|---|']
    for s in safety:
        lines.append(f"| {s['scenario']} | {s['remote_reference_steps']} | {s['remote_reference_with_support']} | {s['inactive_steps']} | {s['inactive_actions']} |")
    lines+=['','Неполное покрытие REMOTE_SUPPORT не объявляется автоматически нарушением ресурса: запросов может быть больше шести, важны приоритеты. Неактивные шаги исключены из качества ODD, но показаны отдельно, поскольку ожидаемая команда по правилам — NO_ACTION.', '',
        '## Окна событий','',
        '| Сценарий | Окно / событие | F1 дорог | F1 ODD | Команды |','|---|---|---:|---:|---|']
    for w in windows:
        lines.append(f"| {w['scenario']} | {w['window_id']} / {w['target']} | {float(w['road_macro_f1']):.4f} | {float(w['odd_macro_f1']):.4f} | {w['actions']} |")
    lines+=['','Классы в окне могут отличаться от полного сценария, поэтому macro F1 окна нельзя напрямую трактовать как изменение одной и той же доли правильных ответов.', '',
        '## Время обработки','','| Сценарий | p50, мс | p95, мс | p99, мс | Максимум, мс | >2000 мс |','|---|---:|---:|---:|---:|---:|']
    for r in reports:
        t=r['latency_ms']; lines.append(f"| {r['scenario']} | {t['p50']:.1f} | {t['p95']:.1f} | {t['p99']:.1f} | {t['max']:.1f} | {r['timing_over_2000_ms']} |")
    lines+=['','Это время controller.process в последовательном локальном прогоне. Чтение labels, экспорт подробностей, baseline, JSON-сериализация и Docker-транспорт сюда не входят. Оно не заменяет контрольный Docker-прогон с лимитами.', '',
        '## Сравнение с предварительными прогонами','','| Сценарий | Дороги: раньше → сейчас | ODD: раньше → сейчас | Источники proxy: раньше → сейчас |','|---|---|---|---|']
    if previous:
        for r in reports:
            old_path=previous/(r['scenario']+'.json')
            if old_path.exists():
                old=json.loads(old_path.read_text(encoding='utf-8'))
                values=[f"{old['metrics'][m]['macro']:.4f} → {r['metrics'][m]['macro']:.4f}" for m in ['road_final','odd_active','source_status_proxy']]
                lines.append('| '+r['scenario']+' | '+' | '.join(values)+' |')
    lines+=['','Старые отчёты не содержат новых подробных проверок безопасности; отсутствие поля не означает нулевое число эпизодов. Различия относятся к нескольким исправлениям одновременно, поэтому не являются отдельным ablation-экспериментом.', '',
        '## План улучшений по приоритету','',
        '### P0. Устранить опасные шаги и предпосылки критических эпизодов','',
        'Разобрать строки vehicle_steps.csv с gate_odd=True и moving_outside_odd=True, а при появлении эпизодов — critical_odd_episodes.csv. Сопоставить истинную и объединённую видимость, GNSS/карту, доступность V2X, оценки RSU, загрузку поддержки и итоговую команду. В первую очередь исправлять обнаружение и реакцию на текущую опасность. Требование приёмки: устранение выявленных движущих рекомендаций вне ODD и отсутствие трёхшаговых критических эпизодов на всех четырёх TRAIN плюс регрессия. Это не гарантия отсутствия эпизодов на скрытых данных.', '',
        'Не подставлять labels в runtime и не вводить исключения по scenario_id/времени/ID. Передавать кодам решений только наблюдаемые признаки. Не менять согласованное сравнение полных статического и динамического путей.', '',
        '### P1. ODD: погодная применимость и V2X','',
        'Разобрать false negative и UNKNOWN отдельно по профилю и коду. Проверить точечное применение погоды, диапазоны и доверие к метеостанциям, не смешивать данные разных пространственных условий. Проверить работоспособность V2X, не считать любую свежую публикацию достаточным доказательством исправности связи. Более строгий допуск UNKNOWN будущего ODD — возможное изменение политики, которое требует обсуждения и проверки ложных остановок.', '',
        '### P1. Заторы и дорожные свидетельства','',
        'Проверить road_errors.csv по сегментам и окнам. Отделить неверный road_vote от недостаточного доверия и конфликтов с цифровым двойником. Рассмотреть устойчивый временной признак затора и снижение зависимости от единственного порога queue/occupancy. Проверять precision и recall отдельно; не превращать любую низкую скорость в CLOSED. CLOSED требует синтетических тестов, поскольку истинных закрытий в TRAIN нет.', '',
        'Подтверждённые примеры: S047 в 14:32:05 UTC — истинный CONGESTED, но доступные сообщения DT/RSU обозначают OPEN, измеренной скорости потока нет, очередь RSU 30 м, прогноз OPEN с confidence≈0,9314. S017 в 14:41:40 UTC — истинный CONGESTED с тремя полосами, но DT и RSU сообщают PARTIAL_BLOCK, объединённая очередь 7746,2 м, измеренной скорости нет, прогноз PARTIAL_BLOCK. Нужны отдельные признаки доступности и загрузки: согласованное OPEN подтверждает проезд, но не отсутствие затора. Поддержка очереди без измеренной скорости и правила конфликтов требуют отдельного тестирования; advisory speed нельзя просто переименовать в скорость потока.', '',
        '### P2. Типы отказов и восстановление','',
        'Сверить source_errors.csv с интервалами: необнаруженные типы, задержка, ложные PACKET_LOSS/FREEZE. Анализировать предел метода при отсутствии независимых сопоставимых наблюдений. Улучшать идентификацию независимых источников и временное сопоставление, а не начислять репутацию за отсутствие ошибок. Принятие: улучшение recall и задержки без роста ложного исключения исправных источников.', '',
        'Для TRAIN-002 проверен конкретный пример: 09:25:00 UTC, S026, DET-007 сообщает скорость 136,67 км/ч и получает статус OK с trust=0,933333. Других ROAD_OBSERVATION на этом сегменте в актуальном снимке нет; RSU сообщает advisory speed 99,5, а цифровой двойник не передаёт измеренную скорость. Текущий residual-метод не может получить сопоставимый независимый остаток. Это ограничение метода, а не только неверный порог. Согласование доступности OPEN не подтверждает точность числовой скорости. Возможная доработка: разделить доверие по типам показателей и добавить проверенные признаки физической правдоподобности; не сравнивать advisory speed со скоростью потока как одну величину.', '',
        'BIAS погоды сейчас вообще не входит в residual-диагностику: она реализована для скорости ROAD_OBSERVATION. Соседние станции нельзя безусловно считать одинаковыми условиями. Для доработки нужны пространственно сопоставимые подтверждения и явная модель допустимого расхождения без ML.', '',
        '### P2. Confidence и устойчивость','',
        'Проверить чувствительность freshness, 0,4/0,7, MAD и масштаба 0,5 на нескольких сценариях с отдельной проверкой переноса. Не выбирать параметры по итоговой общей точности, игнорируя критические эпизоды. Confidence остаётся эвристикой до проверки; фиксированная уверенность статуса источника — отдельная точка улучшения. ML не использовать.', '',
        '### P3. Время и ресурсы','',
        'Профилировать по модулям; индексировать историю по сегменту и времени вместо повторного полного просмотра, группировать одинаковые ограничения автомобилей и проверять кэширование. Усилить контроль подъезда к площадке и вместимости HOLD. После исправлений повторить TRAIN, PUBLIC и Docker-проверку, сверить source.zip и образ.', '',
        '## Границы вывода','',
        'Файл правил раскрывает safety gate и веса категорий, но не полную таблицу utility, функцию control-consistency и определение необоснованных переключений. Поэтому официальный балл не реконструируется. LOCAL gate-эпизоды не равны физическим авариям и не подтверждают причину четырёх приватных эпизодов. Движение записано заранее и не меняется от наших команд. Последний HOLD — протокольная реакция, а не доказательство безопасности.', '',
        'TRAIN-004 ранее выделялся для проверки переноса, однако его ошибки уже известны команде. После дальнейшей настройки по ним он не является независимым неизвестным тестом. Скрытые данные нужны для следующей внешней проверки.', '',
        '## Воспроизведение','',
        'Из корня репозитория:', '', '```powershell',
        'python tools/evaluate.py --data <путь-к-данным> --out results/train-current --details',
        'python tools/train_analysis.py --data <путь-к-данным> --results results/train-current --out <папка-аналитики>',
        'python tools/train_report.py --results results/train-current --analysis <папка-аналитики> --document docs/TRAIN_CURRENT_ANALYSIS.md --previous results/verified',
        '```','',
        'CSV с ошибками, полные snapshots, JSON-метрики и PNG находятся вне Git. В Git сохраняются инструменты и текстовый отчёт. SHA256 исходного runtime позволяет проверить неизменность версии между сценариями.']
    document.parent.mkdir(parents=True,exist_ok=True)
    document.write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--results',type=Path,required=True)
    parser.add_argument('--analysis',type=Path,required=True)
    parser.add_argument('--document',type=Path,required=True)
    parser.add_argument('--previous',type=Path)
    args=parser.parse_args()
    build(args.results,args.analysis,args.document,args.previous)
