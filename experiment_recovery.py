"""Independent best-effort cleanup steps and explicit experiment failure records."""
import json
from pathlib import Path
import sys
import traceback


def failure_record(error):
    return dict(type=type(error).__name__, message=str(error),
                details=getattr(error, "details", {}),
                traceback="".join(traceback.format_exception(error)))


class Finalizer:
    def __init__(self):
        self.errors = []

    def attempt(self, stage, action):
        try:
            return action()
        except Exception as error:
            record = dict(stage=stage, **failure_record(error))
            self.errors.append(record)
            print(f"收尾失败 [{stage}]：{record['message']}", file=sys.stderr, flush=True)
            return None


def write_json(path, value):
    """Replace only after the new report is fully written; retain .partial on failure."""
    path = Path(path)
    partial = path.with_name(path.name + ".partial")
    with partial.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
    partial.replace(path)
