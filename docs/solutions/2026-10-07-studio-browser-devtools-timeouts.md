# Studio browser tests that all fail on DevTools connects are the machine, not the diff

- 2026-10-07: when every Studio browser test fails with "Chrome did not accept a DevTools target" (the
  `PUT /json/new?about:blank` call in `tests/studio_e2e_support.py`) alongside socket and subprocess
  timeouts, run one on a commit that was green before blaming the change:
  `python3 -m unittest discover -s tests -p "test_studio_browser.py"` in a worktree at that commit. Here
  the known-green commit failed identically after Chrome auto-updated to 155 and `fseventsd` held about
  180% CPU, and the same suites passed with longer DevTools timeouts, so Chrome was slow, not broken.
  Fix the machine (restart `fseventsd` or reboot) and rerun; never widen timeouts in committed tests.
