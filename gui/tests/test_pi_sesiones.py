# pyright: reportAttributeAccessIssue=false
"""Recursos → Pi: one row per Pi session (S17, T14 of odd/tasks/macacoview-gtk.md).

The collector half (``scripts/macacoview_pi_sessions.py``) runs against a fake
``/proc`` and a fake ``~/.pi/agent`` rebuilt from a real capture taken on
2026-11-10 (``fixtures/pi_sessions_2026-11-10/``): process tree, PSS/RSS,
start times, cwd links and the session-file headers. Never the host procfs,
never the real home, never a prompt: the session files are rebuilt with
filler lines that carry a marker the output must never contain.
"""

try:  # importing it points HOME at a sandbox before pc_ai_monitor loads
    import _sandbox
except ModuleNotFoundError:
    from tests import _sandbox  # noqa: F401

try:  # discover puts tests/ on sys.path; `-m unittest tests.x` does not
    import _aislamiento
except ModuleNotFoundError:
    from tests import _aislamiento

import contextlib
import copy
import importlib.util
import io
import json
import os
import runpy
import shutil
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any
from unittest.mock import patch

_aislamiento.isolate()

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "pi_sessions_2026-11-10" / "capture.json"
)
PROMPT_MARKER = "SECRET-PROMPT-TEXT"
ENV_MARKER = "SECRET_TOKEN_VALUE"
# The capture was taken as uid 1000; the fake world gives those rows the uid
# running the tests, so "the current user" means the same thing on any runner.
CAPTURE_UID = 1000
OTHER_UID = 4242 if os.getuid() != 4242 else 4243


def _load(name):
    """Load a collector helper by path, with scripts/ importable for its imports."""
    if str(SCRIPTS) not in sys.path:
        sys.path.append(str(SCRIPTS))
    loader = SourceFileLoader(name, str(SCRIPTS / f"{name}.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def capture():
    return json.loads(FIXTURE.read_text())


def session_dir(cwd):
    """Pi's own rule: drop the leading slash, then / \\ : → «-», wrapped in «--»."""
    trimmed = cwd[1:] if cwd[:1] in ("/", "\\") else cwd
    return "--" + trimmed.replace("/", "-").replace("\\", "-").replace(":", "-") + "--"


class World:
    """A fake /proc + ~/.pi/agent rebuilt from the capture under a temp dir."""

    def __init__(self, base: Path, data: dict):
        self.data = data
        self.home = base / "home"
        self.proc = base / "proc"
        self.agent = self.home / ".pi" / "agent"
        self.proc.mkdir(parents=True)
        self.agent.mkdir(parents=True)
        (self.proc / "stat").write_text(f"cpu  1 2 3 4\nbtime {data['btime']}\n")
        (self.proc / "meminfo").write_text(
            "MemTotal: 134217728 kB\nMemAvailable: 67108864 kB\n"
        )
        self.processes = []
        for row in data["processes"]:
            self._process(row)
        for entry in data["session_files"].values():
            self._session_file(entry)

    def real(self, text):
        return text.replace("@HOME@", str(self.home)) if isinstance(text, str) else text

    def _process(self, row):
        directory = self.proc / str(row["pid"])
        directory.mkdir()
        fields = ["S", str(row["ppid"])] + ["0"] * 17 + [str(row.get("starttime") or 0)]
        (directory / "stat").write_text(f"{row['pid']} ({row['comm']}) " + " ".join(fields))
        if row.get("exe"):
            (directory / "exe").symlink_to(self.real(row["exe"]))
        if row.get("cwd"):
            (directory / "cwd").symlink_to(self.real(row["cwd"]))
        in_tree = "starttime" in row
        pss = row.get("pss_kb")
        if pss is not None or not in_tree:
            # Rows outside every pi tree get a readable smaps too: the cost
            # bound test proves they are never opened.
            pss = 1 if pss is None else pss
            (directory / "smaps_rollup").write_text(
                "00400000-ffffffff ---p 00000000 00:00 0 [rollup]\n"
                f"Rss: {row.get('smaps_rss_kb') or row['rss_kb']} kB\nPss: {pss} kB\n"
            )
        env = [f"{key}={self.real(value)}" for key, value in (row.get("env") or {}).items()]
        env.append(f"OTHER_SECRET={ENV_MARKER}")
        (directory / "environ").write_bytes("\0".join(env).encode() + b"\0")
        uid = os.getuid() if row["uid"] == CAPTURE_UID else row["uid"]
        self.processes.append({
            "pid": row["pid"], "ppid": row["ppid"], "uid": uid,
            "rss_kb": row["rss_kb"], "cpu": row["cpu"], "comm": row["comm"],
            "args": row["comm"],
        })

    def session_path(self, entry):
        header = entry["header"]
        if entry["location"] == "gentle-agents":
            return self.agent / "gentle-agents" / "sessions" / entry["file"]
        return self.agent / "sessions" / session_dir(self.real(header["cwd"])) / entry["file"]

    def _session_file(self, entry, infos=None):
        path = self.session_path(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        header = dict(entry["header"], cwd=self.real(entry["header"]["cwd"]))
        filler = json.dumps({"type": "message", "id": "m", "message": {"content": PROMPT_MARKER * 40}})
        lines = [json.dumps(header, separators=(",", ":")), filler]
        for info in infos if infos is not None else entry["session_info"]:
            lines += [json.dumps(info, separators=(",", ":")), filler]
        path.write_text("\n".join(lines) + "\n")
        os.utime(path, (entry["mtime"], entry["mtime"]))
        return path

    def name_session(self, file_suffix, *names):
        """Append session_info entries (what /name writes) to one captured file."""
        for entry in self.data["session_files"].values():
            if entry["file"].endswith(file_suffix):
                infos = [
                    {"type": "session_info", "id": f"i{n}", "parentId": "m",
                     "timestamp": "2026-11-10T11:00:00.000Z", "name": name}
                    for n, name in enumerate(names)
                ]
                return self._session_file(entry, infos)
        raise KeyError(file_suffix)

    def collect(self, module=None, **kwargs):
        module = module or _load("macacoview_pi_sessions")
        return module.collect_pi_sessions(
            self.processes, proc_root=self.proc, agent_dir=self.agent,
            btime=self.data["btime"], clk_tck=self.data["clk_tck"], **kwargs,
        )


def reference_tree(data, root):
    """Independent oracle: the pi root + descendants via ppid, nested pi excluded."""
    rows = {r["pid"]: r for r in data["processes"]}
    kids = {}
    for r in data["processes"]:
        kids.setdefault(r["ppid"], []).append(r["pid"])
    tree, stack = [root], list(kids.get(root, []))
    while stack:
        pid = stack.pop()
        if rows[pid]["comm"] == "pi":
            continue
        tree.append(pid)
        stack += kids.get(pid, [])
    return sorted(tree), rows, kids


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="macacoview-pi-sessions-")
        self.addCleanup(self.tmp.cleanup)
        self.data = capture()
        self.world = World(Path(self.tmp.name), self.data)

    def by_pid(self, sessions=None):
        return {s["pid"]: s for s in (sessions or self.world.collect())}

    # -- rule: one entry per root pi process, trees through shells --------

    def test_one_entry_per_pi_process(self):
        pis = sorted(r["pid"] for r in self.data["processes"] if r["comm"] == "pi")
        self.assertEqual(len(pis), 11)
        self.assertEqual(sorted(self.by_pid()), pis)

    def test_tree_counts_every_descendant_through_shells_but_not_nested_pi(self):
        sessions = self.by_pid()
        for pid, session in sessions.items():
            tree, _rows, kids = reference_tree(self.data, pid)
            self.assertEqual(session["pids"], tree, pid)
            self.assertEqual(session["pid_count"], len(tree), pid)
            # Hijos = direct children of the pi process (subagent pi included).
            self.assertEqual(session["children"], len(kids.get(pid, [])), pid)
        # 5544 reaches grandchildren under bash/npm, not only direct children.
        self.assertGreater(sessions[5544]["pid_count"], sessions[5544]["children"] + 1)
        # Subagent pi processes are never inside the parent's tree.
        self.assertNotIn(3021130, sessions[2925478]["pids"])

    def test_pss_and_rss_are_tree_sums_with_pi_only_figures_alongside(self):
        sessions = self.by_pid()
        for pid, session in sessions.items():
            tree, rows, _ = reference_tree(self.data, pid)
            self.assertEqual(session["rss_kb"], sum(rows[p]["rss_kb"] for p in tree))
            measured = [rows[p]["pss_kb"] for p in tree if rows[p].get("pss_kb") is not None]
            self.assertEqual(session["pss_kb"], sum(measured))
            self.assertEqual(session["pi_rss_kb"], rows[pid]["rss_kb"])
            self.assertEqual(session["pi_pss_kb"], rows[pid]["pss_kb"])

    def test_coverage_flags_unreadable_pss(self):
        sessions = self.by_pid()
        self.assertEqual(sessions[5544]["pss_coverage"]["status"], "full")
        partial = sessions[3102737]["pss_coverage"]
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["unreadable"], 4)
        self.assertEqual(partial["measured"], sessions[3102737]["pid_count"] - 4)

    def test_nothing_measured_reports_no_pss(self):
        tree, _, _ = reference_tree(self.data, 277162)
        for pid in tree:
            path = self.world.proc / str(pid) / "smaps_rollup"
            if path.exists():
                path.unlink()
        session = self.by_pid()[277162]
        self.assertIsNone(session["pss_kb"])
        self.assertIsNone(session["pi_pss_kb"])
        self.assertEqual(session["pss_coverage"]["status"], "unreadable")

    def test_parent_session_for_subagents(self):
        sessions = self.by_pid()
        for child, parent in ((3021130, 2925478), (3102734, 2925478),
                              (3102737, 2925478), (3099222, 5890), (3101730, 5890)):
            self.assertEqual(sessions[child]["parent_pid"], parent)
            self.assertEqual(sessions[child]["parent_session_id"],
                             sessions[parent]["session_id"])
        for root in (5544, 5890, 277162, 413806, 2922080, 2925478):
            self.assertIsNone(sessions[root]["parent_pid"])

    def test_only_pi_trees_are_pss_sampled(self):
        reads = []
        original = Path.read_text

        def tracked(path, *args, **kwargs):
            if path.name == "smaps_rollup":
                reads.append(int(path.parent.name))
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", tracked):
            sessions = self.world.collect()
        sampled = set().union(*(s["pids"] for s in sessions))
        self.assertTrue(reads)
        self.assertLessEqual(set(reads), sampled)
        outside = {r["pid"] for r in self.data["processes"] if "starttime" not in r}
        self.assertTrue(outside)
        self.assertFalse(outside & set(reads))

    # -- rule: session identity, never guessed silently -------------------

    def test_child_environ_maps_the_session_exactly(self):
        session = self.by_pid()[3101730]
        self.assertEqual(session["session_id"], "01a1c536-2c4d-7078-9c5f-7ddea1294c3f")
        self.assertEqual(session["session_source"], "environ")
        self.assertEqual(session["session_confidence"], "exact")

    def test_start_time_maps_new_sessions(self):
        sessions = self.by_pid()
        for pid, suffix in ((277162, "305218"), (413806, "7e6957"), (2925478, "609426"),
                            (3021130, "751b56"), (3099222, "5002ec")):
            self.assertTrue(sessions[pid]["session_id"].endswith(suffix), pid)
            self.assertEqual(sessions[pid]["session_source"], "start_time", pid)
            self.assertEqual(sessions[pid]["session_confidence"], "high", pid)

    def test_ambiguous_start_time_stays_unmapped(self):
        sessions = self.by_pid()
        for pid in (3102734, 3102737):  # two subagents, same cwd, same second
            self.assertIsNone(sessions[pid]["session_id"])
            self.assertIsNone(sessions[pid]["session_source"])
            self.assertEqual(sessions[pid]["name"], f"k2-05-chat-agente · pid {pid}")
            self.assertEqual(sessions[pid]["name_source"], "pid")

    def test_single_recent_file_in_cwd_is_a_low_confidence_match(self):
        session = self.by_pid()[5890]
        self.assertTrue(session["session_id"].endswith("a8e027be8940"))
        self.assertEqual(session["session_source"], "recent_mtime")
        self.assertEqual(session["session_confidence"], "low")

    def test_several_recent_files_in_cwd_stay_unmapped(self):
        session = self.by_pid()[5544]
        self.assertIsNone(session["session_id"])
        self.assertEqual(session["name"], "koda · pid 5544")

    def test_no_session_file_yet_stays_unmapped(self):
        session = self.by_pid()[2922080]
        self.assertIsNone(session["session_id"])
        self.assertEqual(session["name"], "local-ai-tooling · pid 2922080")

    def test_env_claim_removes_the_file_from_start_time_competition(self):
        # Pin one of the two contested subagent files through a bash child:
        # the other subagent is then the only owner left for the other file.
        row = next(r for r in self.data["processes"] if r["pid"] == 3159608)
        child = dict(row, pid=99999001, ppid=3102734,
                     env={"PI_SESSION_ID": "01a1c536-4bfc-740d-a482-c42593b93b20"})
        child.pop("pss_kb", None)
        self.world._process(child)
        sessions = self.by_pid()
        self.assertEqual(sessions[3102734]["session_source"], "environ")
        self.assertTrue(sessions[3102734]["session_id"].endswith("c42593b93b20"))
        self.assertEqual(sessions[3102737]["session_source"], "start_time")
        self.assertTrue(sessions[3102737]["session_id"].endswith("ff7abc90d17c"))

    # -- rule: names ---------------------------------------------------------

    def test_name_falls_back_to_cwd_and_short_session_id(self):
        sessions = self.by_pid()
        self.assertEqual(sessions[277162]["name"], "koda · 5218")
        self.assertEqual(sessions[277162]["name_source"], "session_id")
        self.assertEqual(sessions[3101730]["name"], "macacoview-design · 4c3f")
        self.assertEqual(sessions[3101730]["cwd_name"], "macacoview-design")

    def test_session_info_name_wins_and_the_last_one_counts(self):
        self.world.name_session("305218.jsonl", "Primero", "MacacoView 08 · diseño")
        session = self.by_pid()[277162]
        self.assertEqual(session["name"], "MacacoView 08 · diseño")
        self.assertEqual(session["name_source"], "session_info")

    def test_session_info_name_is_cleaned_and_capped(self):
        self.world.name_session("305218.jsonl", "  uno\n\tdos\x07  " + "x" * 200)
        name = self.by_pid()[277162]["name"]
        self.assertTrue(name.startswith("uno dos "))
        self.assertLessEqual(len(name), 80)
        self.world.name_session("305218.jsonl", "nombre", "   ")
        self.assertEqual(self.by_pid()[277162]["name"], "koda · 5218")

    def test_output_never_carries_prompts_or_other_env_values(self):
        text = json.dumps(self.world.collect())
        self.assertNotIn(PROMPT_MARKER, text)
        self.assertNotIn(ENV_MARKER, text)

    def test_never_writes_under_the_pi_agent_dir(self):
        before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in self.world.agent.rglob("*")}
        self.world.collect()
        after = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in self.world.agent.rglob("*")}
        self.assertEqual(before, after)

    def test_broken_or_missing_session_files_do_not_break_the_list(self):
        shutil.rmtree(self.world.agent / "sessions")
        sessions = self.by_pid()
        self.assertEqual(len(sessions), 11)
        self.assertEqual(sessions[277162]["name"], "koda · pid 277162")


@contextlib.contextmanager
def recorded_reads():
    """Every path the collector opens or reads (a spy, not a block).

    Opened paths are resolved, so a symlink counts as its target; readlink
    paths stay as given (resolving a link does not open its target). Resolved
    once the patches are gone: realpath itself calls os.readlink.
    """
    raw: list[str] = []
    links: list[str] = []
    seen: list[str] = []

    def note(path, into=raw):
        if isinstance(path, (str, bytes, os.PathLike)):
            into.append(os.fsdecode(path))

    builtin_open, os_open = open, os.open
    read_text, read_bytes, readlink = Path.read_text, Path.read_bytes, os.readlink

    def spy_open(path, *args, **kwargs):
        note(path)
        return builtin_open(path, *args, **kwargs)

    def spy_os_open(path, *args, **kwargs):
        note(path)
        return os_open(path, *args, **kwargs)

    def spy_read_text(path, *args, **kwargs):
        note(path)
        return read_text(path, *args, **kwargs)

    def spy_read_bytes(path, *args, **kwargs):
        note(path)
        return read_bytes(path, *args, **kwargs)

    def spy_readlink(path, *args, **kwargs):
        note(path, links)
        return readlink(path, *args, **kwargs)

    with (
        patch("builtins.open", spy_open),
        patch("os.open", spy_os_open),
        patch.object(Path, "read_text", spy_read_text),
        patch.object(Path, "read_bytes", spy_read_bytes),
        patch("os.readlink", spy_readlink),
    ):
        yield seen
    seen.extend(os.path.realpath(path) for path in raw)
    seen.extend(os.path.abspath(path) for path in links)


class EnvironTrustTests(unittest.TestCase):
    """PI_SESSION_FILE / PI_SESSION_ID come from another process's environ.

    They are untrusted input: a file is read only when it resolves to a regular
    ``.jsonl`` owned by the current user inside the two Pi session roots, and an
    id only when it looks like one. Anything else is ignored and the session
    falls back to the other methods; the rejected target is never opened.
    """

    PI = 2922080  # no session file was created at its start: unmapped without environ
    SESSION_ID = "01a1c48f-5fbe-7503-974e-b7e83a9f846e"
    SESSION_FILE = f"2026-11-10T08-24-52-927Z_{SESSION_ID}.jsonl"
    OTHER_ID = "01a1ad91-f4ee-77ee-b6e1-35188e3d1afc"
    OTHER_FILE = f"2026-11-05T21-16-26-223Z_{OTHER_ID}.jsonl"
    OUTSIDE_ID = "01a1c48f-0000-7000-8000-0000000ff51d"
    OUTSIDE_NAME = "FUERA DE LA RAIZ"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="macacoview-pi-environ-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.world = self.fresh_world("w0")

    def fresh_world(self, name):
        world = World(self.base / name, capture())
        world.cwd = os.readlink(world.proc / str(self.PI) / "cwd")
        world.dir = world.agent / "sessions" / session_dir(world.cwd)
        # A well-formed session file the user owns, outside both Pi roots.
        world.outside = self.base / name / "outside" / f"2026-11-10T09-00-00-000Z_{self.OUTSIDE_ID}.jsonl"
        world.outside.parent.mkdir(parents=True)
        self.write_session(world.outside, self.OUTSIDE_ID, world.cwd, self.OUTSIDE_NAME)
        return world

    @staticmethod
    def write_session(path, session_id, cwd, name=None):
        compact = (",", ":")
        lines = [json.dumps({"type": "session", "id": session_id, "cwd": cwd}, separators=compact)]
        if name:
            info = {"type": "session_info", "id": "i", "name": name}
            lines.append(json.dumps(info, separators=compact))
        path.write_text("\n".join(lines) + "\n")
        return path

    def child(self, world, env, pid=99999101, starttime=51088545, uid=None, ppid=None):
        row = next(r for r in world.data["processes"] if r["pid"] == 3159608)
        world._process(dict(row, pid=pid, ppid=ppid or self.PI, starttime=starttime,
                            env=env, uid=CAPTURE_UID if uid is None else uid))

    def session(self, world, **kwargs):
        return {s["pid"]: s for s in world.collect(**kwargs)}[self.PI]

    def assert_unmapped(self, session):
        self.assertIsNone(session["session_id"])
        self.assertIsNone(session["session_source"])
        self.assertEqual(session["name"], f"local-ai-tooling · pid {self.PI}")

    # -- accepted ----------------------------------------------------------

    def test_session_file_inside_a_root_maps_the_session_exactly(self):
        self.child(self.world, {"PI_SESSION_FILE": str(self.world.dir / self.SESSION_FILE)})
        session = self.session(self.world)
        self.assertEqual(session["session_id"], self.SESSION_ID)
        self.assertEqual(session["session_source"], "environ")
        self.assertEqual(session["session_confidence"], "exact")

    def test_gentle_agents_root_is_accepted_too(self):
        path = self.write_session(
            self.world.agent / "gentle-agents" / "sessions" / f"2026-11-10T09-00-00-000Z_{self.OUTSIDE_ID}.jsonl",
            self.OUTSIDE_ID, self.world.cwd, "Subagente")
        self.child(self.world, {"PI_SESSION_FILE": str(path)})
        session = self.session(self.world)
        self.assertEqual(session["session_id"], self.OUTSIDE_ID)
        self.assertEqual(session["name"], "Subagente")
        self.assertEqual(session["session_confidence"], "exact")

    def test_session_id_alone_is_looked_up_in_the_session_dirs(self):
        self.child(self.world, {"PI_SESSION_ID": self.SESSION_ID})
        session = self.session(self.world)
        self.assertEqual(session["session_id"], self.SESSION_ID)
        self.assertEqual(session["session_source"], "environ")
        self.assertEqual(session["session_confidence"], "exact")

    def test_disagreeing_children_pick_the_newest_with_high_confidence(self):
        self.child(self.world, {"PI_SESSION_FILE": str(self.world.dir / self.OTHER_FILE)},
                   pid=99999101, starttime=51088000)
        self.child(self.world, {"PI_SESSION_FILE": str(self.world.dir / self.SESSION_FILE)},
                   pid=99999102, starttime=51088545)
        session = self.session(self.world)
        self.assertEqual(session["session_id"], self.SESSION_ID)
        self.assertEqual(session["session_source"], "environ")
        self.assertEqual(session["session_confidence"], "high")

    # -- rejected: never opened, the session falls back --------------------

    def rejected_values(self, world):
        inside = world.dir
        escape = inside / f"2026-11-10T09-00-01-000Z_{self.OUTSIDE_ID}.jsonl"
        escape.symlink_to(world.outside)
        notes = self.write_session(inside / "notas.json", self.OUTSIDE_ID, world.cwd, self.OUTSIDE_NAME)
        renamed = inside / f"2026-11-10T09-00-02-000Z_{self.OUTSIDE_ID}.jsonl"
        renamed.symlink_to(notes)
        folder = inside / f"2026-11-10T09-00-03-000Z_{self.OUTSIDE_ID}.jsonl"
        folder.mkdir()
        climb = os.path.relpath(world.outside, inside)
        return {
            "outside the roots": str(world.outside),
            "symlink escaping the root": str(escape),
            "dot-dot escaping the root": f"{inside}/{climb}",
            "not .jsonl": str(notes),
            ".jsonl symlink to a non-jsonl file": str(renamed),
            "a directory": str(folder),
            "relative path": f"sessions/{session_dir(world.cwd)}/{self.SESSION_FILE}",
            "missing file": str(inside / f"2026-11-10T09-00-04-000Z_{self.OUTSIDE_ID}.jsonl"),
        }

    def test_untrusted_session_files_are_ignored_and_never_opened(self):
        cases = self.rejected_values(self.world)
        for n, (label, value) in enumerate(cases.items()):
            with self.subTest(label):
                world = self.fresh_world(f"case{n}")
                value = self.rejected_values(world)[label]
                self.child(world, {"PI_SESSION_FILE": value})
                with recorded_reads() as reads:
                    sessions = world.collect()
                session = {s["pid"]: s for s in sessions}[self.PI]
                self.assert_unmapped(session)
                self.assertNotIn(os.path.realpath(world.outside), reads)
                self.assertFalse([p for p in reads if p.endswith("notas.json")])
                text = json.dumps(sessions)
                self.assertNotIn(self.OUTSIDE_NAME, text)
                self.assertNotIn(self.OUTSIDE_ID, text)

    def test_rejected_file_falls_back_to_a_valid_session_id(self):
        self.child(self.world, {"PI_SESSION_FILE": str(self.world.outside),
                                "PI_SESSION_ID": self.SESSION_ID})
        with recorded_reads() as reads:
            session = self.session(self.world)
        self.assertEqual(session["session_id"], self.SESSION_ID)
        self.assertEqual(session["session_source"], "environ")
        self.assertNotIn(os.path.realpath(self.world.outside), reads)

    def test_rejected_environ_lets_start_time_map_the_session(self):
        # 277162 is mapped by start time; a bogus child must not take that away.
        self.child(self.world, {"PI_SESSION_FILE": str(self.world.outside)}, ppid=277162)
        session = {s["pid"]: s for s in self.world.collect()}[277162]
        self.assertTrue(session["session_id"].endswith("305218"))
        self.assertEqual(session["session_source"], "start_time")

    def test_session_file_owned_by_another_user_is_ignored(self):
        # Files can only be created as ourselves, so the collector runs as
        # another uid that also owns every captured process.
        for process in self.world.processes:
            if process["uid"] == os.getuid():
                process["uid"] = OTHER_UID
        self.child(self.world, {"PI_SESSION_FILE": str(self.world.dir / self.SESSION_FILE)},
                   uid=OTHER_UID)
        with patch("os.getuid", return_value=OTHER_UID):
            session = self.session(self.world)
        self.assertNotEqual(session["session_source"], "environ")

    def test_malformed_session_ids_are_ignored(self):
        for n, value in enumerate(("../../../etc/passwd", "x" * 200, "id con espacios", "a/b")):
            with self.subTest(value=value[:20]):
                world = self.fresh_world(f"id{n}")
                self.child(world, {"PI_SESSION_ID": value})
                sessions = world.collect()
                self.assert_unmapped({s["pid"]: s for s in sessions}[self.PI])
                self.assertNotIn(value, json.dumps(sessions))

    def test_reader_refuses_symlinks_and_fifos_without_blocking(self):
        module = _load("macacoview_pi_sessions")
        real = self.world.dir / self.SESSION_FILE
        self.assertIsNotNone(module.read_session_file(real)[0])
        link = self.base / "link.jsonl"
        link.symlink_to(real)
        self.assertEqual(module.read_session_file(link), (None, None))
        fifo = self.base / "fifo.jsonl"
        os.mkfifo(fifo)
        self.assertEqual(module.read_session_file(fifo), (None, None))
        self.assertEqual(module.read_session_file(self.base), (None, None))


class OwnerTests(unittest.TestCase):
    """Only the current user's pi processes are listed, sampled or read."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="macacoview-pi-owner-")
        self.addCleanup(self.tmp.cleanup)
        self.world = World(Path(self.tmp.name), capture())
        self.bash = next(r for r in self.world.data["processes"] if r["pid"] == 3159608)
        self.pi = next(r for r in self.world.data["processes"] if r["pid"] == 2922080)
        self.session_file = str(
            self.world.agent / "sessions" / session_dir(self.world.real(self.pi["cwd"]))
            / EnvironTrustTests.SESSION_FILE)

    def test_another_users_pi_is_never_listed_or_read(self):
        self.world._process(dict(self.pi, pid=99998001, ppid=1, uid=OTHER_UID))
        self.world._process(dict(self.bash, pid=99998002, ppid=99998001, uid=OTHER_UID,
                                 env={"PI_SESSION_FILE": self.session_file}))
        with recorded_reads() as reads:
            sessions = self.world.collect()
        self.assertNotIn(99998001, {s["pid"] for s in sessions})
        self.assertEqual(len(sessions), 11)
        foreign = [p for p in reads if Path(p).is_relative_to(self.world.proc / "99998001")
                   or Path(p).is_relative_to(self.world.proc / "99998002")]
        self.assertEqual(foreign, [])

    def test_another_users_descendant_is_counted_but_never_read(self):
        # e.g. a sudo child: its RSS (from ps) is part of what the session
        # launched, but its /proc entries belong to someone else.
        before = {s["pid"]: s for s in self.world.collect()}[2922080]
        self.world._process(dict(self.bash, pid=99998003, ppid=2922080, uid=0,
                                 env={"PI_SESSION_FILE": self.session_file}))
        with recorded_reads() as reads:
            session = {s["pid"]: s for s in self.world.collect()}[2922080]
        self.assertIn(99998003, session["pids"])
        self.assertEqual(session["rss_kb"], before["rss_kb"] + self.bash["rss_kb"])
        self.assertEqual(session["pss_kb"], before["pss_kb"])
        self.assertEqual(session["pss_coverage"]["not_sampled"],
                         before["pss_coverage"]["not_sampled"] + 1)
        self.assertIsNone(session["session_source"])
        self.assertEqual([p for p in reads if Path(p).is_relative_to(self.world.proc / "99998003")], [])

    def test_another_users_pi_inside_a_tree_is_a_descendant_not_an_entry(self):
        self.world._process(dict(self.pi, pid=99998004, ppid=2922080, uid=OTHER_UID))
        with recorded_reads() as reads:
            sessions = {s["pid"]: s for s in self.world.collect()}
        self.assertNotIn(99998004, sessions)
        self.assertIn(99998004, sessions[2922080]["pids"])
        self.assertEqual([p for p in reads if Path(p).is_relative_to(self.world.proc / "99998004")], [])


class CollectorScriptTests(unittest.TestCase):
    """macacoview-stats emits pi_sessions without renaming anything it had."""

    def run_stats(self, world, bin_dir=None, argv=(), parse=True) -> Any:
        script = (bin_dir or SCRIPTS) / "macacoview-stats"
        path_open, readlink = Path.open, os.readlink
        proc = world.proc

        def mapped(path):
            path = Path(path)
            return proc / path.relative_to("/proc") if path.is_relative_to("/proc") else path

        rows = "".join(
            f"{p['comm']:<64} {p['pid']} {p['ppid']} {p['uid']} {p['rss_kb']} {p['cpu']} {p['args']}\n"
            for p in world.processes
        )

        def fake_output(cmd, **kwargs):
            if cmd[0] == "ps":
                return rows
            return ""

        out = io.StringIO()
        with (
            patch("sys.path", list(sys.path)),
            patch("pathlib.Path.home", return_value=world.home),
            patch.object(Path, "open", lambda p, *a, **kw: path_open(mapped(p), *a, **kw)),
            patch("builtins.open", lambda p, *a, **kw: path_open(mapped(p), *a, **kw)),
            patch("os.readlink", lambda p: readlink(mapped(p))),
            patch("subprocess.check_output", side_effect=fake_output),
            patch("sys.argv", [str(script), *argv]),
            contextlib.redirect_stdout(out),
        ):
            runpy.run_path(str(script), run_name="__main__")
        return json.loads(out.getvalue()) if parse else out.getvalue()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="macacoview-pi-stats-")
        self.addCleanup(self.tmp.cleanup)
        self.world = World(Path(self.tmp.name), capture())

    def test_stats_json_has_pi_sessions_and_keeps_every_existing_key(self):
        with patch.dict(sys.modules):
            data = self.run_stats(self.world)
        for key in ("memory", "models", "groups", "processes", "programs",
                    "program_coverage", "program_enumeration"):
            self.assertIn(key, data)
        sessions = {s["pid"]: s for s in data["pi_sessions"]}
        self.assertEqual(len(sessions), 11)
        self.assertEqual(sessions[277162]["name"], "koda · 5218")
        self.assertIn(data["pi_sessions_coverage"]["status"], ("full", "partial"))

    def test_installed_copy_finds_the_adjacent_helper(self):
        bin_dir = Path(self.tmp.name) / "bin"
        bin_dir.mkdir()
        for name in ("macacoview-stats", "macacoview_programs.py", "macacoview_pi_sessions.py"):
            shutil.copyfile(SCRIPTS / name, bin_dir / name)
        with (
            patch("sys.path", [p for p in sys.path if p and not Path(p).resolve().is_relative_to(ROOT)]),
            patch.dict(sys.modules),
        ):
            sys.modules.pop("macacoview_programs", None)
            sys.modules.pop("macacoview_pi_sessions", None)
            data = self.run_stats(self.world, bin_dir)
        self.assertEqual(len(data["pi_sessions"]), 11)

    def test_missing_helper_keeps_the_collector_working(self):
        bin_dir = Path(self.tmp.name) / "bin"
        bin_dir.mkdir()
        for name in ("macacoview-stats", "macacoview_programs.py"):
            shutil.copyfile(SCRIPTS / name, bin_dir / name)
        with (
            patch("sys.path", [p for p in sys.path if p and not Path(p).resolve().is_relative_to(ROOT)]),
            patch.dict(sys.modules),
        ):
            sys.modules.pop("macacoview_programs", None)
            sys.modules.pop("macacoview_pi_sessions", None)
            data = self.run_stats(self.world, bin_dir)
        self.assertNotIn("pi_sessions", data)
        self.assertIn("groups", data)

    def test_ci_stages_the_helper_next_to_stats(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text()
        line = ('install -m 644 "$GITHUB_WORKSPACE/scripts/macacoview_pi_sessions.py" '
                '"$HOME/.local/bin/macacoview_pi_sessions.py"')
        self.assertTrue(line in text, "ci.yml must install the helper next to the collector")


# ---------------------------------------------------------------------------
# Cost: the bar never pays for sessions; the app asks for them every few seconds
# ---------------------------------------------------------------------------


class CostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="macacoview-pi-cost-")
        self.addCleanup(self.tmp.cleanup)
        self.world = World(Path(self.tmp.name), capture())

    def test_bar_modes_and_opt_out_skip_session_sampling(self):
        runner = CollectorScriptTests()
        for argv in (["--pill"], ["--bar"], ["--no-pi-sessions"], []):
            calls = []
            module = _load("macacoview_pi_sessions")
            original = module.collect_pi_sessions

            def spy(*args, _orig=original, _calls=calls, **kwargs):
                _calls.append(1)
                return _orig(*args, **kwargs)

            module.collect_pi_sessions = spy
            with patch.dict(sys.modules, {"macacoview_pi_sessions": module}):
                output = runner.run_stats(self.world, argv=argv, parse=False)
            if argv:
                self.assertEqual(calls, [], argv)
                self.assertNotIn('"pi_sessions"', output)
            else:
                self.assertEqual(calls, [1])
                self.assertIn('"pi_sessions"', output)

    def test_app_asks_for_sessions_at_most_every_few_seconds_and_keeps_the_last(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor import datos

        stats_bin = Path(self.tmp.name) / "stats"
        stats_bin.write_text("")
        collector = datos.Collector(cfg.Config(stats_bin=stats_bin))
        calls, clock = [], [1000.0]
        sessions = [{"pid": 1, "name": "koda · 5218"}]

        def fake_run(argv, timeout):
            calls.append(argv)
            payload: dict[str, Any] = {"memory": {}, "groups": {}}
            if "--no-pi-sessions" not in argv:
                payload["pi_sessions"] = sessions
                payload["pi_sessions_coverage"] = {"status": "full"}
            return json.dumps(payload), None

        with (
            patch.object(datos, "_run", fake_run),
            patch.object(datos.time, "monotonic", lambda: clock[0]),
        ):
            collector._collect_stats()
            clock[0] += 1.0
            collector._collect_stats()
            clock[0] += datos.PI_SESSIONS_INTERVAL_S
            collector._collect_stats()
            clock[0] += 1.0
            collector._collect_stats()

        self.assertEqual(datos.PI_SESSIONS_INTERVAL_S, 5.0)
        self.assertEqual(
            ["--no-pi-sessions" in argv for argv in calls], [False, True, False, True]
        )
        # In between, the previous list is carried forward, not dropped.
        snapshot = collector.snapshot()
        self.assertEqual(snapshot.pi_sessions(), sessions)
        self.assertEqual((snapshot.stats or {})["pi_sessions_coverage"], {"status": "full"})

    def test_old_collector_without_the_key_reports_none(self):
        from pc_ai_monitor.datos import Snapshot

        self.assertIsNone(Snapshot(stats={"groups": {}}).pi_sessions())
        self.assertIsNone(Snapshot().pi_sessions())
        self.assertEqual(Snapshot(stats={"pi_sessions": []}).pi_sessions(), [])


class StatsResilienceTests(unittest.TestCase):
    """The 5 s sessions carry-forward in Collector._collect_stats (S20, R4-001).

    A failed or slow sessions run never stops Recursos from refreshing, a
    carried list never outlives a few intervals, and an odd payload never
    raises out of the collector thread.
    """

    def setUp(self):
        from pc_ai_monitor import config as cfg
        from pc_ai_monitor import datos

        self.datos = datos
        self.tmp = tempfile.TemporaryDirectory(prefix="macacoview-pi-resilience-")
        self.addCleanup(self.tmp.cleanup)
        stats_bin = Path(self.tmp.name) / "stats"
        stats_bin.write_text("")
        self.collector = datos.Collector(cfg.Config(stats_bin=stats_bin))
        self.clock = [1000.0]
        self.calls: list[bool] = []
        self.sessions = [{"pid": 1, "name": "koda · 5218"}]
        self.full_fails = False
        self.used = 1.0
        self.raw: str | None = None

    def fake_run(self, argv, timeout):
        full = "--no-pi-sessions" not in argv
        self.calls.append(full)
        if self.raw is not None:
            return self.raw, None
        if full and self.full_fails:
            return None, f"timeout de {timeout:.0f}s: stats"
        payload: dict[str, Any] = {"memory": {"used_gib": self.used}, "groups": {}}
        if full:
            payload["pi_sessions"] = self.sessions
            payload["pi_sessions_coverage"] = {"status": "full"}
        return json.dumps(payload), None

    def tick(self, seconds=1.0):
        with (
            patch.object(self.datos, "_run", self.fake_run),
            patch.object(self.datos.time, "monotonic", lambda: self.clock[0]),
        ):
            result = self.collector._collect_stats()
        self.clock[0] += seconds
        self.used += 1.0
        return result

    def test_a_failed_sessions_run_does_not_make_every_tick_pay_for_it(self):
        self.full_fails = True
        for _ in range(3):
            self.tick()
        # One full attempt, then light runs until the interval elapses.
        self.assertEqual(self.calls, [True, False, False])
        snapshot = self.collector.snapshot()
        self.assertEqual((snapshot.stats or {})["memory"]["used_gib"], 3.0)
        self.assertNotIn("recursos", " ".join(snapshot.errors))

    def test_a_carried_list_expires_when_sessions_keep_failing(self):
        self.tick()  # t=1000: full run, list read
        self.full_fails = True
        seen = {}
        for _ in range(29):  # ends at t=1029, a light run (full ones fail)
            now = self.clock[0]
            self.tick()
            seen[now - 1000.0] = self.collector.snapshot().pi_sessions()
        max_age = self.datos.PI_SESSIONS_MAX_AGE_S
        self.assertGreaterEqual(max_age, self.datos.PI_SESSIONS_INTERVAL_S)
        self.assertEqual(seen[1.0], self.sessions)
        self.assertEqual(seen[max_age], self.sessions)
        # Past its age the stale list is gone: Recursos falls back to processes.
        self.assertTrue(all(value is None for age, value in seen.items() if age > max_age))
        # Meanwhile the rest of the snapshot kept refreshing.
        stats = self.collector.snapshot().stats or {}
        self.assertEqual(stats["memory"]["used_gib"], self.used - 1.0)
        self.assertNotIn("pi_sessions_coverage", stats)

    def test_a_new_good_read_brings_the_list_back_and_records_its_time(self):
        self.tick()
        self.assertEqual(self.collector.snapshot().collected_at.get("pi_sessions"), 1000.0)
        self.full_fails = True
        for _ in range(20):
            self.tick()
        self.assertIsNone(self.collector.snapshot().pi_sessions())
        self.full_fails = False
        while not self.calls[-1] or self.collector.snapshot().pi_sessions() is None:
            self.tick()
            self.assertLess(len(self.calls), 60)
        snapshot = self.collector.snapshot()
        self.assertEqual(snapshot.pi_sessions(), self.sessions)
        self.assertEqual(snapshot.collected_at["pi_sessions"], self.clock[0] - 1.0)

    def test_a_payload_that_is_not_an_object_never_raises(self):
        self.tick()
        good = self.collector.snapshot()
        for raw in ("[]", "null", "3", '"texto"'):
            with self.subTest(raw=raw):
                self.raw = raw
                self.assertTrue(self.tick())
                snapshot = self.collector.snapshot()
                self.assertEqual(snapshot.stats, good.stats)
                self.assertTrue(any(e.startswith("recursos:") for e in snapshot.errors))

    def test_odd_memory_and_groups_never_raise(self):
        self.raw = json.dumps({"memory": [], "groups": 3, "pi_sessions": "x"})
        self.assertTrue(self.tick())
        snapshot = self.collector.snapshot()
        self.assertEqual(snapshot.history[-1].used_gib, 0.0)
        self.assertIsNone(snapshot.pi_sessions())


# ---------------------------------------------------------------------------
# View: logic (pure) and widget
# ---------------------------------------------------------------------------


def live_like_sessions():
    """Collector output for the fixture world, as the GUI receives it."""
    with tempfile.TemporaryDirectory(prefix="macacoview-pi-view-") as tmp:
        world = World(Path(tmp), capture())
        world.name_session("609426.jsonl", "Koda principal")
        sessions = world.collect()
        home = str(world.home)
    return sessions, home


class LogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sessions, cls.home = live_like_sessions()

    def rows(self, sessions=None):
        from pc_ai_monitor.blocks import pi_sesiones_logica as logica

        return logica.session_rows(self.sessions if sessions is None else sessions, home=self.home)

    def test_rows_sort_by_pss_with_subagents_nested_under_their_session(self):
        rows = self.rows()
        self.assertEqual(len(rows), 11)
        top = [r for r in rows if r.depth == 0]
        pss = [r.pss_kb or 0 for r in top]
        self.assertEqual(pss, sorted(pss, reverse=True))
        index = {r.pid: i for i, r in enumerate(rows)}
        for child, parent in ((3021130, 2925478), (3102734, 2925478), (3102737, 2925478),
                              (3099222, 5890), (3101730, 5890)):
            row = rows[index[child]]
            self.assertEqual(row.depth, 1)
            self.assertGreater(index[child], index[parent])
            between = rows[index[parent] + 1:index[child]]
            self.assertTrue(all(r.depth >= 1 for r in between))
        self.assertTrue(all(r.depth == 0 for r in rows if r.pid in (5544, 5890, 2925478)))

    def test_subagent_rows_name_their_parent_session(self):
        rows = {r.pid: r for r in self.rows()}
        tags = [text for text, _kind in rows[3021130].tags]
        self.assertIn("subagente de Koda principal", tags)

    def test_named_and_low_confidence_sessions_are_marked(self):
        rows = {r.pid: r for r in self.rows()}
        self.assertIn(("con nombre", "named"), rows[2925478].tags)
        self.assertEqual(rows[2925478].name, "Koda principal")
        self.assertIn(("probable", "dim"), rows[5890].tags)
        self.assertNotIn(("probable", "dim"), rows[277162].tags)

    def test_sessions_sharing_a_cwd_get_distinct_names(self):
        rows = self.rows()
        koda = [r.name for r in rows if r.name.startswith("koda")]
        self.assertGreaterEqual(len(koda), 3)
        self.assertEqual(len({r.name for r in rows}), len(rows))

    def test_duplicate_names_are_disambiguated(self):
        sessions = copy.deepcopy(self.sessions)
        for session in sessions:
            if session["pid"] in (277162, 413806):
                session["name"], session["name_source"] = "Mismo nombre", "session_info"
        names = {r.pid: r.name for r in self.rows(sessions)}
        self.assertEqual(names[277162], "Mismo nombre · 5218")
        self.assertEqual(names[413806], "Mismo nombre · 6957")

    def test_row_numbers_detail_and_bar(self):
        from pc_ai_monitor.blocks import pi_sesiones_logica as logica

        rows = {r.pid: r for r in self.rows()}
        row = rows[277162]
        session = next(s for s in self.sessions if s["pid"] == 277162)
        self.assertEqual(row.pss_text, logica.mib(session["pss_kb"]))
        self.assertEqual(row.rss_text, logica.mib(session["rss_kb"]))
        self.assertEqual(row.pid_count, session["pid_count"])
        self.assertEqual(row.children, session["children"])
        self.assertEqual(row.detail, "pid 277162 · ~/…/koda")
        biggest = max(r.pss_kb or 0 for r in rows.values())
        self.assertAlmostEqual(row.bar, session["pss_kb"] / biggest)
        self.assertEqual(max(r.bar for r in rows.values()), 1.0)

    def test_pid_named_rows_do_not_repeat_the_pid(self):
        rows = {r.pid: r for r in self.rows()}
        self.assertEqual(rows[5544].name, "koda · pid 5544")
        self.assertEqual(rows[5544].detail, "~/…/koda")

    def test_mib_format(self):
        from pc_ai_monitor.blocks import pi_sesiones_logica as logica

        self.assertEqual(logica.mib(1646592), "1 608 MiB")
        self.assertEqual(logica.mib(920576), "899 MiB")
        self.assertEqual(logica.mib(None), "—")

    def test_header_puts_group_rss_and_session_pss_side_by_side(self):
        from pc_ai_monitor.blocks import pi_sesiones_logica as logica

        head = logica.header(self.sessions, group_rss_gib=6.34)
        self.assertEqual(head.title, "Pi · 6 sesiones · 5 subagentes")
        self.assertEqual(head.rss_text, "6.34 GiB")
        total = sum(s["pss_kb"] or 0 for s in self.sessions)
        self.assertEqual(head.pss_text, f"≥ {total / 1024 / 1024:.2f} GiB")
        self.assertTrue(head.partial)  # 3102737 has unreadable PSS
        full = [s for s in self.sessions if s["pss_coverage"]["status"] == "full"]
        self.assertFalse(logica.header(full, 1.0).partial)
        self.assertFalse(logica.header(full, 1.0).pss_text.startswith("≥"))

    def test_header_singulars_and_empty(self):
        from pc_ai_monitor.blocks import pi_sesiones_logica as logica

        one = [s for s in self.sessions if s["pid"] in (2925478, 3021130)]
        self.assertEqual(logica.header(one, 1.0).title, "Pi · 1 sesión · 1 subagente")
        self.assertEqual(logica.header([], 0.0).title, "Pi · 0 sesiones")


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sessions, cls.home = live_like_sessions()

    @staticmethod
    def texts(widget):
        from gi.repository import Gtk

        found, stack = [], [widget]
        while stack:
            node = stack.pop()
            if isinstance(node, Gtk.Label):
                found.append(node.get_text())
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return found

    def snapshot(self, sessions=None, with_key=True):
        from pc_ai_monitor.datos import Snapshot

        stats = {
            "memory": {"total_gib": 128.0, "used_gib": 40.0, "available_gib": 88.0},
            "groups": {"pi": {"rss_gib": 6.34, "pids": [5544]},
                       "other": {"rss_gib": 2.0, "pids": [1]}},
            "processes": [{"pid": 5544, "comm": "pi", "rss_kb": 1000, "cpu": 0.0, "args": "pi"},
                          {"pid": 1, "comm": "systemd", "rss_kb": 10, "cpu": 0.0, "args": "init"}],
        }
        if with_key:
            stats["pi_sessions"] = self.sessions if sessions is None else sessions
        return Snapshot(stats=stats)

    def test_view_builds_header_table_and_footnote(self):
        from pc_ai_monitor.blocks.pi_sesiones import PiSessionsView

        view = PiSessionsView()
        view.update(self.sessions, group_rss_gib=6.34, home=self.home)
        texts = self.texts(view)
        self.assertIn("Pi · 6 sesiones · 5 subagentes", texts)
        self.assertIn("6.34 GiB", texts)
        self.assertIn("no sumar", texts)
        for heading in ("SESIÓN", "PIDS", "PSS ↓", "RSS", "HIJOS"):
            self.assertIn(heading, texts)
        self.assertIn("Koda principal", texts)
        self.assertIn("pid 277162 · ~/…/koda", texts)
        self.assertTrue(any("todo lo que lanzó" in t for t in texts))
        self.assertEqual(len(view._rows), 11)
        self.assertIn("directos", view._children_heading.get_tooltip_text())

    def test_view_is_width_independent(self):
        from gi.repository import Gtk

        from pc_ai_monitor.blocks.pi_sesiones import PiSessionsView

        view = PiSessionsView()
        view.update(self.sessions, group_rss_gib=6.34, home=self.home)
        minimum, _natural, _b1, _b2 = view.measure(Gtk.Orientation.HORIZONTAL, -1)
        # Fits the half-width Recursos column of today (~560 px) as well as
        # the wide 08 column: long names and tags wrap or ellipsize.
        self.assertLessEqual(minimum, 520)

    def test_view_updates_in_place(self):
        from pc_ai_monitor.blocks.pi_sesiones import PiSessionsView

        view = PiSessionsView()
        view.update(self.sessions, group_rss_gib=6.34, home=self.home)
        view.update(self.sessions[:2], group_rss_gib=1.0, home=self.home)
        self.assertEqual(len(view._rows), 2)
        view.update([], group_rss_gib=0.0)
        self.assertIn("No hay sesiones de Pi ahora mismo.", self.texts(view))

    def test_pi_tile_hands_sessions_to_the_page_instead_of_the_process_top10(self):
        # The page owns the one sessions view (08 tab 4, wide card beside the
        # groups); the block only reports what to show, and never draws it.
        from pc_ai_monitor.blocks.grupos import GroupsBlock
        from pc_ai_monitor.blocks.pi_sesiones import PiSessionsView

        calls = []
        block = GroupsBlock()
        block.on_pi_sessions = lambda sessions, rss: calls.append((sessions, rss))
        block.update_snapshot(self.snapshot())
        block._toggle("pi")
        self.assertEqual(calls, [(self.sessions, 6.34)])
        self.assertNotIn("PROCESOS DE 🥧 PI", self.texts(block))
        self.assertFalse(
            any(isinstance(c, PiSessionsView) for c in _walk(block)),
            "the sessions list is never drawn inside the groups card",
        )
        # A new snapshot keeps the open list fresh.
        block.update_snapshot(self.snapshot(self.sessions[:1]))
        self.assertEqual(calls[-1], (self.sessions[:1], 6.34))
        # Closing it tells the page to put the list away.
        block._toggle("pi")
        self.assertEqual(calls[-1], (None, 0.0))
        count = len(calls)
        block.update_snapshot(self.snapshot())
        self.assertEqual(len(calls), count)

    def test_opening_another_group_closes_the_pi_list(self):
        from pc_ai_monitor.blocks.grupos import GroupsBlock

        calls = []
        block = GroupsBlock()
        block.on_pi_sessions = lambda sessions, rss: calls.append(sessions)
        block.update_snapshot(self.snapshot())
        block._toggle("pi")
        block._toggle("other")
        self.assertEqual(calls[-1], None)
        self.assertIn("PROCESOS DE 📦 OTROS", self.texts(block._detail))
        self.assertTrue(block._revealer.get_reveal_child())

    def test_other_groups_keep_their_process_list(self):
        from pc_ai_monitor.blocks.grupos import GroupsBlock

        block = GroupsBlock()
        block.update_snapshot(self.snapshot())
        block._toggle("other")
        self.assertIn("PROCESOS DE 📦 OTROS", self.texts(block._detail))

    def test_old_collector_without_pi_sessions_keeps_the_process_list(self):
        from pc_ai_monitor.blocks.grupos import GroupsBlock

        block = GroupsBlock()
        block.update_snapshot(self.snapshot(with_key=False))
        block._toggle("pi")
        self.assertIn("PROCESOS DE 🥧 PI", self.texts(block._detail))

    def test_pss_never_reaches_the_treemap_or_rss_total(self):
        from pc_ai_monitor.blocks.grupos import GroupsBlock

        block = GroupsBlock()
        block.update_snapshot(self.snapshot())
        block._toggle("pi")
        self.assertEqual(block._donut._center_value, "8.34G")
        pi_tile = next(s for s in block._donut._slices if "Pi" in s.label)
        self.assertAlmostEqual(pi_tile.value, 6.34)


def _walk(widget):
    stack = [widget]
    while stack:
        node = stack.pop()
        yield node
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()


if __name__ == "__main__":
    unittest.main()
