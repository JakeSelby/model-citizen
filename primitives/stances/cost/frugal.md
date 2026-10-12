# Cost stance: frugal

Session effort runs **low** except for design or adversarial review. Subagents are gatherers only, fan-out no wider than
three; fast mode and agent teams stay off. Past 160,000 tokens the feed flags the cost; **at 180,000 the turn's end is
blocked once to save the handoff; at 200,000 the context resets, reseeds from what was saved and work carries on.** A
finished task refreshes the handoff; a desktop session clears itself when nothing is open. Try a deterministic command
before a model call. The table prices roles and bands; tier comes from `delegation`. Cache: `cache-hygiene.md`.
