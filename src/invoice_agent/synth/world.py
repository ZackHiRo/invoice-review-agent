"""The fictional world: one buyer company, its vendor master file and its ledger history."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field

from invoice_agent.money import iban_with_check_digits

BUYER = {
    "name": "Kestrel Labs B.V.",
    "address": ["Keizersgracht 418", "1016 GC Amsterdam", "Netherlands"],
    "vat_id": "NL859273641B01",
}

COUNTRY = {
    # country: (locale, currency, standard VAT, reduced VAT, IBAN BBAN pattern)
    "GB": ("en_GB", "GBP", 20.0, 5.0, "LLLL" + "D" * 14),
    "IE": ("en_IE", "EUR", 23.0, 13.5, "LLLL" + "D" * 14),
    "FR": ("fr_FR", "EUR", 20.0, 5.5, "D" * 23),
    "DE": ("de_DE", "EUR", 19.0, 7.0, "D" * 18),
    "NL": ("nl_NL", "EUR", 21.0, 9.0, "LLLL" + "D" * 10),
    "US": ("en_US", "USD", 0.0, 0.0, ""),
}

CITIES = {
    "GB": [("14 Wharf Road", "Bristol BS1 4RN"), ("7 Mill Lane", "Leeds LS2 7DT"),
           ("88 Gresham Street", "London EC2V 7NQ")],
    "IE": [("21 Dock Street", "Dublin 2, D02 X285"), ("4 Quay Street", "Galway H91 F2K3")],
    "FR": [("12 rue des Tanneurs", "69002 Lyon"), ("45 avenue Jean Jaurès", "33000 Bordeaux"),
           ("3 place du Marché", "44000 Nantes")],
    "DE": [("Hafenstraße 31", "20457 Hamburg"), ("Lindenallee 9", "50968 Köln"),
           ("Ostring 140", "04103 Leipzig")],
    "NL": [("Stationsplein 17", "3511 ED Utrecht"), ("Marktstraat 5", "2611 GS Delft")],
    "US": [("500 Market Street, Suite 210", "San Francisco, CA 94105"),
           ("1200 Elm Avenue", "Austin, TX 78701")],
}

COUNTRY_NAME = {
    "en": {"GB": "United Kingdom", "IE": "Ireland", "FR": "France", "DE": "Germany",
           "NL": "Netherlands", "US": "United States"},
}

# (name, country, category). All invented.
VENDOR_SPECS = [
    ("Brightwater Office Supplies Ltd", "GB", "office"),
    ("Halcyon Data Centres Ltd", "GB", "cloud"),
    ("Merrow & Finch Consulting LLP", "GB", "consulting"),
    ("Cobalt Harbour Hosting Ltd", "IE", "cloud"),
    ("Liffey Translation Services Ltd", "IE", "translation"),
    ("Atelier Lumen SARL", "FR", "marketing"),
    ("Transports Garonne SAS", "FR", "logistics"),
    ("Boulangerie Collective Traiteur SARL", "FR", "catering"),
    ("Nordhafen Logistik GmbH", "DE", "logistics"),
    ("Brandt & Kessel IT-Service GmbH", "DE", "hardware"),
    ("Weißdorn Beratung GmbH", "DE", "consulting"),
    ("Grachtwerk Design B.V.", "NL", "marketing"),
    ("Delftse Kantoorartikelen B.V.", "NL", "office"),
    ("Pinecrest Software Inc.", "US", "cloud"),
    ("Redwood Analytics LLC", "US", "consulting"),
]

UNKNOWN_VENDOR_SPECS = [
    ("Silverline Procurement Ltd", "GB", "office"),
    ("Arcadia Print Solutions Ltd", "IE", "marketing"),
    ("Société Méridienne de Services SAS", "FR", "consulting"),
    ("Falkenberg Technik GmbH", "DE", "hardware"),
    ("Oostpoort Facilitair B.V.", "NL", "catering"),
    ("Harbourview Supplies Ltd", "GB", "office"),
    ("Kranich Datentechnik GmbH", "DE", "cloud"),
    ("Polder Logistics B.V.", "NL", "logistics"),
]

NUMBER_STYLES = ["{prefix}-{year}-{n:04d}", "{prefix}{n:06d}", "{prefix}/{year}/{n:05d}",
                 "{n:07d}", "{prefix}-{n:05d}"]


@dataclass
class Vendor:
    vendor_id: str
    name: str
    country: str
    category: str
    currency: str
    vat_id: str | None
    iban: str | None
    bic: str | None
    address: list[str] = field(default_factory=list)
    number_style: str = "{prefix}-{year}-{n:04d}"
    prefix: str = "INV"

    @property
    def locale(self) -> str:
        return COUNTRY[self.country][0]

    def public(self) -> dict:
        """The vendor master record as the AP team stores it."""
        d = asdict(self)
        for k in ("number_style", "prefix", "category"):
            d.pop(k)
        return d


def _fill(rng: random.Random, pattern: str) -> str:
    out = []
    for ch in pattern:
        if ch == "L":
            out.append(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ"))
        else:
            out.append(rng.choice("0123456789"))
    return "".join(out)


def vat_id_for(rng: random.Random, country: str) -> str | None:
    if country == "GB":
        return "GB" + _fill(rng, "D" * 9)
    if country == "IE":
        return "IE" + _fill(rng, "D" * 7) + rng.choice("ABCDEFGHW")
    if country == "FR":
        return "FR" + _fill(rng, "D" * 11)
    if country == "DE":
        return "DE" + _fill(rng, "D" * 9)
    if country == "NL":
        return "NL" + _fill(rng, "D" * 9) + "B0" + rng.choice("123")
    if country == "US":
        d = _fill(rng, "D" * 9)
        return f"{d[:2]}-{d[2:]}"
    raise ValueError(country)


def iban_for(rng: random.Random, country: str) -> str | None:
    pattern = COUNTRY[country][4]
    if not pattern:
        return None
    return iban_with_check_digits(country, _fill(rng, pattern))


def make_vendor(rng: random.Random, idx: int, spec: tuple[str, str, str], prefix_id: str) -> Vendor:
    name, country, category = spec
    street, city = rng.choice(CITIES[country])
    initials = "".join(w[0] for w in name.replace("&", "").split()[:3]).upper()
    iban = iban_for(rng, country)
    return Vendor(
        vendor_id=f"{prefix_id}{idx:03d}",
        name=name,
        country=country,
        category=category,
        currency=COUNTRY[country][1],
        vat_id=vat_id_for(rng, country),
        iban=iban,
        bic=(_fill(rng, "LLLL") + country + _fill(rng, "LL")) if iban else None,
        address=[street, city, COUNTRY_NAME["en"][country]],
        number_style=rng.choice(NUMBER_STYLES),
        prefix=rng.choice(["INV", initials, "F", "RE", "BILL", initials + "I"]),
    )


def build_vendors(seed: int) -> list[Vendor]:
    rng = random.Random(seed)
    return [make_vendor(rng, i + 1, spec, "V") for i, spec in enumerate(VENDOR_SPECS)]


def build_unknown_vendors(seed: int) -> list[Vendor]:
    rng = random.Random(seed + 1)
    return [make_vendor(rng, i + 1, spec, "X") for i, spec in enumerate(UNKNOWN_VENDOR_SPECS)]
