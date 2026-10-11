# Josui — Claude Code as a crunchtools autonomous agent
#
# The image is tooling only. Who Josui is (CLAUDE.md, settings, skills) and
# what it may touch (workspaces) arrive as bind mounts at run time, so the
# same image runs on a server and as a copy on a laptop. See
# README.md and deploy/josui.service.example.
#
# Build happens in GitHub Actions (.github/workflows/build.yml).

FROM registry.access.redhat.com/ubi10/ubi-minimal@sha256:bcecd3e74c9d03eb1a596c6c8f366775a289d422ac5925ec1539924d762ebf23

LABEL maintainer="fatherlinux <scott.mccarty@crunchtools.com>"
LABEL description="Josui - Claude Code packaged as a crunchtools autonomous agent"

ARG CLAUDE_CODE_VERSION=2.1.296
ARG GH_VERSION=2.102.0
ARG GH_SHA256=bb766f710eef8ede859c18578c72c327597cd4c8a85b06001b1f3843c6019386

# git, gh and ssh for source work; jq and python3 for the hooks and the
# skills' own scripts; nodejs because Claude Code installs from npm and the
# web projects Josui works on run their unit tests with it.
RUN microdnf install -y --setopt=install_weak_deps=0 --nodocs \
        findutils grep sed gawk tar gzip which procps-ng \
        ca-certificates git-core openssh-clients jq python3 nodejs npm && \
    microdnf clean all

# gh from the release tarball, checked against the published checksum. The
# rpm depends on the full git package; git-core is what Josui needs.
RUN curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" -o /tmp/gh.tar.gz && \
    echo "${GH_SHA256}  /tmp/gh.tar.gz" | sha256sum -c - && \
    tar -xzf /tmp/gh.tar.gz -C /tmp && \
    install -m 0755 "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh && \
    rm -rf /tmp/gh.tar.gz "/tmp/gh_${GH_VERSION}_linux_amd64" && \
    gh --version

# Global, not the native installer: that one installs under $HOME and updates
# itself in place. The root filesystem is read-only at run time and the
# version is the one pinned here.
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" && \
    npm cache clean --force && \
    claude --version

RUN useradd --uid 1000 --user-group --create-home --home-dir /home/josui --shell /bin/bash josui && \
    mkdir -p /etc/josui /var/lib/josui/josui-skills /var/log/josui && \
    chown josui:josui /var/lib/josui /var/lib/josui/josui-skills /var/log/josui

COPY rootfs/ /

RUN chmod 0755 /usr/local/bin/josui-entrypoint /usr/local/libexec/josui/hooks.py && \
    python3 -m py_compile /usr/local/libexec/josui/hooks.py && \
    jq -e . /etc/claude-code/managed-settings.json > /dev/null

# HOME is a volume: the Claude and gh logins, sessions and git identity live
# there and survive a restart. Nothing else in the container is writable.
ENV HOME=/home/josui \
    JOSUI_CONFIG_DIR=/etc/josui \
    JOSUI_SKILLS_DIR=/var/lib/josui/josui-skills \
    JOSUI_AUDIT_DIR=/var/log/josui \
    JOSUI_BREAKER_DIR=/tmp/josui-breaker \
    DISABLE_AUTOUPDATER=1

USER 1000
WORKDIR /home/josui

ENTRYPOINT ["/usr/local/bin/josui-entrypoint"]
