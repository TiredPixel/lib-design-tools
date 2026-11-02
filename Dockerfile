# FROMFREEZE docker.io/library/debian:13
# ARGFREEZE ACTIONLINT_VERSION github:rhysd/actionlint
# ARGFREEZE DOCKERFMT_VERSION github:reteps/dockerfmt
# ARGFREEZE HADOLINT_VERSION github:hadolint/hadolint
# ARGFREEZE MDFORMAT_FRONTMATTER_VERSION pypi:mdformat-frontmatter
# ARGFREEZE MDFORMAT_GFM_VERSION pypi:mdformat-gfm
# ARGFREEZE MDFORMAT_VERSION pypi:mdformat
# ARGFREEZE PRETTIER_VERSION github:prettier/prettier
# ARGFREEZE RUFF_VERSION github:astral-sh/ruff
# ARGFREEZE SEMISTANDARD_VERSION github:standard/semistandard
# ARGFREEZE SNAZZY_VERSION npm:snazzy
# ARGFREEZE TAPLO_VERSION github:tamasfe/taplo
# ARGFREEZE YQ_VERSION github:mikefarah/yq
#
# dev copies nothing in, so a repository taking this one as a submodule builds
# the same image, through a Dockerfile symlinked to this one, and runs it on its
# own tree; test adds that tree for the gate. README.md, Building, describes
# both stages.
#===============================================================================
FROM docker.io/library/debian@sha256:2dc419c6d7fe3dad0a2f9fd469c34cc90b0f9ba642d8fde837fe3d7d34a0e7f1 AS dev

LABEL org.opencontainers.image.source=https://github.com/TiredPixel/lib-design-tools

SHELL ["/bin/bash", "-Eeuo", "pipefail", "-O", "inherit_errexit", "-O", "failglob", "-c"]

# Without python3-psutil, which nothing here installs, xdist's -n auto counts
# the CPUs the container may use rather than the machine's physical cores.
# DL3008: the versions are those of the Debian snapshot the base image's digest
# pins, which make upgrade moves.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        curl \
        diffutils \
        docker-compose \
        file \
        jq \
        npm \
        pipx \
        python3 \
        python3-pytest \
        python3-pytest-subtests \
        python3-pytest-xdist \
        shellcheck \
        shfmt \
    && rm -rf /var/lib/apt/lists/*

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

# mikefarah's yq, which bin/format and lint/yaml-keys need: Debian's yq is the
# Python wrapper.
ARG YQ_VERSION=4.54.1
RUN curl -fsL "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_$(dpkg --print-architecture)" -o /usr/local/bin/yq \
    && chmod +x /usr/local/bin/yq \
    && yq --version \
    && yq --version | grep -qF "version v${YQ_VERSION}"

# uname -m, not dpkg --print-architecture above: ruff names assets by triple.
ARG RUFF_VERSION=0.17.3
RUN curl -fsL "https://github.com/astral-sh/ruff/releases/download/${RUFF_VERSION}/ruff-$(uname -m)-unknown-linux-gnu.tar.gz" \
    | tar -xz -C /usr/local/bin --strip-components=1 --wildcards '*/ruff' \
    && ruff --version \
    && ruff --version | grep -qFx "ruff ${RUFF_VERSION}"

ARG ACTIONLINT_VERSION=1.7.12
RUN curl -fsL "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_$(dpkg --print-architecture).tar.gz" \
    | tar -xz -C /usr/local/bin actionlint \
    && actionlint --version \
    && actionlint --version | grep -qFx "${ACTIONLINT_VERSION}"

ARG DOCKERFMT_VERSION=0.5.4
RUN curl -fsL "https://github.com/reteps/dockerfmt/releases/download/v${DOCKERFMT_VERSION}/dockerfmt-v${DOCKERFMT_VERSION}-linux-$(dpkg --print-architecture).tar.gz" \
    | tar -xz -C /usr/local/bin dockerfmt \
    && dockerfmt version \
    && dockerfmt version | grep -qF "${DOCKERFMT_VERSION}"

# hadolint names its arm64 asset as dpkg does and its amd64 one as uname -m does.
ARG HADOLINT_VERSION=2.15.1
RUN case "$(uname -m)" in aarch64) arch=arm64 ;; *) arch=$(uname -m) ;; esac \
    && curl -fsL "https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint-linux-${arch}" -o /usr/local/bin/hadolint \
    && chmod +x /usr/local/bin/hadolint \
    && hadolint --version \
    && hadolint --version | grep -qF " ${HADOLINT_VERSION}"

ARG TAPLO_VERSION=0.10.0
RUN curl -fsL "https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-linux-$(uname -m).gz" \
    | gunzip >/usr/local/bin/taplo \
    && chmod +x /usr/local/bin/taplo \
    && taplo --version \
    && taplo --version | grep -qFx "taplo ${TAPLO_VERSION}"

# Global, so mdformat sits outside root's home and on the PATH of x, who runs it.
ARG MDFORMAT_FRONTMATTER_VERSION=2.2.0
ARG MDFORMAT_GFM_VERSION=1.0.0
ARG MDFORMAT_VERSION=1.0.0
RUN pipx install --global "mdformat==${MDFORMAT_VERSION}" \
    && pipx inject --global mdformat \
        "mdformat-frontmatter==${MDFORMAT_FRONTMATTER_VERSION}" \
        "mdformat-gfm==${MDFORMAT_GFM_VERSION}" \
    && mdformat --version \
    && pipx list --global --json \
    | jq -e \
        --arg m "${MDFORMAT_VERSION}" \
        --arg f "${MDFORMAT_FRONTMATTER_VERSION}" \
        --arg g "${MDFORMAT_GFM_VERSION}" \
        '.venvs.mdformat.metadata | \
    .main_package.package_version == $m and \
    .injected_packages."mdformat-frontmatter".package_version == $f and \
    .injected_packages."mdformat-gfm".package_version == $g'

# Global packages, so nothing npm writes lands in the mounted tree.
ARG PRETTIER_VERSION=3.9.10
ARG SEMISTANDARD_VERSION=17.0.0
ARG SNAZZY_VERSION=9.0.0
RUN npm install --global \
    "prettier@${PRETTIER_VERSION}" \
    "semistandard@${SEMISTANDARD_VERSION}" \
    "snazzy@${SNAZZY_VERSION}" \
    && npm ls --global --depth=0 >/tmp/npm-ls \
    && grep -qF "prettier@${PRETTIER_VERSION}" /tmp/npm-ls \
    && grep -qF "semistandard@${SEMISTANDARD_VERSION}" /tmp/npm-ls \
    && grep -qF "snazzy@${SNAZZY_VERSION}" /tmp/npm-ls \
    && rm /tmp/npm-ls

# uid 1000 so a bind-mounted tree is writable from a host whose user is the
# usual 1000.
RUN if id -u 1000 > /dev/null 2>&1; then userdel -r "$(id -nu 1000)"; fi && \
    useradd -u 1000 x -m && \
    mkdir /home/x/r && \
    chown -R x:x /home/x
#-------------------------------------------------------------------------------
USER 1000

WORKDIR /home/x/r
#===============================================================================
FROM dev AS test

COPY --chown=x:x . .

CMD ["test"]
