"""Read-only reproducible audit; labels are only used by offline tools."""
import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET


def rows(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def audit(root):
    report = {'references': {}, 'schemas': {}, 'scenarios': {}, 'materials': {}}
    for path in sorted((root / '01_reference').glob('*.csv')):
        records = rows(path)
        report['references'][path.name] = {'count': len(records), 'fields': list(records[0])}
    for path in sorted((root / '04_contract').glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        report['schemas'][path.name] = data
    for group in ('02_train', '03_public'):
        for directory in sorted((root / group).iterdir()):
            scenario = json.loads((directory / 'scenario.json').read_text(encoding='utf-8'))
            packet_count = event_count = 0
            events = Counter()
            with gzip.open(directory / 'packets.ndjson.gz', 'rt', encoding='utf-8') as stream:
                for line in stream:
                    packet = json.loads(line)
                    packet_count += 1
                    assert packet['event_count'] == len(packet['events'])
                    event_count += len(packet['events'])
                    events.update(e['event_type'] for e in packet['events'])
            assert packet_count == scenario['packet_count']
            entry = {'packets': packet_count, 'events': event_count, 'event_types': dict(events), 'labels': {}}
            for path in sorted((directory / 'labels').glob('*')):
                records = rows(path)
                classes = {}
                for field in ('true_state', 'reference_action_class', 'odd_status', 'violation_codes', 'fault_type'):
                    if records and field in records[0]:
                        classes[field] = dict(Counter(r[field] for r in records))
                entry['labels'][path.name] = {'rows': len(records), 'fields': list(records[0]) if records else [], 'classes': classes}
            report['scenarios'][directory.name] = entry
    for path in sorted(root.parent.glob('*')):
        if path.suffix not in ('.docx', '.md', '.ipynb'):
            continue
        item = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        if path.suffix == '.docx':
            with ZipFile(path) as archive:
                xml = ET.fromstring(archive.read('word/document.xml'))
                item['text'] = '\n'.join(''.join(p.itertext()) for p in xml.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
        elif path.suffix == '.ipynb':
            notebook = json.loads(path.read_text(encoding='utf-8'))
            item['cells'] = len(notebook['cells'])
            item['executed_cells'] = sum(c.get('execution_count') is not None for c in notebook['cells'])
            item['errors'] = [o for c in notebook['cells'] for o in c.get('outputs', []) if o['output_type'] == 'error']
        else:
            item['text'] = path.read_text(encoding='utf-8')
        report['materials'][path.name] = item
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.data)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'references': result['references'], 'scenarios': result['scenarios']}, ensure_ascii=False))
