# SPDX-License-Identifier: MIT
"""Home's routine items: a failed last run or an overdue routine (`routines.attention_items`)."""
from harness_core.studio.attention import register_attention
from harness_core.studio.routines import attention_items

register_attention("routines", attention_items)
