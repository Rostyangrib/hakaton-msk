# Беспилотный коридор

Потоковая программа на Python для оценки дорожной сети, диагностики источников, проверки ODD и выбора действий для 72 ВАТС. Работает без сети, GPU и внешних библиотек. Обученная модель включена в поставку.

## Запуск готового образа

```bash
docker load -i solution-image.tar

gzip -dc /path/to/scenario/packets.ndjson.gz | docker run --rm -i \
  --platform linux/amd64 --network none --read-only --cpus 8 --memory 16g \
  -v /path/to/01_reference:/data/reference:ro \
  -v /path/to/scenario/scenario.json:/data/scenario/scenario.json:ro \
  corridor-solution:final > result.ndjson
```

На каждую входную строку возвращается ровно один JSON-ответ с немедленным сбросом буфера. Диагностика записывается в stderr. Конец stdin завершает программу с кодом 0. Все назначения ресурсов относятся к текущему полному снимку решения.

## Запуск исходного кода

Достаточно Python 3.13 и стандартной библиотеки. Установка requirements.txt не требуется: файл намеренно пустой.

```bash
python3 -m corridor --reference /path/to/01_reference < packets.ndjson > result.ndjson
```

Путь справочника можно также задать переменной `CORRIDOR_REFERENCE`. По умолчанию используется `/data/reference`. Описание сценария доступно по `/data/scenario/scenario.json`, но алгоритм не использует его название, сложность, описание событий или ID для выбора решений.

## Сборка

```bash
docker build --platform linux/amd64 -t corridor-solution:final .
docker save -o solution-image.tar corridor-solution:final
```

Базовый образ закреплён по SHA-256. В образ копируются только программа и финальная модель; TRAIN, PUBLIC, разметка, инструменты обучения и результаты туда не входят. Контейнер работает от непривилегированного пользователя.

## Разработка и воспроизведение оценки

В следующих командах `DATA` — абсолютный путь к каталогу `03_Данные_Беспилотный_коридор`. Это обычная переменная примера; её нужно задать для собственной копии данных.

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
python3 tools/train.py --data "$DATA"
python3 tools/evaluate.py --data "$DATA" --split train --model models/holdout.json \
  --tail --output results/holdout --report reports/holdout.json
python3 tools/calibrate.py
python3 tools/evaluate.py --data "$DATA" --split all
CORRIDOR_DATA="$DATA" python3 -m unittest discover -s tests -v
```

После изменения модели контейнер нужно пересобрать. Проверка контейнера передаёт следующий пакет только после ответа на предыдущий, отключает сеть и побайтно сравнивает результаты с локальным прогоном:

```bash
python3 tools/verify_container.py --data "$DATA"
```

Официальные разъяснения и аудит соответствия сохранены в [ORGANIZER_AUDIT.md](ORGANIZER_AUDIT.md). JSON Schema приоритетна; исходный маршрут не предполагается известным. CONTINUE не назначает путь, а REROUTE всегда содержит явно проверенную рекомендацию.

Полная логика, соответствие требованиям и принятые решения приведены в [LOGIC.md](LOGIC.md). Измеренные результаты приведены в [METRICS.md](METRICS.md) и `reports/metrics.json`. `WORKLOG.md` содержит краткое состояние работы для продолжения.

Презентация не создавалась по указанию пользователя. В конкурсном ТЗ она указана как отдельный материал; данный комплект намеренно содержит программу, результаты и документацию без презентации.
