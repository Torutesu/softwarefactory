"""ホールドアウトシナリオ: コードベース外に置き、実装 Worker から隠された検証シナリオ。

- 生成は Spec Generator とは別セッションの Worker が行う (実装者と共犯関係を作らない)。
- シナリオは JSON: id / title / steps[{name, cmd, expect_exit, expect_contains, expect_regex, expect_not_contains}]
- 実装 Worker にはパスも中身も渡さず、検証結果 (pass/fail + 概要) のみフィードバックする。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .state import Factory, Mission
from .spec_generator import list_seed_files
from .util import ensure_dir, load_text
from .workers.router import Router


@dataclass
class Step:
    name: str = "step"
    cmd: str = ""
    expect_exit: int = 0
    expect_contains: Optional[str] = None
    expect_not_contains: Optional[str] = None
    expect_regex: Optional[str] = None
    timeout: int = 120

    def to_dict(self) -> dict:
        return vars(self)


@dataclass
class Scenario:
    id: str
    title: str
    weight: float = 1.0
    steps: List[Step] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "weight": self.weight,
                "steps": [s.to_dict() for s in self.steps], "notes": self.notes}

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        steps = [Step(**{k: v for k, v in st.items() if k in Step.__dataclass_fields__}) for st in d.get("steps", [])]
        return cls(id=d["id"], title=d.get("title", d["id"]), weight=float(d.get("weight", 1.0)),
                   steps=steps, notes=d.get("notes", ""))


def scenario_files(factory: Factory, mid: str) -> List[Path]:
    d = factory.holdout_dir_for(mid)
    if not d.exists():
        return []
    return sorted(p for p in d.glob("*.json"))


def load_scenarios(factory: Factory, mid: str) -> List[Scenario]:
    out = []
    for p in scenario_files(factory, mid):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append(Scenario.from_dict(d))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return out


def holdout_prompt(factory: Factory, m: Mission, spec_text: str) -> str:
    seed_dir = factory.seeds_dir / m.seed if m.seed else factory.seeds_dir
    files = list_seed_files(seed_dir)
    files_block = "\n".join(f"  - {f}" for f in files) or "  (ファイルなし)"
    hdir = str(factory.holdout_dir_for(m.id).resolve())
    return f"""[Software Factory] Holdout Generator — ミッション {m.id}: {m.title}

あなたは検証シナリオ生成担当です。実装担当とは完全に別のセッションです。

## 入力材料
- Seed ディレクトリ: {seed_dir.resolve()} 内のファイル一覧:
{files_block}
- 仕様書 (spec.md) の内容は以下に同梱:

=== spec.md ===
{spec_text}
=== spec.md 終わり ===

## タスク
`{hdir}` に検証シナリオを SC-001.json … SC-00N.json (N = 3〜6) として作成する。

各ファイルの形式:

```json
{{
  "id": "SC-001",
  "title": "何を確認するか",
  "weight": 1.0,
  "notes": "(任意) シナリオ設計の説明",
  "steps": [
    {{"name": "health", "cmd": "curl -sf http://127.0.0.1:${{FACTORY_PORT}}/health", "expect_exit": 0}},
    {{"name": "api", "cmd": "curl -s http://127.0.0.1:${{FACTORY_PORT}}/api/x | jq -e .ok", "expect_exit": 0}}
  ]
}}
```

## 制約
- シナリオは spec.md の機能要件を独立に検証できること。主要な要件を漏れなくカバーすること。
- コマンドは非対話で動作するもののみ (curl, jq, python3, grep, diff など)。
- 作業ディレクトリは実装リポジトリ: {Path(m.repo).resolve() if m.repo and Path(m.repo).exists() else "(greenfield: 未作成)"}
  環境変数 $FACTORY_REPO に同じパス、$FACTORY_PORT にアプリの待受ポートが入る。
- 実装リポジトリのファイルは書き換えない (シナリオJSONの作成のみ)。
- 起動が必要な検証では、spec.md の run コマンドが $FACTORY_PORT で待ち受ける前提で curl を書く。
- 最終メッセージは 3〜5 行の要約 (シナリオの網羅ポイント)。
"""


def verify_scenarios(factory: Factory, mid: str) -> List[str]:
    errors = []
    for p in scenario_files(factory, mid):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if not d.get("id") or not d.get("steps"):
                errors.append(f"{p.name}: id/steps が不足")
            for st in d.get("steps", []):
                if not st.get("cmd"):
                    errors.append(f"{p.name}: cmd の無い step がある")
        except json.JSONDecodeError as e:
            errors.append(f"{p.name}: JSON不正 ({e})")
    return errors


def generate(factory: Factory, m: Mission, router: Router) -> bool:
    ensure_dir(factory.holdout_dir_for(m.id))
    spec_text = load_text(factory.spec_file(m.id))

    if _mock_mode(router):
        return _mock_generate(factory, m)

    prompt = holdout_prompt(factory, m, spec_text)
    errors = ["scenario files not created"]
    for attempt in range(2):
        try:
            _, res = router.run("holdout", factory.mission_dir(m.id), prompt)
        except Exception:
            break
        errors = verify_scenarios(factory, m.id)
        if not errors:
            break
    if errors:
        factory.event("holdout_failed", f"{m.id}: " + "; ".join(errors[:3]))
        return False
    factory.event("holdout_generated", m.id)
    return True


def _mock_mode(router: Router) -> bool:
    cands = router.candidates("holdout")
    return bool(cands) and cands[0] == "mock"


def _mock_generate(factory: Factory, m: Mission) -> bool:
    """mock モード: Seed に同梱されたシナリオ (答えキー) をコピーする。"""
    import shutil

    seed_dir = factory.seeds_dir / m.seed if m.seed else factory.seeds_dir
    src = seed_dir / "mock" / "scenarios"
    if not src.is_dir():
        factory.event("holdout_failed", f"{m.id}: mock scenarios not found in seed")
        return False
    d = factory.holdout_dir_for(m.id)
    ensure_dir(d)
    for p in sorted(src.iterdir()):
        if p.suffix in (".json", ".py"):
            shutil.copy2(p, d / p.name)
    errors = verify_scenarios(factory, m.id)
    if errors:
        factory.event("holdout_failed", f"{m.id}: " + "; ".join(errors[:3]))
        return False
    factory.event("holdout_generated", m.id)
    return True
