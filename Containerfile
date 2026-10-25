# Josui — Claude Code as a crunchtools autonomous agent
#
# The image is tooling only. Who Josui is (CLAUDE.md, settings, skills) and
# what it may touch (workspaces) arrive as bind mounts at run time, so the
# same image runs on a server and as a copy on a laptop. See
# README.md and deploy/josui.service.example.
#
# The agent runtime (session supervisor, Matrix bridge, breaker and audit
# hooks, the crunchtools policy) comes from crunchtools/claude-agent's
# packages; this image adds Josui's own wiring in josui-entrypoint.
#
# Build happens in GitHub Actions (.github/workflows/build.yml).

FROM registry.access.redhat.com/ubi10/ubi-minimal@sha256:ab67cd02caf8706e34a695a1432ebe965934f6128ec352a6c3f4048b43a4fb5e

LABEL maintainer="fatherlinux <scott.mccarty@crunchtools.com>"
LABEL description="Josui - Claude Code packaged as a crunchtools autonomous agent"

# A new Claude Code or claude-agent is tested before Josui runs on it: bump
# by hand.
ARG CLAUDE_CODE_VERSION=2.1.304
ARG CLAUDE_AGENT_VERSION=0.2.1

# Anthropic's signed repository for claude-code and crunchtools' for
# claude-agent, keys imported up front (constitution XIV: repository
# definitions and keys may live in an image).
RUN rpm --import https://downloads.claude.ai/keys/claude-code.asc && \
    rpm --import https://crunchtools.github.io/packages/crunchtools.asc && \
    printf '%s\n' '[claude-code]' 'name=Claude Code' \
        'baseurl=https://downloads.claude.ai/claude-code/rpm/stable' \
        'enabled=1' 'gpgcheck=1' 'gpgkey=https://downloads.claude.ai/keys/claude-code.asc' \
        > /etc/yum.repos.d/claude-code.repo && \
    curl -fsSL -o /etc/yum.repos.d/crunchtools.repo \
        https://crunchtools.github.io/packages/rpm/crunchtools.repo

# git and ssh for source work (GitHub is reached through the gateway, so
# there is no gh); jq and python3 for the hooks and the skills' own scripts;
# nodejs because the web projects Josui works on run their unit tests with
# it, with full ICU: without the locale data, tests that format a date pass in
# the site's container and fail here.
RUN microdnf install -y --setopt=install_weak_deps=0 --nodocs \
        findutils grep sed gawk tar gzip which procps-ng \
        ca-certificates git-core openssh-clients jq python3 \
        nodejs nodejs-full-i18n npm \
        "claude-code-${CLAUDE_CODE_VERSION}" \
        "claude-agent-${CLAUDE_AGENT_VERSION}" "claude-agent-matrix-${CLAUDE_AGENT_VERSION}" \
        "claude-agent-trentina-${CLAUDE_AGENT_VERSION}" && \
    microdnf clean all && \
    claude --version && claude-agent --version

RUN useradd --uid 1000 --user-group --create-home --home-dir /home/josui --shell /bin/bash josui && \
    mkdir -p /etc/josui /var/lib/josui/josui-skills /var/log/josui && \
    chown josui:josui /var/lib/josui /var/lib/josui/josui-skills /var/log/josui

COPY rootfs/ /

RUN chmod 0755 /usr/local/bin/josui-entrypoint && \
    bash -n /usr/local/bin/josui-entrypoint

# HOME is a volume: the Claude login, sessions and git identity live
# there and survive a restart. Nothing else in the container is writable.
# CLAUDE_AGENT=1: every Claude Code session here is the agent's, so the
# breaker and audit hooks count and log all of them.
ENV HOME=/home/josui \
    JOSUI_CONFIG_DIR=/etc/josui \
    JOSUI_SKILLS_DIR=/var/lib/josui/josui-skills \
    CLAUDE_AGENT=1 \
    CLAUDE_AGENT_AUDIT_DIR=/var/log/josui \
    CLAUDE_AGENT_BREAKER_DIR=/tmp/josui-breaker \
    DISABLE_AUTOUPDATER=1 \
    DISABLE_ERROR_REPORTING=1

USER 1000
WORKDIR /home/josui

ENTRYPOINT ["/usr/local/bin/josui-entrypoint"]
