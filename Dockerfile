# TRACK FROM docker.io/library/debian:13
# TRACK ARG ACTIONLINT_SHA256_AMD64 sha256:https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_checksums.txt
# TRACK ARG ACTIONLINT_SHA256_ARM64 sha256:https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_arm64.tar.gz https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_checksums.txt
# TRACK ARG ACTIONLINT_VERSION github:rhysd/actionlint
# TRACK ARG CHECKMAKE_SHA256_AMD64 sha256:https://github.com/checkmake/checkmake/releases/download/v${CHECKMAKE_VERSION}/checkmake-v${CHECKMAKE_VERSION}.linux.amd64 https://github.com/checkmake/checkmake/releases/download/v${CHECKMAKE_VERSION}/checksums.txt
# TRACK ARG CHECKMAKE_SHA256_ARM64 sha256:https://github.com/checkmake/checkmake/releases/download/v${CHECKMAKE_VERSION}/checkmake-v${CHECKMAKE_VERSION}.linux.arm64 https://github.com/checkmake/checkmake/releases/download/v${CHECKMAKE_VERSION}/checksums.txt
# TRACK ARG CHECKMAKE_VERSION github:checkmake/checkmake
# TRACK ARG DOCKERFMT_SHA256_AMD64 sha256:https://github.com/reteps/dockerfmt/releases/download/v${DOCKERFMT_VERSION}/dockerfmt-v${DOCKERFMT_VERSION}-linux-amd64.tar.gz
# TRACK ARG DOCKERFMT_SHA256_ARM64 sha256:https://github.com/reteps/dockerfmt/releases/download/v${DOCKERFMT_VERSION}/dockerfmt-v${DOCKERFMT_VERSION}-linux-arm64.tar.gz
# TRACK ARG DOCKERFMT_VERSION github:reteps/dockerfmt
# TRACK ARG DOTENV_LINTER_SHA256_AMD64 sha256:https://github.com/dotenv-linter/dotenv-linter/releases/download/v${DOTENV_LINTER_VERSION}/dotenv-linter-linux-x86_64.tar.gz
# TRACK ARG DOTENV_LINTER_SHA256_ARM64 sha256:https://github.com/dotenv-linter/dotenv-linter/releases/download/v${DOTENV_LINTER_VERSION}/dotenv-linter-linux-aarch64.tar.gz
# TRACK ARG DOTENV_LINTER_VERSION github:dotenv-linter/dotenv-linter
# TRACK ARG HADOLINT_SHA256_AMD64 sha256:https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint-linux-x86_64 https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/checksums.sha256
# TRACK ARG HADOLINT_SHA256_ARM64 sha256:https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint-linux-arm64 https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/checksums.sha256
# TRACK ARG HADOLINT_VERSION github:hadolint/hadolint
# TRACK ARG TAPLO_SHA256_AMD64 sha256:https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-linux-x86_64.gz
# TRACK ARG TAPLO_SHA256_ARM64 sha256:https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-linux-aarch64.gz
# TRACK ARG TAPLO_VERSION github:tamasfe/taplo
# TRACK ARG YQ_SHA256_AMD64 sha256:https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_amd64 https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/checksums-bsd
# TRACK ARG YQ_SHA256_ARM64 sha256:https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_arm64 https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/checksums-bsd
# TRACK ARG YQ_VERSION github:mikefarah/yq
# TRACK LOCK js npm:prettier npm:semistandard
# TRACK LOCK mdformat pypi:mdformat pypi:mdformat-gfm pypi:mdformat-frontmatter
# TRACK LOCK ruff pypi:ruff
#
# wheels downloads the wheels Dockerfile.lock/mdformat and Dockerfile.lock/ruff
# name, each checked against its hash, for dev to install offline. dev copies
# nothing in, reading the locks only through bind mounts, so a repository taking
# this one as a submodule builds the same image, through a Dockerfile and a
# Dockerfile.lock symlinked to this one's; test adds that repository's tree,
# which every service runs on.
#===============================================================================
FROM docker.io/library/debian@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6 AS wheels

SHELL ["/bin/bash", "-Eeuo", "pipefail", "-O", "inherit_errexit", "-O", "failglob", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=bind,source=Dockerfile.lock/mdformat,target=/lock/mdformat \
    --mount=type=bind,source=Dockerfile.lock/ruff,target=/lock/ruff \
    python3 -m pip download --disable-pip-version-check --require-hashes \
    --no-deps --only-binary=:all: -d /wheels \
    -r /lock/mdformat/requirements.txt -r /lock/ruff/requirements.txt
#===============================================================================
FROM docker.io/library/debian@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6 AS dev

LABEL org.opencontainers.image.source=https://github.com/TiredPixel/lib-design-tools

SHELL ["/bin/bash", "-Eeuo", "pipefail", "-O", "inherit_errexit", "-O", "failglob", "-c"]

# Without python3-psutil, which nothing here installs, xdist's -n auto counts
# the CPUs the container may use rather than the machine's physical cores. git
# is for the tests of bin/format-host, which runs on the host.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        curl \
        diffutils \
        docker-compose \
        git \
        jq \
        kcov \
        npm \
        pipx \
        python3 \
        python3-pytest \
        python3-pytest-cov \
        python3-pytest-subtests \
        python3-pytest-xdist \
        shellcheck \
        shfmt \
        yamllint \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8 \
    LANGUAGE=C.UTF-8 \
    LC_ALL=C.UTF-8

# Otherwise ruff caches into the tree, where the paths make format mounts could
# carry it to the host.
ENV RUFF_CACHE_DIR=/tmp/ruff-cache

# The same for Python's own cache, which would otherwise land beside the modules
# imported, in a mounted tree and owned by the container.
ENV PYTHONDONTWRITEBYTECODE=1

ENV PATH=/home/x/r/bin:$PATH

# Each downloaded tool is checked against the SHA-256 for this architecture
# before it is installed, the hash read from the release's own checksums file
# where it publishes one, so a replaced asset fails the build.

# mikefarah's yq, which bin/format, lib/shell-common/lint/yaml-keys and
# tools/palette-generate need: Debian's yq is the Python wrapper.
ARG YQ_VERSION=4.54.3
ARG YQ_SHA256_AMD64=129534601871538efe0ba4c5e62a34fb1d5f469bcb975c4fddeb467d82974973
ARG YQ_SHA256_ARM64=89f69310b61b68d1245065a1241d1fc843f7a36cd6103ffd77ad987613bd1b67
RUN case $(dpkg --print-architecture) in \
    amd64) arch=amd64 sha256=${YQ_SHA256_AMD64} ;; \
    arm64) arch=arm64 sha256=${YQ_SHA256_ARM64} ;; \
    *) exit 1 ;; \
    esac \
    && curl -fsL "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_${arch}" -o /tmp/yq \
    && echo "${sha256}  /tmp/yq" | sha256sum -c - \
    && install -m 755 /tmp/yq /usr/local/bin/yq \
    && rm /tmp/yq \
    && yq --version \
    && yq --version | grep -qF "version v${YQ_VERSION}"

ARG ACTIONLINT_VERSION=1.7.12
ARG ACTIONLINT_SHA256_AMD64=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8
ARG ACTIONLINT_SHA256_ARM64=325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6
RUN case $(dpkg --print-architecture) in \
    amd64) arch=amd64 sha256=${ACTIONLINT_SHA256_AMD64} ;; \
    arm64) arch=arm64 sha256=${ACTIONLINT_SHA256_ARM64} ;; \
    *) exit 1 ;; \
    esac \
    && curl -fsL "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_${arch}.tar.gz" -o /tmp/actionlint.tar.gz \
    && echo "${sha256}  /tmp/actionlint.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/actionlint.tar.gz -C /usr/local/bin actionlint \
    && rm /tmp/actionlint.tar.gz \
    && actionlint --version \
    && actionlint --version | grep -qFx "${ACTIONLINT_VERSION}"

ARG CHECKMAKE_VERSION=0.3.2
ARG CHECKMAKE_SHA256_AMD64=e2effb876913f3ee2caef0ba35f6202c5e8a3cd55a077d8d2b9ce2034257b6af
ARG CHECKMAKE_SHA256_ARM64=409167c4abb99407bd232c3bbd351b8a39df57997feafde5a08bddffb0f2dcb4
RUN case $(dpkg --print-architecture) in \
    amd64) arch=amd64 sha256=${CHECKMAKE_SHA256_AMD64} ;; \
    arm64) arch=arm64 sha256=${CHECKMAKE_SHA256_ARM64} ;; \
    *) exit 1 ;; \
    esac \
    && curl -fsL "https://github.com/checkmake/checkmake/releases/download/v${CHECKMAKE_VERSION}/checkmake-v${CHECKMAKE_VERSION}.linux.${arch}" -o /tmp/checkmake \
    && echo "${sha256}  /tmp/checkmake" | sha256sum -c - \
    && install -m 755 /tmp/checkmake /usr/local/bin/checkmake \
    && rm /tmp/checkmake \
    && checkmake --version \
    && checkmake --version | grep -qF "checkmake v${CHECKMAKE_VERSION} "

# dockerfmt publishes only an MD5 beside each asset (its release page,
# github.com/reteps/dockerfmt/releases, checked 2026-11), so the hashes are
# taken from the downloads themselves.
ARG DOCKERFMT_VERSION=0.6.0
ARG DOCKERFMT_SHA256_AMD64=c77c7fe6c068de8d2132cbc00b3d17e53e8bcd0ae2bb14f0dadf42c8f6206247
ARG DOCKERFMT_SHA256_ARM64=801c1a9583a91144c57ff65fc1ddf71bfe446220a1a0f423d3e1444fc8faa767
RUN case $(dpkg --print-architecture) in \
    amd64) arch=amd64 sha256=${DOCKERFMT_SHA256_AMD64} ;; \
    arm64) arch=arm64 sha256=${DOCKERFMT_SHA256_ARM64} ;; \
    *) exit 1 ;; \
    esac \
    && curl -fsL "https://github.com/reteps/dockerfmt/releases/download/v${DOCKERFMT_VERSION}/dockerfmt-v${DOCKERFMT_VERSION}-linux-${arch}.tar.gz" -o /tmp/dockerfmt.tar.gz \
    && echo "${sha256}  /tmp/dockerfmt.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/dockerfmt.tar.gz -C /usr/local/bin dockerfmt \
    && rm /tmp/dockerfmt.tar.gz \
    && dockerfmt version \
    && dockerfmt version | grep -qF "${DOCKERFMT_VERSION}"

# dotenv-linter publishes no checksum (its release page,
# github.com/dotenv-linter/dotenv-linter/releases, checked 2026-11), so the
# hashes are taken from the downloads themselves.
ARG DOTENV_LINTER_VERSION=4.0.0
ARG DOTENV_LINTER_SHA256_AMD64=9e2f1f6eb88a71ac15ef209f78f5a68644db82f132d2677112b4f80ec2bd2127
ARG DOTENV_LINTER_SHA256_ARM64=c4113aafd35e5f9095b2f757061d07cc8eb5cc0bcd755249877af08b7ffafb9a
RUN case $(dpkg --print-architecture) in \
    amd64) arch=x86_64 sha256=${DOTENV_LINTER_SHA256_AMD64} ;; \
    arm64) arch=aarch64 sha256=${DOTENV_LINTER_SHA256_ARM64} ;; \
    *) exit 1 ;; \
    esac \
    && curl -fsL "https://github.com/dotenv-linter/dotenv-linter/releases/download/v${DOTENV_LINTER_VERSION}/dotenv-linter-linux-${arch}.tar.gz" -o /tmp/dotenv-linter.tar.gz \
    && echo "${sha256}  /tmp/dotenv-linter.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/dotenv-linter.tar.gz -C /usr/local/bin dotenv-linter \
    && rm /tmp/dotenv-linter.tar.gz \
    && dotenv-linter --version \
    && dotenv-linter --version | grep -qF " ${DOTENV_LINTER_VERSION}"

ARG HADOLINT_VERSION=2.15.1
ARG HADOLINT_SHA256_AMD64=c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507
ARG HADOLINT_SHA256_ARM64=f6198ef8090f404dbb771abfee086eb8c48ac177f30da7fd3510aca35b344b5d
RUN case $(dpkg --print-architecture) in \
    amd64) arch=x86_64 sha256=${HADOLINT_SHA256_AMD64} ;; \
    arm64) arch=arm64 sha256=${HADOLINT_SHA256_ARM64} ;; \
    *) exit 1 ;; \
    esac \
    && curl -fsL "https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint-linux-${arch}" -o /tmp/hadolint \
    && echo "${sha256}  /tmp/hadolint" | sha256sum -c - \
    && install -m 755 /tmp/hadolint /usr/local/bin/hadolint \
    && rm /tmp/hadolint \
    && hadolint --version \
    && hadolint --version | grep -qF " ${HADOLINT_VERSION}"

# taplo publishes no checksum (its release page,
# github.com/tamasfe/taplo/releases, checked 2026-11), so the hashes are taken
# from the downloads themselves.
ARG TAPLO_VERSION=0.10.0
ARG TAPLO_SHA256_AMD64=8fe196b894ccf9072f98d4e1013a180306e17d244830b03986ee5e8eabeb6156
ARG TAPLO_SHA256_ARM64=033681d01eec8376c3fd38fa3703c79316f5e14bb013d859943b60a07bccdcc3
RUN case $(dpkg --print-architecture) in \
    amd64) arch=x86_64 sha256=${TAPLO_SHA256_AMD64} ;; \
    arm64) arch=aarch64 sha256=${TAPLO_SHA256_ARM64} ;; \
    *) exit 1 ;; \
    esac \
    && curl -fsL "https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-linux-${arch}.gz" -o /tmp/taplo.gz \
    && echo "${sha256}  /tmp/taplo.gz" | sha256sum -c - \
    && gunzip -c /tmp/taplo.gz >/usr/local/bin/taplo \
    && chmod 755 /usr/local/bin/taplo \
    && rm /tmp/taplo.gz \
    && taplo --version \
    && taplo --version | grep -qFx "taplo ${TAPLO_VERSION}"

# ruff, and mdformat with its gfm and frontmatter plugins, from pipx outside
# root's home and on the PATH of x, who runs them. Offline, pip can take only
# the wheels the wheels stage downloaded, so every version is the lock's. pipx's
# shared venv is made here, from Debian's bundled pip, since pipx would
# otherwise fetch pip unpinned to make it, and in the same RUN as every pipx
# call, since pipx upgrades it from PyPI once its pip is older than pipx's
# SHARED_LIBS_MAX_AGE_SEC. DL3013 asks for each version written here, which the
# locks alone pin.
# hadolint ignore=DL3013
RUN --network=none \
    --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    export PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin \
    && python3 -m venv "$PIPX_HOME/shared" \
    && pipx install \
        --pip-args='--disable-pip-version-check --no-index --find-links=/wheels' \
        ruff \
    && pipx install \
        --pip-args='--disable-pip-version-check --no-index --find-links=/wheels' \
        mdformat \
    && pipx inject \
        --pip-args='--disable-pip-version-check --no-index --find-links=/wheels' \
        mdformat mdformat-gfm mdformat-frontmatter \
    && "$PIPX_HOME/venvs/ruff/bin/python" -m pip check \
    && "$PIPX_HOME/venvs/mdformat/bin/python" -m pip check \
    && ruff --version \
    && version=$(mdformat --version) \
    && echo "$version" \
    && echo "$version" | grep -qF 'mdformat-gfm ' \
    && echo "$version" | grep -qF 'mdformat_frontmatter '

# npm ci, the one npm command that installs a lock as given, outside the mounted
# tree. The mount is read-only, so the lock files are linked into a directory of
# their own.
RUN --mount=type=bind,source=Dockerfile.lock/js,target=/lock \
    mkdir /opt/js \
    && ln -s /lock/package.json /lock/package-lock.json /opt/js/ \
    && npm ci --prefix /opt/js --ignore-scripts --no-audit --no-fund \
    && /opt/js/node_modules/.bin/prettier --version \
    && /opt/js/node_modules/.bin/semistandard --version

ENV PATH=/opt/js/node_modules/.bin:$PATH

# uid 1000 so a bind-mounted tree is writable from a host whose user is the
# usual 1000.
RUN useradd -u 1000 x -m \
    && mkdir /home/x/r \
    && chown -R x:x /home/x
#-------------------------------------------------------------------------------
USER 1000

WORKDIR /home/x/r

# What git describe prints for the tree built, which docker/build-compose and CI
# pass: empty for a tree with no tag to describe it by, and where no build
# argument is given, as under docker compose run --build. Last, so a new stamp
# invalidates no earlier layer.
ARG GIT_DESCRIBE
LABEL org.opencontainers.image.version=$GIT_DESCRIBE
#===============================================================================
FROM dev AS test

COPY --chown=x:x . .

CMD ["test"]
