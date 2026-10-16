"""Download the official Windows CUDA wheel to E: with resumable HTTP ranges.

The SHA256 is published in the official PyTorch cu128 package index.
"""
import concurrent.futures
import hashlib
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = "https://download-r2.pytorch.org/whl/cu128/torch-2.11.0%2Bcu128-cp312-cp312-win_amd64.whl"
SHA256 = "7c78215c3af4f62e63f2b2e360f1722fc719b0853c7ac22666483d9810613a4c"
SIZE = 2753189216
DEST = ROOT / ".cache" / "torch-2.11.0+cu128-cp312-cp312-win_amd64.whl"
CHUNK = 8 * 1024**2


def download_part(index):
    start = index * CHUNK
    end = min(start + CHUNK, SIZE) - 1
    path = DEST.with_suffix(f".part{index:04}")
    if path.exists() and path.stat().st_size == end - start + 1:
        return end - start + 1
    error = None
    for attempt in range(5):
        try:
            request = urllib.request.Request(URL, headers={"Range": f"bytes={start}-{end}"})
            with urllib.request.urlopen(request, timeout=90) as response:
                if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{SIZE}":
                    raise OSError("Server did not return the requested byte range")
                with path.open("wb") as output:
                    while data := response.read(1024 * 1024):
                        output.write(data)
            if path.stat().st_size != end - start + 1:
                raise OSError("Incomplete range")
            return end - start + 1
        except Exception as exc:
            error = exc
            time.sleep(1 + attempt)
    raise OSError(f"Part {index} failed") from error


def main():
    DEST.parent.mkdir(exist_ok=True)
    count = (SIZE + CHUNK - 1) // CHUNK
    started = time.perf_counter()
    if not DEST.exists():
        done_bytes = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(download_part, i) for i in range(count)]
            for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
                done_bytes += future.result()
                if done % 8 == 0 or done == count:
                    elapsed = time.perf_counter() - started
                    print(f"Torch: {done_bytes / SIZE:.0%}, {done_bytes / 1024**2:.0f} MiB, "
                          f"{done_bytes / 1024**2 / elapsed:.1f} MiB/s", flush=True)
        temp = DEST.with_suffix(".assembling")
        with temp.open("wb") as output:
            for i in range(count):
                with DEST.with_suffix(f".part{i:04}").open("rb") as part:
                    while data := part.read(4 * 1024**2):
                        output.write(data)
        temp.replace(DEST)
    digest = hashlib.file_digest(DEST.open("rb"), "sha256").hexdigest() if hasattr(hashlib, "file_digest") else None
    if digest is None:
        hasher = hashlib.sha256()
        with DEST.open("rb") as file:
            while data := file.read(4 * 1024**2):
                hasher.update(data)
        digest = hasher.hexdigest()
    if digest != SHA256:
        raise OSError(f"Official wheel SHA256 mismatch: {digest}")
    print(f"Verified official PyTorch wheel: {DEST}", flush=True)
    # Parts are task-owned immediate children of DEST.parent.
    for i in range(count):
        DEST.with_suffix(f".part{i:04}").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
