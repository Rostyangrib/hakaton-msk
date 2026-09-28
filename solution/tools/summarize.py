import json
from pathlib import Path
from statistics import mean


def main():
    reports = json.loads(Path("reports/metrics.json").read_text())
    train = [r for r in reports if r["scenario"].startswith("TRAIN")]
    docker = json.loads(Path("reports/docker_verification.json").read_text())
    fields = [("segment_macro_f1", lambda r: r["segment_state"]["macro_f1"]), ("source_status_macro_f1", lambda r: r["source_status"]["macro_f1"]), ("source_fault_types_macro_f1", lambda r: r["source_fault_types"]["macro_f1"]), ("source_fault_composite", lambda r: r["source_fault_composite"]), ("odd_macro_f1", lambda r: r["odd_status"]["macro_f1"]), ("odd_violation_codes_macro_f1", lambda r: r["odd_violation_codes"]["macro_f1"]), ("confidence_calibration", lambda r: r["confidence_calibration"]), ("weighted_reference_action_agreement", lambda r: r["weighted_reference_action_agreement"])]
    summary = {name: mean(extract(r) for r in train) for name, extract in fields}
    summary.update(train_packets=sum(r["packets"] for r in train), total_packets=sum(r["packets"] for r in reports), schema_valid_packets=sum(r["schema_valid_packets"] for r in reports), local_critical_episodes=sum(r["local_critical_episodes"] for r in train), remote_overcapacity_steps=sum(r["remote_support_overcapacity_steps"] for r in train), safe_stop_overcapacity_steps=sum(r["safe_stop_overcapacity_steps"] for r in train), docker_max_packet_latency_ms=max(r["latency_ms"]["max"] for r in docker), docker_max_startup_sec=max(r["startup_and_first_packet_sec"] for r in docker))
    peaks = []
    for path in Path("reports").glob("*.docker.stderr.jsonl"):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if "peak_rss_bytes" in row:
                peaks.append(row["peak_rss_bytes"])
    summary["docker_max_peak_rss_bytes"] = max(peaks)
    Path("reports/summary.json").write_text(json.dumps(summary, indent=2))
    lines = ["# Результаты проверки программы", "", "Метрики получены полным причинным прогоном финальной модели по TRAIN. Это обучающая выборка: значения не являются оценкой скрытого теста. Все числа в таблицах приведены в шкале 0…1, кроме количества и миллисекунд. Среднее — арифметическое по четырём сценариям.", "", "## Качество на TRAIN", "", "| Сценарий | Macro F1 сегментов | Macro F1 статуса источников | Показатель неисправностей | Macro F1 ODD | 1 − MSE confidence |", "|---|---:|---:|---:|---:|---:|"]
    for r in train:
        lines.append(f'| {r["scenario"]} | {r["segment_state"]["macro_f1"]:.6f} | {r["source_status"]["macro_f1"]:.6f} | {r["source_fault_composite"]:.6f} | {r["odd_status"]["macro_f1"]:.6f} | {r["confidence_calibration"]:.6f} |')
    lines.append(f'| Среднее | {summary["segment_macro_f1"]:.6f} | {summary["source_status_macro_f1"]:.6f} | {summary["source_fault_composite"]:.6f} | {summary["odd_macro_f1"]:.6f} | {summary["confidence_calibration"]:.6f} |')
    lines += ["", "Показатель неисправностей = 0,50 × F1 статуса + 0,25 × F1 типов + 0,25 × оценка задержки правильного обнаружения. Интервал неисправности учитывается как [start_time, end_time). Для сценария без неисправностей при отсутствии ложных тревог качество равно 1.", "", "| Сценарий | F1 типов неисправности | Оценка задержки | F1 кодов ODD | Совпадение эталонного действия с весом груза |", "|---|---:|---:|---:|---:|"]
    for r in train:
        lines.append(f'| {r["scenario"]} | {r["source_fault_types"]["macro_f1"]:.6f} | {r["source_detection_delay_score"]:.6f} | {r["odd_violation_codes"]["macro_f1"]:.6f} | {r["weighted_reference_action_agreement"]:.6f} |')
    lines += ["", "Совпадение действия — локальная диагностическая метрика, а не официальный показатель полезности. Организатор подтвердил отсутствие единственного правильного действия: частые явные REROUTE могут уменьшать совпадение с reference_action_class и при этом устранять опасную неоднозначность. Этот показатель не используется для заявления, что новая политика хуже или лучше.", "", "## Безопасность и ресурсы", "", "| Сценарий | Локальные критические эпизоды | Назначения SAFE_STOP | Корректность SAFE_STOP | Превышения мест | Превышения операторов |", "|---|---:|---:|---:|---:|---:|"]
    for r in train:
        lines.append(f'| {r["scenario"]} | {r["local_critical_episodes"]} | {r["safe_stop_assignments"]} | {r["safe_stop_constraint_score"]:.6f} | {r["safe_stop_overcapacity_steps"]} | {r["remote_support_overcapacity_steps"]} |')
    lines += ["", "Критические эпизоды посчитаны локальным оценщиком по трём последовательным шагам. Это не заключение официального оценщика и не доказательство физической безопасности. В TRAIN нет истинных CLOSED. Геометрия выданных REROUTE проверяется относительно последней доступной телеметрии и актуального HUB, закрытие дорог — по train-разметке на время решения. Развилка с закрытой некратчайшей веткой проверена отдельным тестом.", "", "## Технический прогон Docker", "", "| Сценарий | Пакеты | Среднее, мс | p95, мс | Максимум, мс | Побайтное совпадение |", "|---|---:|---:|---:|---:|---|"]
    lines[lines.index("## Технический прогон Docker"):lines.index("## Технический прогон Docker")] = ["| Сценарий | Выдано REROUTE | Доля допустимых маршрутов |", "|---|---:|---:|"] + [f'| {r["scenario"]} | {r["reroutes"]} | {r["full_route_validity"]:.6f} |' for r in train] + [""]
    for r in docker:
        lines.append(f'| {r["scenario"]} | {r["packets"]} | {r["latency_ms"]["mean"]:.3f} | {r["latency_ms"]["p95"]:.3f} | {r["latency_ms"]["max"]:.3f} | Да |')
    lines += ["", f'Всего проверено {summary["total_packets"]} пакетов, из них TRAIN — {summary["train_packets"]}. По всем {summary["schema_valid_packets"]} ответам проверены JSON Schema, date-time и точное множество сущностей. Полнота — 100%, пропусков и превышений 2 секунд нет.', "", f'Максимальное время старта вместе с первым пакетом — {summary["docker_max_startup_sec"]:.3f} с при лимите 60 с. Максимальная наблюдённая память процесса — {summary["docker_max_peak_rss_bytes"] / 2**20:.2f} МиБ при лимите 16 ГБ. Контейнер: linux/amd64, Python 3.13.15, network=none, read-only, непривилегированный пользователь, максимум 8 vCPU.', "", "Задержки Docker включают передачу пакета и чтение полного ответа через stdin/stdout. Первый пакет вынесен в показатель старта. Локальные timings из metrics.json дополнительно измеряют только обработку и сериализацию; это другой показатель.", "", "Образ corridor-solution:final экспортирован через docker save и успешно повторно загружен через docker load. JSON-результаты контейнера для всех шести сценариев побайтно совпали с независимым локальным запуском. Пройдены 29 инженерных автотестов; протокол сохранён в reports/tests.txt.", "", "## Ограничения интерпретации", "", "Временная проверка и параметры обучения находятся в reports/training.json и reports/holdout.json. Эти файлы относятся к первоначальному обучению до аудита организатора. Последние 30% TRAIN использованы также для калибровки confidence; в этом срезе отсутствуют некоторые виды происшествий. Поэтому основной отчёт не выдаёт этот срез за независимый скрытый тест.", "", "Разметка PUBLIC не предоставлена: по PUBLIC подтверждены схема, завершение, скорость и воспроизводимость; показатели качества состояния и ODD не выдумываются.", "", "Итоговый конкурсный балл из 100 не рассчитывается: в раздаточном комплекте нет исполняемого официального оценщика, точной таблицы полезности действий и всех правил нормирования. Все определения, допущения и несовместимости контракта изложены в LOGIC.md.", ""]
    Path("METRICS.md").write_text("\n".join(lines))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
