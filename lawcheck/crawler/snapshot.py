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
    id: str = ""
    checked: bool = False  # для checkbox/radio — состояние по умолчанию
    required: bool = False


@dataclass
class Form:
    action: str
    method: str
    fields: list[FormField] = field(default_factory=list)
    surrounding_text: str = ""
    page_url: str = ""  # на какой странице форма найдена
    has_policy_link: bool = False  # есть ли в радиусе формы ссылка на Политику


@dataclass
class NetworkRequest:
    url: str
    domain: str
    resource_type: str


@dataclass
class CookieBanner:
    text: str = ""
    buttons: list[str] = field(default_factory=list)
    has_decline_option: bool = False


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
    cookie_banner: CookieBanner | None = None
    error: str = ""


@dataclass
class SiteSnapshot:
    start_url: str
    pages: list[PageSnapshot] = field(default_factory=list)

    def all_forms(self) -> list[Form]:
        return [f for p in self.pages for f in p.forms]
