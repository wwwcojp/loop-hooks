"""ゲートの状態を集めて(collect)、人間向けに整形する(render)。

gate.py --status と /loop-hooks:status が共用する。ゲートと同じ経路(repo_root →
config.load → fingerprint.compute → state)を辿るが、コマンドは実行しない。
"""
from . import config, fingerprint, log, state

RECENT = 5


def collect(cwd: str) -> dict:
    root = fingerprint.repo_root(cwd)
    cfg = config.load(root or cwd)
    info = {
        "cwd": cwd, "root": root,
        "config_source": None, "config_error": None, "notice": None,
        "command": None, "on": None, "watch": None, "ignore": None, "timeout_sec": None,
        "fingerprint": None, "verified": None, "will_run": None, "blocked": None,
        "recent": log.tail(root or cwd, RECENT),
    }
    if cfg is None:
        return info
    if "_error" in cfg:
        info["config_error"] = cfg["_error"]
        return info
    info["config_source"] = cfg.get("_source")
    info["notice"] = cfg.get("_notice")
    gate = cfg["gate"]
    info.update(command=gate["command"], on=gate["on"], watch=gate["watch"],
                ignore=gate["ignore"], timeout_sec=gate["timeout_sec"])
    if root is None:
        return info
    current = fingerprint.compute(root, gate)
    verified = state.read_verified(root)
    info.update(fingerprint=current, verified=verified,
                will_run=current != verified,
                blocked=current is not None and current == state.read_blocked(root))
    return info


def _row(label: str, value) -> str:
    return f"  {label:<9} {value}"


def render(info: dict) -> str:
    lines = ["loop-hooks status"]
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
    elif info["will_run"]:
        lines.append(_row("state", "changed since last pass -> gate will run at next stop"))
    else:
        lines.append(_row("state", "unchanged since last pass -> gate will not run"))
    if info["blocked"] is not None:
        blocked_text = ("yes (this state was already blocked once)" if info["blocked"]
                        else "no")
        lines.append(_row("blocked", blocked_text))
    if info["recent"]:
        rows = [_safe_format_recent(r) for r in info["recent"]]
        lines.append(_row("recent", rows[0]))
        lines.extend(_row("", r) for r in rows[1:])
    else:
        lines.append(_row("recent", "(no runs recorded)"))
    return "\n".join(lines)


def _safe_format_recent(r: dict) -> str:
    try:
        return _format_recent(r)
    except (TypeError, ValueError):
        return "(unreadable record)"


def _format_recent(r: dict) -> str:
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
    return " ".join(parts).rstrip()
