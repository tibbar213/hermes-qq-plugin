#!/usr/bin/python3 -I
"""Privileged transport for the trusted Hermes supervisor; never mounted in a task sandbox."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import secrets

ROOT = Path("REPLACE_WITH_ABSOLUTE_TASKS_ROOT")
STATE = Path("/run/hermes-qq-exec")
IMAGE = "REPLACE_WITH_BUILT_IMAGE_ID"
UID = -1  # Installation must set the actual service UID
DOCKER = "/usr/bin/docker"
ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root",
       "USER": "root", "LOGNAME": "root", "LANG": "C.UTF-8"}


def invoke(args, **kwargs):
    return subprocess.run(args, env=ENV, **kwargs)


def workspace(raw, check_owner=True):
    p = Path(raw)
    base = ROOT.resolve(strict=True)
    if p.is_symlink() or p.resolve(strict=True) != p or p.parent != base or not p.is_dir():
        raise ValueError("workspace must be one existing direct task directory")
    if check_owner and p.stat().st_uid != UID:
        raise ValueError("workspace has unexpected owner")
    return p


def identity(raw):
    if not re.fullmatch("[0-9a-f]{24}", raw):
        raise ValueError("invalid execution id")
    return raw


def load(run, validate=True):
    data = json.loads((STATE/(run+".json")).read_text())
    if data["id"] != run:
        raise ValueError("invalid execution state")
    if validate:
        workspace(data["workspace"], check_owner=False)
    return data


def handback(path):
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            p = Path(directory)/name
            st = p.lstat()
            if st.st_nlink <= 1 or p.is_dir():
                os.chown(p, UID, UID, follow_symlinks=False)


def stop(run):
    state = STATE/(run+".json")
    if not state.exists():
        return
    data = load(run, validate=False)
    if data["kind"] == "sandbox":
        invoke([DOCKER, "rm", "-f", "hermes-qq-"+run], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    if data["kind"] == "host":
        prefix = "hermes-qq-" + run + "-"
        listing = invoke(["/usr/bin/systemctl", "list-units", "--all", "--plain", "--no-legend", prefix+"*.service"],
                         capture_output=True, text=True, check=True)
        units = [line.split()[0] for line in listing.stdout.splitlines() if line.split()]
        for unit in units:
            if re.fullmatch(re.escape(prefix) + r"[0-9a-f]{16}\.service", unit):
                invoke(["/usr/bin/systemctl", "stop", unit], check=True, timeout=15)
    try:
        work = workspace(data["workspace"], check_owner=False)
    except (ValueError, FileNotFoundError):
        work = None
    if work:
        handback(work)
    state.unlink()


def start(run, kind, raw, parent):
    if kind not in {"sandbox", "host"}:
        raise ValueError("invalid execution kind")
    work = workspace(raw)
    pid = int(parent)
    if Path("/proc/"+str(pid)).stat().st_uid != UID:
        raise ValueError("invalid supervisor")
    birth = Path("/proc/"+str(pid)+"/stat").read_text().rsplit(")",1)[1].split()[19]
    state = STATE/(run+".json")
    if state.exists():
        data = load(run)
        if data["kind"] != kind or data["workspace"] != str(work) or data["pid"] != pid or data["birth"] != birth:
            raise ValueError("execution id already belongs to another task")
        return
    data = {"id": run, "kind": kind, "workspace": str(work), "pid": pid, "birth": birth}
    state.write_text(json.dumps(data))
    state.chmod(0o600)
    if kind == "sandbox":
        try:
            invoke(["/usr/local/sbin/hermes-qq-network"], check=True, timeout=20)
        except BaseException:
            stop(run)
            raise
        argv = [DOCKER, "run", "-d", "--name", "hermes-qq-"+run,
            "--label", "hermes-qq-execution="+run, "--read-only", "--network", "hermes-qq-sandbox",
            "--dns", "1.1.1.1", "--dns", "8.8.8.8",
            "--sysctl", "net.ipv6.conf.all.disable_ipv6=1",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--memory", "384m", "--memory-swap", "384m", "--cpus", "1",
            "--user", str(UID)+":"+str(UID), "--tmpfs", "/tmp:rw,nosuid,nodev,size=96m,mode=1777",
            "--mount", "type=bind,src="+str(work)+",dst=/workspace",
            "--workdir", "/workspace", "--env", "HOME=/tmp/home", "--env", "PIP_CACHE_DIR=/tmp/pip"]
        for source, target in [
            ("REPLACE_WITH_PROFILE_SKILLS", "/opt/hermes-skills"),
            ("REPLACE_WITH_BUILTIN_SKILLS", "/opt/hermes-builtin-skills")]:
            p = Path(source)
            if p.is_dir() and not p.is_symlink() and str(p.resolve()) == source:
                argv += ["--mount", "type=bind,src="+source+",dst="+target+",readonly"]
        argv += [IMAGE, "/bin/bash", "-c", "mkdir -p /tmp/home; exec sleep infinity"]
        try:
            invoke(argv, check=True, stdout=subprocess.DEVNULL, timeout=30)
        except BaseException:
            stop(run)
            raise


def reap():
    for state in STATE.glob("*.json"):
        run = identity(state.stem)
        data = load(run, validate=False)
        proc = Path("/proc/"+str(data["pid"]))
        try:
            alive = proc.stat().st_uid == UID and proc.joinpath("stat").read_text().rsplit(")",1)[1].split()[19] == data["birth"]
        except FileNotFoundError:
            alive = False
        if not alive:
            stop(run)


def main():
    if os.geteuid() != 0:
        raise ValueError("requires the trusted supervisor transport")
    STATE.mkdir(mode=0o700, exist_ok=True)
    action = sys.argv[1]
    if action == "reap" and len(sys.argv) == 2:
        reap()
        return
    run = identity(sys.argv[2])
    if action == "start" and len(sys.argv) == 6:
        start(run, sys.argv[3], sys.argv[4], sys.argv[5])
    elif action == "stop" and len(sys.argv) == 3:
        stop(run)
    elif action == "exec" and len(sys.argv) == 5:
        data = load(run)
        command, login = sys.argv[3], sys.argv[4] == "login"
        if data["kind"] == "sandbox":
            argv = [DOCKER, "exec", "-i", "hermes-qq-"+run, "/bin/bash", "-lc" if login else "-c", command]
            os.execve(DOCKER, argv, ENV)
        else:
            unit = "hermes-qq-" + run + "-" + secrets.token_hex(8)
            argv = ["/usr/bin/systemd-run", "--quiet", "--pipe", "--wait", "--collect", "--expand-environment=no",
                    "--unit="+unit, "--description=Hermes QQ approved task command",
                    "--property=KillMode=control-group", "--property=TimeoutStopSec=5",
                    "--property=UMask=0022", "--working-directory="+data["workspace"],
                    "--", "/usr/bin/env", "-i", *[k+"="+v for k,v in ENV.items()],
                    "/bin/bash", "-c", command]
            os.execve(argv[0], argv, ENV)
    else:
        raise ValueError("invalid transport operation")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("QQ task transport refused: "+str(exc), file=sys.stderr)
        sys.exit(1)
