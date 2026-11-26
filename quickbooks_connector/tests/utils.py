"""Shared fixtures for payload tests.

Everything that talks to QuickBooks or the database is mocked, so these
tests never call Intuit and never write to the site's database.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

import frappe

API = "quickbooks_connector.api"

TAX_ACCOUNT = "VAT - TC"
FREIGHT_ACCOUNT = "Freight - TC"

QB_IDS = {
	("Customer", "quickbooks_id"): {"Cust A": "58"},
	("Supplier", "quickbooks_id"): {"Supp A": "41"},
	("Item", "quickbooks_id"): {"ITEM-1": "11", "ITEM-2": "12"},
	("Customer", "email_id"): {"Cust A": "cust@example.com"},
	("Account", "account_type"): {TAX_ACCOUNT: "Tax", FREIGHT_ACCOUNT: "Chargeable"},
}

EXPENSE_ACCOUNT_QB_ID = "77"


def make_settings(**overrides):
	"""A settings object that behaves like a Document for getattr(..., default).

	SimpleNamespace is used on purpose: like a frappe Document, a missing
	attribute falls back to the getattr default instead of returning None.
	"""
	values = dict(
		name="Quickbooks Setting",
		company="Test Co",
		realm_id_company_id="123",
		default_tax_code="12",
		default_country=None,
		is_connected=1,
	)
	values.update(overrides)
	return SimpleNamespace(**values)


def fake_get_value(doctype, filters=None, fieldname=None, *args, **kwargs):
	if doctype == "Account" and fieldname == "quickbooks_id" and isinstance(filters, list):
		# expense account lookup used by the bill builders
		return EXPENSE_ACCOUNT_QB_ID
	if doctype in ("Sales Invoice", "Purchase Invoice") and fieldname == "quickbooks_id":
		return "900"  # QB id of the original (amended_from) document
	return QB_IDS.get((doctype, fieldname), {}).get(filters)


def tax_row(rate, amount, account=TAX_ACCOUNT, after_discount=None, charge_type="On Net Total"):
	return SimpleNamespace(
		rate=rate,
		tax_amount=amount,
		tax_amount_after_discount_amount=amount if after_discount is None else after_discount,
		account_head=account,
		charge_type=charge_type,
		description=account,
	)


def item_row(code="ITEM-1", qty=2, rate=50.0):
	return SimpleNamespace(
		item_code=code,
		item_name=f"{code} name",
		description=f"{code} description",
		qty=qty,
		rate=rate,
		amount=qty * rate,
	)


def _doc(**values):
	doc = SimpleNamespace(**values)
	# record db_set() calls on the doc itself so tests can assert them
	doc.db_set = lambda field, value=None, *a, **k: setattr(doc, field, value)
	return doc


def make_si(rate=20, items=None, extra_taxes=None, **overrides):
	items = items if items is not None else [item_row()]
	net = sum(i.amount for i in items)
	taxes = [tax_row(rate, round(net * rate / 100, 2))] if rate else []
	taxes += extra_taxes or []
	values = dict(
		name="SINV-0001",
		customer="Cust A",
		posting_date="2026-01-15",
		due_date="2026-02-14",
		items=items,
		taxes=taxes,
		total_taxes_and_charges=sum(t.tax_amount for t in taxes),
		customer_address=None,
		contact_email=None,
		payment_terms_template=None,
		quickbooks_id=None,
		is_return=0,
		amended_from=None,
		docstatus=1,
	)
	values.update(overrides)
	return _doc(**values)


def make_pi(rate=20, items=None, extra_taxes=None, **overrides):
	items = items if items is not None else [item_row()]
	net = sum(i.amount for i in items)
	taxes = [tax_row(rate, round(net * rate / 100, 2))] if rate else []
	taxes += extra_taxes or []
	values = dict(
		name="PINV-0001",
		supplier="Supp A",
		posting_date="2026-01-15",
		due_date="2026-02-14",
		items=items,
		taxes=taxes,
		total_taxes_and_charges=sum(t.tax_amount for t in taxes),
		quickbooks_id=None,
		is_return=0,
		amended_from=None,
		grand_total=net + sum(t.tax_amount for t in taxes),
		docstatus=1,
	)
	values.update(overrides)
	return _doc(**values)


def make_return(doc):
	"""Turn a sample invoice into a return (negative qty, amount and tax)."""
	for row in doc.items:
		row.qty = -row.qty
		row.amount = -row.amount
	for t in doc.taxes:
		t.tax_amount = -t.tax_amount
		t.tax_amount_after_discount_amount = -t.tax_amount_after_discount_amount
	doc.total_taxes_and_charges = -doc.total_taxes_and_charges
	doc.is_return = 1
	return doc


class Capture:
	"""Patches every outbound path and records the payloads that were sent."""

	def __init__(self, settings=None):
		self.settings = settings or make_settings()
		self.api_instance = mock.MagicMock()
		self.api_instance.settings = self.settings
		self.api_instance.get_api_endpoint.return_value = "https://qb.test/v3"
		self.api_instance.make_request.side_effect = self._make_request
		self.requests_post = mock.MagicMock(side_effect=self._requests_post)
		self.requests_get = mock.MagicMock(side_effect=self._requests_get)
		self.docs = {}
		# QuickBooks entities to return, by key; a list is consumed in order.
		self.responses = {}
		self._stack = ExitStack()

	def _next(self, key, default):
		value = self.responses.get(key)
		if isinstance(value, list):
			return value.pop(0) if value else default
		return value if value is not None else default

	def _make_request(self, endpoint, method="GET", data=None, params=None, **kwargs):
		if endpoint == "invoice":
			return {"Invoice": self._next("invoice", {"Id": "100"})}
		if endpoint == "bill":
			return {"Bill": {"Id": "101"}}
		if endpoint.startswith("customer/"):
			return {"Customer": {"SyncToken": "3"}}
		return {}

	@staticmethod
	def _response(body):
		resp = mock.MagicMock()
		resp.status_code = 200
		resp.json.return_value = body
		resp.text = frappe.as_json(body)
		return resp

	def _requests_post(self, url, headers=None, json=None, **kwargs):
		if "/creditmemo" in url:
			return self._response({"CreditMemo": self._next("creditmemo", {"Id": "200"})})
		if "/vendorcredit" in url:
			return self._response({"VendorCredit": {"Id": "300"}})
		if "/invoice" in url:
			return self._response({"Invoice": self._next("invoice_update", {"Id": "900"})})
		if "/bill" in url:
			return self._response({"Bill": {"Id": "900"}})
		return self._response({})

	def _requests_get(self, url, headers=None, **kwargs):
		if "/invoice/" in url:
			return self._response({"Invoice": self._next("invoice_get", {"SyncToken": "4"})})
		if "/bill/" in url:
			return self._response({"Bill": {"SyncToken": "2"}})
		return self._response({})

	def _get_doc(self, doctype, name=None, *args, **kwargs):
		return self.docs[(doctype, name)]

	def __enter__(self):
		p = self._stack.enter_context
		p(mock.patch(f"{API}.get_settings", return_value=self.settings))
		p(mock.patch(f"{API}.QuickBooksAPI", return_value=self.api_instance))
		p(mock.patch(f"{API}.get_valid_access_token", return_value="token"))
		self.log_action = p(mock.patch(f"{API}.log_action"))
		p(mock.patch(f"{API}._mark_sales_invoice_sync_error"))
		p(mock.patch(f"{API}._mark_purchase_invoice_sync_error"))
		p(mock.patch(f"{API}._mark_purchase_invoice_sync_success"))
		p(mock.patch(f"{API}.requests.post", self.requests_post))
		p(mock.patch(f"{API}.requests.get", self.requests_get))
		p(mock.patch.object(frappe, "get_doc", side_effect=self._get_doc))
		p(mock.patch.object(frappe, "db", mock.MagicMock()))
		frappe.db.get_value.side_effect = fake_get_value
		return self

	def __exit__(self, *exc):
		return self._stack.__exit__(*exc)

	# -- helpers to read what was sent -------------------------------------

	def posted_all(self, endpoint):
		"""Payloads passed to QuickBooksAPI.make_request(endpoint, method='POST')."""
		return [
			kwargs["data"]
			for args, kwargs in self.api_instance.make_request.call_args_list
			if args and args[0] == endpoint and kwargs.get("method") == "POST"
		]

	def posted(self, endpoint):
		"""First payload POSTed to endpoint through QuickBooksAPI.make_request."""
		sent = self.posted_all(endpoint)
		if not sent:
			raise AssertionError(f"nothing POSTed to {endpoint}")
		return sent[0]

	def posted_raw_all(self, url_part):
		"""JSON bodies passed to requests.post for URLs containing url_part."""
		return [
			kwargs["json"]
			for args, kwargs in self.requests_post.call_args_list
			if url_part in (args[0] if args else kwargs["url"])
		]

	def posted_raw(self, url_part):
		"""First JSON body passed to requests.post for a URL containing url_part."""
		sent = self.posted_raw_all(url_part)
		if not sent:
			raise AssertionError(f"nothing POSTed to a URL containing {url_part}")
		return sent[0]

	def set_values(self, doctype, name):
		"""{field: value} written with frappe.db.set_value(doctype, name, ...)."""
		return {
			args[2]: args[3]
			for args, kwargs in frappe.db.set_value.call_args_list
			if args[:2] == (doctype, name)
		}


def run_builder(builder, doc, settings=None):
	"""Run one of the six payload builders on doc and return the payload sent."""
	from quickbooks_connector import api

	with Capture(settings) as cap:
		if builder == "invoice":
			api.create_qb_invoice_from_sales_invoice(doc)
			return cap.posted("invoice")
		if builder == "bill":
			api.create_qb_bill_from_purchase_invoice(doc)
			return cap.posted("bill")

		doctype = "Sales Invoice" if builder in ("credit_memo", "amend_invoice") else "Purchase Invoice"
		cap.docs[(doctype, doc.name)] = doc
		fn, url_part = {
			"credit_memo": (api.manual_create_credit_memo, "/creditmemo"),
			"amend_invoice": (api.manual_amend_invoice, "/invoice?"),
			"vendor_credit": (api.manual_create_vendor_credit, "/vendorcredit"),
			"amend_bill": (api.manual_amend_bill, "/bill?"),
		}[builder]
		result = fn(doc.name)
		if not result.get("success"):
			raise AssertionError(f"{builder} failed: {result.get('error')}")
		return cap.posted_raw(url_part)
