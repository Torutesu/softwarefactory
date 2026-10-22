"""Validation Harness の実行器。

- build.json の build/test コマンドをリポジトリ (worktree) 内で実行。
- run が指定されていればアプリを起動し、health_url の起動を待つ。
- ホールドアウトシナリオの steps を実行し、期待値と照合する。
- 結果は dict で返し、LLM-as-Judge への入力とする。
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..holdout import load_scenarios
from ..state import Factory, Mission
from ..util import child_env, load_json, run_shell


@dataclass
class StepResult:
    name: str
    cmd: str
    ok: bool
    code: int
    tail: str = ""
    seconds: float = 0.0


@dataclass
class ScenarioResult:
    id: str
    title: str
    ok: bool
    weight: float = 1.0
    steps: List[StepResult] = field(default_factory=list)
    seconds: float = 0.0


@dataclass
class HarnessResult:
    build: Optional[StepResult] = None
    test: Optional[StepResult] = None
    service_started: bool = False
    health_ok: bool = True
    scenarios: List[ScenarioResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def all_scenarios_ok(self) -> bool:
        return all(s.ok for s in self.scenarios) if self.scenarios else True

    @property
    def base_score(self) -> float:
        if not self.scenarios:
            return 100.0
        total = sum(s.weight for s in self.scenarios) or 1.0
        passed = sum(s.weight for s in self.scenarios if s.ok)
        return round(100.0 * passed / total, 1)

    @property
    def ok(self) -> bool:
        if self.build and not self.build.ok:
            return False
        if self.test and not self.test.ok:
            return False
        return self.all_scenarios_ok

    def to_dict(self) -> dict:
        def s(d):
            return {k: getattr(d, k) for k in ("name", "cmd", "ok", "code", "tail", "seconds")}
        return {
            "build": s(self.build) if self.build else None,
            "test": s(self.test) if self.test else None,
            "service_started": self.service_started,
            "health_ok": self.health_ok,
            "scenarios": [
                {"id": x.id, "title": x.title, "ok": x.ok, "weight": x.weight,
                 "seconds": round(x.seconds, 1),
                 "steps": [s(st) for st in x.steps]}
                for x in self.scenarios
            ],
            "all_scenarios_ok": self.all_scenarios_ok,
            "base_score": self.base_score,
            "warnings": self.warnings,
        }


def _shell(cmd: str, cwd: Path, port: Optional[int] = None, timeout: int = 600, env_extra: Optional[dict] = None) -> StepResult:
    t0 = time.time()
    env = child_env({
        "FACTORY_REPO": str(cwd),
        "FACTORY_PORT": str(port) if port else "",
        "REPO": str(cwd),
        **(env_extra or {}),
    })
    pr = run_shell(cmd, cwd=cwd, timeout=timeout, env=env)
    return StepResult(
        name="shell",
        cmd=cmd,
        ok=pr.code == 0 and not pr.timed_out,
        code=pr.code if not pr.timed_out else 124,
        tail=((pr.out + ("\n--- stderr ---\n" + pr.err if pr.err else ""))[-1500:]),
        seconds=time.time() - t0,
    )


def _check_step(step, cwd: Path, port: Optional[int], holdout_dir) -> StepResult:
    cwd = Path(cwd)
    holdout_dir = Path(holdout_dir)
    r = _shell(step.cmd, cwd, port=port, timeout=step.timeout, env_extra={"FACTORY_HOLDOUT_DIR": str(holdout_dir)})
    ok = r.ok
    if ok and step.expect_contains is not None:
        ok = step.expect_contains in r.tail
    if ok and step.expect_not_contains is not None:
        ok = step.expect_not_contains not in r.tail
    if ok and step.expect_regex is not None:
        ok = re.search(step.expect_regex, r.tail) is not None
    cmp_spec = getattr(step, "expect_png_similar", None)
    if ok and cmp_spec:
        import os

        from .png import similarity

        got = os.path.expandvars(cmp_spec.get("file", ""))
        bench = holdout_dir / os.path.expandvars(cmp_spec.get("to", ""))
        threshold = float(cmp_spec.get("ratio", 0.85))
        if not (os.path.isabs(got) and os.path.exists(got)) and not os.path.exists(got):
            got = os.path.join(str(cwd), cmp_spec.get("file", ""))
        if not os.path.exists(got):
            r.tail = (r.tail + f"\n[png-similar] 画像が見つかりません: {got}")[-1500:]
            ok = False
        elif not bench.exists():
            r.tail = (r.tail + f"\n[png-similar] ベンチ画像が見つかりません: {bench}")[-1500:]
            ok = False
        else:
            try:
                score = similarity(got, str(bench), grid=int(cmp_spec.get("grid", 64)))
            except Exception as e:
                score = 0.0
                r.tail = (r.tail + f"\n[png-similar] 比較エラー: {e}")[-1500:]
            r.tail = f"{r.tail}\n[png-similar] similarity={score:.3f} (閾値 {threshold})"[-1500:]
            ok = score >= threshold
    r.ok = ok
    return r


def _wait_health(url: str, timeout: int = 90) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status < 500:
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def _load_build(factory: Factory, m: Mission) -> dict:
    d = load_json(factory.build_file(m.id)) or {}
    out = {
        "build": d.get("build"),
        "test": d.get("test"),
        "run": d.get("run"),
        "health_url": d.get("health_url"),
        "project_subdir": d.get("project_subdir"),
        "wait_s": int(d.get("wait_s", 90)),
        "step_timeout_s": int(d.get("step_timeout_s", 300)),
    }
    return out


def _resolve_port(health_url: str) -> Optional[int]:
    try:
        return int(re.search(r":(\d+)", health_url).group(1))
    except (AttributeError, ValueError):
        return None


def _port_busy(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _pick_port(preferred: int) -> int:
    """preferred が空いていなければ空きポートを返す (並列ミッションの衝突回避)。"""
    import socket

    if not _port_busy(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _apply_port(url: str, port: int) -> str:
    """health_url 内のポート番号を port に差し替える。"""
    return re.sub(r"(127\.0\.0\.1|localhost):\d+", lambda m: f"{m.group(1)}:{port}", url, count=1)


def evaluate(factory: Factory, m: Mission, wt: Path, judge: bool = True) -> HarnessResult:
    """現在の worktree を検証し、結果を返す。"""
    build = _load_build(factory, m)
    hr = HarnessResult()
    cwd = wt / build["project_subdir"] if build["project_subdir"] else wt

    if build["build"]:
        hr.build = _shell(build["build"], cwd, timeout=build["step_timeout_s"])
        if not hr.build.ok:
            hr.warnings.append("build に失敗したため test/run をスキップ")
            return hr
    if build["test"]:
        hr.test = _shell(build["test"], cwd, timeout=build["step_timeout_s"])
        if not hr.test.ok:
            hr.warnings.append("test に失敗したためシナリオ検証を続行 (結果には反映)")

    scenarios = load_scenarios(factory, m.id)
    needs_service = bool(build["run"]) and any(sc.steps for sc in scenarios)
    proc = None
    port = None
    if needs_service:
        try:
            port = _resolve_port(build["health_url"] or "")
            if port is None:
                port = 8765 + (int(m.id[-2:], 16) % 100)
            port = _pick_port(port)
            env = child_env({"FACTORY_PORT": str(port), "FACTORY_REPO": str(cwd)})
            proc = subprocess.Popen(
                ["/bin/bash", "-lc", build["run"]],
                cwd=str(cwd), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            hr.service_started = True
            if build["health_url"]:
                url = _apply_port(build["health_url"].replace("${FACTORY_PORT}", str(port)), port)
                hr.health_ok = _wait_health(url, timeout=build["wait_s"])
                if not hr.health_ok:
                    hr.warnings.append(f"health_url が起動しませんでした: {url}")
            else:
                hr.service_started = True
        except Exception as e:
            hr.warnings.append(f"service 起動失敗: {e}")

    try:
        hdir = factory.holdout_dir_for(m.id)
        for sc in scenarios:
            steps = []
            ok = True
            t0 = time.time()
            for st in sc.steps:
                sr = _check_step(st, cwd, port, hdir)
                steps.append(sr)
                if not sr.ok:
                    ok = False
            hr.scenarios.append(ScenarioResult(
                id=sc.id, title=sc.title, ok=ok, weight=sc.weight, steps=steps, seconds=time.time() - t0))
    finally:
        if proc:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
    return hr
