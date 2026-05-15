from dataclasses import dataclass, field
from enum import Enum

from lawcheck.crawler.snapshot import SiteSnapshot


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    OK = "ok"


@dataclass
class Finding:
    check_id: str
    severity: Severity
    title: str
    evidence: str
    location: str
    law_reference: str
    recommendation: str = ""
    extra: dict = field(default_factory=dict)


class Check:
    id: str = ""
    title: str = ""

    def run(self, snapshot: SiteSnapshot) -> list[Finding]:
        raise NotImplementedError
