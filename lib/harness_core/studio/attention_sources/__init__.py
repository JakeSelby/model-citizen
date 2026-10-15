# SPDX-License-Identifier: MIT
"""Home's attention sources. `attention.queue` imports every module here once; each registers
its items with `attention.register_attention` on import, so a new source adds a module and
touches nothing else."""
