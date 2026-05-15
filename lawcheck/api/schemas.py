from pydantic import BaseModel, Field, HttpUrl


class ScanRequest(BaseModel):
    url: HttpUrl = Field(..., description="URL сайта для проверки")
    max_pages: int | None = Field(None, ge=1, le=100)


class FindingOut(BaseModel):
    check_id: str
    severity: str
    title: str
    evidence: str
    location: str
    law_reference: str
    recommendation: str = ""


class ScanCreated(BaseModel):
    scan_id: str
    status: str = "pending"


class ScanResult(BaseModel):
    scan_id: str
    status: str  # pending | running | done | error
    url: str
    pages_crawled: int = 0
    findings: list[FindingOut] = []
    error: str = ""
