import hashlib
import json
import shutil
import zipfile
from pathlib import Path


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def write_zip(output, entries):
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path, name in entries:
            archive.write(path, name)
    with zipfile.ZipFile(output) as archive:
        error = archive.testzip()
        if error:
            raise ValueError("Archive CRC mismatch: " + error)


def main():
    root = Path(__file__).resolve().parents[1]
    destination = root / "deliverables"
    destination.mkdir(exist_ok=True)
    if not (destination / "solution-image.tar").exists():
        raise FileNotFoundError("Export Docker image before packaging")
    entries = []
    for folder in ["corridor", "tools", "tests", "models", "contracts"]:
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                entries.append((path, path.relative_to(root).as_posix()))
    for name in ["Dockerfile", ".dockerignore", "requirements.txt", "requirements-dev.txt", "README.md", "LOGIC.md", "METRICS.md", "ORGANIZER_AUDIT.md", "WORKLOG.md", "01_requirements.txt", "02_requirements.txt"]:
        entries.append((root / name, name))
    for path in sorted((root / "reports").glob("*")):
        if path.is_file() and not path.name.startswith("debug_"):
            entries.append((path, path.relative_to(root).as_posix()))
    write_zip(destination / "source.zip", entries)
    payload = ["solution-image.tar", "source.zip", "PUBLIC-101.result.ndjson.gz", "PUBLIC-102.result.ndjson.gz", "README.md", "LOGIC.md", "METRICS.md", "ORGANIZER_AUDIT.md"]
    for name in payload:
        if name.startswith("PUBLIC"):
            shutil.copy2(root / "results" / name, destination / name)
        elif name.endswith(".md"):
            shutil.copy2(root / name, destination / name)
    manifest = {name: {"sha256": digest(destination / name), "bytes": (destination / name).stat().st_size} for name in payload}
    (destination / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    archive = destination / "Кариков Team.zip"
    write_zip(archive, [(destination / name, name) for name in payload + ["MANIFEST.json"]])
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size, "sha256": digest(archive), "members": payload + ["MANIFEST.json"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
