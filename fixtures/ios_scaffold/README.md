# ios_scaffold fixture

Empty target repo used only by the integration smoke tests in
`tests/test_cli_adapter_smoke.py` — the requirement defined there scaffolds a new
iOS Xcode project here from scratch. Not referenced by production code: `agentdev run`
requires `--requirement` and `--repo-path` to be passed explicitly and has no built-in
default target.
