# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

FROM python:3.14-alpine
RUN apk add --no-cache ffmpeg
COPY wheels/ /tmp/wheels/
RUN pip install --no-cache-dir --only-binary=:all: "$(ls /tmp/wheels/sashimi_convert-*.whl)[heif]" \
 && rm -rf /tmp/wheels
COPY driver.py /opt/sandbox/driver.py
ENV PYTHONDONTWRITEBYTECODE=1
