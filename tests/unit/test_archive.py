import io
import tarfile

import pytest

from reprohpc.archive import extract, pack, validate_release
from reprohpc.errors import ReproError


def test_archive_deterministic_and_restorable(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("hello")
    pack(source, tmp_path / "one.tar.gz")
    pack(source, tmp_path / "two.tar.gz")
    assert (tmp_path / "one.tar.gz").read_bytes() == (tmp_path / "two.tar.gz").read_bytes()
    extract(tmp_path / "one.tar.gz", tmp_path / "out")
    assert (tmp_path / "out/a.txt").read_text() == "hello"


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", tarfile.REGTYPE),
        ("link", tarfile.SYMTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("device", tarfile.CHRTYPE),
    ],
)
def test_hostile_archive(tmp_path, name, kind):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.size = 0
        tar.addfile(info, io.BytesIO())
    with pytest.raises(ReproError):
        extract(archive, tmp_path / "out")
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "out").exists()


def test_unpublished_release_is_rejected(tmp_path):
    file = tmp_path / "release.json"
    file.write_text("{}")
    with pytest.raises(ReproError):
        validate_release(file)
