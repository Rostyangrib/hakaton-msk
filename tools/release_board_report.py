"""Summarize a frozen runtime's completed TRAIN, BOARD, SYN and Docker checks."""
import argparse
import json
from pathlib import Path


def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))


def build(repo,root,old,new,out,commit):
    safety=read(root/'train-analysis/safety_analysis.json')
    board=read(root/'comparison.json');syn=read(root/'synthetic-properties.json')
    audit=read(root/'stop_audit.json');public=read(root/'public-comparison.json')
    lines=['# Контроль следующей сдачи — бортовые отказы','',
           f'Исполняемый Git-коммит: {commit}. Python без ML, runtime не читает labels, expectations или latent_truth. Согласованное полное сравнение CONTINUE/REROUTE не менялось. Все TRAIN использованы в анализе; результат не является независимым hold-out или официальным баллом.','',
           '## Аудит 13 назначений SAFE_STOP','',
           f"Все {len(audit)} прежних назначений SS-02 относятся к истинному VIOLATED на S010: видимость {min(r['true_visibility'] for r in audit):.1f}–{max(r['true_visibility'] for r in audit):.1f} м. На S010 площадок нет. Статический подъезд только до начала сегмента площадки занимает {min(r['static_distance_to_stop_segment_start_m'] for r in audit)/1000:.1f}–{max(r['static_distance_to_stop_segment_start_m'] for r in audit)/1000:.1f} км; дальнейший путь по сегменту площадки не включён. Восстановить это назначение без подтверждения текущего фрагмента нельзя. Программа сохраняет диагностический HOLD и повышает приоритет поддержки при бортовом отказе. Это не доказательство физической безопасности удержания на дороге.",'',
           '## Реализованная политика','',
           '- perception_health <0.5 при любом режиме либо <0.9 при DEGRADED/REMOTE_REQUESTED; localization_confidence <0.5; задержка >=2000 мс или потери >=50% → неопределённость текущей автоматической допустимости. Это ручные настройки команды, не официальные границы ODD.','- Восстановление: три новых сообщения с возрастающим event_time, разными event_id и одновременно perception >=0.9, localization >=0.8, задержкой <1000 мс, потерями <20%. Дубли и поздние хорошие события не восстанавливают борт. Частичное восстановление не снимает оставшийся отказ.','- Оценка UNKNOWN без выдуманных кодов PERCEPTION/LOCALIZATION/VISIBILITY; подтверждённые нарушения профиля сохраняют VIOLATED. Будущая погода не определяется текущим состоянием борта. Финальный guard проверяет борт напрямую перед выдачей движения.','- Неподтверждённый подъезд к площадке запрещён; пул поддержки ограничен шестью назначениями текущего снимка. Предыдущие команды не считаются исполненными.','',
           '## Полный TRAIN','',
           '| TRAIN | Дороги F1: 197295b → новая | ODD F1 | Движение вне истинного ODD |','|---|---:|---:|---:|']
    for risk in safety:
        sid=risk['scenario'];a=read(old/(sid+'.json'));b=read(new/(sid+'.json'))
        assert a['input_sha256']==b['input_sha256']
        lines.append(f"| {sid} | {a['metrics']['road_final']['macro']:.6f} → {b['metrics']['road_final']['macro']:.6f} | {a['metrics']['odd_active']['macro']:.6f} → {b['metrics']['odd_active']['macro']:.6f} | {risk['moving_outside_odd_steps']} |")
    lines+=['',f"75 unittest успешны. Полных пакетов TRAIN: {sum(r['packets'] for r in safety)}. Обнаруженных ошибок снимка: {sum(len(r['snapshot_issues']) for r in safety)}; нарушений маршрутов/площадок по доступному эталону: {sum(len(r['route_stop_truth_issues']) for r in safety)}. Строгих трёхшаговых ODD-эпизодов по опубликованному JSON: {sum(r['gate_odd_episodes'] for r in safety)}.",'',
            'В четырёх исходных решениях AV-002/AV-066 P0266/P0267 теперь HOLD, но remote_support_required остаётся false: пул из шести операторов насыщен. Повышение приоритета запроса не доказывает получение поддержки; фиктивное назначение не возвращается.','',
            '| TRAIN | ODD MSE: 197295b → новая | Команды HOLD | SAFE_STOP | Поддержка: назначений |','|---|---:|---:|---:|---:|']
    for risk in safety:
        sid=risk['scenario'];a=read(old/(sid+'.json'));b=read(new/(sid+'.json'))
        support=sum(int(k)*v for k,v in risk['support_histogram'].items())
        lines.append(f"| {sid} | {a['confidence_mse']['odd_active']:.6f} → {b['confidence_mse']['odd_active']:.6f} | {b['action_counts'].get('HOLD',0)} | {b['action_counts'].get('SAFE_STOP',0)} | {support} |")
    registry=read(repo/'docs/experiments.json')
    team2=next(e for e in registry if e['code_commit'].startswith('6486a44'))
    lines+=['','### Сравнение с отправленным Team2 (6486a44)','',
            '| TRAIN | Дороги F1: Team2 → новая | ODD F1: Team2 → новая |','|---|---:|---:|']
    for risk in safety:
        sid=risk['scenario'];a=team2['scenarios'][sid];b=read(new/(sid+'.json'))
        assert a['input_sha256']==b['input_sha256']
        lines.append(f"| {sid} | {a['metrics']['road_final']['macro']:.6f} → {b['metrics']['road_final']['macro']:.6f} | {a['metrics']['odd_active']['macro']:.6f} → {b['metrics']['odd_active']['macro']:.6f} |")
    lines+=['','## Бортовые и прежние синтетические случаи','',
            f"BOARD: {len(board['new'])} случаев, {sum(r['packets'] for r in board['new'])} ответов каждой версии. Случаев с нарушенным свойством: {sum(bool(r['failures']) for r in board['old'])} → {sum(bool(r['failures']) for r in board['new'])}. Контроль исправного борта и восстановления измерены отдельно.",'',
            '| BOARD | Ошибки базовой политики | Ошибки новой | Ненулевые индикаторы скрытых условий |','|---|---|---|---|']
    for a,b in zip(board['old'],board['new']):
        lines.append('| '+b['scenario']+' | '+json.dumps(a['failures'],ensure_ascii=False)+' | '+json.dumps(b['failures'],ensure_ascii=False)+' | '+json.dumps(b['risk_indicators'],ensure_ascii=False)+' |')
    lines+=['',f"Прежняя SYN: {len(syn)} случаев/{sum(r['packets'] for r in syn)} ответов, нарушенных проверяемых свойств: {sum(bool(r['failures']) for r in syn)}. Ненулевые индикаторы сохранены: "+json.dumps({r['scenario']:r['risk_indicators'] for r in syn if r['risk_indicators']},ensure_ascii=False)+'.','',
            'BOARD-008: скрытая видимость уже задана плохой, а первые показания здоровья 0.95/0.9167 ещё выше порога; два движущих ответа не скрыты. Проверяемые свойства наблюдаемого отказа не являются полной истиной или гарантией PRIVATE. Числовая полезность действий и точная реализация скрытого оценщика недоступны.','',
            '## Контейнерный PUBLIC','',
            '| PUBLIC | Валидных ответов | Первый ответ, с | Roundtrip максимум, с: прогон / повтор | Ответов >2 с | Изменений motion относительно Team2 |','|---|---:|---:|---:|---:|---:|']
    for entry in public:
        sid=entry['scenario'];r=read(root/'submission'/(sid+'-container.json'))
        repeated=read(root/'public-repeat'/(sid+'-container.json'))
        assert (root/'submission'/(sid+'.ndjson.gz')).read_bytes()==(root/'public-repeat'/(sid+'.ndjson.gz')).read_bytes()
        for control in (r,repeated):
            assert control['complete_schema_valid']==(540 if sid=='PUBLIC-101' else 720)
            assert control['exit_code']==control['processing_over_2000_ms']==control['roundtrip_over_2000_ms']==0
            assert control['first_response_sec']<60
        lines.append(f"| {sid} | {r['complete_schema_valid']} | {r['first_response_sec']:.3f} | {r['roundtrip_ms']['max']/1000:.3f} / {repeated['roundtrip_ms']['max']/1000:.3f} | {r['roundtrip_over_2000_ms']+repeated['roundtrip_over_2000_ms']} | {entry['counts'].get('motion_action_changed',0)} |")
    lines+=['','linux/amd64, 8 CPU, 16 ГБ, сеть отключена, GPU не предоставлен, read-only, EOF код 0. Повтор обоих PUBLIC побайтово совпал с первым прогоном (NDJSON.GZ с mtime=0). Также все JSON-решения обоих PUBLIC совпали с Team2. Результаты PUBLIC не имеют эталонов: изменение команды не доказывает улучшение.','',
            '## Итоговая поставка','',
            'Кариков Team3.zip: solution-image.tar (docker save тега corridor-solution:final), source.zip с совпадающими runtime SHA, PUBLIC-101.result.ndjson.gz, PUBLIC-102.result.ndjson.gz и прежняя пользовательская presentation.pdf (не более семи страниц). Архив Team2 сохранён. Презентация не редактировалась; её старые показатели следует сверить с этим отчётом перед защитой.','',
            'Существует независимый протокольный контроль известных ограничений, но физическая безопасность при скрытой ошибке всех применимых источников не доказана. Диагностический HOLD вне зоны ожидания и ненаблюдаемое начало опасности остаются ограничениями.','',
            'Журнал коммитов и метрик: [EXPERIMENT_LOG.md](../EXPERIMENT_LOG.md). Исходные пакеты и подробные результаты находятся вне Git; runtime использует только поток пакетов и справочники.','']
    out.write_text('\n'.join(lines),encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ['repo','root','old','new','out']:p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--commit',required=True)
    a=p.parse_args();build(a.repo,a.root,a.old,a.new,a.out,a.commit)
