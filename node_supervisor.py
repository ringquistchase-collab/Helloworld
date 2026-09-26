#!/usr/bin/env python3
"""
node_supervisor.py — keeps this PC's nodes running on their own, and
writes a daily report.

What it does:
  - runs NODE_COUNT nodes (run_node_cli.py --work-sharing) as background
    processes on 127.0.0.1 (local-only, nothing exposed to the network),
    every node trusting every other node's saved signing key
  - restarts a node that exits, waiting 5s, 10s, 20s ... up to 5 minutes
    between attempts while it keeps failing
  - keeps running totals per node process (by boot_id) in
    autonomous/period_totals.json, so crashes, restarts and reboots
    don't lose the day's numbers
  - once a day (REPORT_HOUR, local time, or at the next start if the PC
    was off then): stops the nodes, runs the test suite, writes
    autonomous/reports/YYYY-MM-DD.md, moves the day's chain/identity/
    token/log files into autonomous/archive/YYYY-MM-DD/ (keeping 30 days),
    shows a Windows notification, and starts the nodes again

Commands:
  python node_supervisor.py              run in the foreground
  python node_supervisor.py --install    start at every logon (Task Scheduler), and start now
  python node_supervisor.py --uninstall  stop it and remove the logon task
  python node_supervisor.py --status     what's running right now
  python node_supervisor.py --report-now write a report now (the running supervisor does it)
  python node_supervisor.py --stop       stop the supervisor and its nodes

Everything it writes is under ./autonomous/ (gitignored). Signing keys
and pins live in autonomous/node-N/keys/ and are never archived or
deleted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
TASK_NAME = "dna-chain-project nodes"


@dataclass
class Config:
    base_dir: str = os.path.join(PROJECT_DIR, "autonomous")
    node_count: int = 3
    base_port: int = 9611
    host: str = "127.0.0.1"
    heartbeat_seconds: float = 60.0      # a node mines a plain block this often
    round_seconds: float = 300.0         # work-sharing round
    takeover_seconds: float = 20.0
    report_hour: int = 8                 # local time
    keep_archive_days: int = 30
    poll_seconds: float = 5.0
    run_tests: bool = True
    notify: bool = True
    python: str = field(default_factory=lambda: console_python())

    def node_dir(self, i: int) -> str:
        return os.path.join(self.base_dir, f"node-{i}")

    @property
    def logs_dir(self) -> str:
        return os.path.join(self.base_dir, "logs")

    @property
    def reports_dir(self) -> str:
        return os.path.join(self.base_dir, "reports")

    @property
    def archive_dir(self) -> str:
        return os.path.join(self.base_dir, "archive")

    def path(self, name: str) -> str:
        return os.path.join(self.base_dir, name)


def console_python() -> str:
    """python.exe even when the supervisor itself runs under pythonw.exe
    (children need a real interpreter; they get no window anyway)."""
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        candidate = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(candidate):
            return candidate
    return exe


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------------- #
# pure helpers (tested directly)
# ---------------------------------------------------------------------- #

def next_report_time(now: dt.datetime, hour: int, last_report_date: Optional[dt.date]) -> dt.datetime:
    """When the next daily report is due. If today's hasn't been written
    and its time has passed (PC was off/asleep), it's due now."""
    today_slot = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now >= today_slot and last_report_date != now.date():
        return now
    if now < today_slot:
        return today_slot
    return today_slot + dt.timedelta(days=1)


def restart_delay(consecutive_failures: int) -> float:
    return min(5.0 * (2 ** max(0, consecutive_failures - 1)), 300.0)


def merge_totals(snapshots: dict) -> dict:
    """Per-node totals from the latest status snapshot of every node
    process (keyed by boot_id) seen in the period."""
    per_node: dict[int, dict] = {}
    for snap in snapshots.values():
        nid = snap.get("node_id")
        t = per_node.setdefault(nid, {"starts": 0, "stats": {}, "chain_ok": True, "chain_msgs": [],
                                      "audits": [], "uptime_seconds": 0.0})
        t["starts"] += 1
        t["uptime_seconds"] += max(0.0, snap.get("updated_at", 0) - snap.get("started_at", 0))
        for k, v in (snap.get("stats") or {}).items():
            t["stats"][k] = t["stats"].get(k, 0) + v
        work = snap.get("work") or {}
        for k, v in (work.get("stats") or {}).items():
            t["stats"][k] = t["stats"].get(k, 0) + v
        t["audits"].extend(work.get("recent_audits") or [])
        if not snap.get("chain_ok", True):
            t["chain_ok"] = False
            t["chain_msgs"].append(snap.get("chain_msg"))
    return per_node


def build_report(date: dt.date, period_start: float, period_end: float, totals: dict,
                 crashes: list[dict], tests: Optional[dict], log_problems: dict[int, list[str]],
                 node_count: int) -> tuple[str, bool, list[str]]:
    """(markdown, ok, attention reasons)."""
    reasons = []
    per_node = merge_totals(totals)
    for i in range(node_count):
        if i not in per_node:
            reasons.append(f"node-{i} never reported status")
        elif not per_node[i]["chain_ok"]:
            reasons.append(f"node-{i}'s own chain failed verification")
    bad_audits = [a for t in per_node.values() for a in t["audits"] if not a.get("ok")]
    if bad_audits:
        reasons.append(f"{len(bad_audits)} audit(s) found problems in a node's chain")
    failed_blocks = sum(t["stats"].get("blocks_failed_verification", 0) for t in per_node.values())
    if failed_blocks:
        reasons.append(f"{failed_blocks} block(s) failed verification")
    rejected = sum(t["stats"].get("handshakes_rejected", 0) for t in per_node.values())
    if rejected:
        reasons.append(f"{rejected} handshake(s) rejected (bad signature or changed key)")
    if len(crashes) >= 3:
        reasons.append(f"nodes exited unexpectedly {len(crashes)} times")
    if tests is not None and not tests.get("ok"):
        reasons.append(f"test suite: {tests.get('summary')}")
    ok = not reasons

    fmt = lambda ts: dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# dna-chain-project daily report - {date.isoformat()}",
        "",
        f"Period: {fmt(period_start)} to {fmt(period_end)}",
        "",
        f"**Overall: {'OK' if ok else 'NEEDS ATTENTION'}**",
    ]
    if reasons:
        lines += [""] + [f"- {r}" for r in reasons]

    lines += ["", "## Nodes", "",
              "| Node | Uptime | Starts | Blocks mined | Blocks verified | Failed | Replays rejected "
              "| Work done (research / chain-tip / audit) | Takeovers | Own chain |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for i in range(node_count):
        t = per_node.get(i)
        if not t:
            lines.append(f"| node-{i} | - | 0 | - | - | - | - | - | - | no status |")
            continue
        s = t["stats"]
        hours = t["uptime_seconds"] / 3600
        lines.append(
            f"| node-{i} | {hours:.1f} h | {t['starts']} | {s.get('blocks_mined', 0)} | "
            f"{s.get('blocks_verified', 0)} | {s.get('blocks_failed_verification', 0)} | "
            f"{s.get('replays_rejected', 0)} | {s.get('work_done:research', 0)} / "
            f"{s.get('work_done:external_info', 0)} / {s.get('work_done:audit', 0)} | "
            f"{s.get('takeovers', 0)} | {'intact' if t['chain_ok'] else 'FAILED'} |")

    all_audits = [a for t in per_node.values() for a in t["audits"]]
    lines += ["", "## Audits", "",
              f"{sum(1 for a in all_audits if a.get('ok'))} passed, {len(bad_audits)} found problems "
              f"(most recent per node process shown in status files)."]
    for a in bad_audits[:10]:
        lines.append(f"- round {a.get('round')}: node-{a.get('by')} audited node-{a.get('target')}: "
                     f"{'; '.join(a.get('problems') or [])}")

    lines += ["", "## Restarts", ""]
    if crashes:
        for c in crashes[-20:]:
            lines.append(f"- {fmt(c['at'])} node-{c['node']} exited with code {c['code']}")
    else:
        lines.append("No unexpected exits.")

    lines += ["", "## Tests", ""]
    if tests is None:
        lines.append("Not run.")
    else:
        lines.append(f"{'PASSED' if tests.get('ok') else 'FAILED'}: {tests.get('summary')}")
        for f in (tests.get("failures") or [])[:15]:
            lines.append(f"- {f}")

    if any(log_problems.values()):
        lines += ["", "## Errors from the node logs", ""]
        for i, probs in sorted(log_problems.items()):
            for p in probs[-10:]:
                lines.append(f"- node-{i}: `{p}`")

    lines += ["", "---", f"Files: autonomous/archive/{date.isoformat()}/ (this period's chains, identities, "
              "tokens and node logs), autonomous/logs/supervisor.log, "
              "autonomous/node-N/keys/ (signing keys and pins, never archived)."]
    return "\n".join(lines) + "\n", ok, reasons


LOG_PROBLEM = re.compile(r"(Traceback|Error|error:|FAILED|REFUSING|!! audit|rejected|dropping)")


def scan_log(path: str, limit: int = 50) -> list[str]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if LOG_PROBLEM.search(line):
                out.append(line.strip()[:300])
    return out[-limit:]


def rotate(cfg: Config, date: dt.date) -> str:
    """Move the period's per-node data files and logs into the archive.
    Keys and pins are left in place."""
    dest_root = os.path.join(cfg.archive_dir, date.isoformat())
    for i in range(cfg.node_count):
        dest = os.path.join(dest_root, f"node-{i}")
        os.makedirs(dest, exist_ok=True)
        nd = cfg.node_dir(i)
        for name in (f"chain_node-{i}.json", f"identity_node-{i}.dna.json", f"tokens_node-{i}.json",
                     "status.json"):
            src = os.path.join(nd, name)
            if os.path.exists(src):
                shutil.move(src, os.path.join(dest, name))
        log = os.path.join(cfg.logs_dir, f"node-{i}.log")
        if os.path.exists(log):
            shutil.move(log, os.path.join(dest, f"node-{i}.log"))
    return dest_root


def prune_archive(cfg: Config, today: dt.date) -> list[str]:
    removed = []
    if not os.path.isdir(cfg.archive_dir):
        return removed
    cutoff = today - dt.timedelta(days=cfg.keep_archive_days)
    for name in os.listdir(cfg.archive_dir):
        try:
            day = dt.date.fromisoformat(name)
        except ValueError:
            continue
        if day < cutoff:
            shutil.rmtree(os.path.join(cfg.archive_dir, name), ignore_errors=True)
            removed.append(name)
    return removed


def notify(title: str, body: str) -> None:
    """Windows toast notification via Windows PowerShell; text passed in
    environment variables so it can't be interpreted as script."""
    if os.name != "nt":
        return
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$t = $x.GetElementsByTagName('text');"
        "$t.Item(0).AppendChild($x.CreateTextNode($env:DNA_TOAST_TITLE)) > $null;"
        "$t.Item(1).AppendChild($x.CreateTextNode($env:DNA_TOAST_BODY)) > $null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'"
        ").Show([Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    env = dict(os.environ, DNA_TOAST_TITLE=title[:120], DNA_TOAST_BODY=body[:400])
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       env=env, timeout=30, creationflags=NO_WINDOW, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------------- #
# the supervisor
# ---------------------------------------------------------------------- #

@dataclass
class NodeProc:
    index: int
    proc: Optional[subprocess.Popen] = None
    started_at: float = 0.0
    consecutive_failures: int = 0
    restart_at: float = 0.0
    log_file: object = None


class Supervisor:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        os.makedirs(cfg.logs_dir, exist_ok=True)
        os.makedirs(cfg.reports_dir, exist_ok=True)
        self.log = logging.getLogger("supervisor")
        self.nodes = [NodeProc(i) for i in range(cfg.node_count)]
        self.keys: dict[int, str] = {}
        self.state = self._load_state()

    # -- persisted state --

    def _load_state(self) -> dict:
        try:
            with open(self.cfg.path("period_totals.json"), encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {}
        state.setdefault("period_start", time.time())
        state.setdefault("snapshots", {})
        state.setdefault("crashes", [])
        state.setdefault("last_report_date", None)
        return state

    def _save_state(self) -> None:
        path = self.cfg.path("period_totals.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, path)

    def collect_status(self) -> None:
        for i in range(self.cfg.node_count):
            try:
                with open(os.path.join(self.cfg.node_dir(i), "status.json"), encoding="utf-8") as f:
                    snap = json.load(f)
            except (OSError, ValueError):
                continue
            if snap.get("boot_id"):
                self.state["snapshots"][snap["boot_id"]] = snap
        self._save_state()

    # -- node processes --

    def stop_file(self, i: int) -> str:
        return os.path.join(self.cfg.node_dir(i), "STOP")

    def node_command(self, i: int) -> list[str]:
        ports = [self.cfg.base_port + k for k in range(self.cfg.node_count)]
        peers = ",".join(f"{self.cfg.host}:{p}" for k, p in enumerate(ports) if k != i)
        trust = ",".join(f"{k}={key}" for k, key in sorted(self.keys.items()) if k != i)
        cmd = [self.cfg.python, os.path.join(PROJECT_DIR, "run_node_cli.py"),
               "--id", str(i), "--port", str(ports[i]), "--bind", self.cfg.host,
               "--workdir", self.cfg.node_dir(i), "--work-sharing",
               "--mine-interval", str(self.cfg.heartbeat_seconds),
               "--round-seconds", str(self.cfg.round_seconds),
               "--takeover-seconds", str(self.cfg.takeover_seconds),
               "--status-file", os.path.join(self.cfg.node_dir(i), "status.json"),
               "--stop-file", self.stop_file(i)]
        if peers:
            cmd += ["--peers", peers]
        if trust:
            cmd += ["--trust", trust]
        return cmd

    def load_keys(self) -> None:
        for i in range(self.cfg.node_count):
            out = subprocess.run(
                [self.cfg.python, os.path.join(PROJECT_DIR, "run_node_cli.py"), "--id", str(i),
                 "--workdir", self.cfg.node_dir(i), "--show-key"],
                capture_output=True, text=True, timeout=60, cwd=PROJECT_DIR, creationflags=NO_WINDOW)
            m = re.search(r"public signing key: ([0-9a-f]{64})", out.stdout)
            if not m:
                raise RuntimeError(f"could not read node-{i}'s key: {out.stdout} {out.stderr}")
            self.keys[i] = m.group(1)

    def start_node(self, n: NodeProc) -> None:
        if os.path.exists(self.stop_file(n.index)):
            os.remove(self.stop_file(n.index))
        n.log_file = open(os.path.join(self.cfg.logs_dir, f"node-{n.index}.log"), "a",
                          encoding="utf-8", buffering=1)
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        n.proc = subprocess.Popen(self.node_command(n.index), cwd=PROJECT_DIR, stdout=n.log_file,
                                  stderr=subprocess.STDOUT, env=env, creationflags=NO_WINDOW)
        n.started_at = time.time()
        self.log.info("started node-%d (pid %d)", n.index, n.proc.pid)

    def check_nodes(self, now: float) -> None:
        for n in self.nodes:
            if n.proc is None:
                if now >= n.restart_at:
                    self.start_node(n)
                continue
            code = n.proc.poll()
            if code is None:
                if now - n.started_at > 600:
                    n.consecutive_failures = 0   # stable again
                continue
            n.log_file.close()
            n.proc = None
            # keep the dead process's last numbers before its replacement
            # overwrites status.json
            self.collect_status()
            n.consecutive_failures += 1
            delay = restart_delay(n.consecutive_failures)
            n.restart_at = now + delay
            self.state["crashes"].append({"node": n.index, "code": code, "at": now})
            self._save_state()
            self.log.warning("node-%d exited with code %s; restarting in %.0fs", n.index, code, delay)

    def stop_nodes(self, timeout: float = 30.0) -> None:
        for n in self.nodes:
            if n.proc is not None:
                open(self.stop_file(n.index), "w").close()
        deadline = time.time() + timeout
        for n in self.nodes:
            if n.proc is None:
                continue
            try:
                n.proc.wait(timeout=max(1.0, deadline - time.time()))
            except subprocess.TimeoutExpired:
                self.log.warning("node-%d didn't stop in time, terminating", n.index)
                n.proc.terminate()
                n.proc.wait(timeout=10)
            n.log_file.close()
            n.proc = None
            n.restart_at = 0.0

    def stop_orphans(self) -> None:
        """Nodes left running by a supervisor that was killed (e.g. at
        logoff) would hold the ports; they watch their stop files, so ask
        them to exit and give them time."""
        for i in range(self.cfg.node_count):
            os.makedirs(self.cfg.node_dir(i), exist_ok=True)
            open(self.stop_file(i), "w").close()
        time.sleep(3)

    # -- daily report --

    def run_tests(self) -> dict:
        # Its own temp dir, emptied each run, instead of the shared
        # %TEMP%\pytest-of-<user> that interactive pytest runs also use and
        # clean up (a clash there showed up as PermissionErrors in the
        # first live report).
        basetemp = self.cfg.path("pytest-tmp")
        shutil.rmtree(basetemp, ignore_errors=True)
        try:
            out = subprocess.run([self.cfg.python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                  "--color=no", "--basetemp", basetemp], cwd=PROJECT_DIR,
                                 capture_output=True, text=True, timeout=1800, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return {"ok": False, "summary": "timed out after 30 minutes", "failures": []}
        # full output kept for diagnosing any failure the report lists
        with open(os.path.join(self.cfg.logs_dir, f"tests-{dt.date.today().isoformat()}.txt"), "w",
                  encoding="utf-8") as f:
            f.write(out.stdout + "\n" + out.stderr)
        lines = out.stdout.strip().splitlines()
        summary = lines[-1].strip("= ") if lines else "no output"
        failures = [l for l in lines if l.startswith("FAILED") or l.startswith("ERROR")]
        return {"ok": out.returncode == 0, "summary": summary, "failures": failures}

    def daily(self, now: dt.datetime) -> str:
        self.log.info("daily report starting")
        self.stop_nodes()
        self.collect_status()
        tests = self.run_tests() if self.cfg.run_tests else None
        log_problems = {i: scan_log(os.path.join(self.cfg.logs_dir, f"node-{i}.log"))
                        for i in range(self.cfg.node_count)}
        report, ok, reasons = build_report(
            now.date(), self.state["period_start"], now.timestamp(), self.state["snapshots"],
            self.state["crashes"], tests, log_problems, self.cfg.node_count)
        path = os.path.join(self.cfg.reports_dir, f"{now.date().isoformat()}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        rotate(self.cfg, now.date())
        prune_archive(self.cfg, now.date())
        self.state = {"period_start": now.timestamp(), "snapshots": {}, "crashes": [],
                      "last_report_date": now.date().isoformat()}
        self._save_state()
        self.log.info("report written to %s (%s)", path, "OK" if ok else "; ".join(reasons))
        if self.cfg.notify:
            if ok:
                notify("dna-chain-project: all OK", f"Daily report written: {path}")
            else:
                notify("dna-chain-project: needs attention", "; ".join(reasons)[:300] + f"  ({path})")
        return path

    def report_due(self, now: dt.datetime) -> bool:
        last = self.state.get("last_report_date")
        last_date = dt.date.fromisoformat(last) if last else None
        return now >= next_report_time(now, self.cfg.report_hour, last_date)

    # -- main loop --

    def run(self) -> int:
        self.log.info("supervisor starting (pid %d)", os.getpid())
        self.stop_orphans()
        self.load_keys()
        if self.state.get("last_report_date") is None:
            # first ever run: first report at the next report time, not now
            self.state["last_report_date"] = dt.date.today().isoformat() \
                if dt.datetime.now().hour >= self.cfg.report_hour else None
            self._save_state()
        last_collect = 0.0
        try:
            while True:
                if os.path.exists(self.cfg.path("supervisor.stop")):
                    os.remove(self.cfg.path("supervisor.stop"))
                    self.log.info("stop requested")
                    break
                now = dt.datetime.now()
                if os.path.exists(self.cfg.path("report.now")) or self.report_due(now):
                    if os.path.exists(self.cfg.path("report.now")):
                        os.remove(self.cfg.path("report.now"))
                    self.daily(now)
                self.check_nodes(time.time())
                if time.time() - last_collect >= 60:
                    self.collect_status()
                    last_collect = time.time()
                time.sleep(self.cfg.poll_seconds)
        finally:
            self.stop_nodes()
            self.collect_status()
            self.log.info("supervisor stopped")
        return 0


# ---------------------------------------------------------------------- #
# single instance, install/uninstall, CLI
# ---------------------------------------------------------------------- #

class SingleInstance:
    """Holds an OS lock on autonomous/supervisor.lock for the process's
    lifetime; released automatically if the process dies."""

    def __init__(self, path: str):
        self.path = path
        self.fh = None

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False


def pythonw() -> str:
    exe = console_python()
    candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return candidate if os.path.exists(candidate) else exe


def install() -> int:
    if os.name != "nt":
        print("--install uses Windows Task Scheduler; on other systems run it from cron/systemd.")
        return 1
    action = f'"{pythonw()}" "{os.path.abspath(__file__)}"'
    out = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", action, "/SC", "ONLOGON",
                          "/RL", "LIMITED", "/F"], capture_output=True, text=True)
    print(out.stdout.strip() or out.stderr.strip())
    if out.returncode != 0:
        return out.returncode
    run = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], capture_output=True, text=True)
    print(run.stdout.strip() or run.stderr.strip())
    return run.returncode


def uninstall(cfg: Config) -> int:
    request_stop(cfg)
    out = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True)
    print(out.stdout.strip() or out.stderr.strip())
    return out.returncode


def request_stop(cfg: Config) -> None:
    os.makedirs(cfg.base_dir, exist_ok=True)
    open(cfg.path("supervisor.stop"), "w").close()
    print("Stop requested; the supervisor and its nodes will exit within a few seconds.")


def print_status(cfg: Config) -> int:
    lock = SingleInstance(cfg.path("supervisor.lock"))
    running = not lock.acquire()
    print(f"supervisor: {'running' if running else 'NOT running'}")
    for i in range(cfg.node_count):
        try:
            with open(os.path.join(cfg.node_dir(i), "status.json"), encoding="utf-8") as f:
                s = json.load(f)
            age = time.time() - s.get("updated_at", 0)
            st = s.get("stats", {})
            work = (s.get("work") or {}).get("stats", {})
            print(f"node-{i}: updated {age:.0f}s ago, mined {st.get('blocks_mined', 0)}, "
                  f"verified {st.get('blocks_verified', 0)}, failed {st.get('blocks_failed_verification', 0)}, "
                  f"work {sum(v for k, v in work.items() if k.startswith('work_done'))}, "
                  f"connected to {s.get('connected_peers')}, chain {'intact' if s.get('chain_ok') else 'FAILED'}")
        except (OSError, ValueError):
            print(f"node-{i}: no status yet")
    reports = sorted(os.listdir(cfg.reports_dir)) if os.path.isdir(cfg.reports_dir) else []
    print(f"latest report: {os.path.join(cfg.reports_dir, reports[-1]) if reports else '(none yet)'}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Keep this PC's dna-chain-project nodes running and report daily.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true", help="start at every logon (Task Scheduler) and start now")
    g.add_argument("--uninstall", action="store_true", help="stop and remove the logon task")
    g.add_argument("--status", action="store_true", help="show what's running")
    g.add_argument("--report-now", action="store_true", help="have the running supervisor write a report now")
    g.add_argument("--stop", action="store_true", help="stop the running supervisor and its nodes")
    p.add_argument("--report-hour", type=int, default=8, help="local hour for the daily report (0-23)")
    p.add_argument("--no-tests", action="store_true", help="skip running the test suite in the report")
    args = p.parse_args(argv)
    cfg = Config(report_hour=args.report_hour, run_tests=not args.no_tests)

    if args.install:
        return install()
    if args.uninstall:
        return uninstall(cfg)
    if args.status:
        return print_status(cfg)
    if args.stop:
        request_stop(cfg)
        return 0
    if args.report_now:
        lock = SingleInstance(cfg.path("supervisor.lock"))
        if lock.acquire():
            lock.fh.close()
            print("Supervisor isn't running; writing a report directly.")
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
            print(Supervisor(cfg).daily(dt.datetime.now()))
            return 0
        open(cfg.path("report.now"), "w").close()
        print("Report requested; the running supervisor will write it within a few seconds "
              f"(then the test suite runs). It will appear in {cfg.reports_dir}.")
        return 0

    os.makedirs(cfg.logs_dir, exist_ok=True)
    logging.basicConfig(filename=os.path.join(cfg.logs_dir, "supervisor.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    lock = SingleInstance(cfg.path("supervisor.lock"))
    if not lock.acquire():
        logging.getLogger("supervisor").info("another supervisor is already running; exiting")
        print("Another supervisor is already running.")
        return 0
    return Supervisor(cfg).run()


if __name__ == "__main__":
    raise SystemExit(main())
