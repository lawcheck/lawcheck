from dataclasses import dataclass, field


@dataclass
class Link:
    url: str
    text: str


@dataclass
class FormField:
    name: str
    type: str
    placeholder: str = ""
    label: str = ""


@dataclass
class Form:
    action: str
    method: str
    fields: list[FormField] = field(default_factory=list)
    surrounding_text: str = ""


@dataclass
class NetworkRequest:
    url: str
    domain: str
    resource_type: str


@dataclass
class PageSnapshot:
    url: str
    status: int
    title: str = ""
    html: str = ""
    text: str = ""
    links: list[Link] = field(default_factory=list)
    forms: list[Form] = field(default_factory=list)
    network: list[NetworkRequest] = field(default_factory=list)
    cookies: list[dict] = field(default_factory=list)
    error: str = ""


@dataclass
class SiteSnapshot:
    start_url: str
    pages: list[PageSnapshot] = field(default_factory=list)
