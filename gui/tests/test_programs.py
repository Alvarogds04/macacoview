"""Collector tests: program discovery (RAM por programa) and the ps parser.

Ported from the uncommitted ``feat/ram-program-selector`` work (T9 of
``odd/tasks/macacoview-gtk.md``). Deterministic: never runs ``ps``, the
privileged GPU helper or reads host procfs; ``/proc`` is redirected to
fixture directories under the system temp dir, never inside the repo.
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
import hashlib
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
from unittest.mock import patch

_aislamiento.isolate()

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ps_2026-11-09"


loader = SourceFileLoader(
    "macacoview_programs", str(ROOT / "scripts/macacoview_programs.py")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
programs = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = programs
loader.exec_module(programs)


def ps_row(p, rss=None, cpu=None):
    """One row of ``ps -eo PS_COLUMNS``: comm padded to its width first."""
    rss = p["rss_kb"] if rss is None else rss
    cpu = p["cpu"] if cpu is None else cpu
    return f"{p['comm']:<{programs.COMM_WIDTH}} {p['pid']} {p['ppid']} {p['uid']} {rss} {cpu} {p['args']}"


def run_stats(
    home, proc, fake_output, script=ROOT / "scripts/macacoview-stats", argv=()
):
    """Execute only patched glue; redirect procfs to fixtures, never host procfs."""
    path_open, readlink = Path.open, os.readlink

    def mapped(path):
        path = Path(path)
        return (
            proc / path.relative_to("/proc") if path.is_relative_to("/proc") else path
        )

    out = io.StringIO()
    with (
        patch("sys.path", list(sys.path)),
        patch("pathlib.Path.home", return_value=home),
        patch.object(Path, "open", lambda p, *a, **kw: path_open(mapped(p), *a, **kw)),
        patch("builtins.open", lambda p, *a, **kw: path_open(mapped(p), *a, **kw)),
        patch("os.readlink", lambda p: readlink(mapped(p))),
        patch("subprocess.check_output", side_effect=fake_output) as calls,
        patch("sys.argv", [str(script), *argv]),
        contextlib.redirect_stdout(out),
    ):
        runpy.run_path(str(script), run_name="__main__")
        assert calls.call_count == 2, "expected only mocked ps and GPU requests"
    return out.getvalue()


class ProgramTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="macacoview-program-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.proc = self.home / "proc"
        self.proc.mkdir()
        self.processes = []

    def process(
        self,
        pid,
        comm="editor",
        exe="/opt/editor/editor",
        ppid=1,
        args=None,
        rss=100,
        pss: int | None = 60,
        start=7,
    ):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        (directory / "exe").symlink_to(exe)
        fields = ["S", str(ppid)] + ["0"] * 17 + [str(start)]
        (directory / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields))
        if pss is not None:
            (directory / "smaps_rollup").write_text(
                f"00400000-00800000 ---p 00000000 00:00 0 [rollup]\nRss: {rss} kB\nPss: {pss} kB\nPss_Anon: 1 kB\n"
            )
        process = {
            "pid": pid,
            "ppid": ppid,
            "uid": 1000,
            "rss_kb": rss,
            "cpu": 1.5,
            "comm": comm,
            "args": args or exe,
        }
        self.processes.append(process)
        return process

    def discover(self, selected_ids=()):
        return programs.discover_programs(
            self.processes, self.proc, selected_ids=selected_ids
        )

    @staticmethod
    def fixture_id(exe="/opt/editor/editor"):
        return "program:v1:" + hashlib.sha256(("exe:" + exe).encode()).hexdigest()

    @contextlib.contextmanager
    def smaps_reads(self):
        original = Path.open
        reads = []

        def tracked(path, *args, **kwargs):
            if path.name in {"smaps", "smaps_rollup"}:
                reads.append(int(path.parent.name))
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", tracked):
            yield reads

    def test_default_empty_and_absent_selection_never_open_smaps(self):
        self.process(10)
        with self.smaps_reads() as reads:
            result = self.discover()
            self.assertEqual(reads, [])
            self.assertEqual(result["coverage"]["status"], "not_sampled")
            self.assertEqual(result["coverage"]["not_sampled"], 1)
            self.assertEqual(result["coverage"]["unreadable"], 0)
            for ids in ([], None, [self.fixture_id("/opt/absent/absent")]):
                result = programs.discover_programs(
                    self.processes, self.proc, selected_ids=ids
                )
                self.assertEqual(reads, [])
                self.assertIsNone(result["programs"][0]["pss_kb"])
                self.assertEqual(result["programs"][0]["memory_metric"], "rss")

    def test_selected_subtree_only_not_unrelated_descendants(self):
        self.process(10)
        self.process(11, "helper", "/opt/editor/helpers/worker", ppid=10)
        self.process(12, "viewer", "/opt/viewer/viewer", ppid=11)
        self.process(
            13, "python3", "/usr/bin/python3", ppid=10, args="python3 /apps/a.py"
        )
        self.process(14, "bash", "/usr/bin/bash", ppid=10)
        self.process(15, "other", "/opt/other/other")
        with self.smaps_reads() as reads:
            result = programs.discover_programs(
                self.processes, self.proc, selected_ids=[self.fixture_id()]
            )
        self.assertEqual(reads, [10, 11])
        self.assertEqual(result["coverage"]["not_sampled"], 4)
        self.assertEqual(result["coverage"]["measured"], 2)
        self.assertEqual(result["coverage"]["status"], "partial")
        self.assertEqual(result["coverage"]["unreadable"], 0)
        selected = next(g for g in result["programs"] if g["id"] == self.fixture_id())
        self.assertEqual(selected["coverage"]["status"], "full")
        for group in result["programs"]:
            if group is not selected:
                self.assertEqual(group["coverage"]["status"], "not_sampled")

    def test_unselected_groups_never_call_sampler(self):
        self.process(10)
        with patch.object(
            programs, "_sample_pss", side_effect=AssertionError("unselected sampling")
        ) as sample:
            self.discover()
            sample.assert_not_called()

    def test_discovery_to_sample_pid_or_exec_churn_never_reads_smaps(self):
        self.process(10)
        original = programs._sample_pss
        for change in ("start", "exe"):
            with self.subTest(change=change):

                def churn(directory, *args, change=change, **kwargs):
                    if change == "start":
                        stat = directory / "stat"
                        stat.write_text(stat.read_text().rsplit(" ", 1)[0] + " 999")
                    else:
                        exe = directory / "exe"
                        exe.unlink()
                        exe.symlink_to("/opt/other/other")
                    return original(directory, *args, **kwargs)

                with (
                    self.smaps_reads() as reads,
                    patch.object(programs, "_sample_pss", churn),
                ):
                    result = self.discover([self.fixture_id()])
                self.assertEqual(reads, [])
                self.assertEqual(result["coverage"]["raced"], 1)
                self.assertIsNone(result["programs"][0]["pss_kb"])

    def test_interpreter_entry_change_before_sample_never_reads_smaps(self):
        process = self.process(
            10, "python3", "/usr/bin/python3", args="python3 /apps/a.py"
        )
        directory = self.proc / "10"
        (directory / "cmdline").write_bytes(b"python3\0/apps/a.py\0")
        key = programs._program_identity(process, "/usr/bin/python3", directory)["id"]
        original = programs._sample_pss

        def churn(directory, *args, **kwargs):
            (directory / "cmdline").write_bytes(b"python3\0/apps/b.py\0")
            return original(directory, *args, **kwargs)

        with self.smaps_reads() as reads, patch.object(programs, "_sample_pss", churn):
            result = self.discover([key])
        self.assertEqual(reads, [])
        self.assertEqual(result["coverage"]["raced"], 1)

    def test_collector_configured_ids_sample_even_when_visibility_disabled(self):
        self.process(10)
        self.process(11, "viewer", "/opt/viewer/viewer", ppid=10)
        config = self.home / ".config/macacoview/config.toml"
        config.parent.mkdir(parents=True)

        def fake(cmd, **kwargs):
            if cmd[0] == "ps":
                return "\n".join(ps_row(p, rss=100, cpu=1.0) for p in self.processes)
            self.assertEqual(cmd[0], "sudo")
            return ""

        for enabled, ids, expected in (
            (False, [], []),
            (True, [], []),
            (False, [self.fixture_id()], [10]),
            (True, [self.fixture_id()], [10]),
            (True, [self.fixture_id("/opt/absent/absent")], []),
        ):
            with self.subTest(enabled=enabled, ids=bool(ids)):
                config.write_text(
                    f"[bar]\nprograms_enabled = {str(enabled).lower()}\nprogram_ids = {json.dumps(ids)}\n"
                )
                with self.smaps_reads() as reads:
                    data = json.loads(run_stats(self.home, self.proc, fake))
                self.assertEqual(reads, expected)
                self.assertEqual(
                    data["program_coverage"]["not_sampled"], 2 - len(expected)
                )
                chips = run_stats(self.home, self.proc, fake, argv=("--bar",))
                self.assertEqual("RAM programas" in chips, enabled and bool(expected))

    def test_stable_id_pid_churn_and_disappearance(self):
        self.process(10)
        first = self.discover()["programs"][0]["id"]
        self.processes.clear()
        self.assertEqual(self.discover()["programs"], [])
        self.process(91)
        self.assertEqual(self.discover()["programs"][0]["id"], first)

    def test_browser_helpers_and_same_app_subtree(self):
        self.process(10, "firefox", "/opt/firefox/firefox")
        self.process(11, "Isolated Web Co", "/opt/firefox/firefox", ppid=10)
        self.process(12, "plugin-container", "/opt/firefox/plugin-container", ppid=11)
        self.process(13, "worker", "/opt/firefox/helpers/worker", ppid=12)
        groups = self.discover()["programs"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["pids"], [10, 11, 12, 13])
        self.assertEqual(groups[0]["name"], "firefox")

    def test_shells_and_terminals_are_not_grouping_roots(self):
        self.process(10, "gnome-terminal-server", "/usr/bin/gnome-terminal-server")
        self.process(11, "bash", "/usr/bin/bash", ppid=10)
        self.process(12, ppid=11)
        self.process(13, "viewer", "/opt/viewer/viewer", ppid=11)
        groups = self.discover()["programs"]
        self.assertEqual(len(groups), 4)
        self.assertTrue(all(len(g["pids"]) == 1 for g in groups))

    def test_private_dir_terminal_or_shell_does_not_absorb_its_children(self):
        # Only the barrier keeps these apart: the executables share a private
        # install dir, which the same-app rule would otherwise merge.
        self.process(10, "kitty", "/opt/kitty/kitty")
        self.process(11, "kitten", "/opt/kitty/kitten", ppid=10)
        self.process(20, "editor", "/opt/editor/editor")
        self.process(21, "bash", "/opt/editor/tools/bash", ppid=20)
        groups = self.discover()["programs"]
        self.assertEqual(sorted(len(g["pids"]) for g in groups), [1, 1, 1, 1])

    def test_unrelated_siblings_and_generic_bin_are_not_merged(self):
        self.process(10, "one", "/usr/bin/one")
        self.process(11, "two", "/usr/bin/two", ppid=10)
        self.process(12, "helper", "/opt/editor/helper")
        self.assertEqual(len(self.discover()["programs"]), 3)

    def test_interpreter_apps_do_not_merge_even_with_ancestry(self):
        for i, (comm, exe, a, b) in enumerate(
            [
                (
                    "python3",
                    "/usr/bin/python3",
                    "python3 /apps/a.py --token SECRET",
                    "python3 /apps/b.py",
                ),
                ("node", "/usr/bin/node", "node /apps/a.js", "node /apps/b.js"),
                (
                    "java",
                    "/usr/bin/java",
                    "java -jar /apps/a.jar",
                    "java -jar /apps/b.jar",
                ),
            ]
        ):
            pid = 10 + i * 10
            self.process(pid, comm, exe, args=a)
            self.process(pid + 1, comm, exe, ppid=pid, args=b)
            self.process(
                pid + 2, comm, exe, ppid=pid, args=a.replace("SECRET", "CHANGED")
            )
        groups = self.discover()["programs"]
        self.assertEqual(len(groups), 6)
        self.assertEqual(sorted(len(g["pids"]) for g in groups), [1, 1, 1, 2, 2, 2])
        output = json.dumps(groups)
        for forbidden in ("SECRET", "CHANGED", "--token", "/apps/a.py", "args"):
            self.assertNotIn(forbidden, output)

    def test_python_modules_and_inline_code_have_distinct_identity(self):
        for pid, args in enumerate(
            [
                "python3 -m app_a",
                "python3 -m app_b",
                "python3 -c 'print(1)'",
                "python3 -c 'print(2)'",
            ],
            10,
        ):
            self.process(pid, "python3", "/usr/bin/python3", args=args)
        self.assertEqual(len(self.discover()["programs"]), 4)

    def test_full_pss_not_added_to_rss(self):
        self.process(10, rss=100, pss=40)
        self.process(11, ppid=10, rss=200, pss=70)
        group = self.discover([self.fixture_id()])["programs"][0]
        self.assertEqual(group["rss_kb"], 300)
        self.assertEqual(group["pss_kb"], 110)
        self.assertEqual((group["memory_metric"], group["memory_kb"]), ("pss", 110))
        self.assertEqual(
            group["coverage"],
            {
                "status": "full",
                "total": 2,
                "measured": 2,
                "unreadable": 0,
                "raced": 0,
                "not_sampled": 0,
            },
        )

    def test_partial_pss_explicit_rss_fallback(self):
        self.process(10, pss=40)
        self.process(11, ppid=10, pss=None)
        group = self.discover([self.fixture_id()])["programs"][0]
        self.assertEqual(group["pss_kb"], 40)
        self.assertEqual((group["memory_metric"], group["memory_kb"]), ("rss", 200))
        self.assertEqual(
            group["coverage"],
            {
                "status": "partial",
                "total": 2,
                "measured": 1,
                "unreadable": 1,
                "raced": 0,
                "not_sampled": 0,
            },
        )

    def test_unreadable_pss_is_not_zero(self):
        self.process(10)
        original = Path.read_text

        def denied(path, *a, **kw):
            if path.name == "smaps_rollup":
                raise PermissionError("fixture")
            return original(path, *a, **kw)

        with patch.object(Path, "read_text", denied):
            group = self.discover([self.fixture_id()])["programs"][0]
        self.assertIsNone(group["pss_kb"])
        self.assertEqual(group["coverage"]["status"], "unreadable")
        self.assertEqual(group["memory_metric"], "rss")

    def test_malformed_missing_duplicate_or_negative_pss(self):
        self.process(10)
        for text in [
            "Rss: 10 kB\n",
            "Pss: wat kB\n",
            "Pss: -1 kB\n",
            "Pss: 1 MB\n",
            "Pss: 1 kB\nPss: 2 kB\n",
        ]:
            with self.subTest(text=text):
                (self.proc / "10/smaps_rollup").write_text(text)
                group = self.discover([self.fixture_id()])["programs"][0]
                self.assertIsNone(group["pss_kb"])
                self.assertEqual(group["coverage"]["unreadable"], 1)

    def test_real_zero_pss_is_full_not_unreadable(self):
        self.process(10, pss=0)
        group = self.discover([self.fixture_id()])["programs"][0]
        self.assertEqual(group["pss_kb"], 0)
        self.assertEqual(group["coverage"]["status"], "full")

    def test_disappearing_pid_is_counted_as_race(self):
        self.process(10)
        (self.proc / "10/stat").unlink()
        result = self.discover([self.fixture_id()])
        self.assertEqual(result["coverage"]["raced"], 1)
        self.assertEqual(result["programs"][0]["coverage"]["status"], "race")
        self.assertIsNone(result["programs"][0]["pss_kb"])

    def test_pid_reuse_during_rollup_discards_pss(self):
        self.process(10)
        original = Path.read_text

        def churn(path, *a, **kw):
            value = original(path, *a, **kw)
            if path.name == "smaps_rollup":
                stat = path.parent / "stat"
                stat.write_text(original(stat).rsplit(" ", 1)[0] + " 999")
            return value

        with patch.object(Path, "read_text", churn):
            group = self.discover([self.fixture_id()])["programs"][0]
        self.assertEqual(group["coverage"]["raced"], 1)
        self.assertIsNone(group["pss_kb"])

    def test_exec_change_during_rollup_is_race(self):
        self.process(10)
        original = Path.read_text

        def change(path, *a, **kw):
            value = original(path, *a, **kw)
            if path.name == "smaps_rollup":
                exe = path.parent / "exe"
                exe.unlink()
                exe.symlink_to("/opt/other/other")
            return value

        with patch.object(Path, "read_text", change):
            group = self.discover([self.fixture_id()])["programs"][0]
        self.assertEqual(group["coverage"]["raced"], 1)

    def test_executable_permission_falls_back_to_comm(self):
        self.process(10)
        with patch("os.readlink", side_effect=PermissionError("fixture")):
            first = self.discover()["programs"][0]
            self.assertIsNone(first["executable"])
            self.processes[0]["pid"] = 99
            self.assertEqual(first["id"], self.discover()["programs"][0]["id"])

    def test_parent_cycles_are_bounded(self):
        self.process(10, ppid=11)
        self.process(11, ppid=10)
        self.assertEqual(len(self.discover()["programs"]), 1)

    def test_enumeration_has_no_top_n_or_memory_threshold(self):
        for pid in range(10, 160):
            self.process(pid, f"app{pid}", f"/opt/app{pid}/run", rss=0, pss=0)
        result = self.discover()
        self.assertEqual(len(result["programs"]), 150)
        self.assertEqual(result["coverage"]["total"], 150)
        self.assertTrue(result["enumeration_complete"])

    def test_interpreter_uses_nul_delimited_cmdline_not_lossy_ps_args(self):
        for pid in (10, 11):
            self.process(
                pid,
                "python3",
                "/usr/bin/python3",
                args="python3 /apps/shared name.py --token SECRET",
            )
            (self.proc / str(pid) / "cmdline").write_bytes(
                f"python3\0/apps/shared name{pid}.py\0--token\0SECRET\0".encode()
            )
        groups = self.discover()["programs"]
        self.assertEqual(len(groups), 2)
        self.assertNotIn("SECRET", json.dumps(groups))
        self.assertNotIn("shared name", json.dumps(groups))

    def test_relative_java_jars_use_cwd(self):
        for pid in (10, 11):
            self.process(pid, "java", "/usr/bin/java", args="java -jar app.jar")
            (self.proc / str(pid) / "cwd").symlink_to(f"/apps/{pid}")
        self.assertEqual(len(self.discover()["programs"]), 2)

    def test_parser_keeps_empty_args_and_zero_rss_processes(self):
        text = f"{'kworker':<64} 10 1 0 0 0.0\n"
        processes, skipped = programs.parse_ps(text)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0]["args"], "")

    def test_absolute_java_entry_points_ignore_unrelated_working_directory(self):
        for pid, cwd in ((10, "/work/a"), (11, "/work/b")):
            self.process(pid, "java", "/usr/bin/java", args="java -jar /apps/app.jar")
            (self.proc / str(pid) / "cwd").symlink_to(cwd)
        self.assertEqual(len(self.discover()["programs"]), 1)
        for process in self.processes:
            process["args"] = "java -cp /apps/classes Main"
        self.assertEqual(len(self.discover()["programs"]), 1)

    def test_java_equals_classpath_options_do_not_merge_apps(self):
        self.process(10, "java", "/usr/bin/java", args="java --class-path=/apps/a Main")
        self.process(11, "java", "/usr/bin/java", args="java --class-path=/apps/b Main")
        self.assertEqual(len(self.discover()["programs"]), 2)

    def test_user_bin_is_not_a_private_application_directory(self):
        self.process(10, "one", "/home/fixture/.local/bin/one")
        self.process(11, "two", "/home/fixture/.local/bin/two", ppid=10)
        self.assertEqual(len(self.discover()["programs"]), 2)

    def test_java_same_main_different_classpaths_are_distinct(self):
        self.process(10, "java", "/usr/bin/java", args="java -cp /apps/a Main")
        self.process(11, "java", "/usr/bin/java", ppid=10, args="java -cp /apps/b Main")
        self.assertEqual(len(self.discover()["programs"]), 2)

    def test_relative_interpreter_entries_and_modules_use_cwd(self):
        for pid in (10, 11):
            self.process(pid, "python3", "/usr/bin/python3", args="python3 -m app")
            (self.proc / str(pid) / "cwd").symlink_to(f"/apps/{pid}")
        self.assertEqual(len(self.discover()["programs"]), 2)

    def test_parser_preserves_comm_with_spaces_and_reports_bad_rows(self):
        text = f"{'Isolated Web Co':<64} 10 1 1000 20 0.5 /opt/firefox/firefox -contentproc\nbroken\n"
        processes, skipped = programs.parse_ps(text)
        self.assertEqual(processes[0]["comm"], "Isolated Web Co")
        self.assertEqual(processes[0]["args"], "/opt/firefox/firefox -contentproc")
        self.assertEqual(skipped, 1)

    def test_empty_or_malformed_ps_does_not_claim_complete_enumeration(self):
        for output in ("", "broken\n"):
            with self.subTest(output=output):
                data = json.loads(
                    run_stats(
                        self.home, self.proc, lambda *a, output=output, **kw: output
                    )
                )
                self.assertFalse(data["program_enumeration"]["complete"])

    def test_model_and_watch_defaults_retained_with_dynamic_enumeration(self):
        self.process(
            10,
            "llama-server",
            "/opt/llama/llama-server",
            args="llama-server --alias fixture --model /models/fixture.gguf --port 9090",
        )
        self.process(11, "firefox", "/opt/firefox/firefox")
        ps = "\n".join(ps_row(p) for p in self.processes)

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ps":
                return ps
            self.assertEqual(cmd, ["sudo", "-n", "/usr/local/bin/amdgpu-gem-info-read"])
            return "pid 10 fixture\n : 1073741824 byte GTT\n"

        data = json.loads(run_stats(self.home, self.proc, fake_run))
        self.assertEqual(data["models"][0]["alias"], "fixture")
        self.assertEqual(data["models"][0]["gtt_gib"], 1)
        self.assertEqual(data["groups"]["firefox"]["rss_gib"], 100 / 1024**2)
        self.assertEqual(data["groups"]["other"]["rss_gib"], 0)
        self.assertEqual(len(data["programs"]), 2)
        self.assertNotIn("args", json.dumps(data["programs"]))
        self.assertEqual(data["program_coverage"]["measured"], 0)
        self.assertEqual(data["program_coverage"]["not_sampled"], 2)
        chips = "\U0001f9e91.0  \U0001f98a0.0"
        self.assertEqual(
            run_stats(self.home, self.proc, fake_run, argv=("--bar",)).strip(), chips
        )
        self.assertEqual(
            run_stats(self.home, self.proc, fake_run, argv=("--pill",)).strip(),
            "0.0|0.0|" + chips,
        )

    def test_selected_program_chips_opt_in_metrics_and_pid_churn(self):
        self.process(
            10,
            "python3",
            "/usr/bin/python3",
            args="python3 /apps/a.py --token SECRET",
            pss=40,
        )
        config = self.home / ".config/macacoview/config.toml"
        config.parent.mkdir(parents=True)
        key = self.discover()["programs"][0]["id"]

        def fake(cmd, **kw):
            if cmd[0] == "ps":
                return "\n".join(
                    ps_row(dict(p, ppid=1), cpu=1.0) for p in self.processes
                )
            self.assertEqual(cmd[0], "sudo")
            return ""

        def chips():
            return run_stats(self.home, self.proc, fake, argv=("--bar",)).strip()

        config.write_text(f'[bar]\nprogram_ids = ["{key}"]\n')
        self.assertNotIn("PSS", chips())
        for malformed in (
            'programs_enabled = "true"\nprogram_ids = ["' + key + '"]',
            'programs_enabled = true\nprogram_ids = "' + key + '"',
            'programs_enabled = true\nprogram_ids = ["bad", 3]',
        ):
            config.write_text("[bar]\n" + malformed)
            self.assertNotIn("PSS", chips())
        config.write_text(
            f'[bar]\nprograms_enabled = true\nprogram_ids = ["{key}", "bad"]\n'
        )
        self.assertIn("PSS", chips())
        self.assertIn(key[-64:-56], chips())
        self.assertIn("no sumar", chips())
        self.assertNotIn("SECRET", chips())
        self.assertNotIn("/apps/a.py", chips())
        self.process(
            11, "python3", "/usr/bin/python3", args="python3 /apps/a.py", pss=None
        )
        self.assertIn("RSS (PSS parcial)", chips())
        self.processes = [self.processes[1]]
        self.assertIn("RSS (PSS inaccesible)", chips())
        self.processes.clear()
        self.assertNotIn("RSS", chips())
        self.process(
            99, "python3", "/usr/bin/python3", args="python3 /apps/a.py", pss=40
        )
        self.assertIn("PSS", chips())


class PackagingTests(unittest.TestCase):
    def test_ci_stages_non_executable_helper_next_to_stats(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn(
            'install -m 644 "$GITHUB_WORKSPACE/scripts/macacoview_programs.py" '
            '"$HOME/.local/bin/macacoview_programs.py"',
            text,
        )

    def test_copied_stats_imports_adjacent_helper_from_unrelated_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bin_dir = home / "bin"
            bin_dir.mkdir()
            for name in ("macacoview-stats", "macacoview_programs.py"):
                shutil.copyfile(ROOT / "scripts" / name, bin_dir / name)
            self.assertEqual(
                (bin_dir / "macacoview_programs.py").stat().st_mode & 0o111, 0
            )
            proc = home / "proc"
            directory = proc / "10"
            directory.mkdir(parents=True)
            (proc / "meminfo").write_text(
                "MemTotal: 1048576 kB\nMemAvailable: 524288 kB\n"
            )
            (directory / "stat").write_text(
                "10 (editor) " + " ".join(["S", "1"] + ["0"] * 17 + ["7"])
            )
            (directory / "smaps_rollup").write_text("Pss: 40 kB\n")
            (directory / "exe").symlink_to("/opt/editor/editor")

            def fake_output(cmd, **kwargs):
                if cmd == ["ps", "-eo", programs.PS_COLUMNS]:
                    return f"{'editor':<64} 10 1 1000 100 1.5 /opt/editor/editor\n"
                self.assertEqual(
                    cmd, ["sudo", "-n", "/usr/local/bin/amdgpu-gem-info-read"]
                )
                return ""

            config = home / ".config/macacoview/config.toml"
            config.parent.mkdir(parents=True)
            config.write_text(f'[bar]\nprogram_ids = ["{ProgramTests.fixture_id()}"]\n')

            unrelated = home / "unrelated"
            unrelated.mkdir()
            old_cwd = Path.cwd()
            try:
                os.chdir(unrelated)
                with (
                    patch(
                        "sys.path",
                        [
                            p
                            for p in sys.path
                            if p and not Path(p).resolve().is_relative_to(ROOT)
                        ],
                    ),
                    patch.dict(sys.modules),
                ):
                    sys.modules.pop("macacoview_programs", None)
                    output = run_stats(
                        home, proc, fake_output, bin_dir / "macacoview-stats"
                    )
                    self.assertEqual(
                        Path(str(sys.modules["macacoview_programs"].__file__)),
                        bin_dir / "macacoview_programs.py",
                    )
            finally:
                os.chdir(old_cwd)
            data = json.loads(output)
            self.assertEqual(data["memory"]["total_gib"], 1)
            self.assertEqual(data["programs"][0]["memory_kb"], 40)
            self.assertEqual(data["programs"][0]["memory_metric"], "pss")
            self.assertEqual(data["program_coverage"]["status"], "full")


# What `ps` printed on 2026-11-09 for each column order the collector has used
# (fixtures/ps_2026-11-09/README.md). The fake answers the order it is asked
# for with the real bytes of that order, like `ps` itself would.
REAL_PS = {
    "pid=,ppid=,uid=,rss=,%cpu=,comm:64=,args=": FIXTURES / "ps_comm_after_cpu.txt",
    "comm:64=,pid=,ppid=,uid=,rss=,%cpu=,args=": FIXTURES / "ps_comm_first.txt",
}
REAL_NAMES = {
    2: "kthreadd",
    10: "kworker/0:0H-kblockd",
    5454: "pi",
    5890: "pi",
    475647: "pi",
    2352565: "Isolated Web Co",
}


class RealPsSplitTests(unittest.TestCase):
    """L21: a 7-digit RSS plus a 4-digit %CPU ate the padding of a comm placed
    after them, so the collector reported «pi … p» with args «i»."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="macacoview-ps-tests-")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.proc = self.home / "proc"
        self.proc.mkdir()

    def fake_ps(self, cmd, **kwargs):
        if cmd[0] == "ps":
            self.assertEqual(cmd[1], "-eo")
            fixture = REAL_PS.get(cmd[2])
            if fixture is None:
                self.fail(f"no real capture for ps columns {cmd[2]!r}")
            return fixture.read_text()
        self.assertEqual(cmd[0], "sudo")
        return ""

    def test_collector_reports_the_real_names_and_args(self):
        data = json.loads(run_stats(self.home, self.proc, self.fake_ps))
        names = {p["pid"]: p["comm"] for p in data["processes"]}
        self.assertEqual(names, REAL_NAMES)
        args = {p["pid"]: p["args"] for p in data["processes"]}
        for pid in (5454, 5890, 475647):
            self.assertEqual(args[pid], "pi")
        self.assertEqual(args[2], "[kthreadd]")
        self.assertTrue(args[2352565].startswith("/snap/firefox/"))
        self.assertTrue(data["program_enumeration"]["complete"])

    def test_overflowing_rss_and_cpu_row_still_lands_in_the_pi_group(self):
        # PID 475647 had RSS 2153908 and 49.8 % CPU in the old capture: with
        # the broken split its comm missed `^pi$` and it fell into «other».
        data = json.loads(run_stats(self.home, self.proc, self.fake_ps))
        self.assertEqual(sorted(data["groups"]["pi"]["pids"]), [5454, 5890, 475647])
        programs_by_name = {p["name"] for p in data["programs"]}
        self.assertNotIn("i", programs_by_name)
        self.assertTrue(all("  " not in name for name in programs_by_name))

    def test_parser_reads_the_real_comm_first_capture(self):
        processes, skipped = programs.parse_ps(REAL_PS[programs.PS_COLUMNS].read_text())
        self.assertEqual(skipped, 0)
        self.assertEqual({p["pid"]: p["comm"] for p in processes}, REAL_NAMES)
        self.assertEqual(
            programs.PS_COLUMNS.split(",")[0], f"comm:{programs.COMM_WIDTH}="
        )

    def test_parser_rejects_a_row_whose_comm_overflows_its_width(self):
        # comm:64 never prints more than 64 characters; a row that does is
        # not this layout, and must be skipped rather than split wrongly.
        row = "x" * 70 + " 10 1 1000 100 1.0 args\n"
        processes, skipped = programs.parse_ps(row)
        self.assertEqual((processes, skipped), ([], 1))


if __name__ == "__main__":
    unittest.main()
