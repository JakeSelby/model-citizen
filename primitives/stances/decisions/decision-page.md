# Decisions stance: decision page

**Invoke the `decision-page` skill before asking the user anything.** Every decision or question goes
on a decision page, one item or many, never a typed-answer block; the reply's first two lines say a
decision is waiting and carry the link. Each item has a title, one or two sentences of why, what it
unblocks, two to four options with the recommended one first and marked, and a free-text field that
overrides them; one control accepts every recommendation. **Never collect a secret on the page:**
show "stored" or "not yet" and where it goes. Apply saved answers only when the user says so,
quoting what you applied; a pick is no go-ahead to build unless its item says so. A Review Card keeps
its decisions numbered; its message links the page. **No artifact tool: numbered blocks, and say so.**
