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

    def points(self) -> int:
        """Сколько пунктов проверяет этот Check.

        Обычно один, но A3, E1 и G2 разворачиваются в подпункты с собственными
        check_id (разделы Политики, ИНН/ОГРН/наименование, категории рекламы).
        Число нужно сайту: «N проверок» на лендинге считается отсюда, а не
        вписывается руками — иначе цифра расходится с движком, как это уже
        случилось с «30» (к 10.09.2026 пунктов было 33).
        """
        return 1
