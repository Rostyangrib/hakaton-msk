"""Turn aggregate offline checks into a reviewable analytical report."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

parser=argparse.ArgumentParser(); parser.add_argument('--results',type=Path,required=True)
parser.add_argument('--document',type=Path,required=True); args=parser.parse_args()
reports=[json.loads(p.read_text(encoding='utf-8')) for p in sorted(args.results.glob('TRAIN-*.json'))]
assert len(reports)==4
lines=['# Анализ качества на TRAIN','',
       'Таблицы относятся к снимку до последних исправлений FREEZE, PACKET_LOSS и изоляции списков причин. Последние исправления прошли 40 регрессионных тестов, но полный TRAIN для окончательного образа ещё не повторён. TRAIN-001/002/003 используются для ручной проверки правил, TRAIN-004 — отдельная проверка переноса. Обучения нет. По результатам первых трёх сценариев уточнены FREEZE (несколько полей, повтор счётчика либо переход от вариативных показаний к постоянным в течение ≥30 с с сохранением диагноза до изменения содержимого), PACKET_LOSS только по последовательностям за watermark и допуск одиночного свежего свидетельства с накопленным trust ≥0.7 для OPEN/PARTIAL_BLOCK. CLOSED по-прежнему требует двух независимых свидетельств. Строгое маршрутное правило сохранено. Это локальные метрики, не официальный балл.', '',
       '| Сценарий | Пакеты | F1 дорог | F1 ODD активных | F1 статуса источника (proxy) | MSE дорог | MSE ODD | Нарушения финального контроля |',
       '|---|---:|---:|---:|---:|---:|---:|---:|']
for r in reports:
    lines.append(f"| {r['scenario']} | {r['packets']} | {r['metrics']['road_final']['macro']:.4f} | {r['metrics']['odd_active']['macro']:.4f} | {r['metrics']['source_status_proxy']['macro']:.4f} | {r['confidence_mse']['road']:.4f} | {r['confidence_mse']['odd_active']:.4f} | {r['guard_failures']} |")
lines+=['','Проверка против истинного ODD отдельно от внутреннего финального контроля:']
for r in reports: lines.append(f"- {r['scenario']}: небезопасных по labels шагов CONTINUE/NO_ACTION без поддержки — {r.get('labelled_odd_unsafe_steps', 'не измерено')}; эпизодов ≥3 последовательных шагов — {r.get('labelled_odd_critical_episodes', 'не измерено')}.")
lines+=['','## Сравнение этапов','','| Сценарий | Этап 3 F1 дорог | Этап 4 F1 дорог | Финальный F1 дорог |', '|---|---:|---:|---:|']
for r in reports: lines.append(f"| {r['scenario']} | {r['metrics']['road_stage3']['macro']:.4f} | {r['metrics']['road_stage4']['macro']:.4f} | {r['metrics']['road_final']['macro']:.4f} |")
lines+=['','## Время локального прогона','','Четыре сценария запускались параллельно на Windows; эти значения отражают конкуренцию за CPU. Лимит контрольной Linux-среды проверяется отдельно контейнерным прогоном. Первичный прогон обнаружил превышения и вызвал оптимизацию повторных сортировок/разбора времени.', '',
        '| Сценарий | p50 мс | p95 мс | p99 мс | max мс | Ответов >2 с |','|---|---:|---:|---:|---:|---:|']
for r in reports:
    t=r['latency_ms']; lines.append(f"| {r['scenario']} | {t['p50']:.1f} | {t['p95']:.1f} | {t['p99']:.1f} | {t['max']:.1f} | {r['timing_over_2000_ms']} |")
lines+=['','## Действия и строгое маршрутное правило','','| Сценарий | Частоты действий | Изменения motion_action | Изменения полного назначения | Допустимых шагов выбора | Различий путей | REROUTE |','|---|---|---:|---:|---:|---:|---:|']
for r in reports:
    q=r['route_selection']; lines.append(f"| {r['scenario']} | {r['action_counts']} | {r['switches'].get('motion',0)} | {r['switches'].get('full_recommendation',0)} | {q.get('eligible_steps',0)} | {q.get('different_paths',0)} | {q.get('emitted_reroute',0)} |")
lines+=['','Различия маршрутов сопоставляются с фактическими перенаправлениями. Частота переключений не называется числом необоснованных переключений. При текущем ODD/опасности безопасная реакция имеет приоритет; маршрутное сравнение не получает скрытого порога.', '',
        '## Диагностика и задержка','','В labels есть интервалы типов отказа, но нет полной временной таблицы source.status. Для локального сравнения OUTAGE трактуется как FAILED, остальные известные отказы как DEGRADED; вне интервала — OK. Это явно обозначенный proxy. Интервалы полуоткрытые. null означает отсутствие правильного диагноза в интервале.']
for r in reports:
    lines+=['',f"### {r['scenario']}",'',f"Задержка, секунды: `{r['detection_delay_sec']}`",'',f"F1 кодов: `{r['codes']}`",'',f"Диагностика: `{r['diagnostics']}`"]
lines+=['','## Уверенность','','Диаграмма reliability сохранена в results/verified/calibration.png вне Git. Здесь показаны агрегированные наблюдения:','','| Объект | Интервал | Число | Средняя уверенность | Фактическая правильность |','|---|---|---:|---:|---:|']
fig,axes=plt.subplots(1,3,figsize=(12,4))
for axis,metric in zip(axes,['road','source_status_proxy','odd_active']):
    bins={}
    for r in reports:
        for b in r['calibration_bins'][metric]:
            accum=bins.setdefault(b['bucket'],[0,0,0]); n=b['n']; accum[0]+=n; accum[1]+=n*b['mean_confidence']; accum[2]+=n*b['accuracy']
    points=[]
    for bucket,(n,c,a) in sorted(bins.items()):
        lines.append(f'| {metric} | {bucket/10:.1f}–{(bucket+1)/10:.1f} | {n} | {c/n:.4f} | {a/n:.4f} |'); points.append((c/n,a/n))
    axis.plot([0,1],[0,1],'--',color='gray'); axis.plot([p[0] for p in points],[p[1] for p in points],'o-')
    axis.set(title=metric,xlim=(0,1),ylim=(0,1),xlabel='Declared confidence',ylabel='Observed accuracy'); axis.grid(alpha=.3)
fig.tight_layout(); fig.savefig(args.results/'calibration.png',dpi=160); plt.close(fig)
lines+=['','## Ограничения','','Исходные эталоны CLOSED/REROUTE/HOLD/LIMIT_SPEED отсутствуют. Они покрыты синтетическими тестами логики, не заменяющими TRAIN. Некоторые отказы не имеют независимых сопоставимых показаний; отсутствие точного диагноза раскрыто в таблице задержек. Погода станции пространственно применима по радиусу, но не доказывает точную погоду на каждом сегменте. Последний HOLD — протокольный ответ невозможной ситуации, не физическая гарантия. Алгоритмический confidence остаётся эвристикой; результаты MSE не превращают его в откалиброванную вероятность.']
args.document.write_text('\n'.join(lines)+'\n',encoding='utf-8')
