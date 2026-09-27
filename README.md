# hakaton-msk
# Беспилотный коридор

Python 3.13, без ML. Вход NDJSON через stdin, полный ответ на пакет через stdout, структурированные логи через stderr. Источники наблюдений и справочники задаются явно; labels не используются runtime.

```powershell
python -m pip install -r requirements.txt
python -m corridor --reference <путь-к-01_reference>
$env:CORRIDOR_REFERENCE='<путь-к-01_reference>'
python -m unittest discover -s tests -v
```

План и ограничения — IMPLEMENTATION_PLAN.md; фактическая готовность — PROJECT_STATUS.md. На этапе интерфейса UNKNOWN/HOLD является технической политикой, а не завершённым алгоритмом.
