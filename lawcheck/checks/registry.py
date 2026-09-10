"""Единый список проверок — источник истины для API и воркера."""
from lawcheck.checks.base import Check
from lawcheck.checks.cookies.banner import CookieBannerCheck
from lawcheck.checks.cookies.inventory import TrackersInventoryCheck
from lawcheck.checks.pd_152.form_consent import FormConsentCheck
from lawcheck.checks.pd_152.forms_inventory import FormsInventoryCheck
from lawcheck.checks.pd_152.policy_presence import PolicyPresenceCheck
from lawcheck.checks.pd_152.policy_sections import PolicySectionsCheck
from lawcheck.checks.pd_152.policy_validity import PolicyValidityCheck
from lawcheck.checks.requisites.egrul_match import EgrulMatchCheck
from lawcheck.checks.requisites.presence import RequisitesPresenceCheck
from lawcheck.checks.requisites.rkn_match import RknOperatorCheck
from lawcheck.checks.advertising.category_disclaimers import CategoryDisclaimersCheck
from lawcheck.checks.advertising.ord_marking import OrdMarkingCheck
from lawcheck.checks.advertising.superlatives import SuperlativesCheck
from lawcheck.checks.media.age_marking import AgeMarkingCheck
from lawcheck.checks.security.tls import TlsCheck
from lawcheck.checks.zozpp.delivery import DeliveryCheck
from lawcheck.checks.zozpp.oferta import OfertaCheck
from lawcheck.checks.zozpp.returns import ReturnsCheck
from lawcheck.checks.zozpp.state_language import StateLanguageCheck

CHECKS: list[Check] = [
    PolicyPresenceCheck(),
    PolicyValidityCheck(),
    PolicySectionsCheck(),
    FormsInventoryCheck(),
    FormConsentCheck(),
    TrackersInventoryCheck(),
    CookieBannerCheck(),
    RequisitesPresenceCheck(),
    EgrulMatchCheck(),
    RknOperatorCheck(),
    OfertaCheck(),
    DeliveryCheck(),
    ReturnsCheck(),
    StateLanguageCheck(),
    SuperlativesCheck(),
    CategoryDisclaimersCheck(),
    OrdMarkingCheck(),
    AgeMarkingCheck(),
    TlsCheck(),
]


def checkpoints() -> int:
    """Сколько пунктов проверяет сканер — цифра для лендингов и FAQ."""
    return sum(c.points() for c in CHECKS)
