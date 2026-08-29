# Examples

Ready-to-copy pieces for gating a repository with loop-hooks. Everything here is
a starting point: adjust the commands to the scripts your repository actually has.

## Verify runner template (`verify.py`)

A single-file, standard-library-only runner that splits verification into stages
and prints one line per check (`[verify] lint: ok` / `[verify] tests: FAIL (exit 1)`).
loop-hooks records the first `FAIL` line as the failure reason in `--status`.

1. Copy `examples/verify.py` to `scripts/verify.py` in your repository (the
   runner resolves the repository root as the parent of `scripts/`).
2. Edit the `STAGES` table at the top: keep `quick` under about 30 seconds
   (the `--status` summary warns beyond that budget), move slower checks to
   `slow`. Keep `CHECK_TIMEOUT_SEC` in the runner below `gate.timeout_sec` in
   `.loop-hooks.json`; otherwise the gate kills the run first and reports its
   own timeout instead of the runner's `FAIL (timeout after …)` line.
3. Put the matching `.loop-hooks.json` from this directory at the repository
   root and commit it — the gate reads the committed version.

```
uv run python scripts/verify.py quick        # one stage
uv run python scripts/verify.py all          # every stage, in definition order
uv run python scripts/verify.py --print-ci   # the `run:` line for each check
```

### Keep CI identical to the gate

`--print-ci quick` prints the exact command line for each check. Paste those
lines into your CI job as separate `run:` steps, in the same order, and add a
test that regenerates them from `STAGES` and compares with the workflow file —
then a green gate implies a green CI run. loop-hooks does this for itself in
`tests/test_verify.py::test_quick_stage_mirrors_ci`. The lines are POSIX-shell
quoted; Windows runners are out of scope.

### Mutation testing with a ratchet

Not part of the template. See `scripts/verify.py mutation` in this repository
for a per-file killed-count baseline that only the runner may raise.

## Configuration examples

| Directory | `gate.command` | Notes |
| --- | --- | --- |
| `python-uv/` | `uv run python scripts/verify.py quick` | uses the runner template |
| `node-bun/` | `bun run lint && bun test` | expects a `lint` script in `package.json` |
| `rust-cargo/` | `cargo fmt --check && cargo clippy -q -- -D warnings && cargo test -q` | 600 s timeout for cold builds |
| `go/` | `gofmt -l . \| (! grep .) && go vet ./... && go test ./...` | `gofmt -l` lists unformatted files; the pipe fails when it prints any |

`watch` and `ignore` are `fnmatch` patterns against repository-relative paths;
`*` crosses `/`. Omit `on` to gate all three events (`stop`, `subagent_stop`,
`teammate_idle`). See the top-level README for every field.
