# Контейнерная проверка текущей версии

Команда tools/container_check.py запускает представленную версию corridor-solution:final: --platform linux/amd64 --network none --cpus 8 --memory 16g --read-only --cap-drop ALL --security-opt no-new-privileges.

```json
{
  "scenario": "PUBLIC-101",
  "packets": 540,
  "exit_code": 0,
  "first_response_sec": 1.31517509999685,
  "roundtrip_ms": {
    "p50": 364.74789993371814,
    "p95": 658.7315000360832,
    "p99": 992.5869000144303,
    "max": 1289.7621999727562
  },
  "processing_ms": {
    "p50": 305.7926339997721,
    "p95": 549.003040000116,
    "p99": 753.0509230000462,
    "max": 1008.7518790001013
  },
  "processing_over_2000_ms": 0,
  "roundtrip_over_2000_ms": 0,
  "complete_schema_valid": 540,
  "network": "none",
  "cpu_limit": 8,
  "memory_limit": "16g",
  "gpu": false,
  "read_only": true
}
```

```json
{
  "scenario": "PUBLIC-102",
  "packets": 720,
  "exit_code": 0,
  "first_response_sec": 1.421702199964784,
  "roundtrip_ms": {
    "p50": 371.23079993762076,
    "p95": 635.7256999472156,
    "p99": 882.9850000329316,
    "max": 1398.4474000753835
  },
  "processing_ms": {
    "p50": 314.64876699965316,
    "p95": 544.994842000051,
    "p99": 735.4622950001612,
    "max": 937.0263340001657
  },
  "processing_over_2000_ms": 0,
  "roundtrip_over_2000_ms": 0,
  "complete_schema_valid": 720,
  "network": "none",
  "cpu_limit": 8,
  "memory_limit": "16g",
  "gpu": false,
  "read_only": true
}
```

Контракт проверяется для каждого ответа; packet_id соответствует входу. В runtime отсутствуют labels и офлайн-анализ. Все 31 файла corridor/reference/contract побайтово совпадают с source.zip. Презентация добавляется пользователем самостоятельно. Полный повторный PUBLIC-прогон ещё не выполнен; это не утверждение о полной правильности оценок.

Повторный контейнерный прогон первых 20 пакетов PUBLIC-101 дал побайтово идентичные JSON-ответы. Полный повтор обоих сценариев ещё не выполнялся.
