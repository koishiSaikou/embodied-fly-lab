"""Refresh deliberate source changes while preserving original-copy checksum evidence."""
from datetime import datetime, timezone
import json

from verify_repository import ROOT, MANIFEST, digest, repository_files


def main():
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    before = {item['path']: item for item in manifest['files']}
    excluded = set(manifest['excluded_self_and_reports'])
    rows = []
    for path in repository_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative in excluded:
            continue
        checksum = digest(path)
        row = dict(path=relative, bytes=path.stat().st_size, sha256=checksum)
        old = before.get(relative, {})
        original_path = old.get('source_path') or old.get('original_source_path')
        original_checksum = old.get('original_sha256') or old.get('sha256')
        if original_path:
            if checksum == original_checksum:
                row['source_path'] = original_path
            else:
                row.update(original_source_path=original_path, original_sha256=original_checksum,
                           modified_in_release_copy=True)
        rows.append(row)
    manifest.update(files=rows, updated_at_utc=datetime.now(timezone.utc).isoformat(),
                    purpose='Current source candidate inventory; original checksum evidence retained across intentional edits.')
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(files=len(rows), modified_copies=sum(bool(i.get('modified_in_release_copy')) for i in rows))))


if __name__ == '__main__':
    main()
