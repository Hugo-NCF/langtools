# Test corpus sources and licenses

## Committed files (`tests/data/`)

| File | Source | License |
|---|---|---|
| `minimal.eaf` | The example in REIMPLEMENTATION.md §3.1.4 | Same as this repository |
| `edge_cases.eaf` | Written for this project: every stereotype, nested subdivision with unaligned slots, multilingual CV, external CV, lexicon ref, ref links, licenses, unknown elements and attributes, comments, empty and multi-line values, non-BMP and RTL text | Same as this repository |
| `old_cv_2_7.eaf` | Written for this project in the EAF 2.7 form (monolingual CVs) | Same as this repository |
| `problems.eaf` | Written for this project: overlaps, a zero-length annotation, decreasing time order, an unused slot, two associations for one parent, a dangling reference, an unknown tier type | Same as this repository |
| `nested_subdivision_4.eaf` | Hand-made for elan-ng tests, same license as the repo | Same as this repository |
| `format_2_8.eaf` | Hand-made for elan-ng tests, same license as the repo | Same as this repository |

## Downloaded files (`tests/corpus/external/`, not committed; run `fetch.py`)

| File | Source | License |
|---|---|---|
| `pympi_sample_2.7.eaf`, `pympi_sample_2.8.eaf`, `pympi_sample_3.0.eaf` | Test files of [pympi](https://github.com/dopefishh/pympi) (commit `e9eccc4`), saved by ELAN. 8,778 annotations on six tiers covering all four stereotypes, with 1,572 unaligned time slots. | The pympi repository is MIT-licensed. The files carry an MPI demo-material license (`<LICENSE LICENSE_URL="http://www.mpi.nl/license.txt">`), which is why they are downloaded instead of committed. |
| `EAFv3.0.xsd` | <https://www.mpi.nl/tools/elan/EAFv3.0.xsd> | MPI. Used only to validate our output. |

More real-world files (ELAN's own sample files, open archive material) are to be added as their licenses are checked (REIMPLEMENTATION.md §6.2).
