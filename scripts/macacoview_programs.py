"""Unprivileged program discovery and independent PSS/RSS accounting.

No collection at import time; callers supply the ps snapshot. Program fields
never export interpreter arguments. Watch/model/GPU measurements overlap.
"""

import hashlib
import json
import os
import re
import shlex
from pathlib import Path

# Program IDs are versioned, opaque and independent of PIDs. Arguments are
# used privately only to distinguish interpreter entry points; never exported.
_INTERPRETERS = re.compile(
    r"^(python(?:\d+(?:\.\d+)*)?|pypy\d*|node(?:js)?|java|ruby(?:\d+(?:\.\d+)*)?|"
    r"perl|php(?:\d+(?:\.\d+)*)?|lua(?:\d+(?:\.\d+)*)?|dotnet|electron|Rscript)$"
)
_ANCESTRY_BARRIERS = {
    "sh", "bash", "dash", "zsh", "fish", "ksh", "csh", "tcsh", "nu", "pwsh",
    "gnome-terminal", "gnome-terminal-server", "konsole", "xterm", "uxterm",
    "alacritty", "kitty", "wezterm", "wezterm-gui", "tmux", "screen",
    "sshd", "systemd", "init", "sudo", "su",
}
_GENERIC_DIRS = {
    "/", "/bin", "/sbin", "/usr", "/usr/bin", "/usr/sbin", "/usr/lib",
    "/usr/lib64", "/usr/libexec", "/usr/local", "/usr/local/bin",
    "/usr/local/lib", "/opt", "/app", "/app/bin",
}


def _executable(directory):
    try:
        return os.readlink(directory / "exe").removesuffix(" (deleted)")
    except OSError:
        return None


def _proc_identity(directory):
    # comm may contain spaces or ')'; starttime is field 22 after the last ')'.
    stat = (directory / "stat").read_text()
    fields = stat.rsplit(")", 1)[1].split()
    return int(fields[19]), _executable(directory)


def _sample_pss(directory, expected, process, identity):
    executable = _executable(directory)
    try:
        before = _proc_identity(directory)
    except (FileNotFoundError, ProcessLookupError):
        return executable, None, "raced"
    except (OSError, ValueError, IndexError):
        return executable, None, "unreadable"
    if before != expected or _program_identity(process, before[1], directory)["id"] != identity["id"]:
        return before[1], None, "raced"
    pss = None
    try:
        text = (directory / "smaps_rollup").read_text()
        lines = [line for line in text.splitlines() if line.startswith("Pss:")]
        if len(lines) != 1:
            raise ValueError("missing or duplicate Pss")
        match = re.fullmatch(r"Pss:\s+(\d+)\s+kB\s*", lines[0])
        if match is None:
            raise ValueError("invalid Pss")
        pss = int(match.group(1))
    except (OSError, ValueError, UnicodeError):
        pass
    try:
        after = _proc_identity(directory)
    except (FileNotFoundError, ProcessLookupError):
        return before[1], None, "raced"
    except (OSError, ValueError, IndexError):
        return before[1], None, "unreadable"
    if before != after or _program_identity(process, after[1], directory)["id"] != identity["id"]:
        return before[1], None, "raced"
    return before[1], pss, "measured" if pss is not None else "unreadable"


def _launch_key(process, directory, runtime):
    """Private interpreter entry point; ordinary application options ignored."""
    args = process.get("args", "")
    try:
        raw = (directory / "cmdline").read_bytes()
        argv = [os.fsdecode(value) for value in raw.rstrip(b"\0").split(b"\0")][1:]
    except OSError:
        try:
            argv = shlex.split(args)[1:]
        except ValueError:
            argv = []
    try:
        cwd = os.readlink(directory / "cwd")
    except OSError:
        cwd = ""
    context = []
    options_with_value = {
        "-X", "-W", "-cp", "-classpath", "--class-path", "-p", "--module-path",
        "-r", "--require", "--loader", "--import", "--inspect-port",
    }
    while argv:
        item = argv.pop(0)
        option, separator, value = item.partition("=")
        if separator and option in {"--class-path", "--module-path", "--module"}:
            item = option
            argv.insert(0, value)
        if item in {"-m", "--module", "-c", "-e", "--eval", "-jar"}:
            if argv:
                extra = cwd if item in {"-m", "--module"} and not context else ""
                target = argv[0]
                if item == "-jar" and cwd and not os.path.isabs(target):
                    target = os.path.normpath(os.path.join(cwd, target))
                return json.dumps([context, item, target, extra])
            break
        if item in options_with_value:
            if argv and item in {"-cp", "-classpath", "--class-path", "-p", "--module-path"}:
                paths = [os.path.normpath(os.path.join(cwd, path or "."))
                         for path in argv[0].split(os.pathsep)]
                context.append([item, paths])
            argv = argv[1:]
            continue
        if item.startswith("-"):
            continue
        if runtime == "java":
            # A Java main class is not a script path. The implicit classpath
            # is cwd; explicit classpaths above are already normalized.
            return json.dumps([context, "class", item, "" if context else cwd])
        if cwd and not os.path.isabs(item):
            item = os.path.normpath(os.path.join(cwd, item))
        return json.dumps([context, "entry", item])
    # Interactive/opaque launchers must not collapse distinguishable invocations.
    # The fallback is hashed as part of the ID, not emitted as arguments or labels.
    return "opaque:" + args


def _program_identity(process, executable, directory):
    name = Path(executable).name if executable else process["comm"]
    interpreter = bool(_INTERPRETERS.fullmatch(name))
    key = ("exe:" + executable) if executable else ("comm:" + process["comm"])
    if interpreter:
        key += "\0" + _launch_key(process, directory, name)
    return {
        "id": "program:v1:" + hashlib.sha256(key.encode("utf-8", errors="surrogateescape")).hexdigest(),
        "name": name,
        "executable": executable,
        "identity_source": "executable" if executable else "comm",
        "interpreter": interpreter,
        "barrier": name in _ANCESTRY_BARRIERS or process["comm"] in _ANCESTRY_BARRIERS,
    }


def _same_app(parent, child):
    if parent["identity"]["barrier"] or child["identity"]["barrier"]:
        return False
    if parent["process"]["uid"] != child["process"]["uid"]:
        return False
    a, b = parent["identity"], child["identity"]
    if a["id"] == b["id"]:
        return True
    if a["interpreter"] or b["interpreter"]:
        return False
    if not a["executable"] or not b["executable"]:
        return False
    directory = str(Path(a["executable"]).parent)
    # Only a private application install subtree, never generic bin/lib roots.
    return (directory not in _GENERIC_DIRS
            and Path(directory).name not in {"bin", "sbin", "lib", "lib64", "libexec"}
            and b["executable"].startswith(directory + "/"))


def _coverage(total, measured, unreadable, raced, not_sampled):
    if measured == total:
        status = "full"
    elif not_sampled == total:
        status = "not_sampled"
    elif measured or not_sampled:
        status = "partial"
    elif raced == total:
        status = "race"
    else:
        status = "unreadable"
    return {"status": status, "total": total, "measured": measured,
            "unreadable": unreadable, "raced": raced, "not_sampled": not_sampled}


def discover_programs(processes, proc_root=Path("/proc"), selected_ids=()):
    """All supplied ps records, without thresholds or top-N truncation.

    Discover/group first; read PSS only for configured stable group IDs.
    Omitted, empty or None selected_ids never reads smaps. Unselected processes
    count as not_sampled, not unreadable; ancestry alone does not select a group.
    PSS is a measured subset when coverage is partial, None when unavailable.
    memory_kb uses PSS only with full coverage, otherwise snapshot RSS (which
    includes shared-page duplication and possibly vanished processes). These
    fields and legacy watch/model/GPU measurements overlap; do not add them.
    'complete' means no truncation of the supplied snapshot, not all system RAM.
    """
    proc_root = Path(proc_root)
    selected_ids = set(selected_ids or ())
    nodes = {}
    for process in processes:
        directory = proc_root / str(process["pid"])
        executable = _executable(directory)
        expected, discovery_error = None, None
        try:
            expected = _proc_identity(directory)
            executable = expected[1]
        except (FileNotFoundError, ProcessLookupError):
            discovery_error = "raced"
        except (OSError, ValueError, IndexError):
            discovery_error = "unreadable"
        nodes[process["pid"]] = {
            "process": process, "pss": None, "status": "not_sampled",
            "expected": expected, "discovery_error": discovery_error,
            "identity": _program_identity(process, executable, directory),
        }
    parents = {}
    for pid, node in nodes.items():
        parent = nodes.get(node["process"]["ppid"])
        if parent is not None and _same_app(parent, node):
            parents[pid] = node["process"]["ppid"]

    roots = {}
    for pid in nodes:
        trail, seen = [], set()
        current = pid
        while current not in roots and current not in seen:
            trail.append(current)
            seen.add(current)
            if current not in parents:
                break
            current = parents[current]
        if current in roots:
            root = roots[current]
        elif current in parents and parents[current] in seen:
            # Defensive handling of corrupt/test snapshots with ancestry cycles.
            cycle_start = trail.index(current)
            root = min(trail[cycle_start:])
        else:
            root = current
        for item in trail:
            roots[item] = root

    groups = {}
    for pid in sorted(nodes):
        node = nodes[pid]
        identity = nodes[roots[pid]]["identity"]
        group = groups.setdefault(identity["id"], {
            "id": identity["id"], "name": identity["name"],
            "executable": identity["executable"],
            "identity_source": identity["identity_source"],
            "pids": [], "cpu": 0.0, "rss_kb": 0, "pss_kb": None,
            "measured": 0, "unreadable": 0, "raced": 0, "not_sampled": 0,
        })
        group["pids"].append(pid)
        group["cpu"] += node["process"]["cpu"]
        group["rss_kb"] += node["process"]["rss_kb"]

    # All groups and their snapshot RSS are known before any expensive read.
    for group in groups.values():
        for pid in group["pids"]:
            node = nodes[pid]
            if group["id"] in selected_ids:
                if node["discovery_error"]:
                    node["status"] = node["discovery_error"]
                else:
                    _, node["pss"], node["status"] = _sample_pss(
                        proc_root / str(pid), node["expected"], node["process"], node["identity"])
            group[node["status"]] += 1
            if node["pss"] is not None:
                group["pss_kb"] = (group["pss_kb"] or 0) + node["pss"]

    programs = sorted(groups.values(), key=lambda g: (g["name"].casefold(), g["id"]))
    counts = {"measured": 0, "unreadable": 0, "raced": 0, "not_sampled": 0}
    for group in programs:
        counters = {key: group.pop(key) for key in counts}
        for key in counts:
            counts[key] += counters[key]
        group["coverage"] = _coverage(len(group["pids"]), **counters)
        full = group["coverage"]["status"] == "full"
        group["memory_metric"] = "pss" if full else "rss"
        group["memory_kb"] = group["pss_kb"] if full else group["rss_kb"]
    return {"programs": programs, "coverage": _coverage(len(nodes), **counts),
            "enumeration_complete": True}


# comm goes first: ps takes the overflow of a wide column (a 7-digit RSS, a
# 4-digit %CPU) out of the padding of the columns after it, so a fixed-width
# comm placed after them loses characters to args («pi … p» + «i»). Nothing
# precedes the first column, so its 64 characters are always exact.
COMM_WIDTH = 64
PS_COLUMNS = f"comm:{COMM_WIDTH}=,pid=,ppid=,uid=,rss=,%cpu=,args="


def parse_ps(output):
    """Rows of ``ps -eo PS_COLUMNS``; fixed-width comm keeps its spaces."""
    processes, skipped = [], 0
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            if len(line) <= COMM_WIDTH or not line[COMM_WIDTH].isspace():
                raise ValueError("short or overflowing comm field")
            comm = line[:COMM_WIDTH].strip()
            parts = line[COMM_WIDTH:].split(None, 5)
            pid, ppid, uid, rss = (int(value) for value in parts[:4])
            cpu = float(parts[4])
            args = parts[5].strip() if len(parts) > 5 else ""
            if not comm or pid <= 0 or rss < 0:
                raise ValueError("invalid process row")
            processes.append({"pid": pid, "ppid": ppid, "uid": uid,
                              "rss_kb": rss, "cpu": cpu, "comm": comm, "args": args})
        except (ValueError, IndexError):
            skipped += 1
    return processes, skipped
