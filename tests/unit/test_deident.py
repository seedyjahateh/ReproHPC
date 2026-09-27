"""DICOM de-identification against synthetic datasets carrying fabricated identifiers.

Every identifier here is invented for the test. The policy is checked by asserting on what
survives in the written files, not by trusting the function's own report.
"""

import json

import pytest

pydicom = pytest.importorskip("pydicom")

from pydicom.dataset import Dataset, FileMetaDataset  # noqa: E402
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian  # noqa: E402

from reprohpc import deident  # noqa: E402
from reprohpc.errors import ReproError  # noqa: E402
from reprohpc.io import read_json  # noqa: E402

PHI = {
    "PatientName": "Doe^Jane^^Dr",
    "PatientID": "MRN-0099881",
    "PatientBirthDate": "19640712",
    "PatientSex": "F",
    "PatientAge": "061Y",
    "PatientWeight": "70",
    "PatientAddress": "14 Example Street, Springfield",
    "PatientTelephoneNumbers": "555-0100",
    "OtherPatientNames": "Doe^Janet",
    "EthnicGroup": "declined",
    "PatientComments": "follow-up for Ms Doe",
    "InstitutionName": "Example General Hospital",
    "InstitutionAddress": "1 Hospital Way, Springfield",
    "InstitutionalDepartmentName": "Neuroradiology",
    "ReferringPhysicianName": "Smith^Alan",
    "PerformingPhysicianName": "Ng^Wei",
    "OperatorsName": "Tech^Sam",
    "RequestingPhysician": "Smith^Alan",
    "StationName": "MR-SCANNER-03",
    "DeviceSerialNumber": "SN-445566",
    "AccessionNumber": "ACC-7781",
    "StudyID": "STU-42",
    "StudyDate": "20250114",
    "StudyTime": "104500.000",
    "PerformedProcedureStepID": "PPS-9",
    "RequestedProcedureDescription": "MRI brain for Ms Doe",
}
KEEP = {
    "Modality": "MR",
    "Manufacturer": "ACME",
    "ManufacturerModelName": "Scanner 3T",
    "SoftwareVersions": "v1.2",
    "ProtocolName": "T1w MPRAGE",
    "SeriesDescription": "T1w",
    "BodyPartExamined": "BRAIN",
    "SliceThickness": "1.0",
    "SeriesNumber": "4",
}
UIDS = {
    "StudyInstanceUID": "1.2.826.0.1.3680043.8.498.10000001",
    "SeriesInstanceUID": "1.2.826.0.1.3680043.8.498.10000002",
    "FrameOfReferenceUID": "1.2.826.0.1.3680043.8.498.10000004",
}


def synthetic(path, instance=1):
    data = Dataset()
    for name, value in {**PHI, **KEEP, **UIDS}.items():
        setattr(data, name, value)
    data.SOPInstanceUID = f"1.2.826.0.1.3680043.8.498.2000000{instance}"
    data.SOPClassUID = CTImageStorage
    data.InstanceNumber = str(instance)
    data.Rows, data.Columns = 2, 2
    data.BitsAllocated, data.BitsStored, data.HighBit = 8, 8, 7
    data.SamplesPerPixel, data.PhotometricInterpretation = 1, "MONOCHROME2"
    data.PixelRepresentation = 0
    data.PixelData = bytes([1, 2, 3, 4])
    # A date element outside the named list, as real scanner files carry.
    data.InstanceCreationDate = PHI["StudyDate"]
    data.InstanceCreationTime = "104501.000"
    # A private block of the kind Siemens uses for its CSA headers.
    block = data.private_block(0x0019, "ACME CSA", create=True)
    block.add_new(0x10, "LO", "patient Doe^Jane scanned by Tech^Sam")
    data.file_meta = FileMetaDataset()
    data.file_meta.MediaStorageSOPClassUID = CTImageStorage
    data.file_meta.MediaStorageSOPInstanceUID = data.SOPInstanceUID
    data.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    # Real scanner files carry the sending station here as well as in StationName.
    data.file_meta.SourceApplicationEntityTitle = PHI["StationName"]
    path.parent.mkdir(parents=True, exist_ok=True)
    data.save_as(path, enforce_file_format=True)
    return path


@pytest.fixture
def series(tmp_path):
    for index in (1, 2, 3):
        synthetic(tmp_path / "raw" / f"slice{index}.dcm", index)
    return tmp_path


def run(tmp_path, **kwargs):
    return deident.deidentify_series(
        tmp_path / "raw", tmp_path / "clean", tmp_path / "keys/mapping.json", **kwargs
    )


def test_every_identifier_is_gone_from_the_written_files(series):
    result = run(series)
    assert result["files"] == 3
    written = sorted((series / "clean").glob("*.dcm"))
    assert len(written) == 3
    for path in written:
        raw = path.read_bytes()
        for name, value in PHI.items():
            # Values shorter than this match by chance inside UID digits; those tags are
            # checked by absence below instead.
            if len(value) >= deident.MIN_SCAN_LENGTH:
                assert value.encode() not in raw, f"{name} survived in {path.name}"
        for value in UIDS.values():
            assert value.encode() not in raw
        data = pydicom.dcmread(path)
        for name in PHI:
            assert name not in data or str(data[name].value) in ("", result["pseudonym"]), (
                f"{name} still carries a value in {path.name}"
            )
        assert data.PatientIdentityRemoved == "YES"
        assert str(data.PatientName) == result["pseudonym"]
        assert data.PatientID == result["pseudonym"]
        assert data.PatientBirthDate == ""
        assert not [element for element in data if element.tag.is_private]
        for name in ("InstitutionName", "StationName", "DeviceSerialNumber", "PatientAddress"):
            assert name not in data


def test_every_date_and_time_element_is_emptied(series):
    """Regression: the first real series kept its acquisition date in InstanceCreationDate,
    which the named-tag list missed."""
    run(series)
    for path in sorted((series / "clean").glob("*.dcm")):
        data = pydicom.dcmread(path)
        for element in data.iterall():
            assert element.VR not in deident.DATE_VRS or not element.value, (
                f"{element.keyword or element.tag} kept {element.value!r}"
            )
        assert PHI["StudyDate"].encode() not in path.read_bytes()


def test_file_meta_identifiers_are_removed(series):
    """Regression: the first real series leaked its station name through file meta."""
    result = run(series)
    for path in sorted((series / "clean").glob("*.dcm")):
        meta = pydicom.dcmread(path).file_meta
        assert "SourceApplicationEntityTitle" not in meta
        assert PHI["StationName"].encode() not in path.read_bytes()
    assert any(e["action"] == "file-meta-removed" for e in result["audit"])
    assert len(deident.METHOD) <= 64, "DeidentificationMethod is VR LO, capped at 64 characters"


def test_scientific_attributes_and_pixels_are_kept(series):
    run(series)
    data = pydicom.dcmread(next((series / "clean").glob("*.dcm")))
    for name, value in KEEP.items():
        assert str(data[name].value) == value
    assert data.PixelData == bytes([1, 2, 3, 4])
    assert data.Rows == 2 and data.Columns == 2


def test_uids_are_remapped_consistently_and_stay_valid(series):
    run(series)
    written = sorted((series / "clean").glob("*.dcm"))
    studies = {pydicom.dcmread(p).StudyInstanceUID for p in written}
    instances = {pydicom.dcmread(p).SOPInstanceUID for p in written}
    assert len(studies) == 1, "one study must remain one study"
    assert len(instances) == 3, "distinct instances must stay distinct"
    for path in written:
        data = pydicom.dcmread(path)
        assert data.StudyInstanceUID.startswith("2.25.")
        assert len(data.StudyInstanceUID) <= 64
        assert data.file_meta.MediaStorageSOPInstanceUID == data.SOPInstanceUID


def test_pseudonyms_depend_on_the_salt(series, tmp_path):
    first = run(series)
    mapping = read_json(series / "keys/mapping.json")
    assert mapping["salt"] and len(mapping["salt"]) >= 32
    assert mapping["subjects"][0]["originals"]["PatientID"] == [PHI["PatientID"]]
    same = deident.pseudonym(mapping["salt"], PHI["PatientID"], PHI["PatientName"])
    assert same == first["pseudonym"]
    assert deident.pseudonym("a-different-salt", PHI["PatientID"], PHI["PatientName"]) != same
    assert deident.remapped_uid("salt-one", "1.2.3") != deident.remapped_uid("salt-two", "1.2.3")
    assert deident.remapped_uid("salt-one", "1.2.3") == deident.remapped_uid("salt-one", "1.2.3")


def test_the_mapping_is_the_only_link_and_never_sits_in_the_output(series):
    run(series)
    assert (series / "keys/mapping.json").is_file()
    assert not list((series / "clean").rglob("*.json"))
    text = (series / "keys/mapping.json").read_text()
    assert PHI["PatientID"] in text and "Keep outside any shared archive" in text
    with pytest.raises(ReproError, match="must not be written inside the output"):
        deident.deidentify_series(series / "raw", series / "out2", series / "out2/keys.json")


def test_the_audit_names_what_was_removed_and_what_was_kept(series, tmp_path):
    result = run(series)
    actions = {}
    for entry in result["audit"]:
        actions.setdefault(entry["action"], set()).add(entry["keyword"])
    assert "InstitutionName" in actions["removed"] and "DeviceSerialNumber" in actions["removed"]
    assert "PatientBirthDate" in actions["emptied"]
    assert {"StudyDate", "InstanceCreationDate"} <= actions["date-emptied"]
    assert actions["replaced-with-pseudonym"] == {"PatientName", "PatientID"}
    assert "StudyInstanceUID" in actions["uid-remapped"]
    assert actions["private-removed"], "the private block must appear in the audit"
    assert "ProtocolName" in actions["kept"] and "Manufacturer" in actions["kept"]
    assert result["counts"]["removed"] >= 10


def test_verification_rejects_a_file_that_kept_an_identifier(series, monkeypatch):
    """If the policy ever misses a tag, the re-read check must stop the run."""
    original = deident.deidentify

    def leaky(data, salt, subject=None):
        label, audit, first = original(data, salt, subject)
        data.StationName = PHI["StationName"]  # a tag the policy should have removed
        return label, audit, first

    monkeypatch.setattr(deident, "deidentify", leaky)
    with pytest.raises(ReproError, match="StationName survived"):
        run(series)


def test_verification_rejects_a_surviving_original_uid(series, monkeypatch):
    original = deident.deidentify

    def leaky(data, salt, subject=None):
        label, audit, first = original(data, salt, subject)
        data.ProtocolName = UIDS["StudyInstanceUID"]  # leaked into a kept free-text field
        return label, audit, first

    monkeypatch.setattr(deident, "deidentify", leaky)
    with pytest.raises(ReproError, match="original identifier or UID survived"):
        run(series)


def test_empty_and_non_empty_directories_are_refused(tmp_path, series):
    with pytest.raises(ReproError, match="No DICOM files"):
        deident.deidentify_series(tmp_path / "nothing", tmp_path / "o", tmp_path / "m.json")
    run(series)
    with pytest.raises(ReproError, match="not empty"):
        run(series)


def test_command_line_writes_audit_and_mapping(series, capsys):
    code = deident.main(
        [
            "--input", str(series / "raw"),
            "--output", str(series / "cli-clean"),
            "--mapping", str(series / "keys/cli-mapping.json"),
            "--audit", str(series / "keys/audit.json"),
        ]
    )  # fmt: skip
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["files"] == 3 and printed["pseudonym"].startswith("sub-")
    audit = read_json(series / "keys/audit.json")
    assert audit["method"] == deident.METHOD and audit["counts"]["private-removed"] >= 1
