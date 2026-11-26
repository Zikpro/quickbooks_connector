"""Tax handling for QuickBooks payloads, per QuickBooks region.

UK (VAT): every line carries a VAT TaxCodeRef picked from the invoice's tax
rate, and the transaction sets GlobalTaxCalculation. This is the original
behaviour of the app and must not change.

US (sales tax), per Intuit "Manage sales tax for US locales":
- sales lines carry TaxCodeRef "TAX" or "NON";
- the rate is chosen at transaction level with TxnTaxDetail.TxnTaxCodeRef;
- companies on Automated Sales Tax (AST) calculate tax themselves, and the
  ERPNext amount is forced with TxnTaxDetail.TotalTax;
- GlobalTaxCalculation is not supported and is never sent;
- bills and vendor credits take no line tax codes.

TODO: non-tax charges in ERPNext's taxes table (freight, handling, etc.)
are not pushed to QuickBooks in either region.
"""

import frappe
from frappe.utils import flt

REGION_UK = "UK"
REGION_US = "US"

US_TAXABLE = "TAX"
US_NON_TAXABLE = "NON"


def get_region(settings):
	"""Return "US" or "UK". Anything other than "US" (including blank) is UK."""
	region = (getattr(settings, "qb_region", None) or REGION_UK).strip().upper()
	return REGION_US if region == REGION_US else REGION_UK


def is_us(settings):
	return get_region(settings) == REGION_US


# CompanyInfo.Country values that belong to each region
_REGION_COUNTRIES = {
	REGION_US: {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"},
	REGION_UK: {"GB", "UK", "GBR", "UNITED KINGDOM", "GREAT BRITAIN"},
}


def region_country_warning(settings, qb_country):
	"""Warning text if QuickBooks' CompanyInfo.Country does not match qb_region.

	Returns None when they match or QuickBooks did not report a country.
	Never changes the setting.
	"""
	country = " ".join(str(qb_country or "").split()).upper()
	if not country:
		return None
	region = get_region(settings)
	if country in _REGION_COUNTRIES[region]:
		return None
	return (
		f"The QuickBooks company's country is {qb_country}, but QuickBooks Region "
		f"is set to {region}. Check the QuickBooks Region setting."
	)


# ---------------------------------------------------------------------------
# UK (VAT) - unchanged original rules
# ---------------------------------------------------------------------------


def invoice_tax_rate(doc):
	"""Rate of the first tax row with rate > 0, else 0 (original UK rule)."""
	for tax in getattr(doc, "taxes", None) or []:
		if float(tax.rate or 0) > 0:
			return float(tax.rate)
	return 0


def uk_tax_code(rate, settings):
	"""Map a VAT rate to a UK QuickBooks TaxCode Id (original mapping)."""
	default_tax_code = getattr(settings, "default_tax_code", "12") or "12"
	rate = float(rate or 0)
	if rate >= 20:
		return "3"
	elif rate >= 5:
		return "8"
	elif rate > 0:
		return "10"
	return default_tax_code


def uk_global_tax_calculation(rate):
	return "TaxExcluded" if float(rate or 0) > 0 else "NotApplicable"


# ---------------------------------------------------------------------------
# US (sales tax)
# ---------------------------------------------------------------------------


def us_tax_rows(doc):
	"""Tax rows whose account is a "Tax" account and that have an amount.

	Freight and other charges booked through the taxes table are excluded.
	"""
	rows = []
	for tax in getattr(doc, "taxes", None) or []:
		if not flt(tax.tax_amount) or not tax.account_head:
			continue
		if frappe.db.get_value("Account", tax.account_head, "account_type") == "Tax":
			rows.append(tax)
	return rows


def us_is_taxed(doc):
	return bool(us_tax_rows(doc))


def us_total_tax(doc):
	"""Sales tax only, positive for invoices and returns alike."""
	total = sum(flt(t.tax_amount_after_discount_amount) for t in us_tax_rows(doc))
	return abs(flt(total, 2))


# ---------------------------------------------------------------------------
# Public helpers used by the payload builders
# ---------------------------------------------------------------------------


def sales_line_tax_code(doc, settings):
	"""TaxCodeRef value for each line of an Invoice / CreditMemo."""
	if is_us(settings):
		return US_TAXABLE if us_is_taxed(doc) else US_NON_TAXABLE
	return uk_tax_code(invoice_tax_rate(doc), settings)


def apply_sales_tax(payload, doc, settings, qb_tax_code=None):
	"""Add transaction-level tax fields to an Invoice / CreditMemo payload.

	qb_tax_code: for AST companies without us_sales_tax_code, the TaxCode Id
	QuickBooks already put on this transaction (e.g. when amending), if known.
	"""
	if not is_us(settings):
		payload["GlobalTaxCalculation"] = uk_global_tax_calculation(invoice_tax_rate(doc))
		return payload

	payload.pop("GlobalTaxCalculation", None)
	if not us_is_taxed(doc):
		return payload

	if getattr(settings, "us_uses_ast", 0):
		# AST companies calculate tax in QuickBooks. Intuit's override needs
		# both TxnTaxCodeRef and TotalTax to force ERPNext's amount. Without a
		# known code, TotalTax is sent alone and ast_override_update() fixes it
		# up with the code QuickBooks returns.
		detail = {}
		tax_code = getattr(settings, "us_sales_tax_code", None) or qb_tax_code
		if tax_code:
			detail["TxnTaxCodeRef"] = {"value": str(tax_code)}
		detail["TotalTax"] = us_total_tax(doc)
		payload["TxnTaxDetail"] = detail
	else:
		tax_code = getattr(settings, "us_sales_tax_code", None)
		if not tax_code:
			frappe.throw(
				"US Sales Tax Code ID is not set in Quickbooks Setting. "
				"Use 'Fetch US Tax Codes' to pick one."
			)
		payload["TxnTaxDetail"] = {"TxnTaxCodeRef": {"value": str(tax_code)}}
	return payload


def returned_tax_code(entity):
	"""TxnTaxDetail.TxnTaxCodeRef.value of a QuickBooks Invoice / CreditMemo."""
	detail = (entity or {}).get("TxnTaxDetail") or {}
	return (detail.get("TxnTaxCodeRef") or {}).get("value")


def ast_override_update(sent_payload, entity, doc, settings):
	"""Sparse update that completes the AST override, or None if not needed.

	Needed for a taxed US document on an AST company when the request went
	out without a TxnTaxCodeRef: re-send TotalTax with the TaxCode QuickBooks
	assigned to the transaction.
	"""
	if not (is_us(settings) and getattr(settings, "us_uses_ast", 0) and us_is_taxed(doc)):
		return None
	if (sent_payload.get("TxnTaxDetail") or {}).get("TxnTaxCodeRef"):
		return None
	tax_code = returned_tax_code(entity)
	if not tax_code or not entity.get("Id") or entity.get("SyncToken") is None:
		return None
	return {
		"Id": str(entity["Id"]),
		"SyncToken": str(entity["SyncToken"]),
		"sparse": True,
		"TxnTaxDetail": {
			"TxnTaxCodeRef": {"value": str(tax_code)},
			"TotalTax": us_total_tax(doc),
		},
	}


TAX_MISMATCH_TOLERANCE = 0.01


def tax_mismatch(entity, doc, settings):
	"""Compare QuickBooks' TotalTax with ERPNext's sales tax (US only).

	Returns (qb_total, erpnext_total) when they differ by more than 0.01,
	else None. A missing TotalTax in the QuickBooks response counts as 0.
	"""
	if not is_us(settings) or not isinstance(entity, dict):
		return None
	qb_total = abs(flt((entity.get("TxnTaxDetail") or {}).get("TotalTax"), 2))
	erp_total = us_total_tax(doc)
	# round to cents so float noise (20.01 - 20.0 = 0.0100000001) is not a mismatch
	if flt(abs(qb_total - erp_total), 2) > TAX_MISMATCH_TOLERANCE:
		return qb_total, erp_total
	return None


def expense_line_tax_code(doc, settings, uk_fixed=None):
	"""TaxCodeRef value for Bill / VendorCredit lines, or None to omit it.

	uk_fixed: UK builders that always sent a fixed code pass it here so their
	payloads stay exactly as before.
	"""
	if is_us(settings):
		return None
	if uk_fixed is not None:
		return uk_fixed
	return uk_tax_code(invoice_tax_rate(doc), settings)


def expense_line_detail(account_id, tax_code):
	"""AccountBasedExpenseLineDetail, with TaxCodeRef only when tax_code is set."""
	detail = {"AccountRef": {"value": account_id}}
	if tax_code is not None:
		detail["TaxCodeRef"] = {"value": tax_code}
	return detail


def apply_purchase_tax(payload, doc, settings, account_id, uk_fixed_global=None):
	"""Add tax handling to a Bill / VendorCredit payload.

	UK: set GlobalTaxCalculation exactly as before (rate based, or the fixed
	value some builders always sent).
	US: never send GlobalTaxCalculation; if the document has sales tax, add one
	extra expense line for the tax amount on the same expense account.
	"""
	if not is_us(settings):
		payload["GlobalTaxCalculation"] = uk_fixed_global or uk_global_tax_calculation(
			invoice_tax_rate(doc)
		)
		return payload

	payload.pop("GlobalTaxCalculation", None)
	tax_amount = us_total_tax(doc)
	if tax_amount:
		# TODO: add a dedicated "US tax expense account" setting instead of
		# booking purchase tax to the same expense account as the items.
		payload.setdefault("Line", []).append(
			{
				"DetailType": "AccountBasedExpenseLineDetail",
				"Amount": tax_amount,
				"Description": "Sales tax",
				"AccountBasedExpenseLineDetail": {"AccountRef": {"value": account_id}},
			}
		)
	return payload
