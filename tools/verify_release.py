"""Independent offline checks of the exact delivery archive and repeat runs."""
import argparse
import gzip
import hashlib
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from corridor.contract import Contract
from corridor.reference import Reference
from pypdf import PdfReader


def digest(value):
    return hashlib.sha256(value).hexdigest()


def verify(archive, reports, repeat, image_manifest, image_id, out):
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(image_manifest.read_text(encoding='utf-8-sig'))
    validator = Contract(Reference(root / 'reference'))
    checked = []
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None, 'Outer ZIP CRC'
        assert set(package.namelist()) == {
            'solution-image.tar', 'source.zip', 'PUBLIC-101.result.ndjson.gz',
            'PUBLIC-102.result.ndjson.gz', 'presentation.pdf'
        }, 'Delivery file list'
        with zipfile.ZipFile(io.BytesIO(package.read('source.zip'))) as source:
            assert source.testzip() is None, 'Source ZIP CRC'
            for name, expected in manifest.items():
                assert digest(source.read(name)) == expected, name
                assert digest((root / name).read_bytes()) == expected, name
            for name in source.namelist():
                assert source.read(name) == (root / name).read_bytes(), name
            assert {'Dockerfile', 'requirements.txt'} <= set(source.namelist())
            assert not any(n.startswith(('results/', 'output/', '02_train/', '03_public/'))
                           for n in source.namelist())
        with tarfile.open(fileobj=io.BytesIO(package.read('solution-image.tar'))) as image:
            entries = json.load(image.extractfile('manifest.json'))
            entry = next(e for e in entries if 'corridor-solution:final' in e['RepoTags'])
            config_bytes = image.extractfile(entry['Config']).read()
            config = json.loads(config_bytes)
            assert (config['os'], config['architecture']) == ('linux', 'amd64')
            # Docker containerd uses an OCI manifest/index digest as the image ID.
            if 'index.json' in image.getnames():
                index = json.load(image.extractfile('index.json'))
                matching = [m for m in index['manifests'] if
                            m.get('annotations', {}).get('io.containerd.image.name', '').endswith('corridor-solution:final')
                            or m.get('annotations', {}).get('org.opencontainers.image.ref.name', '') == 'final']
                assert matching and any(m['digest'] == image_id for m in matching), 'Saved image ID'
            else:
                assert digest(config_bytes) == image_id.removeprefix('sha256:'), 'Saved image ID'
        for sid, count in [('PUBLIC-101', 540), ('PUBLIC-102', 720)]:
            blob = package.read(sid + '.result.ndjson.gz')
            assert blob == (reports / (sid + '.ndjson.gz')).read_bytes()
            assert blob == (repeat / (sid + '.ndjson.gz')).read_bytes(), 'Repeat differs: ' + sid
            records = [json.loads(line) for line in gzip.decompress(blob).splitlines()]
            assert len(records) == count
            for decision in records:
                validator.validate(decision)
            for directory in (reports, repeat):
                control = json.loads((directory / (sid + '-container.json')).read_text())
                assert control['packets'] == control['complete_schema_valid'] == count
                assert control['exit_code'] == 0 and control['first_response_sec'] < 60
                assert control['processing_over_2000_ms'] == control['roundtrip_over_2000_ms'] == 0
                assert control['network'] == 'none' and control['cpu_limit'] == 8
                assert control['memory_limit'] == '16g' and control['read_only'] and not control['gpu']
            checked.append(dict(scenario=sid, responses=count, repeated_gzip_sha256=digest(blob)))
        pages = len(PdfReader(io.BytesIO(package.read('presentation.pdf'))).pages)
        assert pages <= 7
    result = dict(archive=str(archive.resolve()), sha256=digest(archive.read_bytes()),
                  image_id=image_id, runtime_files_matching_image=len(manifest),
                  public=checked, presentation_pages=pages, all_checks_passed=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for key in ('archive', 'reports', 'repeat', 'image-manifest', 'out'):
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--image-id', required=True)
    a = p.parse_args()
    verify(a.archive, a.reports, a.repeat, a.image_manifest, a.image_id, a.out)
