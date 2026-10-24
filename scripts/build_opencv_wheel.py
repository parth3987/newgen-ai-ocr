"""Build the minimal OpenCV for production images (ADR-0015, ADR-0031).

Compiles OpenCV from the opencv-python-headless source distribution (its SHA-256 is checked
by the container build against the hash PyPI publishes) with only the modules this
application and PaddleX's OCR pipelines call: core, imgproc, imgcodecs (PNG and JPEG only)
and photo. No videoio or FFmpeg, no GUI, no OpenSSL, no network-fetched components (IPP,
ADE and the like are switched off, so CMake downloads nothing). Libraries are linked
statically into the ``cv2`` extension.

The result is packaged as a wheel named ``opencv-python-headless`` with the local version
``4.14.0.94+ngocr.minimal``: it satisfies the lock's ``==4.14.0.94`` and replaces the PyPI
wheel in the image. Run inside the builder stage:

    python scripts/build_opencv_wheel.py --source /tmp/opencv --out /wheels
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import subprocess  # nosec B404 - fixed build commands, no shell
import sys
import sysconfig
import zipfile
from pathlib import Path

VERSION = "4.14.0.94+ngocr.minimal"
MODULES = "core,imgproc,imgcodecs,photo,python3,python_bindings_generator"
OFF = [
    "WITH_FFMPEG",
    "WITH_GSTREAMER",
    "WITH_V4L",
    "WITH_1394",
    "WITH_GTK",
    "WITH_QT",
    "WITH_OPENGL",
    "WITH_VTK",
    "WITH_IPP",
    "WITH_ITT",
    "WITH_OPENCL",
    "WITH_OPENCLAMDFFT",
    "WITH_OPENCLAMDBLAS",
    "WITH_VA",
    "WITH_VA_INTEL",
    "WITH_LAPACK",
    "WITH_EIGEN",
    "WITH_PROTOBUF",
    "WITH_ADE",
    "WITH_OPENEXR",
    "WITH_WEBP",
    "WITH_OPENJPEG",
    "WITH_JASPER",
    "WITH_TIFF",
    "WITH_AVIF",
    "WITH_IMGCODEC_HDR",
    "WITH_IMGCODEC_SUNRASTER",
    "WITH_IMGCODEC_PXM",
    "WITH_IMGCODEC_PFM",
    "WITH_GDAL",
    "WITH_GDCM",
    "WITH_QUIRC",
    "WITH_OBSENSOR",
    "WITH_CUDA",
    "WITH_TBB",
    "WITH_HALIDE",
    "WITH_ONNX",
    "WITH_OPENVINO",
    "BUILD_TESTS",
    "BUILD_PERF_TESTS",
    "BUILD_EXAMPLES",
    "BUILD_DOCS",
    "BUILD_opencv_apps",
    "BUILD_JAVA",
    "BUILD_opencv_python2",
    "BUILD_SHARED_LIBS",
    "OPENCV_GENERATE_PKGCONFIG",
    "OPENCV_ENABLE_NONFREE",
    "BUILD_opencv_python_tests",
]
ON = ["BUILD_PNG", "BUILD_JPEG", "BUILD_ZLIB", "WITH_PNG", "WITH_JPEG", "BUILD_opencv_python3"]


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def run(*command: str, cwd: Path | None = None) -> None:
    _out("+ " + " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)  # noqa: S603 - fixed arguments  # nosec B603


def _libpython() -> str:
    """The shared libpython of this interpreter (CMake cannot find it in uv's Python)."""
    library = Path(str(sysconfig.get_config_var("LIBDIR"))) / str(
        sysconfig.get_config_var("LDLIBRARY")
    )
    if not library.is_file():
        raise SystemExit(f"libpython not found at {library}")
    return str(library)


def build(source: Path, work: Path) -> Path:
    """Configure, compile and install into ``work/install``; returns the cv2 package dir."""
    import numpy

    install = work / "install"
    site = install / "site-packages"
    flags = [f"-D{name}=OFF" for name in OFF] + [f"-D{name}=ON" for name in ON]
    run(
        "cmake",
        "-S",
        str(source),
        "-B",
        str(work / "build"),
        "-G",
        "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_INSTALL_PREFIX={install}",
        f"-DBUILD_LIST={MODULES}",
        f"-DPYTHON3_EXECUTABLE={sys.executable}",
        f"-DPYTHON3_INCLUDE_DIR={sysconfig.get_paths()['include']}",
        f"-DPYTHON3_LIBRARY={_libpython()}",
        f"-DPYTHON3_NUMPY_INCLUDE_DIRS={numpy.get_include()}",
        f"-DOPENCV_PYTHON3_INSTALL_PATH={site}",
        "-DOPENCV_SKIP_PYTHON_LOADER=OFF",
        "-DCV_TRACE=OFF",
        *flags,
    )
    run("cmake", "--build", str(work / "build"), "--parallel", str(os.cpu_count() or 2))
    run("cmake", "--install", str(work / "build"))
    package = site / "cv2"
    if not package.is_dir():
        raise SystemExit(f"cv2 was not installed under {site}")
    return package


def _record_line(arcname: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{arcname},sha256={digest},{len(data)}"


def _relocatable(path: Path, data: bytes) -> bytes:
    """The loader's config files name absolute build paths; point them at the package
    itself (libraries are static, so no extra library directory is needed)."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if path.name == "config.py":
        return b"BINARIES_PATHS = [] + BINARIES_PATHS\n"
    if path.name == f"config-{version}.py":
        return (
            "PYTHON_EXTENSIONS_PATHS = [\n"
            f"    os.path.join(LOADER_DIR, 'python-{version}')\n"
            "] + PYTHON_EXTENSIONS_PATHS\n"
        ).encode()
    return data


def package(cv2_dir: Path, out: Path) -> Path:
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    name = f"opencv_python_headless-{VERSION}-{tag}-{tag}-linux_x86_64.whl"
    dist_info = f"opencv_python_headless-{VERSION}.dist-info"
    metadata = (
        "Metadata-Version: 2.1\n"
        "Name: opencv-python-headless\n"
        f"Version: {VERSION}\n"
        "Summary: Minimal OpenCV build (core, imgproc, imgcodecs, photo) for New Gen AI OCR\n"
        "License: Apache-2.0\n"
        "Requires-Python: >=3.11\n"
        "Requires-Dist: numpy\n"
    ).encode()
    wheel = (
        "Wheel-Version: 1.0\nGenerator: newgen-ocr build_opencv_wheel\n"
        f"Root-Is-Purelib: false\nTag: {tag}-{tag}-linux_x86_64\n"
    ).encode()
    out.mkdir(parents=True, exist_ok=True)
    target = out / name
    records: list[str] = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(p for p in cv2_dir.rglob("*") if p.is_file()):
            if "__pycache__" in path.parts:
                continue
            arcname = f"cv2/{path.relative_to(cv2_dir).as_posix()}"
            data = _relocatable(path, path.read_bytes())
            zf.writestr(arcname, data)
            records.append(_record_line(arcname, data))
        licence = cv2_dir.parents[1] / "share" / "licenses" / "opencv4" / "LICENSE"
        extra = {"METADATA": metadata, "WHEEL": wheel}
        if licence.is_file():
            extra["LICENSE"] = licence.read_bytes()
        for filename, data in extra.items():
            arcname = f"{dist_info}/{filename}"
            zf.writestr(arcname, data)
            records.append(_record_line(arcname, data))
        records.append(f"{dist_info}/RECORD,,")
        zf.writestr(f"{dist_info}/RECORD", "\n".join(records) + "\n")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    _out(f"built {target.name} sha256={digest}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True, help="the OpenCV source tree")
    parser.add_argument(
        "--work",
        type=Path,
        default=Path("/tmp/opencv-build"),  # noqa: S108  # nosec B108 - build container scratch
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    package(build(args.source, args.work), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
