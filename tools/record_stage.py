"""Record a verified stage; does not run tests, commit, or publish."""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('stage', type=int)
parser.add_argument('summary')
parser.add_argument('--next', required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
with (root / 'CHANGE_LOG.md').open('a', encoding='utf-8') as stream:
    stream.write(f'\n## Этап {args.stage}\n\n{args.summary}\n')
(root / 'PROJECT_STATUS.md').write_text(
    f'# Статус проекта\n\nЭтапы 0–{args.stage}: выполнены и проверены. Остальные этапы ожидают реализации.\n\n'
    f'Следующий шаг: {args.next}\n\n'
    'Ограничения: неизвестная полезность действий и определение необоснованных переключений; '
    'пространственная семантика погоды, точная семантика поддержки и физическая допустимость последнего HOLD. '
    'Алгоритмический confidence не является доказанно откалиброванной вероятностью. '
    'Полные метрики и контейнерные ограничения подтверждаются только после этапов 11–12.\n', encoding='utf-8')
