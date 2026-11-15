"""Local configuration; no account credentials are read or stored."""
from dataclasses import dataclass, field
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    state_dir: Path = field(default_factory=lambda: PACKAGE_ROOT / "state")
    baseline_ledger: Path | None = field(default_factory=lambda:
        PACKAGE_ROOT.parent / "sofia_sar" / "evidence" / "transfer_ledger.jsonl")
    network_limit_bytes: int = 2_000_000_000
    max_response_bytes: int = 8_000_000
    max_cache_bytes: int = 256_000_000
    timeout_seconds: int = 30

    @property
    def cache_dir(self):
        return self.state_dir / "cache"

    @property
    def ledger_path(self):
        return self.state_dir / "transfer_ledger.jsonl"
