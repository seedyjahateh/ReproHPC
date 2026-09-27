"""DICOM de-identification for research ingest. This is not a HIPAA compliance claim.

What this does: applies an explicit, auditable tag policy to a DICOM series, replaces patient
identifiers with a salted pseudonym, remaps UIDs deterministically, drops every private
element, and then re-reads what it wrote and refuses to continue if any original identifier
or UID string survives anywhere in the output. The pseudonym mapping is written outside the
de-identified output, and the salt lives only in that mapping.

What this does not do: it does not inspect pixel data for burned-in identifiers, it does not
implement a DICOM PS3.15 confidentiality profile, and it is not a substitute for an
institution's approved de-identification process or for any legal determination. Removing
tags is one part of de-identification, not the whole of it.
"""

import hashlib
import hmac
import json
import re
import secrets
from pathlib import Path

from .errors import ReproError
from .io import write_json

# Patient identity: replaced by the pseudonym, so the series stays linkable within itself.
REPLACED = ("PatientName", "PatientID")
# Kept present but zero-length: DICOM requires these attributes to exist (type 2).
EMPTIED = (
    "PatientBirthDate",
    "PatientSex",
    "ReferringPhysicianName",
    "AccessionNumber",
    "StudyID",
)
# Every date, datetime and time element is emptied by value representation, wherever it sits.
# Naming them was not enough: the first real series kept its acquisition date in
# InstanceCreationDate, which the named list missed. Dates are removed rather than shifted, so
# relative timing does not survive; an analysis that needs it would need a date-shift policy.
DATE_VRS = ("DA", "DT", "TM")
# Optional attributes (type 3) that identify a person, a place, or a machine: deleted.
REMOVED = (
    "PatientAge",
    "PatientWeight",
    "PatientSize",
    "PatientAddress",
    "PatientTelephoneNumbers",
    "PatientMotherBirthName",
    "OtherPatientIDs",
    "OtherPatientIDsSequence",
    "OtherPatientNames",
    "EthnicGroup",
    "Occupation",
    "AdditionalPatientHistory",
    "PatientComments",
    "MedicalRecordLocator",
    "InstitutionName",
    "InstitutionAddress",
    "InstitutionalDepartmentName",
    "StationName",
    "DeviceSerialNumber",
    "PerformingPhysicianName",
    "PhysiciansOfRecord",
    "NameOfPhysiciansReadingStudy",
    "OperatorsName",
    "RequestingPhysician",
    "RequestingService",
    "RequestedProcedureDescription",
    "RequestAttributesSequence",
    "PerformedProcedureStepID",
    "PerformedProcedureStepStartDate",
    "PerformedProcedureStepStartTime",
    "PerformedProcedureStepDescription",
    "ScheduledProcedureStepID",
    "ScheduledProcedureStepStartDate",
    "ScheduledProcedureStepStartTime",
    "ScheduledProcedureStepDescription",
    "IssuerOfPatientID",
    "CurrentPatientLocation",
    "PatientInstitutionResidence",
    # Free text that named the facility in the first real series ("FMRIF^QA").
    "StudyDescription",
    "PerformedProcedureStepDescription",
    "PerformedProtocolCodeSequence",
)
# Every UID-valued element (VR "UI") is remapped, wherever it sits, including inside
# sequences. Enumerating UID keywords was not enough: the first real series embedded the
# scanner's serial number in UIDs this list did not name. These are the exceptions, which
# identify a standard rather than a site: they must keep their registered values.
KEPT_UIDS = (
    "SOPClassUID",
    "MediaStorageSOPClassUID",
    "TransferSyntaxUID",
    "ImplementationClassUID",
)
# Named here only so the audit and the documentation stay readable; the rule is by VR.
UID_TAGS = (
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SOPInstanceUID",
    "FrameOfReferenceUID",
)
# File meta (group 0002) identifies the sending system, not the image. The first real series
# processed carried the scanner's station name here as well as in StationName, which the
# dataset-only policy missed and the re-read check caught.
FILE_META_REMOVED = (
    "SourceApplicationEntityTitle",
    "SendingApplicationEntityTitle",
    "ReceivingApplicationEntityTitle",
    "PrivateInformation",
    "PrivateInformationCreatorUID",
)
# Value representation LO caps at 64 characters, so this string must stay short.
METHOD = "ReproHPC deident.py tag policy (not DICOM PS3.15)"


def new_salt() -> str:
    return secrets.token_hex(32)


def pseudonym(salt: str, *parts: str) -> str:
    """Stable per salt and input; different salts give unrelated pseudonyms."""
    digest = hmac.new(salt.encode(), "|".join(parts).encode(), hashlib.sha256).hexdigest()
    return f"sub-{digest[:12]}"


def remapped_uid(salt: str, original: str) -> str:
    """A fresh UID under the 2.25 OID arc, derived from the salt and the original."""
    digest = hmac.new(salt.encode(), original.encode(), hashlib.sha256).digest()
    return f"2.25.{int.from_bytes(digest[:16], 'big')}"[:64]


# Substring scanning needs a value long enough to be meaningful: a short one like the sample
# series' PatientID "DEV" matches inside ordinary words such as "DEVICE". Shorter values are
# covered by the exact tag checks in verify() instead.
MIN_SCAN_LENGTH = 5


def _identifying_values(data) -> set:
    """The strings that must not appear anywhere in the output."""
    values = set()
    names = (*REPLACED, *UID_TAGS, "InstitutionName", "InstitutionAddress", "StationName",
             "DeviceSerialNumber", "AccessionNumber", "PatientBirthDate", "StudyDate",
             "SeriesDate", "AcquisitionDate", "InstanceCreationDate")  # fmt: skip
    for name in names:
        if name in data:
            text = str(data[name].value).strip()
            if len(text) >= MIN_SCAN_LENGTH:
                values.add(text)
    return values


def datasets(data):
    """The dataset itself and every dataset nested inside its sequences."""
    yield data
    for element in data:
        if element.VR == "SQ":
            for item in element.value or []:
                yield from datasets(item)


def deidentify(data, salt: str, subject: str | None = None):
    """Apply the policy to one dataset in place; return the audit and the pseudonym."""
    import pydicom

    original = {name: str(data[name].value) for name in (*REPLACED, *UID_TAGS) if name in data}
    original.update(
        {
            element.keyword: str(element.value)
            for element in data.iterall()
            if element.VR == "UI" and element.keyword and element.keyword not in KEPT_UIDS
        }
    )
    label = subject or pseudonym(
        salt, original.get("PatientID", ""), original.get("PatientName", "")
    )
    audit = []
    # Sequences carry copies of the same attributes, so every rule below runs on the top-level
    # dataset and on each nested item. The first real series hid a facility name in a sequence.
    for item in datasets(data):
        for element in list(item):
            if element.tag.is_private:
                del item[element.tag]
                audit.append(
                    {
                        "tag": str(element.tag),
                        "keyword": element.keyword or "",
                        "action": "private-removed",
                    }
                )
        for name in REMOVED:
            if name in item:
                audit.append({"tag": str(item[name].tag), "keyword": name, "action": "removed"})
                del item[name]
        for name in EMPTIED:
            if name in item:
                item[name].value = ""
                audit.append({"tag": str(item[name].tag), "keyword": name, "action": "emptied"})
        for name in REPLACED:
            if name in item:
                item[name].value = label
                audit.append(
                    {
                        "tag": str(item[name].tag),
                        "keyword": name,
                        "action": "replaced-with-pseudonym",
                    }
                )
    for element in data.iterall():
        if element.VR in DATE_VRS and element.value:
            element.value = ""
            audit.append(
                {
                    "tag": str(element.tag),
                    "keyword": element.keyword or "",
                    "action": "date-emptied",
                }
            )
    for element in data.iterall():
        if element.VR == "UI" and element.keyword not in KEPT_UIDS and element.value:
            values = element.value if element.VM > 1 else [element.value]
            element.value = (
                [remapped_uid(salt, str(v)) for v in values]
                if element.VM > 1
                else remapped_uid(salt, str(element.value))
            )
            audit.append(
                {
                    "tag": str(element.tag),
                    "keyword": element.keyword or "",
                    "action": "uid-remapped",
                }
            )
    if hasattr(data, "file_meta"):
        if "MediaStorageSOPInstanceUID" in data.file_meta:
            data.file_meta.MediaStorageSOPInstanceUID = data.SOPInstanceUID
        for name in FILE_META_REMOVED:
            if name in data.file_meta:
                audit.append(
                    {
                        "tag": str(data.file_meta[name].tag),
                        "keyword": name,
                        "action": "file-meta-removed",
                    }
                )
                del data.file_meta[name]
    data.PatientIdentityRemoved = "YES"
    data.DeidentificationMethod = METHOD
    kept = [
        {"tag": str(e.tag), "keyword": e.keyword or "", "action": "kept"}
        for e in data
        if e.keyword not in ("PatientIdentityRemoved", "DeidentificationMethod")
        and e.keyword not in REPLACED
        and e.keyword not in EMPTIED
        and e.keyword not in UID_TAGS
        and e.VR != "OW"
        and e.keyword != "PixelData"
    ]
    _ = pydicom  # imported for its side effect of validating the install
    return label, audit + kept, original


def verify(path: Path, forbidden: set, label: str):
    """Re-read a written file and fail if anything identifying survived."""
    import pydicom

    data = pydicom.dcmread(path)
    if str(getattr(data, "PatientIdentityRemoved", "")) != "YES":
        raise ReproError(f"{path.name}: PatientIdentityRemoved is not set", 4)
    for name in REPLACED:
        if name in data and str(data[name].value) != label:
            raise ReproError(f"{path.name}: {name} is not the pseudonym", 4)
    for item in datasets(data):
        for name in REMOVED:
            if name in item:
                raise ReproError(f"{path.name}: {name} survived de-identification", 4)
        if any(element.tag.is_private for element in item):
            raise ReproError(f"{path.name}: private elements survived de-identification", 4)
    for name in FILE_META_REMOVED:
        if name in data.file_meta:
            raise ReproError(f"{path.name}: file meta {name} survived de-identification", 4)
    for element in data.iterall():
        if element.VR in DATE_VRS and element.value:
            raise ReproError(f"{path.name}: {element.keyword or element.tag} kept a date", 4)
    text = "\n".join(
        str(element.value) for element in data.iterall() if element.keyword != "PixelData"
    )
    text += "\n" + "\n".join(str(v) for v in data.file_meta.values())
    for value in forbidden:
        if re.search(re.escape(value), text):
            raise ReproError(
                f"{path.name}: original identifier or UID survived: {value[:24]}...", 4
            )


def deidentify_series(source: Path, destination: Path, mapping_path: Path, salt: str | None = None):
    """De-identify every DICOM under `source` into `destination`, then verify what was written.

    The mapping is the only link back to the original identity, so it must not live inside the
    de-identified output.
    """
    import pydicom

    destination = destination.resolve()
    mapping_path = mapping_path.resolve()
    if mapping_path.is_relative_to(destination):
        raise ReproError("The pseudonym mapping must not be written inside the output", 2)
    if destination.exists() and any(destination.iterdir()):
        raise ReproError(f"Output directory is not empty: {destination}", 2)
    files = sorted(p for p in source.rglob("*") if p.is_file() and p.suffix.lower() == ".dcm")
    if not files:
        raise ReproError(f"No DICOM files under {source}", 2)
    salt = salt or new_salt()
    entries, audits, label = {}, None, None
    for path in files:
        data = pydicom.dcmread(path)
        forbidden = _identifying_values(data)
        label, audit, original = deidentify(data, salt, label)
        target = destination / f"{label}_{path.stem}.dcm"
        target.parent.mkdir(parents=True, exist_ok=True)
        data.save_as(target, enforce_file_format=True)
        verify(target, forbidden, label)
        audits = audits or audit
        entries.setdefault(label, {"pseudonym": label, "source": str(source), "originals": {}})
        for name, value in original.items():
            entries[label]["originals"].setdefault(name, set()).add(value)
    for entry in entries.values():
        entry["originals"] = {k: sorted(v) for k, v in entry["originals"].items()}
    write_json(
        mapping_path,
        {
            "schema_version": "1.0.0",
            "warning": "Re-identification key. Keep outside any shared archive.",
            "salt": salt,
            "method": METHOD,
            "subjects": list(entries.values()),
        },
    )
    return {
        "files": len(files),
        "pseudonym": label,
        "mapping": str(mapping_path),
        "audit": audits,
        "counts": _summarise(audits),
    }


def _summarise(audit):
    counts = {}
    for entry in audit:
        counts[entry["action"]] = counts.get(entry["action"], 0) + 1
    return dict(sorted(counts.items()))


def convert_to_nifti(source: Path, destination: Path, name: str):
    """dcm2niix on de-identified DICOM only. `-ba y` also anonymises the BIDS sidecar."""
    from .mri import run, tool

    destination.mkdir(parents=True, exist_ok=True)
    output = run(
        [
            str(tool("dcm2niix")),
            "-o",
            str(destination),
            "-f",
            name,
            "-z",
            "y",
            "-b",
            "y",
            "-ba",
            "y",
            str(source),
        ]  # fmt: skip
    )
    written = sorted(p.name for p in destination.iterdir())
    if not any(n.endswith(".nii.gz") for n in written):
        raise ReproError(f"dcm2niix wrote no NIfTI: {output[-500:]}", 4)
    return {"files": written, "dcm2niix_output": output.strip().splitlines()[-3:]}


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="De-identify a DICOM series for research ingest.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True, help="written outside --output")
    parser.add_argument("--audit", type=Path, help="write the per-tag audit here")
    parser.add_argument("--salt-file", type=Path, help="reuse a salt so pseudonyms stay stable")
    parser.add_argument("--convert", type=Path, help="also convert to NIfTI here with dcm2niix")
    args = parser.parse_args(argv)
    salt = (
        args.salt_file.read_text().strip() if args.salt_file and args.salt_file.is_file() else None
    )
    try:
        result = deidentify_series(args.input, args.output, args.mapping, salt)
    except ReproError as exc:
        print(str(exc))
        return exc.code
    if args.convert:
        # Conversion reads the de-identified copies, never the originals.
        try:
            result["conversion"] = convert_to_nifti(args.output, args.convert, result["pseudonym"])
        except ReproError as exc:
            print(str(exc))
            return exc.code
    if args.audit:
        write_json(args.audit, {"schema_version": "1.0.0", "method": METHOD, **result})
    print(json.dumps({k: v for k, v in result.items() if k != "audit"}, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
