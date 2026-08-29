"""ゲートの状態を集めて(collect)、人間向けに整形する(render)。

gate.py --status と /loop-hooks:status が共用する。ゲートと同じ経路(repo_root →
config.load → fingerprint.compute → state)を辿るが、コマンドは実行しない。
"""

from typing import Any

from . import config, fingerprint, log, state

RECENT = 5
RECENT_SEARCH = 200  # 最新の ran をこの範囲まで遡って探す
SLOW_BUDGET_SEC = 30  # Stop ゲートの予算(親 spec §7)。超えたら summary で分離を促す


def _recent(root: str) -> list[dict[str, Any]]:
    """直近 RECENT 件。その中に ran が無ければ、最新の ran を末尾に 1 件足す。"""
    records = log.tail(root, RECENT_SEARCH)
    recent = records[:RECENT]
    if any(r.get("decision") == "ran" for r in recent):
        return recent
    last_ran = next((r for r in records[RECENT:] if r.get("decision") == "ran"), None)
    return recent + [last_ran] if last_ran else recent


def summarize(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """ログ全体(新しい順)の集計。空なら None。中央値は上側中央値(偶数件なら大きい側)。"""
    if not records:
        return None
    ran = [r for r in records if r.get("decision") == "ran"]
    ms: list[int] = sorted(r["ms"] for r in ran if isinstance(r.get("ms"), int))
    median: int | None = ms[len(ms) // 2] if ms else None
    recent_ms: list[int] = [r["ms"] for r in ran[:RECENT] if isinstance(r.get("ms"), int)]
    budget = SLOW_BUDGET_SEC * 1000
    slow = (median is not None and median > budget) or any(m > budget for m in recent_ms)
    return {
        "records": len(records),
        "since": str(records[-1].get("ts") or ""),
        "ran": len(ran),
        "pass": sum(1 for r in ran if r.get("result") == "pass"),
        "fail": sum(1 for r in ran if r.get("result") == "fail"),
        "warn": sum(1 for r in ran if r.get("result") == "warn"),
        "skipped": sum(1 for r in records if r.get("decision") == "skipped"),
        "median_ms": median,
        "slow": slow,
    }


def collect(cwd: str) -> dict[str, Any]:
    root = fingerprint.repo_root(cwd)
    cfg = config.load(root or cwd)
    info: dict[str, Any] = {
        "cwd": cwd,
        "root": root,
        "config_source": None,
        "config_error": None,
        "notice": None,
        "command": None,
        "on": None,
        "watch": None,
        "ignore": None,
        "timeout_sec": None,
        "fingerprint": None,
        "verified": None,
        "will_run": None,
        "blocked": None,
        "recent": _recent(root or cwd),
        "summary": summarize(log.tail(root or cwd, log.MAX_LINES)),
        "state_dir": str(state.state_dir()),
    }
    if cfg is None:
        return info
    if "_error" in cfg:
        info["config_error"] = cfg["_error"]
        return info
    info["config_source"] = cfg.get("_source")
    info["notice"] = cfg.get("_notice")
    gate = cfg["gate"]
    info.update(
        command=gate["command"],
        on=gate["on"],
        watch=gate["watch"],
        ignore=gate["ignore"],
        timeout_sec=gate["timeout_sec"],
    )
    if root is None:
        return info
    current = fingerprint.compute(root, gate)
    verified = state.read_verified(root)
    key = current if current is not None else state.FP_UNAVAILABLE_KEY
    info.update(
        fingerprint=current,
        verified=verified,
        # gate と同じ: 指紋が取れなければ走る側に倒す
        will_run=current is None or current != verified,
        blocked=key == state.read_blocked(root),
    )
    return info


def _row(label: str, value: Any) -> str:
    return f"  {label:<9} {value}"


def render(info: dict[str, Any]) -> str:
    version = config.plugin_version()
    lines = [f"loop-hooks status ({version})" if version else "loop-hooks status"]
    lines.append(_row("repo", info["root"] or f"{info['cwd']} (not a git repository)"))
    if info["config_error"]:
        lines.append(_row("config", f"gate disabled: {info['config_error']}"))
        return "\n".join(lines)
    if info["command"] is None:
        lines.append(_row("config", "no .loop-hooks.json -> gate inactive in this repository"))
        return "\n".join(lines)
    lines.append(_row("config", f"{info['config_source']} ({config.CONFIG_NAME})"))
    if info["notice"]:
        lines.append(_row("notice", info["notice"]))
    lines.append(_row("command", info["command"]))
    lines.append(_row("on", ", ".join(info["on"])))
    lines.append(_row("watch", ", ".join(info["watch"])))
    lines.append(_row("ignore", ", ".join(info["ignore"])))
    lines.append(_row("timeout", f"{info['timeout_sec']}s"))
    if info["root"] is None:
        lines.append(_row("state", "gate disabled: not a git repository"))
    elif info["fingerprint"] is None:
        # 指紋が取れない(git が観測できない)。gate は走らせる側に倒すので、理由をそのまま書く
        lines.append(_row("state", "fingerprint unavailable -> gate will run at next stop"))
    elif info["will_run"]:
        lines.append(_row("state", "changed since last pass -> gate will run at next stop"))
    else:
        lines.append(_row("state", "unchanged since last pass -> gate will not run"))
    if info["blocked"] is not None:
        blocked_text = "yes (this state was already blocked once)" if info["blocked"] else "no"
        lines.append(_row("blocked", blocked_text))
    lines.append(_row("records", info["state_dir"]))
    lines.append(_row("summary", _format_summary(info["summary"])))
    if info["recent"]:
        rows = [_safe_format_recent(r) for r in info["recent"]]
        lines.append(_row("recent", rows[0]))
        lines.extend(_row("", r) for r in rows[1:])
    else:
        lines.append(_row("recent", "(no runs recorded)"))
    return "\n".join(lines)


def _format_summary(s: dict[str, Any] | None) -> str:
    if not s:
        return "(no records)"
    since = str(s["since"])[:16].replace("T", " ")
    median = f"{s['median_ms'] / 1000:.1f}s" if s["median_ms"] is not None else "n/a"
    text = (
        f"{s['records']} records since {since}: ran {s['ran']} "
        f"(pass {s['pass']} / fail {s['fail']} / warn {s['warn']}), "
        f"skipped {s['skipped']}, median {median}"
    )
    if s["slow"]:
        text += f" (slow: over the {SLOW_BUDGET_SEC}s budget, split the command)"
    return text


def _safe_format_recent(r: dict[str, Any]) -> str:
    try:
        return _format_recent(r)
    except (TypeError, ValueError):
        return "(unreadable record)"


def _format_recent(r: dict[str, Any]) -> str:
    ts = str(r.get("ts") or "")[:16].replace("T", " ")
    event = str(r.get("event") or "")
    decision = str(r.get("decision") or "")
    parts = [f"{ts:<16}", f"{event:<13}", f"{decision:<9}"]
    if r.get("result"):
        parts.append(f"{str(r['result']):<5}")
    if isinstance(r.get("ms"), int):
        parts.append(f"{r['ms'] / 1000:.1f}s")
    if r.get("note"):
        parts.append(str(r["note"]))
    if r.get("reason"):
        parts.append(str(r["reason"]))
    return " ".join(parts).rstrip()
