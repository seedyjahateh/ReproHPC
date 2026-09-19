# Contributing

Read PRD.md and TASKS.md before changing behavior. Keep science in testable Python functions and site policy in Nextflow configuration. Add a meaningful regression test for changed scientific, integrity, cache, or failure behavior. Run the commands in [testing](docs/testing.md); record actual results and environment limitations.

Do not commit SIFs, caches, credentials, or private operational logs. Fixtures are synthetic and intentionally small. Keep manifests, hashes, independent goldens, schemas, and documentation consistent. A changed scientific byte requires a new immutable artifact version. Do not overwrite historical fixture releases once published.

Submit changes with the problem, resulting behavior, and verification evidence. Release changes require the full [release procedure](docs/release.md). An external scientific or usability review is reported separately from an author self-test.
