# hakaton-msk
# Беспилотный коридор

Python 3.13, без ML. Вход NDJSON через stdin, полный ответ на пакет через stdout, структурированные логи через stderr. Источники наблюдений и справочники задаются явно; labels не используются runtime.

```powershell
python -m pip install -r requirements.txt
python -m corridor --reference <путь-к-01_reference>
$env:CORRIDOR_REFERENCE='<путь-к-01_reference>'
python -m unittest discover -s tests -v
```

Реализованы потоковое состояние, FDIR и доверие, объединение наблюдений, ODD, два направленных маршрута, ограничения скорости, совместное назначение площадок и поддержки, финальный контроль решений. Правило CONTINUE/REROUTE сохранено без порога выигрыша и cooldown.

```sh
docker build --platform linux/amd64 -t corridor-solution:final .
docker run --rm -i --network none --cpus 8 --memory 16g corridor-solution:final < packets.ndjson > results.ndjson
docker save -o solution-image.tar corridor-solution:final
```

Демонстрация на синтетических ситуациях с реальными справочниками: `python tools/demo.py`. Детали — DEMO.md. Для полного набора тестов укажите CORRIDOR_REFERENCE на исходный 01_reference рядом с 02_train: два интеграционных теста читают TRAIN-001 по этому явно переданному пути. Данные в репозиторий не включаются.

План и ограничения — IMPLEMENTATION_PLAN.md; фактическая готовность — PROJECT_STATUS.md. Это текущая версия для предварительной технической проверки. Диагностический HOLD в невозможной ситуации не доказывает физическую безопасность; confidence остаётся эвристикой. Инструменты evaluate/quality_report предназначены только для офлайн-анализа и не входят в Docker runtime.

## Подробная проверка всех TRAIN

Актуальный разбор: [docs/TRAIN_CURRENT_ANALYSIS.md](docs/TRAIN_CURRENT_ANALYSIS.md). Предварительный отчёт сохранён отдельно и не заменяет проверку окончательной версии.

```powershell
python tools/evaluate.py --data <путь-к-данным> --out results/train-current --details
python tools/train_analysis.py --data <путь-к-данным> --results results/train-current --out <папка-аналитики>
python tools/train_report.py --results results/train-current --analysis <папка-аналитики> --document docs/TRAIN_CURRENT_ANALYSIS.md --previous results/verified
```

Для графиков нужен matplotlib с numpy. Метрики, snapshots, CSV и графики сохраняются вне Git. Анализ считает трёхшаговые критические ODD-эпизоды по опубликованному условию и отдельно любые движущие команды вне истинного ODD. Это локальные проверки, а не полный официальный оценщик или симуляция аварий. Исходное правило CONTINUE/REROUTE и runtime не изменяются.
