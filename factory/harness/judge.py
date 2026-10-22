"""LLM-as-Judge: シナリオ結果とテスト結果から満足度スコアを判定する。

- ジャッジは実装 Worker とは別セッションで動く。
- 入力: spec.md (共有情報), シナリオ結果 (pass/fail + 失敗概要), テスト結果概要, 実装 Worker の要約
- 出力: {"score": 0-100, "verdict": "pass"|"revise", "comments": "...", "priorities": [...]}
- ホールドアウトの中身は渡さない (洗練の対象にさせないため)。
"""
from __future__ import annotations

import json
from typing import Optional

from ..state import Factory, Mission
from ..util import load_text
from .runner import HarnessResult

# 出力の上限設計 (token 最適化): judge の指摘は次反復の prompt に全量載るため長さを絞る
JUDGE_MAX_COMMENTS_CHARS = 400
JUDGE_MAX_PRIORITIES = 3
JUDGE_MAX_PRIORITY_CHARS = 160


def judge_prompt(m: Mission, spec_text: str, hr: HarnessResult, worker_tail: str,
                 diff_text: Optional[str] = None) -> str:
    scen_lines = []
    for s in hr.scenarios:
        mark = "PASS" if s.ok else "FAIL"
        detail = ""
        if not s.ok:
            failed_steps = [st for st in s.steps if not st.ok]
            detail = "; ".join(f"step={st.name} code={st.code}" for st in failed_steps[:3])
        scen_lines.append(f"- {s.id} {mark} 「{s.title}」 {detail}")

    test_block = ""
    if hr.test and not hr.test.ok:
        test_block = "### テスト実行 (失敗)\n```\n" + hr.test.tail[-1200:] + "\n```\n"
    elif hr.test:
        test_block = "### テスト実行 (成功)\n```\n" + hr.test.tail[-400:] + "\n```\n"

    return f"""[Software Factory] Validator — ミッション {m.id}: {m.title}

あなたは検証ジャッジです。実装担当とは別セッションです。
以下の材料から「仕様を満たす実装が行われたか」を 0〜100 点で採点し、判定してください。

## 仕様 (spec.md)
=== spec.md ===
{spec_text}
=== ここまで ===

## ホールドアウトシナリオの実行結果 (内容は非公開。結果のみ)
{chr(10).join(scen_lines) if scen_lines else "(シナリオ未実施)"}

{test_block}
## 実際の変更 (main からの git diff。判断の主根拠)
{_diff_block(diff_text)}

## 実装担当の自己申告 (参考情報。差分・シナリオ結果と食い違う場合は差分を信じる)
{worker_tail}

## 出力 (厳密にこの JSON だけを返す。他のテキストを含めない)
```json
{{
  "score": 0,
  "verdict": "pass",
  "comments": "1〜3文の根拠",
  "priorities": ["次の改善提案1", "改善提案2"]
}}
```

- verdict: 全シナリオが仕様を満たし、かつ実装の品質に重大な懸念がなければ "pass"。それ以外は "revise"。
- score: 0〜100 (シナリオ結果と実際の差分が主根拠。自己申告は参考程度)。
- priorities: verdict が "revise" のときは次反復の具体的な改善提案 (仕様レベルの指摘のみ。コード例は書かない)。pass のときは将来の改善提案 (省略可)。
- 長さの上限: comments は {JUDGE_MAX_COMMENTS_CHARS} 字以内、priorities は重要な順に最大 {JUDGE_MAX_PRIORITIES} 件・各 {JUDGE_MAX_PRIORITY_CHARS} 字以内 (超過分は切り捨てられる)。
"""


def _diff_block(diff_text: Optional[str]) -> str:
    if diff_text is None:
        return "(差分は取得していません)"
    if not diff_text.strip():
        return ("差分なし — main から何も変更されていません。"
                "自己申告で「完了」とあっても、変更ゼロで仕様を満たせる場合を除き pass にしないこと。")
    return "```diff\n" + diff_text + "\n```"


def parse_judge_json(text: str) -> Optional[dict]:
    if not text:
        return None
    dec = json.JSONDecoder()
    idx = text.find("{")
    while idx >= 0:
        try:
            data, _ = dec.raw_decode(text[idx:])
            if isinstance(data, dict) and "score" in data:
                return data
        except json.JSONDecodeError:
            pass
        idx = text.find("{", idx + 1)
    return None


def run_judge(factory: Factory, m: Mission, hr: HarnessResult, worker_tail: str, router,
              diff_text: Optional[str] = None) -> Optional[dict]:
    spec_text = load_text(factory.spec_file(m.id))
    prompt = judge_prompt(m, spec_text, hr, worker_tail, diff_text)
    try:
        _, res = router.run("judge", factory.mission_dir(m.id), prompt)
    except Exception:
        return None
    data = parse_judge_json(res.text)
    if data is None:
        return None
    try:
        data["score"] = int(data.get("score", 0))
    except (TypeError, ValueError):
        data["score"] = 0
    data["verdict"] = str(data.get("verdict", "revise"))
    return cap_judge_output(data)


def cap_judge_output(data: dict) -> dict:
    """comments / priorities を上限に切り詰める (モデルが指示を守らなくても prompt を太らせない)。"""
    data["comments"] = str(data.get("comments") or "")[:JUDGE_MAX_COMMENTS_CHARS]
    pr = data.get("priorities")
    if isinstance(pr, str):
        pr = [pr]
    if not isinstance(pr, list):
        pr = []
    data["priorities"] = [str(p)[:JUDGE_MAX_PRIORITY_CHARS] for p in pr if p][:JUDGE_MAX_PRIORITIES]
    return data
