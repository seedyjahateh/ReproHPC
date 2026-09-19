"""Download protocol unit tests; response doubles are not public DOI evidence."""

import io

import pytest

from reprohpc import archive
from reprohpc.errors import ReproError
from reprohpc.io import sha256


@pytest.fixture
def transfer(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "content.txt").write_text("immutable archive content")
    packed = tmp_path / "source.tar.gz"
    archive.pack(source, packed)
    sif = tmp_path / "protocol-bytes"
    sif.write_bytes(b"unit-only arbitrary transfer bytes; not an executable SIF")
    payloads, lock = {}, {}
    for kind in ("source", "sif", "data", "reference", "expected"):
        path = sif if kind == "sif" else packed
        url = f"https://unit.invalid/{kind}"
        payloads[url] = path.read_bytes()
        lock[kind] = {"url": url, "size_bytes": path.stat().st_size, "sha256": sha256(path)}
    # Publication metadata is deliberately outside this protocol unit test.
    monkeypatch.setattr(archive, "validate_release", lambda path: lock)
    return lock, payloads


class Response(io.BytesIO):
    def __init__(self, payload, url):
        super().__init__(payload)
        self.url = url

    def geturl(self):
        return self.url


def test_verified_download_cache_and_reextraction(tmp_path, monkeypatch, transfer):
    lock, payloads = transfer
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        return Response(payloads[url], url)

    monkeypatch.setattr(archive.urllib.request, "urlopen", fetch)
    _, first = archive.prepare(tmp_path / "protocol-lock", tmp_path / "cache")
    from pathlib import Path

    (Path(first["source"]) / "content.txt").write_text("locally modified")
    count = len(calls)
    _, second = archive.prepare(tmp_path / "protocol-lock", tmp_path / "cache")
    assert len(calls) == count
    assert first["source"] != second["source"]
    assert (Path(second["source"]) / "content.txt").read_text() == "immutable archive content"
    assert sha256(Path(second["sif"])) == lock["sif"]["sha256"]
    Path(second["sif"]).write_bytes(b"corrupted cache")
    with pytest.raises(ReproError, match="Cached artifact checksum"):
        archive.prepare(tmp_path / "protocol-lock", tmp_path / "cache")


@pytest.mark.parametrize("mode", ["redirect", "oversize", "corrupt", "truncated"])
def test_rejects_bad_download_and_cleans_partial(tmp_path, monkeypatch, transfer, mode):
    _, payloads = transfer

    def fetch(url, timeout):
        payload = payloads[url]
        if mode == "oversize":
            payload += b"extra"
        elif mode == "corrupt":
            payload = bytes([payload[0] ^ 1]) + payload[1:]
        elif mode == "truncated":
            payload = payload[:-1]
        return Response(payload, "http://unit.invalid/insecure" if mode == "redirect" else url)

    monkeypatch.setattr(archive.urllib.request, "urlopen", fetch)
    with pytest.raises(ReproError):
        archive.prepare(tmp_path / "protocol-lock", tmp_path / "cache")
    assert not list((tmp_path / "cache").rglob("download.partial"))


def test_extraction_size_limit(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload").write_bytes(b"longer than allowed")
    packed = tmp_path / "archive.tar.gz"
    archive.pack(source, packed)
    with pytest.raises(ReproError, match="extraction limit"):
        archive.extract(packed, tmp_path / "destination", limit_bytes=1)
    assert not (tmp_path / "destination").exists()
