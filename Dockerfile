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
# TRACK ARG RUFF_SHA256_AMD64 sha256:https://github.com/astral-sh/ruff/releases/download/${RUFF_VERSION}/ruff-x86_64-unknown-linux-gnu.tar.gz https://github.com/astral-sh/ruff/releases/download/${RUFF_VERSION}/ruff-x86_64-unknown-linux-gnu.tar.gz.sha256
# TRACK ARG RUFF_SHA256_ARM64 sha256:https://github.com/astral-sh/ruff/releases/download/${RUFF_VERSION}/ruff-aarch64-unknown-linux-gnu.tar.gz https://github.com/astral-sh/ruff/releases/download/${RUFF_VERSION}/ruff-aarch64-unknown-linux-gnu.tar.gz.sha256
# TRACK ARG RUFF_VERSION github:astral-sh/ruff
# TRACK ARG TAPLO_SHA256_AMD64 sha256:https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-linux-x86_64.gz
# TRACK ARG TAPLO_SHA256_ARM64 sha256:https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-linux-aarch64.gz
# TRACK ARG TAPLO_VERSION github:tamasfe/taplo
# TRACK ARG YQ_SHA256_AMD64 sha256:https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_amd64 https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/checksums-bsd
# TRACK ARG YQ_SHA256_ARM64 sha256:https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_arm64 https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/checksums-bsd
# TRACK ARG YQ_VERSION github:mikefarah/yq
# TRACK LOCK js npm:@prettier/plugin-xml npm:c8 npm:prettier npm:semistandard
# TRACK LOCK mdformat pypi:mdformat pypi:mdformat-frontmatter pypi:mdformat-gfm
#===============================================================================
FROM docker.io/library/debian@sha256:3cda3ca472ce45507a5109c6a3907093e24d9ae67d55954c9a07a45665dafba2 AS wheels

SHELL ["/bin/bash", "-Eeuo", "pipefail", "-O", "inherit_errexit", "-O", "failglob", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=bind,source=Dockerfile.lock,target=/lock \
    python3 -m pip download --disable-pip-version-check --require-hashes \
    --no-deps --only-binary=:all: -d /wheels \
    -r /lock/mdformat/requirements.txt
#===============================================================================
FROM docker.io/library/debian@sha256:3cda3ca472ce45507a5109c6a3907093e24d9ae67d55954c9a07a45665dafba2 AS dev

SHELL ["/bin/bash", "-Eeuo", "pipefail", "-O", "inherit_errexit", "-O", "failglob", "-c"]

# python3-psutil is left out, so xdist's -n auto counts the CPUs the container
# may use.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        curl \
        diffutils \
        docker-compose \
        expat \
        git \
        jq \
        kcov \
        npm \
        python3 \
        python3-pytest \
        python3-pytest-cov \
        python3-pytest-subtests \
        python3-pytest-xdist \
        python3-tinycss2 \
        python3-venv \
        shellcheck \
        shfmt \
        yamllint \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    RUFF_CACHE_DIR=/tmp/ruff-cache

ARG ACTIONLINT_SHA256_AMD64=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8
ARG ACTIONLINT_SHA256_ARM64=325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6
ARG ACTIONLINT_VERSION=1.7.12
RUN case $(uname -m) in \
        aarch64) arch=arm64 sha256=${ACTIONLINT_SHA256_ARM64} ;; \
        x86_64) arch=amd64 sha256=${ACTIONLINT_SHA256_AMD64} ;; \
    esac \
        && curl -fsSL "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_${arch}.tar.gz" -o /tmp/actionlint.tar.gz \
        && echo "${sha256}  /tmp/actionlint.tar.gz" | sha256sum -c - \
        && tar -xzf /tmp/actionlint.tar.gz -C /usr/local/bin actionlint \
        && rm /tmp/actionlint.tar.gz

ARG CHECKMAKE_SHA256_AMD64=e2effb876913f3ee2caef0ba35f6202c5e8a3cd55a077d8d2b9ce2034257b6af
ARG CHECKMAKE_SHA256_ARM64=409167c4abb99407bd232c3bbd351b8a39df57997feafde5a08bddffb0f2dcb4
ARG CHECKMAKE_VERSION=0.3.2
RUN case $(uname -m) in \
        aarch64) arch=arm64 sha256=${CHECKMAKE_SHA256_ARM64} ;; \
        x86_64) arch=amd64 sha256=${CHECKMAKE_SHA256_AMD64} ;; \
    esac \
        && curl -fsSL "https://github.com/checkmake/checkmake/releases/download/v${CHECKMAKE_VERSION}/checkmake-v${CHECKMAKE_VERSION}.linux.${arch}" -o /tmp/checkmake \
        && echo "${sha256}  /tmp/checkmake" | sha256sum -c - \
        && install -m 755 /tmp/checkmake /usr/local/bin/checkmake \
        && rm /tmp/checkmake

# dockerfmt publishes no SHA-256 (github.com/reteps/dockerfmt/releases, read
# 2026-11-20), so the hashes are of the downloads.
ARG DOCKERFMT_SHA256_AMD64=da0cd8bbb3f1b6cdbe0bc16d4e762ffb2351a69eb259d84a7ddc35bcf156e1f7
ARG DOCKERFMT_SHA256_ARM64=fb361d10597b998766c72db47c4a70350832860571bb772a8b8cfc780377b7c9
ARG DOCKERFMT_VERSION=1.0.0
RUN case $(uname -m) in \
        aarch64) arch=arm64 sha256=${DOCKERFMT_SHA256_ARM64} ;; \
        x86_64) arch=amd64 sha256=${DOCKERFMT_SHA256_AMD64} ;; \
    esac \
        && curl -fsSL "https://github.com/reteps/dockerfmt/releases/download/v${DOCKERFMT_VERSION}/dockerfmt-v${DOCKERFMT_VERSION}-linux-${arch}.tar.gz" -o /tmp/dockerfmt.tar.gz \
        && echo "${sha256}  /tmp/dockerfmt.tar.gz" | sha256sum -c - \
        && tar -xzf /tmp/dockerfmt.tar.gz -C /usr/local/bin dockerfmt \
        && rm /tmp/dockerfmt.tar.gz

# dotenv-linter publishes no SHA-256
# (github.com/dotenv-linter/dotenv-linter/releases, read 2026-11-20), so the
# hashes are of the downloads.
ENV DOTENV_LINTER_SKIP_UPDATES=true
ARG DOTENV_LINTER_SHA256_AMD64=9e2f1f6eb88a71ac15ef209f78f5a68644db82f132d2677112b4f80ec2bd2127
ARG DOTENV_LINTER_SHA256_ARM64=c4113aafd35e5f9095b2f757061d07cc8eb5cc0bcd755249877af08b7ffafb9a
ARG DOTENV_LINTER_VERSION=4.0.0
RUN case $(uname -m) in \
        aarch64) arch=aarch64 sha256=${DOTENV_LINTER_SHA256_ARM64} ;; \
        x86_64) arch=x86_64 sha256=${DOTENV_LINTER_SHA256_AMD64} ;; \
    esac \
        && curl -fsSL "https://github.com/dotenv-linter/dotenv-linter/releases/download/v${DOTENV_LINTER_VERSION}/dotenv-linter-linux-${arch}.tar.gz" -o /tmp/dotenv-linter.tar.gz \
        && echo "${sha256}  /tmp/dotenv-linter.tar.gz" | sha256sum -c - \
        && tar -xzf /tmp/dotenv-linter.tar.gz -C /usr/local/bin dotenv-linter \
        && rm /tmp/dotenv-linter.tar.gz

ARG HADOLINT_SHA256_AMD64=c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507
ARG HADOLINT_SHA256_ARM64=f6198ef8090f404dbb771abfee086eb8c48ac177f30da7fd3510aca35b344b5d
ARG HADOLINT_VERSION=2.15.1
RUN case $(uname -m) in \
        aarch64) arch=arm64 sha256=${HADOLINT_SHA256_ARM64} ;; \
        x86_64) arch=x86_64 sha256=${HADOLINT_SHA256_AMD64} ;; \
    esac \
        && curl -fsSL "https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint-linux-${arch}" -o /tmp/hadolint \
        && echo "${sha256}  /tmp/hadolint" | sha256sum -c - \
        && install -m 755 /tmp/hadolint /usr/local/bin/hadolint \
        && rm /tmp/hadolint

ARG RUFF_SHA256_AMD64=94b33791f48cd54ab35ad673a443a5c001d3e6d87effeaba9899070b700d45f3
ARG RUFF_SHA256_ARM64=d4eea36ee6228f6c6bbcf0aea4ebf66fe0e595eedc6cffe628d7b8ba3623390f
ARG RUFF_VERSION=0.17.6
RUN case $(uname -m) in \
        aarch64) arch=aarch64 sha256=${RUFF_SHA256_ARM64} ;; \
        x86_64) arch=x86_64 sha256=${RUFF_SHA256_AMD64} ;; \
    esac \
        && curl -fsSL "https://github.com/astral-sh/ruff/releases/download/${RUFF_VERSION}/ruff-${arch}-unknown-linux-gnu.tar.gz" -o /tmp/ruff.tar.gz \
        && echo "${sha256}  /tmp/ruff.tar.gz" | sha256sum -c - \
        && tar -xzf /tmp/ruff.tar.gz -C /usr/local/bin --strip-components=1 --wildcards '*/ruff' \
        && rm /tmp/ruff.tar.gz

# taplo publishes no SHA-256 (github.com/tamasfe/taplo/releases, read
# 2026-11-20), so the hashes are of the downloads.
ENV TAPLO_CONFIG=/etc/taplo.toml
ARG TAPLO_SHA256_AMD64=8fe196b894ccf9072f98d4e1013a180306e17d244830b03986ee5e8eabeb6156
ARG TAPLO_SHA256_ARM64=033681d01eec8376c3fd38fa3703c79316f5e14bb013d859943b60a07bccdcc3
ARG TAPLO_VERSION=0.10.0
RUN case $(uname -m) in \
        aarch64) arch=aarch64 sha256=${TAPLO_SHA256_ARM64} ;; \
        x86_64) arch=x86_64 sha256=${TAPLO_SHA256_AMD64} ;; \
    esac \
        && curl -fsSL "https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-linux-${arch}.gz" -o /tmp/taplo.gz \
        && echo "${sha256}  /tmp/taplo.gz" | sha256sum -c - \
        && gunzip /tmp/taplo.gz \
        && install -m 755 /tmp/taplo /usr/local/bin/taplo \
        && rm /tmp/taplo \
        && printf '[schema]\nenabled = false\n' > /etc/taplo.toml

# Debian 13's yq is a Python wrapper around jq, not mikefarah's yq (apt-cache
# show yq, read 2026-11-20).
ARG YQ_SHA256_AMD64=129534601871538efe0ba4c5e62a34fb1d5f469bcb975c4fddeb467d82974973
ARG YQ_SHA256_ARM64=89f69310b61b68d1245065a1241d1fc843f7a36cd6103ffd77ad987613bd1b67
ARG YQ_VERSION=4.54.3
RUN case $(uname -m) in \
        aarch64) arch=arm64 sha256=${YQ_SHA256_ARM64} ;; \
        x86_64) arch=amd64 sha256=${YQ_SHA256_AMD64} ;; \
    esac \
        && curl -fsSL "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_${arch}" -o /tmp/yq \
        && echo "${sha256}  /tmp/yq" | sha256sum -c - \
        && install -m 755 /tmp/yq /usr/local/bin/yq \
        && rm /tmp/yq

# Debian 13's mdformat package carries neither plugin (apt-cache show mdformat,
# read 2026-11-20).
RUN --network=none \
    --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    --mount=type=bind,source=Dockerfile.lock,target=/lock \
    python3 -m venv /opt/mdformat \
    && /opt/mdformat/bin/python -m pip install --disable-pip-version-check \
        --no-index --find-links=/wheels -r /lock/mdformat/requirements.txt \
    && ln -s /opt/mdformat/bin/mdformat /usr/local/bin/

# The lock is linked in, since npm ci installs beside it and the mount is
# read-only.
RUN --mount=type=bind,source=Dockerfile.lock,target=/lock \
    mkdir /opt/js \
    && ln -s /lock/js/package.json /lock/js/package-lock.json /opt/js/ \
    && npm ci --prefix /opt/js --engine-strict --ignore-scripts --no-audit \
        --no-fund --no-update-notifier

ENV PATH=/opt/js/node_modules/.bin:$PATH

# -o: the host's ids may already be taken in the image; -l: a large uid would
# grow lastlog and faillog.
ARG USER_GID=1000
ARG USER_UID=1000
RUN groupadd -o -g "$USER_GID" x \
    && useradd -l -o -u "$USER_UID" -g x -m x \
    && mkdir /home/x/r \
    && chown -R x:x /home/x
#-------------------------------------------------------------------------------
USER $USER_UID:$USER_GID

WORKDIR /home/x/r

ENV PATH=/home/x/r/bin:$PATH

# Last, so the labels invalidate no earlier layer.
ARG GIT_DESCRIBE
ARG IMAGE_SOURCE=https://github.com/TiredPixel/lib-design-tools
LABEL org.opencontainers.image.source=$IMAGE_SOURCE
LABEL org.opencontainers.image.version=$GIT_DESCRIBE
#===============================================================================
FROM dev AS test

COPY --chown=x:x . .

# By path, so a missing bin/test fails rather than PATH finding coreutils' test.
CMD ["bin/test"]
