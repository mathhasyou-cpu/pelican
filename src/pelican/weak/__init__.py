"""Слабые сигналы: единица выдачи — технология, а не работа.

Ветка живёт рядом с веткой болей и ничего в ней не трогает. Общего у них два
механизма — эмбеддер и сведение формулировок (`pipeline/cluster.py`), и они
переиспользуются, а не копируются.
"""

from __future__ import annotations

from pelican.weak.corpus import junk_ids
from pelican.weak.shards import ShardCorpus, top_k

__all__ = ["ShardCorpus", "junk_ids", "top_k"]
