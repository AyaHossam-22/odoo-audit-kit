from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Options:
    since_days: int = 365          # look-back window for moves and bills
    warn_days: int = 30            # "expiring soon" horizon for lots
    tolerance_pct: float = 0.5     # price tolerance (%) for bill vs PO
    tolerance_amount: float = 1.0  # currency tolerance for stock vs ledger
    max_rows: int = 5000           # cap on anomaly rows per check


@dataclass
class CheckResult:
    name: str
    title: str
    description: str
    columns: List[str]
    rows: List[Dict[str, Any]] = field(default_factory=list)
    checked: int = 0
    skipped_reason: Optional[str] = None
    error: Optional[str] = None
    truncated: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        if self.skipped_reason:
            return "skipped"
        return "anomalies" if self.rows else "clean"
