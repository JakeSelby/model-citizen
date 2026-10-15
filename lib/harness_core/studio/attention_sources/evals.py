# SPDX-License-Identifier: MIT
"""Home's "eval result ready" items (#1333); `evals.ready_items` builds them."""
from harness_core.studio.attention import register_attention
from harness_core.studio.evals import ready_items

register_attention("evals", ready_items)
