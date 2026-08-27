# Calibre-Web with the Flibusta blueprint baked in.
# Digest pinned in README.md §1 (Calibre-Web 0.6.27).
FROM lscr.io/linuxserver/calibre-web@sha256:1870b57874a831d7c0c389547826e5be38089c437276299e1646b7c81a497347 AS base

# The base image ships no `patch` binary, so the two upstream files are patched in a
# throwaway busybox stage and copied back into the final image.
FROM alpine:3.20 AS patcher
RUN apk add --no-cache patch
WORKDIR /src
COPY --from=base /app/calibre-web/cps/main.py cps/main.py
COPY --from=base /app/calibre-web/cps/templates/layout.html cps/templates/layout.html
COPY patches/ /tmp/flibusta-patches/
RUN for p in /tmp/flibusta-patches/*.patch; do patch -p1 --no-backup-if-mismatch < "$p"; done \
 && rm -rf /tmp/flibusta-patches

FROM base
# The blueprint package deploys as `cps.flibusta` (import: `from .flibusta import flibusta`).
COPY cps_flibusta /app/calibre-web/cps/flibusta
COPY --from=patcher /src/cps/main.py /app/calibre-web/cps/main.py
COPY --from=patcher /src/cps/templates/layout.html /app/calibre-web/cps/templates/layout.html
# `requests` 2.34.2 is already present in the image (README §8) — nothing to pip install.
RUN find /app/calibre-web/cps -name '__pycache__' -type d -prune -exec rm -rf {} + || true
