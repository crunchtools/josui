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

# git and ssh for source work (GitHub is reached through the gateway, so
# there is no gh); jq and python3 for the hooks and the
# skills' own scripts; nodejs because Claude Code installs from npm and the
# web projects Josui works on run their unit tests with it. Full ICU with
# it: without the locale data, tests that format a date pass in the site's
# container and fail here.
RUN microdnf install -y --setopt=install_weak_deps=0 --nodocs \
        findutils grep sed gawk tar gzip which procps-ng \
        ca-certificates git-core openssh-clients jq python3 \
        nodejs nodejs-full-i18n npm && \
    microdnf clean all

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

RUN chmod 0755 /usr/local/bin/josui-entrypoint /usr/local/libexec/josui/hooks.py \
        /usr/local/libexec/josui/allow-hook.sh /usr/local/libexec/josui/deny-local-memory.sh && \
    python3 -m py_compile /usr/local/libexec/josui/hooks.py && \
    bash -n /usr/local/libexec/josui/allow-hook.sh && \
    bash -n /usr/local/libexec/josui/deny-local-memory.sh && \
    jq -e . /etc/claude-code/managed-settings.json > /dev/null

# HOME is a volume: the Claude login, sessions and git identity live
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
