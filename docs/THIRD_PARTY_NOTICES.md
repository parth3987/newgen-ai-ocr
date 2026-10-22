# Third-Party Notices

Generated from `scripts/check_licenses.py` against `requirements.lock` on 2026-10-22.
Update this file whenever a lock file changes.

## Runtime (shipped)

| Package | Version | License |
|---|---|---|
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| fastapi | 0.143.0 | MIT |
| h11 | 0.16.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| opentelemetry-api | 1.45.1 | Apache-2.0 (see ADR-0008) |
| pillow | 13.0.0 | MIT-CMU (security override, see ADR-0007) |
| pydantic | 2.14.0 | MIT |
| pydantic-core | 2.50.0 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| pypdfium2 | 5.14.0 | BSD-3-Clause / Apache-2.0, plus bundled PDFium dependency licenses (below) |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| starlette | 1.7.0 | BSD-3-Clause |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |

Full license texts are included in each package's distribution metadata and will be collected
into the offline bundle (Phase 10).

### pypdfium2 bundled PDFium binary (reviewed 2026-10-22)

The wheel ships `libpdfium.so` built from PDFium and these components. Their license texts are
in `pypdfium2-5.14.0.dist-info/licenses/`.

| Component | License |
|---|---|
| PDFium | BSD-3-Clause |
| pdfium-binaries build | MIT |
| Abseil, llvm-libc | Apache-2.0 (llvm-libc: with LLVM exception) |
| Anti-Grain Geometry 2.3 | AGG permissive notice |
| FreeType | FreeType License (FTL) |
| ICU | Unicode License |
| Little CMS | MIT |
| libjpeg-turbo | IJG + BSD-3-Clause + zlib |
| OpenJPEG | BSD-2-Clause |
| libpng | libpng license |
| zlib | zlib |
| simdutf, fast_float | MIT / Apache-2.0 |
| pypdfium2 data files | CC-BY-4.0 (documentation/data only) |

No copyleft licenses are present. **Attribution obligations for distributed products:**
- FreeType (FTL): "Portions of this software are copyright © The FreeType Project
  (www.freetype.org). All rights reserved."
- libjpeg-turbo (IJG): "This software is based in part on the work of the Independent JPEG
  Group."
- ICU / Unicode: include the Unicode license notice.

## Development and CI only (not shipped)

pytest, pytest-cov, coverage, httpx, ruff, mypy, bandit, pip-audit, pip-licenses and their
dependencies, as pinned in `requirements-dev.lock` (`certifi` is a security override; see
ADR-0007). Container image used in CI: `ghcr.io/gitleaks/gitleaks:v8.21.2` (MIT).

## Models

None. See MODEL_INVENTORY.md.
