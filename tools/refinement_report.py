"""Compare completed refinement experiments with the submitted Team2 baseline."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

SCENARIOS=['TRAIN-001','TRAIN-002','TRAIN-003','TRAIN-004']


def build(old,new,old_safety,new_safety,out,commit,synthetic):
    pairs=[(json.loads((old/(s+'.json')).read_text(encoding='utf-8')),json.loads((new/(s+'.json')).read_text(encoding='utf-8'))) for s in SCENARIOS]
    left=json.loads(old_safety.read_text(encoding='utf-8'));right=json.loads(new_safety.read_text(encoding='utf-8'))
    lines=['# Уточнение дорожных состояний и ODD','',
           'Базовая версия Team2: 6486a44. Новый runtime: '+commit+'. Полные TRAIN являются проверкой известных разработчику сценариев, а не независимым прогнозом приватного качества. Формулы ручные, ML отсутствует. Правило CONTINUE/REROUTE не менялось.','',
           'Macro F1 усредняется по классам, присутствующим в эталоне данного сценария; ODD измерен для эталонно активных автомобилей. CLOSED в TRAIN отсутствует и проверяется отдельно на синтетике. Оценка статуса источников в JSON — proxy по опубликованным интервалам отказов, а не полный эталон фактической исправности. Сравнения ведутся по тем же входным файлам.','',
           '## Измеренные изменения','',
           '| Сценарий | Дороги macro F1: Team2 → новая | ODD macro F1: Team2 → новая | Движение вне истинного ODD: Team2 → новая |','|---|---:|---:|---:|']
    for (a,b),sa,sb in zip(pairs,left,right):
        assert a['input_sha256']==b['input_sha256']
        lines.append(f"| {a['scenario']} | {a['metrics']['road_final']['macro']:.6f} → {b['metrics']['road_final']['macro']:.6f} | {a['metrics']['odd_active']['macro']:.6f} → {b['metrics']['odd_active']['macro']:.6f} | {sa['moving_outside_odd_steps']} → {sb['moving_outside_odd_steps']} |")
    lines+=['','### Дорожные классы TRAIN-004','',
            '| Класс | F1 Team2 | F1 новая |','|---|---:|---:|']
    a,b=pairs[-1]
    for label in sorted(set(a['metrics']['road_final']['classes'])|set(b['metrics']['road_final']['classes'])):
        lines.append(f"| {label} | {a['metrics']['road_final']['classes'].get(label,0):.6f} | {b['metrics']['road_final']['classes'].get(label,0):.6f} |")
    lines+=['','### Коды ODD','',
            '| Сценарий | Код | TP: Team2 → новая | FP | FN |','|---|---|---:|---:|---:|']
    for a,b in pairs[2:]:
        x=a['codes']['odd_codes'];y=b['codes']['odd_codes']
        for code in sorted(set(x)|set(y)):
            p=x.get(code,dict(tp=0,fp=0,fn=0));q=y.get(code,dict(tp=0,fp=0,fn=0))
            lines.append(f"| {a['scenario']} | {code} | {p['tp']} → {q['tp']} | {p['fp']} → {q['fp']} | {p['fn']} → {q['fn']} |")
    lines+=['','## Причины и реализованные исправления','',
            '1. **PARTIAL_BLOCK подменялся CONGESTED.** 332 шага истинной частичной блокировки на TRAIN-004 были отнесены к затору. Фильтр сохранял приоритет только для ROAD_OBSERVATION, хотя подтверждение полос приходит также от RSU/цифрового двойника. `corridor/fusion.py` сохраняет приоритет подтверждённого ограничения любого источника. Загрузка остаётся отдельным полем `load`; `corridor/limits.py` использует его для предела 30 км/ч даже при основном PARTIAL_BLOCK и короткой очереди.','',
            '2. **Пересекающееся покрытие смешивало разные осадки.** На TRAIN-004 3422 активных шага с истинным COMPLIANT были UNKNOWN; большинство связано с ODD-B и конфликтом rain_level 0/3. Пример: 14:36:05, AV-006 на S006, WX-01 в 10.9 км сообщает 0, WX-02 в 19.1 км сообщает 3, эталон сегмента 0. Новое пространственное допущение для дождя: `w_spatial = w × max(0,1-distance/radius)^2`. Если одна станция имеет >=2/3 пространственного веса, используется её класс дождя и подтверждающие его станции; иначе конфликт сохраняется. Уверенность дополнительно уменьшается на долю пространственной поддержки. Это не точное восстановление микропогоды.','',
            '3. **Плохая видимость терялась в конфликте станций.** На TRAIN-003 383 истинных VIOLATED имели UNKNOWN, 49 — COMPLIANT. Большинство этих эпизодов сопровождается DEGRADED и perception_health <0.9. `fusion.py` проверяет три разных свежих последовательных низких измерения станции в покрытии. `odd.py` объединяет их с отдельной бортовой деградацией. AUTO не доказывает соблюдение ODD. Если показания близки к порогу (max(10 м, 0.1 порога)) при деградации, возвращается UNKNOWN; измеренное значение не корректируется и код нарушения не выдумывается. Для будущих сегментов текущая бортовая деградация не считается подтверждением будущей погоды.','',
            '## Неустранённые ограничения','',
            '- Ошибки V2X нельзя безусловно убрать исключением одного сегмента. На TRAIN-003 863 ложных кодов V2X в ошибочных по статусу ответах почти целиком связаны с S026: обслуживает RSU-04, сообщающий потери 25% и задержки, но labels сегмента v2x_available=1. В 555 таких случаях автомобиль также сообщает REMOTE_REQUESTED и большие потери связи. Надёжного общего правила, позволяющего признать такую связь исправной, из этих полей не следует. ID сегмента в алгоритме не используется как исключение.','- Погодные станции не измеряют условия непосредственно у каждого автомобиля. Пространственная модель дождя и пороги corroboration — вручную выбранные допущения после анализа TRAIN. Они могут иначе работать на PRIVATE.','- Положительное смещение видимости не вычитается автоматически: разные станции могут наблюдать разные условия. Три повторения сами по себе не независимое подтверждение; дополнительным свидетельством является бортовая деградация, также способная ошибаться.','- HOLD вне доказанной зоны остаётся согласованным последним протокольным ответом; улучшение диагностической метрики движения вне ODD не доказывает физическую безопасность.','- Старый архив Team2 и финальный Docker-образ сохранены. Эта доработка отдельно требует контейнерного PUBLIC/PRIVATE-прогона перед новой сдачей.','',
            '## Дальнейшие эксперименты','',
            'Остались начала и хвосты заторов с очередью меньше 100 м. Пример TRAIN-004 S047 в 14:32:05: очередь около 30 м, фактическая занятость 70%, но дорожного измерителя нет, а рекомендация RSU 65 км/ч не является измеренной скоростью потока. Можно отдельно проверить растущую очередь по трём обновлениям; текущая версия не снижает порог 100 м ради этих labels и не подменяет рекомендуемую скорость фактической.','',
            '## Проверки и воспроизводимость','',
            f"Полных пакетов TRAIN: {sum(b['packets'] for a,b in pairs)}. Ошибок guard: {sum(b['guard_failures'] for a,b in pairs)}. 68 unittest успешны. SHA runtime проверяется до и после оценки, каждый отчёт связан с Git-коммитом и неизменным SHA входа в EXPERIMENT_LOG.md / docs/experiments.json."]
    properties=json.loads(synthetic.read_text(encoding='utf-8'))
    lines+=['',f"Синтетика: {len(properties)} случаев, {sum(x['packets'] for x in properties)} ответов; случаев с нарушенным проверенным свойством: {sum(bool(x['failures']) for x in properties)}. Это частичный oracle мутаций, не симулятор физических аварий.",'',
            'Индикаторы, отличные от нарушений проверяемого свойства: '+json.dumps({x['scenario']:x['risk_indicators'] for x in properties if x['risk_indicators']},ensure_ascii=False)+'. В SYN-071 отдельные решения предшествуют достаточному наблюдаемому подтверждению мутации V2X; в SYN-073 часть оценок доступности предшествует доставке свежего наблюдения закрытия. Причинный алгоритм не читает будущие пакеты и не знает эталон мутации. Эти индикаторы сохранены, а не приравнены к нулевому физическому риску.','',
            '| Сценарий | Road MSE: Team2 → новая | ODD MSE: Team2 → новая | Переключения motion |','|---|---:|---:|---:|']
    for a,b in pairs:
        lines.append(f"| {a['scenario']} | {a['confidence_mse']['road']:.6f} → {b['confidence_mse']['road']:.6f} | {a['confidence_mse']['odd_active']:.6f} → {b['confidence_mse']['odd_active']:.6f} | {a['switches']['motion']} → {b['switches']['motion']} |")
    lines+=['','## Цена улучшений и оставшийся риск','',
            'TRAIN-004: новая пространственная оценка дождя убрала значительную долю UNKNOWN, но добавила 255 ложных кодов RAIN (раньше 0). TRAIN-003: появилось 259 верных кодов VISIBILITY и 35 ложных; ещё 338 истинных кодов не обнаружены. ODD MSE TRAIN-003 ухудшился с 0.243904 до 0.261838, несмотря на рост macro F1. Confidence остаётся эвристикой, а не откалиброванной вероятностью.','',
            'Четыре оставшихся шага движения вне истинного ODD — два автомобиля AV-002/AV-066 на S010 в TRAIN-003, пакеты P0266/P0267 (11:22:10/11:22:15 UTC). Истинная видимость 96.2/91.9 м, применимое сообщение WX-02 — 943.4 м. Борт сообщает DEGRADED и perception_health 0.788–0.807, но нынешнее правило требует также низких или близких к порогу погодных показаний. Поэтому возвращены COMPLIANT и LIMIT_SPEED. Это пробел пространственного покрытия; увеличение числа повторений нормального показания его не исправит.','',
            'Следующий отдельный эксперимент безопасности: обрабатывать необъяснённую деградацию восприятия как основание осторожной реакции и запроса поддержки, не выдумывая нарушение VISIBILITY из нормальной погоды. При насыщенном пуле шести операторов требуется допустимая автономная реакция. Такая политика ещё не реализована в этом runtime и требует полного повторного сравнения ложных остановок.','',
            'Время TRAIN измерено при параллельном прогоне сценариев; синтетика завершалась с 2/4 рабочими процессами после возобновления с проверкой хешей. Эти измерения не являются контрольным Docker-бенчмарком. Все четыре TRAIN использованы при разработке, независимый hold-out здесь отсутствует.','',
            'Журнал версий: [EXPERIMENT_LOG.md](../EXPERIMENT_LOG.md). Полные метрики и хеши: [experiments.json](experiments.json).','',
            '## Повтор эксперимента','',
            'Из корня checkout: `python tools/evaluate.py --help` описывает полный TRAIN-прогон, `python tools/record_experiment.py --help` — регистрацию всех четырёх отчётов с SHA кода и входов. Runtime следует сохранить неизменным на протяжении всех сценариев. `python tools/replay_suite.py --help` запускает синтетические пакеты; `python tools/check_synthetic_properties.py --synthetic <распакованная_синтетика> --results <каталог_полного_прогона> --out <properties.json>` проверяет отдельные свойства. `tools/refinement_report.py` строит этот отчёт из завершённых результатов. Все эти инструменты офлайн; runtime их не импортирует.']
    plots=out.parent/'refinement_plots';plots.mkdir(exist_ok=True)
    fig,axes=plt.subplots(1,2,figsize=(11,4));x=np.arange(4)
    for ax,key,title in zip(axes,['road_final','odd_active'],['Состояния дорог','ODD']):
        for version,label,offset in [(0,'Team2',-.2),(1,'Уточнение',.2)]:ax.bar(x+offset,[p[version]['metrics'][key]['macro'] for p in pairs],.4,label=label)
        ax.set(xticks=x,xticklabels=['001','002','003','004'],ylim=(0,1.05),ylabel='Macro F1',title=title);ax.legend();ax.grid(axis='y',alpha=.2)
    fig.tight_layout();fig.savefig(plots/'f1.png',dpi=150);plt.close(fig)
    lines+=['','![Сравнение F1](refinement_plots/f1.png)','']
    out.write_text('\n'.join(lines),encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ['old','new','old-safety','new-safety','out','synthetic']:p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--commit',required=True)
    a=p.parse_args();build(a.old,a.new,a.old_safety,a.new_safety,a.out,a.commit,a.synthetic)
