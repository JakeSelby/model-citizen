# Studio architecture currency review

## Verdict

Pass after two should-fix items.

## Findings and resolution

- Added React DOM and React type packages as explicit AH-SP017 candidates.
- Corrected Mantine status to direct-package MIT verification; the exact transitive tree, integrity
  values, notices, and distribution evidence wait on the lockfile.

Python 3.9 supports the selected standard-library server primitives. Node 22.22.3 satisfies the Vite
and plugin engine floors. Candidate versions remain seed for AH-SP017 rather than adopted runtime truth.

