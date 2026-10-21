#!/usr/bin/env python3
"""A Claude Code channel that carries a Matrix DM into the session and back.

Claude Code starts this as a stdio MCP server and loads it as a channel.
It logs in to the agent's own homeserver with an access token, long-polls
/sync, and pushes each message from an allowed sender into the session as
a channel event. Claude answers with the reply tool, which sends into the
room the message came from.

Only senders named in JOSUI_MATRIX_ALLOW get through, checked on the
sender and never on the room: anyone who can post in a room the agent sits
in would otherwise be able to prompt it. Invites are joined only from the
same senders.

A text that is one of the built-in commands in COMMANDS never reaches
Claude: only the session's input box runs a built-in. It goes, checked,
to the control FIFO in JOSUI_CONTROL_FIFO, where channel-session types it
into the session. Every other text, skills included, is a channel event
as before. The command's result is the only text back: Claude Code writes
it to the session transcript, and Results finds it there.

Standard library only; the homeserver is plaintext on an internal network.
"""

from __future__ import annotations

import errno
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

NAME = "matrix"
SYNC_TIMEOUT_MS = 30000
REQUEST_TIMEOUT_SECONDS = 30
METHOD_NOT_FOUND = -32601
RETRY_SECONDS = 5
INSTRUCTIONS = (
    "Messages from the operator arrive as <channel source=\"matrix\" room_id=... sender=...>. "
    "They come from his phone. Always answer with the reply tool, passing the room_id from the "
    "tag: text you write in the session does not reach him. Keep replies short and plain, like "
    "a text message, with no Markdown. For long work, reply first that you are on it, then "
    "reply again when it is done."
)
# Built-ins that work from a typed line, with no dialog and no one reading
# the screen: name -> (arguments pattern, usage). channel-session allows the
# same names. Arguments of the commands outside FREE_TEXT are lowercased.
COMMANDS = {
    "clear": (r".{0,500}", "/clear [name]"),
    "compact": (r".{0,500}", "/compact [instructions]"),
    "model": (r"[\w.\[\]-]{1,100}", "/model <alias or id>"),
    "effort": (r"low|medium|high|xhigh|max|auto", "/effort low|medium|high|xhigh|max|auto"),
    "fast": (r"on|off", "/fast on|off"),
    "goal": (r".{1,500}", "/goal <condition>|clear"),
    "mcp": (r"reconnect [\w.-]{1,100}", "/mcp reconnect <server>|all"),
    "reload-skills": (r"", "/reload-skills"),
    "reload-plugins": (r"", "/reload-plugins"),
}
FREE_TEXT = {"clear", "compact", "goal"}
HELP = (
    "Commands by text: " + ", ".join(usage for _, usage in COMMANDS.values())
    + ". They run after any reply in progress. Anything else, skills included, goes to the session."
)
NOT_READY = "The session is not taking commands yet. Try again in a minute."
RESULT_TIMEOUT_SECONDS = 900
POLL_SECONDS = 1
OUTPUT_CHARS = 1000
SUMMARY_CHARS = 3000
OUTPUT = re.compile(r"<local-command-(stdout|stderr)>(.*?)</local-command-\1>", re.S)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
REPLY_TOOL = {
    "name": "reply",
    "description": "Send a text message to the operator in a Matrix room he wrote from.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "room_id": {"type": "string", "description": "room_id from the channel tag"},
            "text": {"type": "string", "description": "the message"},
        },
        "required": ["room_id", "text"],
    },
}


def log(message: str) -> None:
    print(f"matrix-channel: {message}", file=sys.stderr, flush=True)


class Homeserver:
    """The few client-server API calls the channel makes."""

    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self.token = token

    def call(
        self, method: str, path: str, body: dict | None = None, timeout: float = REQUEST_TIMEOUT_SECONDS
    ) -> dict:
        payload = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=payload, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")

    def whoami(self) -> str:
        return self.call("GET", "/_matrix/client/v3/account/whoami")["user_id"]

    def sync(self, since: str | None) -> dict:
        query = {"timeout": str(SYNC_TIMEOUT_MS if since else 0)}
        if since:
            query["since"] = since
        path = "/_matrix/client/v3/sync?" + urllib.parse.urlencode(query)
        return self.call("GET", path, timeout=SYNC_TIMEOUT_MS / 1000 + REQUEST_TIMEOUT_SECONDS)

    def join(self, room_id: str) -> None:
        self.call("POST", f"/_matrix/client/v3/join/{urllib.parse.quote(room_id, safe='')}", {})

    def send_text(self, room_id: str, text: str) -> None:
        room = urllib.parse.quote(room_id, safe="")
        path = f"/_matrix/client/v3/rooms/{room}/send/m.room.message/{uuid.uuid4().hex}"
        self.call("PUT", path, {"msgtype": "m.text", "body": text})


class Channel:
    def __init__(
        self, homeserver: Homeserver, allow: set[str], state: Path, control: Path | None = None,
        results: Results | None = None,
    ) -> None:
        self.homeserver = homeserver
        self.allow = allow
        self.state = state
        self.control = control
        self.results = results or Results(Path.home() / ".claude", self.tell)
        self.user_id = ""
        self.rooms: set[str] = set()
        self.write_lock = threading.Lock()
        self.started = False

    # MCP over stdio ---------------------------------------------------

    def send(self, message: dict) -> None:
        with self.write_lock:
            sys.stdout.write(json.dumps(message) + "\n")
            sys.stdout.flush()

    def result(self, request_id: object, result: dict) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def handle(self, message: dict) -> None:
        request_id = message.get("id")
        match message.get("method"):
            case "initialize":
                self.result(request_id, {
                    "protocolVersion": message["params"]["protocolVersion"],
                    "capabilities": {"tools": {}, "experimental": {"claude/channel": {}}},
                    "serverInfo": {"name": NAME, "version": "1.0.0"},
                    "instructions": INSTRUCTIONS,
                })
            case "notifications/initialized":
                self.start()
            case "tools/list":
                self.result(request_id, {"tools": [REPLY_TOOL]})
            case "tools/call":
                self.result(request_id, self.call_tool(message["params"]))
            case _ if request_id is not None:
                # Method not found, never an empty success: Claude Code probes
                # with server/discover first, and a result there negotiates the
                # stateless protocol revision, which has no path for channel
                # notifications, so the session would silently skip the channel.
                error = {"code": METHOD_NOT_FOUND, "message": f"no method {message.get('method')}"}
                self.send({"jsonrpc": "2.0", "id": request_id, "error": error})

    def call_tool(self, params: dict) -> dict:
        args = params.get("arguments") or {}
        if params.get("name") != "reply":
            return error_result(f"no tool {params.get('name')}")
        room_id, text = str(args.get("room_id", "")), str(args.get("text", ""))
        if room_id not in self.rooms:
            return error_result(f"{room_id} is not a room the operator wrote from")
        if not text.strip():
            return error_result("empty message")
        try:
            self.homeserver.send_text(room_id, text)
        except (urllib.error.URLError, OSError) as exc:
            return error_result(f"send failed: {exc}")
        return {"content": [{"type": "text", "text": "sent"}]}

    # Matrix -------------------------------------------------------------

    def start(self) -> None:
        # Events go out only after Claude Code has finished the handshake;
        # it drops channel events it has not registered a listener for.
        if not self.started:
            self.started = True
            threading.Thread(target=self.run, daemon=True).start()

    def run(self) -> None:
        while not self.user_id:
            try:
                self.user_id = self.homeserver.whoami()
            except (urllib.error.URLError, OSError, KeyError) as exc:
                log(f"whoami failed, retrying: {exc}")
                time.sleep(RETRY_SECONDS)
        log(f"signed in as {self.user_id}")
        since = self.load_since()
        while True:
            try:
                batch = self.homeserver.sync(since)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                log(f"sync failed, retrying: {exc}")
                time.sleep(RETRY_SECONDS)
                continue
            # The first sync of a new store sets the mark without replaying
            # history; after that, messages that came in while the session
            # was down are delivered when it is back.
            self.process(batch, deliver=since is not None)
            since = batch.get("next_batch", since)
            self.save_since(since)

    def process(self, batch: dict, deliver: bool) -> None:
        rooms = batch.get("rooms", {})
        for room_id, room in rooms.get("invite", {}).items():
            self.consider_invite(room_id, room)
        for room_id, room in rooms.get("join", {}).items():
            for event in room.get("timeline", {}).get("events", []):
                self.consider_event(room_id, event, deliver)

    def consider_invite(self, room_id: str, room: dict) -> None:
        for event in room.get("invite_state", {}).get("events", []):
            if (event.get("type") == "m.room.member" and event.get("state_key") == self.user_id
                    and event.get("content", {}).get("membership") == "invite"):
                if event.get("sender") not in self.allow:
                    log(f"ignored an invite to {room_id} from {event.get('sender')}")
                    return
                try:
                    self.homeserver.join(room_id)
                    self.rooms.add(room_id)
                    log(f"joined {room_id} on an invite from {event['sender']}")
                except (urllib.error.URLError, OSError) as exc:
                    log(f"join {room_id} failed: {exc}")
                return

    def consider_event(self, room_id: str, event: dict, deliver: bool) -> None:
        sender = event.get("sender")
        if event.get("type") != "m.room.message" or sender == self.user_id:
            return
        if sender not in self.allow:
            log(f"dropped a message in {room_id} from {sender}")
            return
        self.rooms.add(room_id)
        body = event.get("content", {}).get("body")
        if deliver and isinstance(body, str) and body.strip():
            if self.control and self.command(room_id, body):
                return
            self.send({
                "jsonrpc": "2.0",
                "method": "notifications/claude/channel",
                "params": {"content": body, "meta": {"room_id": room_id, "sender": sender}},
            })

    # Built-in commands -------------------------------------------------

    def command(self, room_id: str, body: str) -> bool:
        """Relay a built-in command and answer it; False if body is not one."""
        text = " ".join(body.split())
        if not text.startswith("/"):
            return False
        name, _, args = text[1:].partition(" ")
        # Phone keyboards capitalize and punctuate.
        name = name.lower().rstrip(".!?")
        if name == "help":
            self.tell(room_id, HELP)
            return True
        if name not in COMMANDS:
            return False
        pattern, usage = COMMANDS[name]
        if name not in FREE_TEXT:
            args = args.lower().rstrip(".!?")
        if not re.fullmatch(pattern, args) or not args.isprintable():
            self.tell(room_id, f"Usage: {usage}")
            return True
        line = f"/{name} {args}".rstrip()
        pending = self.results.expect(name, room_id)
        if not self.relay(line):
            self.results.forget(pending)
            self.tell(room_id, NOT_READY)
        return True

    def relay(self, line: str) -> bool:
        try:
            fd = os.open(self.control, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            # ENXIO: channel-session has not opened it yet.
            if exc.errno not in (errno.ENXIO, errno.ENOENT):
                log(f"control FIFO: {exc}")
            return False
        try:
            os.write(fd, (line + "\n").encode())
            log(f"relayed {line.split()[0]}")  # arguments can be private
            return True
        except OSError as exc:
            log(f"control FIFO: {exc}")
            return False
        finally:
            os.close(fd)

    def tell(self, room_id: str, text: str) -> None:
        try:
            self.homeserver.send_text(room_id, text)
        except (urllib.error.URLError, OSError) as exc:
            log(f"send to {room_id} failed: {exc}")

    def load_since(self) -> str | None:
        try:
            return self.state.read_text().strip() or None
        except OSError:
            return None

    def save_since(self, since: str | None) -> None:
        if since:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            staged = self.state.with_suffix(".tmp")
            staged.write_text(since)
            staged.replace(self.state)


class Pending:
    """A relayed command whose result has not been found yet."""

    def __init__(self, name: str, room_id: str, deadline: float) -> None:
        self.name = name
        self.room_id = room_id
        self.deadline = deadline
        self.seen = False  # its own record is in the transcript
        self.compacted: dict | None = None
        self.summary: str | None = None


class Results:
    """Finds each relayed command's result in the session transcript.

    Every built-in leaves a record of itself and its output in the
    transcript, and /compact also leaves its token counts and summary. Only
    the channel session's own transcript is read: Claude Code names the
    current session of each process in sessions/<pid>.json, and that
    process is this server's parent. /clear starts a new session, so a
    transcript that appears while a command is pending is read from the
    start.
    """

    def __init__(
        self, claude_dir: Path, tell: Callable[[str, str], None], timeout: float = RESULT_TIMEOUT_SECONDS,
        pid: int | None = None,
    ) -> None:
        self.claude_dir = claude_dir
        self.pid = os.getppid() if pid is None else pid
        self.tell = tell
        self.timeout = timeout
        self.pending: list[Pending] = []
        self.offsets: dict[Path, int] = {}
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def expect(self, name: str, room_id: str) -> Pending:
        """Watch for name's result; call before relaying, so none is missed."""
        with self.lock:
            if not self.pending:
                self.offsets = {path: path.stat().st_size for path in self.files()}
            pending = Pending(name, room_id, time.monotonic() + self.timeout)
            self.pending.append(pending)
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, daemon=True)
                self.thread.start()
        return pending

    def forget(self, pending: Pending) -> None:
        with self.lock:
            self.pending.remove(pending)

    def files(self) -> list[Path]:
        """The channel session's transcript, if there is one yet."""
        try:
            session = json.loads((self.claude_dir / f"sessions/{self.pid}.json").read_text())["sessionId"]
            if not re.fullmatch(r"[0-9a-f-]{36}", session):
                return []
            return list((self.claude_dir / "projects").glob(f"*/{session}.jsonl"))
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def run(self) -> None:
        while True:
            time.sleep(POLL_SECONDS)
            with self.lock:
                texts = self.poll() if self.pending else []
            for room_id, text in texts:
                self.tell(room_id, text)

    def poll(self) -> list[tuple[str, str]]:
        texts = []
        for record in self.records():
            texts += self.consider(record)
        now = time.monotonic()
        for pending in [p for p in self.pending if p.deadline < now]:
            self.pending.remove(pending)
            log(f"no result for /{pending.name}")
            minutes = max(1, round(self.timeout / 60))
            texts.append((pending.room_id, f"No result from /{pending.name} within {minutes} min."))
        return texts

    def records(self) -> list[dict]:
        """The complete lines written since the last poll, parsed."""
        found = []
        for path in self.files():
            offset = self.offsets.get(path, 0)
            try:
                with path.open("rb") as transcript:
                    transcript.seek(offset)
                    appended = transcript.read()
            except OSError as exc:
                log(f"transcript {path.name}: {exc}")
                continue
            appended = appended[:appended.rfind(b"\n") + 1]
            self.offsets[path] = offset + len(appended)
            for line in appended.splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    found.append(record)
        return found

    def consider(self, record: dict) -> list[tuple[str, str]]:
        message = record.get("message")
        text = message.get("content") if isinstance(message, dict) else record.get("content")
        text = text if isinstance(text, str) else ""
        if record.get("subtype") == "compact_boundary" or record.get("isCompactSummary"):
            compact = next((p for p in self.pending if p.name == "compact"), None)
            if compact and record.get("isCompactSummary"):
                compact.summary = text
            elif compact:
                compact.compacted = record.get("compactMetadata") or {}
            return []
        run = record.get("commandRun") or {}
        for pending in self.pending:
            if not pending.seen and (run.get("command") == pending.name
                                     or f"<command-name>/{pending.name}</command-name>" in text):
                pending.seen = True
                break
        output = OUTPUT.search(text)
        pending = next((p for p in self.pending if p.seen), None)
        if not output or not pending:
            return []
        self.pending.remove(pending)
        log(f"result for /{pending.name}")
        return [(pending.room_id, text) for text in answer(pending, output[1], output[2])]


def answer(pending: Pending, stream: str, output: str) -> list[str]:
    """The texts that report a command's output."""
    output = ANSI.sub("", output).strip()
    if pending.name == "compact" and stream == "stdout" and pending.compacted is not None:
        meta = pending.compacted
        texts = [f"Compacted {meta.get('preTokens', 0):,} → {meta.get('postTokens', 0):,} tokens"
                 f" in {round(meta.get('durationMs', 0) / 1000)}s."]
        summary = (pending.summary or "").partition("Summary:")[2].strip() or (pending.summary or "")
        if len(summary) > SUMMARY_CHARS:
            summary = summary[:SUMMARY_CHARS].rstrip() + f"… (trimmed, {len(summary):,} chars)"
        return texts + [summary] if summary.strip() else texts
    if len(output) > OUTPUT_CHARS:
        output = output[:OUTPUT_CHARS].rstrip() + "…"
    return [f"/{pending.name}: {output or 'done'}"]


def error_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def main() -> int:
    env = os.environ
    try:
        homeserver = env["JOSUI_MATRIX_HOMESERVER"]
        token = Path(env["JOSUI_MATRIX_TOKEN_FILE"]).read_text().strip()
    except (KeyError, OSError) as exc:
        log(f"not configured: {exc}")
        return 1
    allow = {mxid.strip() for mxid in env.get("JOSUI_MATRIX_ALLOW", "").split(",") if mxid.strip()}
    if not allow:
        log("JOSUI_MATRIX_ALLOW is empty: no message will get through")
    state = Path(env.get("JOSUI_MATRIX_STATE", Path.home() / ".config/josui/matrix-sync"))
    control = Path(env["JOSUI_CONTROL_FIFO"]) if env.get("JOSUI_CONTROL_FIFO") else None
    channel = Channel(Homeserver(homeserver, token), allow, state, control)
    # Tests point these at a stand-in Claude Code home.
    if env.get("JOSUI_CLAUDE_DIR"):
        timeout = float(env.get("JOSUI_RESULT_TIMEOUT", RESULT_TIMEOUT_SECONDS))
        channel.results = Results(Path(env["JOSUI_CLAUDE_DIR"]), channel.tell, timeout)
    for line in sys.stdin:
        if line.strip():
            channel.handle(json.loads(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
