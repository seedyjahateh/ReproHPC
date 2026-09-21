# Third-party software and data

ReproHPC code is MIT licensed; see LICENSE. The bundled `demo` demonstration is generated synthetic data, not an imported scientific dataset.

| Dependency | License | Upstream |
|---|---|---|
| Python | PSF-2.0 | https://www.python.org/ |
| NumPy | BSD-3-Clause | https://numpy.org/ |
| OpenCV | Apache-2.0 | https://opencv.org/ |
| opencv-python packaging | MIT; bundled component notices apply | https://github.com/opencv/opencv-python |
| PyYAML | MIT | https://pyyaml.org/ |
| jsonschema and referencing | MIT | https://github.com/python-jsonschema |
| attrs | MIT | https://www.attrs.org/ |
| rpds-py | MIT | https://github.com/crate-py/rpds |
| Nextflow | Apache-2.0 | https://github.com/nextflow-io/nextflow |
| Apptainer | BSD-3-Clause | https://github.com/apptainer/apptainer |
| Slurm | GPL-2.0-or-later | https://slurm.schedmd.com/ |

Ubuntu packages retain their installed copyright notices in `/usr/share/doc`; Python wheels retain `.dist-info/licenses`. The analysis SIF does not bundle Slurm or Nextflow. The disposable lab does. Final release license review must include these notices and the resolved transitive package inventory.

## FSL (optional neuroimaging image)

`containers/Dockerfile.fsl` installs a minimal subset of FSL 6.0.7.23 (`fsl-bet2`, `fsl-avwutils` and their dependencies) from the FSL conda channel, pinned by `containers/fsl-6.0.7.23-linux-64.lock`. FSL is developed by the FMRIB Analysis Group, University of Oxford, and most of it is licensed under the **FSL Free for Non-Commercial Purposes License**: use for which any financial return is received is commercial use and needs a separate licence from Oxford University Innovation (fsl@innovation.ox.ac.uk). Some FSL components and bundled libraries carry their own open-source licences. Source: [FSL licence](https://fsl.fmrib.ox.ac.uk/fsl/docs/license.html).

The licence restricts redistribution: reproducing or transferring the software without the University's permission is allowed only without financial return, with the licence conditions imposed on the recipient, and with all original and amended source code included. This repository therefore never distributes FSL. It contains only the Dockerfile and the lockfile; the FSL image and SIF are built locally and are never committed or attached to a release. Publishing an FSL-containing image would first require bundling the corresponding source and licence, or Oxford's permission. When publishing results computed with FSL, cite the tools used, for BET: S.M. Smith, "Fast robust automated brain extraction", Human Brain Mapping 17(3):143-155, 2002.

## Optional imported data

The repository imports no scientific dataset. `scripts/fetch_bbbc039.py` optionally downloads image set BBBC039v1 from the Broad Bioimage Benchmark Collection at the user's request and converts it locally; neither the archives nor the converted images are redistributed here. That image set is dedicated to the public domain under CC0 1.0 by its contributors, and the collection asks to be cited: "We used image set BBBC039v1 Caicedo et al. 2018, available from the Broad Bioimage Benchmark Collection [Ljosa et al., Nature Methods, 2012]." Licences in that collection are assigned per image set and several are CC BY or CC BY-NC-SA, so record the terms of any further set before using it. Source: [BBBC039](https://bbbc.broadinstitute.org/BBBC039). See [real microscopy data](docs/real-data.md).

The unmodified Citation File Format 1.2.0 validation schema in `tests/vendor/cff-1.2.0/` is by the Citation File Format contributors and distributed under CC BY 4.0. Its upstream license text and source URLs/checksums are retained alongside it. Source: [Citation File Format 1.2.0](https://github.com/citation-file-format/citation-file-format/tree/1.2.0).
