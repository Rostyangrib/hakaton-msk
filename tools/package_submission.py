"""Package the tested image and exactly matching working-tree source bytes."""
import argparse
import gzip
import hashlib
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--results',type=Path)
    parser.add_argument('--image-tar',type=Path)
    parser.add_argument('--image-manifest',type=Path)
    parser.add_argument('--presentation',type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    results = args.results or root / 'results' / 'technical-submission'
    image_tar=args.image_tar or root / 'results/solution-image.tar'
    manifest = json.loads((args.image_manifest or root / 'results/image-files.json').read_text(encoding='utf-8-sig'))
    for name, digest in manifest.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    with tarfile.open(image_tar) as image:
        entries = json.load(image.extractfile('manifest.json'))
        entry = next(e for e in entries if 'corridor-solution:final' in e['RepoTags'])
        config = json.load(image.extractfile(entry['Config']))
        assert config['architecture'] == 'amd64' and config['os'] == 'linux'
    names = subprocess.check_output(['git', '-c', f'safe.directory={root.as_posix()}', 'ls-files'], cwd=root, text=True).splitlines()
    source = results / 'source.zip'
    with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            path = root / name
            if path.is_file():
                archive.write(path, name)
    with zipfile.ZipFile(source) as archive:
        assert archive.testzip() is None
        for name, digest in manifest.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest, name
        assert all(not n.startswith(('results/', '02_train/', '03_public/')) for n in archive.namelist())
        assert {'Dockerfile', 'requirements.txt', 'corridor/__main__.py'} <= set(archive.namelist())
    reports = []
    for sid, count in [('PUBLIC-101', 540), ('PUBLIC-102', 720)]:
        report = json.loads((results / (sid + '-container.json')).read_text(encoding='utf-8'))
        assert report['packets'] == report['complete_schema_valid'] == count
        assert report['exit_code'] == 0
        assert report.get('image','corridor-solution:final')=='corridor-solution:final'
        with gzip.open(results / (sid + '.ndjson.gz'), 'rt', encoding='utf-8') as stream:
            assert sum(1 for _ in stream) == count
        reports.append(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(image_tar, 'solution-image.tar')
        archive.write(source, 'source.zip')
        for sid in ['PUBLIC-101', 'PUBLIC-102']:
            archive.write(results / (sid + '.ndjson.gz'), sid + '.result.ndjson.gz')
        if args.presentation:
            from pypdf import PdfReader
            assert len(PdfReader(args.presentation).pages)<=7
            archive.write(args.presentation,'presentation.pdf')
    with zipfile.ZipFile(args.output) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist())=={'solution-image.tar','source.zip','PUBLIC-101.result.ndjson.gz','PUBLIC-102.result.ndjson.gz'}|({'presentation.pdf'} if args.presentation else set())
    validation = dict(archive=str(args.output), bytes=args.output.stat().st_size,
                      sha256=hashlib.sha256(args.output.read_bytes()).hexdigest(),
                      runtime_files_matching_image=len(manifest), reports=reports,
                      missing_user_supplied_file=None if args.presentation else 'presentation.pdf')
    (results / 'package-validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(validation, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
