"""Pi sessions: one entry per running ``pi`` process with its tree PSS/RSS.

Read-only and unprivileged: /proc of the same user and the first line plus the
``session_info`` entries of Pi session files. Never writes, never signals,
never exports prompts or environment values other than the session id.

Tree: the pi process plus every descendant via ppid -- through shells, MCP
servers, LSPs -- stopping at a nested pi, which is a subagent with its own
entry. PSS is sampled only for those trees (PID-reuse checked); RSS comes from
the ps snapshot. Trees are disjoint, so entries can be added up among
themselves, but they overlap the watch groups: never add them to group RSS.

Session identity, most to least reliable (``session_source``):
  environ     PI_SESSION_ID / PI_SESSION_FILE in a descendant's environ (Pi
              sets them for bash tool children). Exact while such a child runs.
  start_time  the session file name carries its creation time; a new session
              is created within a second of the process start. High: unique
              file in the window with the same header cwd, not claimed by
              another process.
  recent_mtime  last resort, low: the only unclaimed file in the cwd's session
              dir written since the process started, and only one unmapped pi
              in that cwd. Covers resumed sessions; flagged as probable.
Anything else stays unmapped and is named «<cwd basename> · pid <PID>».
"""

import datetime
import json
import mmap
import os
import re
from pathlib import Path

from macacoview_programs import (
    _coverage,
    _proc_identity,
    _program_identity,
    _sample_pss,
)

PI_COMM = "pi"
# btime has whole-second resolution, so allow a little before the start.
START_WINDOW_S = (-2.0, 5.0)
LINE_LIMIT = 64 * 1024
NAME_LIMIT = 80
_FILE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d-\d{3})Z_([0-9A-Za-z-]+)\.jsonl$")
_INFO_MARKER = b'\n{"type":"session_info"'
_ENV_KEYS = (b"PI_SESSION_ID", b"PI_SESSION_FILE")


def session_dir_name(cwd):
    """Pi's rule: drop the leading separator, then / \\ : become «-»."""
    trimmed = re.sub(r"^[/\\]", "", cwd)
    return "--" + re.sub(r"[/\\:]", "-", trimmed) + "--"


def _clean_name(value):
    if not isinstance(value, str):
        return None
    text = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return text[:NAME_LIMIT] or None


def read_session_file(path):
    """(header, name) from a session JSONL, reading only what is needed.

    The header is the first line; the name is the last ``session_info`` entry,
    found by searching the mapped file for the entry's own prefix, so message
    lines are never decoded.
    """
    header, name = None, None
    try:
        with open(path, "rb") as handle:
            first = handle.readline(LINE_LIMIT)
            try:
                parsed = json.loads(first)
                if isinstance(parsed, dict) and parsed.get("type") == "session":
                    header = parsed
            except ValueError:
                pass
            try:
                mapped = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
            except (ValueError, OSError):
                return header, None
            with mapped:
                pos = mapped.rfind(_INFO_MARKER)
                while pos != -1:
                    end = mapped.find(b"\n", pos + 1, pos + 1 + LINE_LIMIT)
                    line = mapped[pos + 1:end if end != -1 else pos + 1 + LINE_LIMIT]
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        pos = mapped.rfind(_INFO_MARKER, 0, pos)
                        continue
                    if isinstance(entry, dict) and entry.get("type") == "session_info":
                        name = _clean_name(entry.get("name"))
                    break
    except OSError:
        return None, None
    return header, name


def _file_time(name):
    match = _FILE.match(name)
    if match is None:
        return None
    try:
        stamp = datetime.datetime.strptime(match.group(1) + "+0000", "%Y-%m-%dT%H-%M-%S-%f%z")
    except ValueError:
        return None
    return stamp.timestamp()


def _listing(directory):
    """[(path, created_ts, mtime)] for the session files of one directory."""
    entries = []
    try:
        names = os.listdir(directory)
    except OSError:
        return entries
    for name in names:
        created = _file_time(name)
        if created is None:
            continue
        path = Path(directory) / name
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        entries.append((path, created, mtime))
    return entries


def _environ_session(directory):
    try:
        raw = (directory / "environ").read_bytes()
    except OSError:
        return None, None
    found = {}
    for item in raw.split(b"\0"):
        key, sep, value = item.partition(b"=")
        if sep and key in _ENV_KEYS:
            found[key] = os.fsdecode(value)
    return found.get(b"PI_SESSION_ID") or None, found.get(b"PI_SESSION_FILE") or None


def _measure(proc_root, process):
    """(pss_kb | None, status, starttime | None) with programs' PID-reuse checks."""
    directory = proc_root / str(process["pid"])
    try:
        expected = _proc_identity(directory)
    except (FileNotFoundError, ProcessLookupError):
        return None, "raced", None
    except (OSError, ValueError, IndexError):
        return None, "unreadable", None
    identity = _program_identity(process, expected[1], directory)
    _, pss, status = _sample_pss(directory, expected, process, identity)
    return pss, status, expected[0]


def _btime(proc_root):
    try:
        for line in (proc_root / "stat").read_text().splitlines():
            if line.startswith("btime "):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def collect_pi_sessions(processes, proc_root=Path("/proc"), agent_dir=None,
                        btime=None, clk_tck=None):
    """One dict per pi process in the ps snapshot, sorted by PID."""
    proc_root = Path(proc_root)
    agent_dir = Path(agent_dir) if agent_dir is not None else Path.home() / ".pi" / "agent"
    btime = btime if btime is not None else _btime(proc_root)
    clk_tck = clk_tck or os.sysconf("SC_CLK_TCK")

    by_pid = {p["pid"]: p for p in processes}
    kids = {}
    for process in processes:
        kids.setdefault(process["ppid"], []).append(process["pid"])
    roots = sorted(pid for pid, p in by_pid.items() if p["comm"] == PI_COMM)
    root_set = set(roots)

    entries = {}
    for root in roots:
        tree, seen, stack = [root], {root}, list(kids.get(root, ()))
        while stack:
            pid = stack.pop()
            if pid in seen or pid in root_set:
                continue
            seen.add(pid)
            tree.append(pid)
            stack.extend(kids.get(pid, ()))
        tree.sort()

        parent, current, guard = None, by_pid[root]["ppid"], {root}
        while current in by_pid and current not in guard:
            if current in root_set:
                parent = current
                break
            guard.add(current)
            current = by_pid[current]["ppid"]

        counts = {"measured": 0, "unreadable": 0, "raced": 0, "not_sampled": 0}
        pss_sum, root_pss, start = None, None, None
        env_hits = []
        for pid in tree:
            pss, status, starttime = _measure(proc_root, by_pid[pid])
            counts[status] += 1
            if pss is not None:
                pss_sum = (pss_sum or 0) + pss
            if pid == root:
                root_pss, start = pss, starttime
            else:
                session_id, session_file = _environ_session(proc_root / str(pid))
                if session_id or session_file:
                    env_hits.append((starttime or 0, session_id, session_file))
        try:
            cwd = os.readlink(proc_root / str(root) / "cwd")
        except OSError:
            cwd = None
        started = (btime + start / clk_tck) if btime is not None and start is not None else None
        entries[root] = {
            "pid": root, "parent_pid": parent, "cwd": cwd,
            "cwd_name": (os.path.basename(cwd.rstrip("/")) or cwd) if cwd else PI_COMM,
            "pids": tree, "pid_count": len(tree),
            "children": len(kids.get(root, ())),
            "rss_kb": sum(by_pid[pid]["rss_kb"] for pid in tree),
            "pss_kb": pss_sum,
            "pi_rss_kb": by_pid[root]["rss_kb"], "pi_pss_kb": root_pss,
            "pss_coverage": _coverage(len(tree), **counts),
            "_started": started, "_env": env_hits,
        }

    _map_sessions(entries, agent_dir)

    sessions = []
    for root in roots:
        entry = entries[root]
        entry.pop("_started")
        entry.pop("_env")
        parent = entries.get(entry["parent_pid"]) if entry["parent_pid"] else None
        entry["parent_session_id"] = parent["session_id"] if parent else None
        sessions.append(entry)
    return sessions


def _assign(entry, path, header, name, source, confidence):
    session_id = (header or {}).get("id")
    if not isinstance(session_id, str) or not session_id:
        match = _FILE.match(Path(path).name) if path else None
        session_id = match.group(2) if match else None
    entry.update(session_id=session_id, session_source=source,
                 session_confidence=confidence, _name=name)


def _map_sessions(entries, agent_dir):
    sessions_root = agent_dir / "sessions"
    gentle = agent_dir / "gentle-agents" / "sessions"
    listings = {}

    def listing(directory):
        if directory not in listings:
            listings[directory] = _listing(directory)
        return listings[directory]

    headers = {}

    def read(path):
        if path not in headers:
            headers[path] = read_session_file(path)
        return headers[path]

    def candidate_dirs(entry):
        dirs = [gentle]
        if entry["cwd"]:
            dirs.insert(0, sessions_root / session_dir_name(entry["cwd"]))
        return dirs

    for entry in entries.values():
        entry.update(session_id=None, session_source=None, session_confidence=None, _name=None)

    claimed = set()
    # 1. environ of a descendant: the newest process names the current session.
    for entry in entries.values():
        if not entry["_env"]:
            continue
        distinct = {(sid, sfile) for _, sid, sfile in entry["_env"]}
        _, session_id, session_file = max(entry["_env"], key=lambda hit: hit[0])
        path = None
        if session_file and session_file.endswith(".jsonl") and Path(session_file).is_file():
            path = Path(session_file)
        elif session_id:
            for directory in candidate_dirs(entry):
                path = next((p for p, _, _ in listing(directory)
                             if p.name.endswith(f"_{session_id}.jsonl")), None)
                if path:
                    break
        header, name = read(path) if path else (None, None)
        if path:
            claimed.add(path)
        confidence = "exact" if len(distinct) == 1 else "high"
        _assign(entry, path, header, name, "environ", confidence)
        if session_id and entry["session_id"] != session_id and header is None:
            entry["session_id"] = session_id

    # 2. start time: unique, uncontested file created right after the start.
    pending = {}
    for pid, entry in entries.items():
        if entry["session_source"] or entry["_started"] is None or not entry["cwd"]:
            continue
        found = set()
        for directory in candidate_dirs(entry):
            for path, created, _ in listing(directory):
                delta = created - entry["_started"]
                if START_WINDOW_S[0] <= delta <= START_WINDOW_S[1] and path not in claimed:
                    header, _ = read(path)
                    if header and header.get("cwd") == entry["cwd"]:
                        found.add(path)
        if found:
            pending[pid] = found
    changed = True
    while changed:
        changed = False
        for pid, found in list(pending.items()):
            found -= claimed
            if len(found) != 1:
                continue
            (path,) = found
            if any(path in other for o, other in pending.items() if o != pid):
                continue
            header, name = read(path)
            _assign(entries[pid], path, header, name, "start_time", "high")
            claimed.add(path)
            del pending[pid]
            changed = True

    # 3. last resort, flagged low: one unmapped pi, one recent file in its cwd.
    unmapped = {}
    for pid, entry in entries.items():
        if not entry["session_source"] and entry["cwd"]:
            unmapped.setdefault(entry["cwd"], []).append(pid)
    for cwd, pids in unmapped.items():
        if len(pids) != 1:
            continue
        entry = entries[pids[0]]
        if entry["_started"] is None:
            continue
        recent = []
        for path, _, mtime in listing(sessions_root / session_dir_name(cwd)):
            if path in claimed or mtime < entry["_started"]:
                continue
            header, _ = read(path)
            if header and header.get("cwd") == cwd:
                recent.append(path)
        if len(recent) == 1:
            header, name = read(recent[0])
            _assign(entry, recent[0], header, name, "recent_mtime", "low")
            claimed.add(recent[0])

    for entry in entries.values():
        name = entry.pop("_name")
        if name:
            entry["name"], entry["name_source"] = name, "session_info"
        elif entry["session_id"]:
            entry["name"] = f"{entry['cwd_name']} · {entry['session_id'][-4:]}"
            entry["name_source"] = "session_id"
        else:
            entry["name"] = f"{entry['cwd_name']} · pid {entry['pid']}"
            entry["name_source"] = "pid"
