# Third-party notices

Everything in this repository is MIT-licensed by its author (see `LICENSE`) except the items
listed here, which are derived from or include third-party material. Each keeps its upstream
notice.

## design-loop skill — derived from dream-loop (MIT)

`claude/skills/design-loop/` is derived from [dream-loop](https://github.com/achimala/dream-loop)
by achimala, MIT License. The upstream notice is preserved in
`claude/skills/design-loop/ATTRIBUTION.md`. Modifications: the loop was rewritten for Claude
Code skills, with rubrics, hard gates, escalation rules and asset-licensing gates added.

## Vendored wheels

`lib/vendor/` holds two unmodified MIT wheels: [tomlkit](https://github.com/python-poetry/tomlkit)
0.15.1, which `lib/harness_core/reconcile.py` imports, and
[ruleprobe](https://github.com/JakeSelby/ruleprobe) 0.1.0, the rule-measurement engine
`claude/hooks/rule-detectors.py` imports. Their upstream notices are in `THIRD_PARTY_NOTICES`,
and `third-party.json` records each one's version, artifact URL and SHA-256.

## concise voice stance — adapted wording (Apache-2.0, MIT)

`primitives/stances/voice/concise.md` adapts short passages from three sources: from
[openai/codex](https://github.com/openai/codex) (Apache-2.0, commit 1f17a0a04b5c), the Codex CLI
prompt's "let the shape of the answer match the shape of the problem"; from
[openai/openai-cookbook](https://github.com/openai/openai-cookbook) (MIT, commit 5986832a5541), the
tiered length limits of `output_verbosity_spec` in the GPT-5.2 prompting guide; and from
[garrytan/gstack](https://github.com/garrytan/gstack) (MIT, commit 730a1017d1a1), the bounded
closer and a good/bad example pair from its voice directive. Modifications: reworded, shortened and
merged into six reply shapes and seven rules. Upstream licence texts are in `THIRD_PARTY_NOTICES`.
Claude Code's built-in Concise style is selected by name; none of its text is included.

## Studio browser bundle

`studio/dist/` contains the browser bundle built from the exact npm dependency tree in
`studio/package-lock.json`. `studio/public/third-party.json` records every package, version, licence,
artifact and integrity digest; `studio/public/THIRD_PARTY_NOTICES.txt` preserves the licence text for
every runtime package and is copied into the distributed bundle by the build.

## Not included, on purpose

The code of conduct is original text. The Contributor Covenant was not vendored because its
steward publishes it under CC BY-SA 4.0, a share-alike licence this repository does not carry.

The [BMad Method](https://github.com/bmad-code-org/BMAD-METHOD) 6.12.0 is MIT-licensed development
tooling by BMad Code, LLC. Its runtime and generated skill projections are intentionally not
redistributed by this repository; only original agent-harness customizations and authored planning
artifacts are committed. BMad and BMad Method are trademarks of BMad Code, LLC, and no endorsement
is implied.

The CI workflows run [actions/checkout](https://github.com/actions/checkout) v7.0.1 and
[actions/setup-python](https://github.com/actions/setup-python) v6.3.0, both MIT-licensed by GitHub,
Inc. and pinned by full commit SHA in `.github/workflows/`. They execute on GitHub's runners and are
not redistributed by this repository. The Studio bundle reproducibility workflow likewise runs
[actions/setup-node](https://github.com/actions/setup-node) v7.0.0 and
[actions/upload-artifact](https://github.com/actions/upload-artifact) v7.0.1, both MIT-licensed by
GitHub, Inc. and pinned by full commit SHA.
