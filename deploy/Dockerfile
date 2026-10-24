# API and worker image (ADR-0031). Built at staging (network for the base image, apt, uv's
# Python and the OpenCV source); the result runs with no network at all.
#
#   make images          (needs `make frontend` and `make models` first)
#
# Every input is pinned: base images by digest, Python packages by the hash-pinned lock,
# OpenCV by the SHA-256 PyPI publishes for its source, models by models.lock.json (verified
# here, with nothing unlisted allowed in).

ARG UBUNTU=docker.io/library/ubuntu:noble-20260917@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
ARG UV=docker.io/astral/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a

FROM ${UV} AS uv

# ------------------------------------------------------------------ toolchain
FROM ${UBUNTU} AS toolchain
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake ninja-build ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_PREFERENCE=only-managed UV_NO_CACHE=1 \
    UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
# uv checks the Python download against the SHA-256 in its own release manifest.
RUN uv python install 3.11 && uv venv /opt/venv --python 3.11

# ------------------------------------------------------------------ opencv
# Its own stage, depending only on NumPy's pin and the build script, so that updating other
# dependencies does not rebuild OpenCV (about 25 minutes on 4 cores).
FROM toolchain AS opencv
COPY deploy/opencv-build.lock /src/opencv-build.lock
RUN uv pip install --python /opt/venv/bin/python --require-hashes --no-deps -r /src/opencv-build.lock
# Minimal OpenCV from the source distribution, checked against PyPI's published SHA-256.
ARG OPENCV_URL=https://files.pythonhosted.org/packages/67/8a/51ff1c6287be0b6cd3070cf095875da4ff7d6eda38507e6ada5608786a78/opencv_python_headless-4.14.0.94.tar.gz
ARG OPENCV_SHA256=4afa2ea1214453648be88259f035712454faa9039b686de7753569ba8eec1577
COPY scripts/build_opencv_wheel.py /src/scripts/build_opencv_wheel.py
RUN curl -fsSLo /tmp/opencv.tar.gz "$OPENCV_URL" \
 && echo "$OPENCV_SHA256  /tmp/opencv.tar.gz" | sha256sum -c - \
 && mkdir /tmp/opencv-sdist && tar -xzf /tmp/opencv.tar.gz -C /tmp/opencv-sdist --strip-components=1 \
 && /opt/venv/bin/python /src/scripts/build_opencv_wheel.py --source /tmp/opencv-sdist/opencv --out /wheels \
 && rm -rf /tmp/opencv.tar.gz /tmp/opencv-sdist /tmp/opencv-build \
 && sha256sum /wheels/*.whl | sed "s|/wheels/||" > /wheels/SHA256SUMS

# ------------------------------------------------------------------ build
FROM toolchain AS build
# The base interpreter's own pip, setuptools and ensurepip wheels (with their vendored
# certifi, urllib3, msgpack, ...) are never used: the venv has its own locked packages and
# nothing runs pip at run time. Remove them so they never reach the image.
RUN base=$(echo /opt/python/cpython-3.11.*/lib/python3.11) \
 && rm -rf "$base"/site-packages/pip "$base"/site-packages/pip-* \
           "$base"/site-packages/setuptools "$base"/site-packages/setuptools-* \
           "$base"/site-packages/_distutils_hack "$base"/site-packages/distutils-precedence.pth \
           "$base"/ensurepip
COPY requirements.lock /src/requirements.lock
RUN uv pip sync --python /opt/venv/bin/python --require-hashes /src/requirements.lock
COPY --from=opencv /wheels /wheels
RUN uv pip install --python /opt/venv/bin/python --no-deps --reinstall /wheels/opencv_python_headless-*.whl

# The application (with the web application built by `make frontend`).
COPY pyproject.toml README.md /src/
COPY src /src/src
# No build isolation: setuptools comes from the hash-pinned lock, nothing else is fetched.
RUN test -f /src/src/newgen_ocr/webapp/dist/index.html \
 && uv pip install --python /opt/venv/bin/python --no-deps --no-build-isolation /src
# The production guard must accept this OpenCV (no FFmpeg).
RUN /opt/venv/bin/python -c "from newgen_ocr.imaging.runtime import check_opencv_build; check_opencv_build('production'); import cv2; print('opencv', cv2.__version__)"

# ------------------------------------------------------------------ runtime
FROM ${UBUNTU} AS runtime
ARG DEBIAN_FRONTEND=noninteractive
# tesseract-ocr: the OCR binary (models come from /opt/ngocr/models); libpq5: PostgreSQL client
# library for psycopg; libgomp1: OpenMP for Paddle. Nothing else.
# `upgrade` applies the Ubuntu security updates published since the base image was built.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends tesseract-ocr libpq5 libgomp1 \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /var/log/* \
 && groupadd --gid 10001 ngocr \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin ngocr \
 && mkdir -p /data && chown 10001:10001 /data && chmod 0750 /data
COPY --from=build /opt/python /opt/python
COPY --from=build /opt/venv /opt/venv
COPY --from=build /wheels/SHA256SUMS /opt/ngocr/opencv-wheel.sha256
COPY models.lock.json /opt/ngocr/models.lock.json
COPY models /opt/ngocr/models
COPY scripts/fetch_models.py /tmp/fetch_models.py
RUN /opt/venv/bin/python /tmp/fetch_models.py --verify --lock /opt/ngocr/models.lock.json --models-dir /opt/ngocr/models \
 && rm /tmp/fetch_models.py \
 && chmod -R a-w /opt/ngocr /opt/venv /opt/python

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp \
    NGOCR_ENVIRONMENT=production NGOCR_DATA_DIR=/data TMPDIR=/data/tmp \
    NGOCR_MODELS_DIR=/opt/ngocr/models NGOCR_MODELS_LOCK=/opt/ngocr/models.lock.json \
    NGOCR_SECRETS_DIR=/run/secrets NGOCR_OPENCV_THREADS=1
USER 10001:10001
WORKDIR /data
EXPOSE 8000
# Overridden for the worker and one-shot commands in deploy/compose.yaml.
CMD ["uvicorn", "newgen_ocr.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--no-server-header", "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "30"]
