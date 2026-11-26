"""Address helpers for QuickBooks payloads.

US QuickBooks expects CountrySubDivisionCode to be the 2-letter state code
("CA"), while ERPNext stores the state as free text ("California").
"""

US_STATE_CODES = {
	"alabama": "AL",
	"alaska": "AK",
	"arizona": "AZ",
	"arkansas": "AR",
	"california": "CA",
	"colorado": "CO",
	"connecticut": "CT",
	"delaware": "DE",
	"district of columbia": "DC",
	"florida": "FL",
	"georgia": "GA",
	"hawaii": "HI",
	"idaho": "ID",
	"illinois": "IL",
	"indiana": "IN",
	"iowa": "IA",
	"kansas": "KS",
	"kentucky": "KY",
	"louisiana": "LA",
	"maine": "ME",
	"maryland": "MD",
	"massachusetts": "MA",
	"michigan": "MI",
	"minnesota": "MN",
	"mississippi": "MS",
	"missouri": "MO",
	"montana": "MT",
	"nebraska": "NE",
	"nevada": "NV",
	"new hampshire": "NH",
	"new jersey": "NJ",
	"new mexico": "NM",
	"new york": "NY",
	"north carolina": "NC",
	"north dakota": "ND",
	"ohio": "OH",
	"oklahoma": "OK",
	"oregon": "OR",
	"pennsylvania": "PA",
	"rhode island": "RI",
	"south carolina": "SC",
	"south dakota": "SD",
	"tennessee": "TN",
	"texas": "TX",
	"utah": "UT",
	"vermont": "VT",
	"virginia": "VA",
	"washington": "WA",
	"west virginia": "WV",
	"wisconsin": "WI",
	"wyoming": "WY",
}

_US_STATE_ALIASES = {
	"washington dc": "DC",
	"washington d.c.": "DC",
	"d.c.": "DC",
}

US_COUNTRY = "United States"
_US_COUNTRY_ALIASES = {"usa", "us", "u.s.", "u.s.a.", "united states of america", "united states"}

FALLBACK_COUNTRY = "United Kingdom"


def us_state_code(value):
	"""Convert a US state name to its 2-letter code.

	Values that are already 2 letters are upper-cased and passed through.
	Unknown values are returned unchanged so nothing is silently dropped.
	"""
	if not value:
		return value
	text = " ".join(str(value).split())
	if len(text) == 2 and text.isalpha():
		return text.upper()
	key = text.lower()
	return US_STATE_CODES.get(key) or _US_STATE_ALIASES.get(key) or value


def normalize_country(value, settings=None):
	"""Map a country coming from QuickBooks to an ERPNext Country name.

	USA / US / United States of America -> "United States". Empty values fall
	back to settings.default_country, then "United Kingdom" (the old default).
	"""
	text = " ".join(str(value).split()) if value else ""
	if not text:
		return getattr(settings, "default_country", None) or FALLBACK_COUNTRY
	if text.lower() in _US_COUNTRY_ALIASES:
		return US_COUNTRY
	return text


def subdivision_code(state, settings):
	"""CountrySubDivisionCode for an outbound address: US codes, UK unchanged."""
	from quickbooks_connector.qb_tax import is_us

	return us_state_code(state) if is_us(settings) else state
