# syntax=docker/dockerfile:experimental

ARG GS_MGMT_BUILDER_IMAGE=ghcr.io/oopt-goldstone/goldstone-mgmt/gs-mgmt-builder:latest
ARG GS_MGMT_BASE=ghcr.io/oopt-goldstone/goldstone-mgmt/gs-mgmt:latest

FROM $GS_MGMT_BUILDER_IMAGE as builder

ARG http_proxy
ARG https_proxy

FROM $GS_MGMT_BASE

RUN --mount=type=cache,target=/var/cache/apt,sharing=private --mount=type=cache,target=/var/lib/apt,sharing=private \
            apt update && apt install -qy --no-install-recommends libi2c0

RUN --mount=type=bind,from=builder,source=/usr/share/onlp,target=/src ls /src/*.deb | awk '$0 !~ /python/ && $0 !~ /-dbg_/ && $0 !~ /-dev_/ { print $0 }' | xargs dpkg -i

RUN ldconfig

RUN --mount=type=bind,source=src/south/onlp,target=/src,rw pip install /src

# vim:filetype=dockerfile
