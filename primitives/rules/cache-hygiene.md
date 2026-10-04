# Cache hygiene

- **Keep the cached prefix stable mid-task:** avoid model, effort, fast-mode, tool-set or MCP changes. Native cache behavior differs; do not assume identical invalidation rules.
- **Never wait in the foreground:** no `sleep` over 30 s or poll loop; background it with an exit condition, or use Monitor.
- **New sessions start cold:** batch small tasks; use the native fresh-session control, not compaction, unless `cost` allows it.
