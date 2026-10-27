# FROMFREEZE docker.io/library/debian:13
# ARGFREEZE PRETTIER_VERSION github:prettier/prettier
# ARGFREEZE RUFF_VERSION github:astral-sh/ruff
# ARGFREEZE SEMISTANDARD_VERSION github:standard/semistandard
# ARGFREEZE YQ_VERSION github:mikefarah/yq
#
# dev copies nothing in, so a repository taking this one as a submodule builds
# the same image, through a Dockerfile symlinked to this one, and runs it on its
# own tree; test adds that tree for the gate. README.md, Building the images,
# describes both stages.
#===============================================================================
FROM docker.io/library/debian@sha256:4c25275446ca371766b8b093c696782eabce06263fccacb8d5d813b3dc44b308 AS dev

LABEL org.opencontainers.image.source=https://github.com/TiredPixel/lib-design-tools

SHELL ["/bin/bash", "-Eeuo", "pipefail", "-c"]

# Without python3-psutil, which nothing here installs, xdist's -n auto counts
# the CPUs the container may use rather than the machine's physical cores.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    chromium \
    curl \
    diffutils \
    file \
    jq \
    npm \
    python3 \
    python3-pytest \
    python3-pytest-subtests \
    python3-pytest-xdist \
    shellcheck \
    shfmt \
    && \
    rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8 \
    LANGUAGE=C.UTF-8 \
    LC_ALL=C.UTF-8

# Otherwise ruff caches into the tree, which make format's mount carries to the
# host.
ENV RUFF_CACHE_DIR=/tmp/ruff-cache

# The same for Python's own cache, which would otherwise land beside the modules
# imported, in a mounted tree and owned by the container.
ENV PYTHONDONTWRITEBYTECODE=1

ENV PATH=/home/x/r/bin:$PATH

ARG YQ_VERSION=4.54.1
RUN curl -fsL "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_$(dpkg --print-architecture)" -o /usr/local/bin/yq && \
    chmod +x /usr/local/bin/yq && \
    yq --version && \
    yq --version | grep -qF "version v${YQ_VERSION}"

# uname -m, not dpkg --print-architecture above: ruff names assets by triple.
ARG RUFF_VERSION=0.17.3
RUN curl -fsL "https://github.com/astral-sh/ruff/releases/download/${RUFF_VERSION}/ruff-$(uname -m)-unknown-linux-gnu.tar.gz" | \
    tar -xz -C /usr/local/bin --strip-components=1 --wildcards '*/ruff' && \
    ruff --version && \
    ruff --version | grep -qFx "ruff ${RUFF_VERSION}"

# Global packages, so nothing npm writes lands in the mounted tree.
ARG PRETTIER_VERSION=3.9.10
ARG SEMISTANDARD_VERSION=17.0.0
# Held out of ARGFREEZE, which reads GitHub releases, and moved by hand from the
# npm registry: as of 2026-10-27, github.com/standard/snazzy/releases lists none.
ARG SNAZZY_VERSION=9.0.0
RUN npm install --global \
    "prettier@${PRETTIER_VERSION}" \
    "semistandard@${SEMISTANDARD_VERSION}" \
    "snazzy@${SNAZZY_VERSION}" \
    && \
    npm ls --global --depth=0 > /tmp/npm-ls && \
    grep -qF "prettier@${PRETTIER_VERSION}" /tmp/npm-ls && \
    grep -qF "semistandard@${SEMISTANDARD_VERSION}" /tmp/npm-ls && \
    grep -qF "snazzy@${SNAZZY_VERSION}" /tmp/npm-ls && \
    rm /tmp/npm-ls

# uid 1000 so a bind-mounted tree is writable from a host whose user is the
# usual 1000.
RUN if id -u 1000 > /dev/null 2>&1; then userdel -r "$(id -nu 1000)"; fi && \
    useradd -u 1000 x -m && \
    mkdir /home/x/r && \
    chown -R x:x /home/x
#-------------------------------------------------------------------------------
USER x

WORKDIR /home/x/r
#===============================================================================
FROM dev AS test

COPY --chown=x:x . .

CMD ["test"]
