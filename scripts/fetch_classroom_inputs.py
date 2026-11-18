"""Acquire and checksum the four published GEO files for the bundled classroom case."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import re
import shutil
from urllib.parse import quote
from urllib.request import Request, urlopen


SOURCE = "https://ftp.ncbi.nlm.nih.gov/geo/samples/GSM4219nnn/GSM4219576/suppl/"
MATRIX = "GSM4219576_Run2_matrix_fresh.mtx.gz"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(url, temp):
    def get(headers=None):
        return urlopen(Request(url, headers=headers or {}), timeout=40)

    if url.endswith("_matrix.mtx.gz"):
        # GEO supports byte ranges. Each worker has a bounded transfer size.
        try:
            with get({"Range": "bytes=0-0"}) as response:
                match = re.fullmatch(r"bytes 0-0/(\d+)", response.headers.get("Content-Range", ""))
                if response.status != 206 or match is None:
                    raise ValueError("Range not supported")
                size = int(match[1])
            if size > 32 * 1024**2:
                raise ValueError("Unexpected matrix size")
            width = 512 * 1024

            def chunk(start):
                end = min(start + width, size) - 1
                with get({"Range": f"bytes={start}-{end}"}) as response:
                    if response.headers.get("Content-Range") != f"bytes {start}-{end}/{size}":
                        raise ValueError("Unexpected GEO byte range")
                    block = response.read(width + 1)
                    if len(block) != end - start + 1:
                        raise ValueError("Incomplete GEO byte range")
                    return block

            with ThreadPoolExecutor(max_workers=10) as workers, temp.open("wb") as out:
                for block in workers.map(chunk, range(0, size, width)):
                    out.write(block)
            return
        except (OSError, ValueError):
            pass
    with get() as response, temp.open("wb") as out:
        shutil.copyfileobj(response, out)


def ensure_inputs(folder: Path):
    rows = (folder / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    downloads = 0
    for row in rows:
        expected, name = row.split(maxsplit=1)
        if not re.fullmatch(r"GSM4219576_Run2_[a-zA-Z0-9_.]+\.gz", name):
            raise ValueError("Unexpected file in GEO checksum list")
        path = folder / name
        if path.is_file() and sha256(path) == expected:
            continue
        source_name = "GSM4219576_Run2_matrix.mtx.gz" if name == MATRIX else name
        temp = path.with_suffix(".part")
        fetch(SOURCE + quote(source_name), temp)
        if sha256(temp) != expected:
            raise ValueError(f"GEO 文件指纹不符：{name}")
        temp.replace(path)
        downloads += 1
    return {"verified": len(rows), "downloaded": downloads}
