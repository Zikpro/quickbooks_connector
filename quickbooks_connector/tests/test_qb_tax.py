"""Unit tests for qb_tax.py and the US payloads built with it.

QuickBooks is never called: see tests/utils.py.
"""

import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import frappe

from quickbooks_connector import qb_tax
from quickbooks_connector.tests.utils import (
	FREIGHT_ACCOUNT,
	Capture,
	fake_get_value,
	make_pi,
	make_return,
	make_settings,
	make_si,
	run_builder,
	tax_row,
)


def us_settings(**overrides):
	values = dict(qb_region="US", us_sales_tax_code="5", us_uses_ast=0)
	values.update(overrides)
	return make_settings(**values)


class DbMocked(unittest.TestCase):
	"""Mock frappe.db so Account.account_type lookups never hit the database."""

	def setUp(self):
		patcher = mock.patch.object(frappe, "db", mock.MagicMock())
		patcher.start()
		self.addCleanup(patcher.stop)
		frappe.db.get_value.side_effect = fake_get_value


class TestRegion(unittest.TestCase):
	def test_default_is_uk(self):
		self.assertEqual(qb_tax.get_region(make_settings()), "UK")
		self.assertEqual(qb_tax.get_region(make_settings(qb_region="")), "UK")
		self.assertEqual(qb_tax.get_region(make_settings(qb_region=None)), "UK")

	def test_us(self):
		self.assertEqual(qb_tax.get_region(make_settings(qb_region="US")), "US")
		self.assertEqual(qb_tax.get_region(make_settings(qb_region=" us ")), "US")


class TestUKMapping(unittest.TestCase):
	def test_rates(self):
		s = make_settings()
		self.assertEqual(qb_tax.uk_tax_code(20, s), "3")
		self.assertEqual(qb_tax.uk_tax_code(5, s), "8")
		self.assertEqual(qb_tax.uk_tax_code(1, s), "10")
		self.assertEqual(qb_tax.uk_tax_code(0, s), "12")

	def test_boundaries(self):
		s = make_settings()
		self.assertEqual(qb_tax.uk_tax_code(25, s), "3")
		self.assertEqual(qb_tax.uk_tax_code(19.99, s), "8")
		self.assertEqual(qb_tax.uk_tax_code(4.99, s), "10")
		self.assertEqual(qb_tax.uk_tax_code(None, s), "12")

	def test_default_tax_code_setting(self):
		self.assertEqual(qb_tax.uk_tax_code(0, make_settings(default_tax_code="99")), "99")
		self.assertEqual(qb_tax.uk_tax_code(0, make_settings(default_tax_code="")), "12")
		self.assertEqual(qb_tax.uk_tax_code(0, SimpleNamespace()), "12")

	def test_invoice_tax_rate_is_first_positive_row(self):
		doc = make_si(0, extra_taxes=[tax_row(0, 3), tax_row(5, 5), tax_row(20, 20)])
		self.assertEqual(qb_tax.invoice_tax_rate(doc), 5)
		self.assertEqual(qb_tax.invoice_tax_rate(make_si(0)), 0)

	def test_uk_apply_sales_tax(self):
		s = make_settings()
		self.assertEqual(qb_tax.apply_sales_tax({}, make_si(20), s), {"GlobalTaxCalculation": "TaxExcluded"})
		self.assertEqual(qb_tax.apply_sales_tax({}, make_si(0), s), {"GlobalTaxCalculation": "NotApplicable"})

	def test_uk_expense_line_tax_code(self):
		s = make_settings()
		self.assertEqual(qb_tax.expense_line_tax_code(make_pi(20), s), "3")
		self.assertEqual(qb_tax.expense_line_tax_code(make_pi(20), s, uk_fixed="12"), "12")


class TestUSHelpers(DbMocked):
	def test_sales_line_tax_code(self):
		s = us_settings()
		self.assertEqual(qb_tax.sales_line_tax_code(make_si(8.25), s), "TAX")
		self.assertEqual(qb_tax.sales_line_tax_code(make_si(0), s), "NON")

	def test_taxed_ignores_rate_uses_account_type(self):
		s = us_settings()
		actual_tax = tax_row(0, 7.0, charge_type="Actual")  # rate 0, Tax account
		self.assertEqual(qb_tax.sales_line_tax_code(make_si(0, extra_taxes=[actual_tax]), s), "TAX")
		freight = tax_row(10, 10.0, account=FREIGHT_ACCOUNT)  # rate > 0, not a Tax account
		self.assertEqual(qb_tax.sales_line_tax_code(make_si(0, extra_taxes=[freight]), s), "NON")
		zero = tax_row(8, 0)  # Tax account but no amount
		self.assertEqual(qb_tax.sales_line_tax_code(make_si(0, extra_taxes=[zero]), s), "NON")

	def test_total_tax_excludes_freight_and_uses_after_discount(self):
		doc = make_si(
			0,
			extra_taxes=[
				tax_row(8, 8.0, after_discount=7.5),
				tax_row(2, 2.0),
				tax_row(0, 15.0, account=FREIGHT_ACCOUNT),
			],
		)
		self.assertEqual(qb_tax.us_total_tax(doc), 9.5)

	def test_total_tax_positive_for_returns(self):
		self.assertEqual(qb_tax.us_total_tax(make_return(make_si(20))), 20.0)

	def test_apply_sales_tax_ast_off(self):
		payload = qb_tax.apply_sales_tax({"GlobalTaxCalculation": "x"}, make_si(20), us_settings())
		self.assertEqual(payload, {"TxnTaxDetail": {"TxnTaxCodeRef": {"value": "5"}}})

	def test_apply_sales_tax_ast_on_sends_code_and_total(self):
		payload = qb_tax.apply_sales_tax({}, make_si(20), us_settings(us_uses_ast=1))
		self.assertEqual(payload, {"TxnTaxDetail": {"TxnTaxCodeRef": {"value": "5"}, "TotalTax": 20.0}})

	def test_apply_sales_tax_ast_on_code_fallbacks(self):
		no_code = us_settings(us_uses_ast=1, us_sales_tax_code="")
		# no configured code and none known yet: TotalTax alone, fixed up after create
		self.assertEqual(qb_tax.apply_sales_tax({}, make_si(20), no_code), {"TxnTaxDetail": {"TotalTax": 20.0}})
		# code QuickBooks already put on the transaction
		self.assertEqual(
			qb_tax.apply_sales_tax({}, make_si(20), no_code, qb_tax_code="8"),
			{"TxnTaxDetail": {"TxnTaxCodeRef": {"value": "8"}, "TotalTax": 20.0}},
		)
		# the configured code wins
		self.assertEqual(
			qb_tax.apply_sales_tax({}, make_si(20), us_settings(us_uses_ast=1), qb_tax_code="8"),
			{"TxnTaxDetail": {"TxnTaxCodeRef": {"value": "5"}, "TotalTax": 20.0}},
		)

	def test_ast_override_update(self):
		no_code = us_settings(us_uses_ast=1, us_sales_tax_code="")
		entity = {"Id": "100", "SyncToken": "0", "TxnTaxDetail": {"TxnTaxCodeRef": {"value": "8"}, "TotalTax": 18.5}}
		sent = {"TxnTaxDetail": {"TotalTax": 20.0}}
		self.assertEqual(
			qb_tax.ast_override_update(sent, entity, make_si(20), no_code),
			{
				"Id": "100",
				"SyncToken": "0",
				"sparse": True,
				"TxnTaxDetail": {"TxnTaxCodeRef": {"value": "8"}, "TotalTax": 20.0},
			},
		)

	def test_ast_override_update_not_needed(self):
		no_code = us_settings(us_uses_ast=1, us_sales_tax_code="")
		entity = {"Id": "100", "SyncToken": "0", "TxnTaxDetail": {"TxnTaxCodeRef": {"value": "8"}}}
		sent_with_code = {"TxnTaxDetail": {"TxnTaxCodeRef": {"value": "5"}, "TotalTax": 20.0}}
		sent = {"TxnTaxDetail": {"TotalTax": 20.0}}
		self.assertIsNone(qb_tax.ast_override_update(sent_with_code, entity, make_si(20), no_code))
		self.assertIsNone(qb_tax.ast_override_update(sent, entity, make_si(0), no_code))  # untaxed
		self.assertIsNone(qb_tax.ast_override_update(sent, entity, make_si(20), us_settings()))  # AST off
		self.assertIsNone(qb_tax.ast_override_update(sent, entity, make_si(20), make_settings()))  # UK
		self.assertIsNone(qb_tax.ast_override_update(sent, {"Id": "100", "SyncToken": "0"}, make_si(20), no_code))

	def test_apply_sales_tax_untaxed(self):
		self.assertEqual(qb_tax.apply_sales_tax({}, make_si(0), us_settings()), {})
		self.assertEqual(qb_tax.apply_sales_tax({}, make_si(0), us_settings(us_uses_ast=1)), {})

	def test_apply_sales_tax_requires_code_when_ast_off(self):
		with self.assertRaises(frappe.ValidationError):
			qb_tax.apply_sales_tax({}, make_si(20), us_settings(us_sales_tax_code=""))

	def test_expense_line_tax_code_is_none(self):
		self.assertIsNone(qb_tax.expense_line_tax_code(make_pi(20), us_settings()))
		self.assertIsNone(qb_tax.expense_line_tax_code(make_pi(20), us_settings(), uk_fixed="12"))

	def test_apply_purchase_tax(self):
		payload = {"Line": [], "GlobalTaxCalculation": "x"}
		qb_tax.apply_purchase_tax(payload, make_pi(20), us_settings(), "77", uk_fixed_global="NotApplicable")
		self.assertNotIn("GlobalTaxCalculation", payload)
		self.assertEqual(
			payload["Line"],
			[
				{
					"DetailType": "AccountBasedExpenseLineDetail",
					"Amount": 20.0,
					"Description": "Sales tax",
					"AccountBasedExpenseLineDetail": {"AccountRef": {"value": "77"}},
				}
			],
		)
		untaxed = {"Line": []}
		qb_tax.apply_purchase_tax(untaxed, make_pi(0), us_settings(), "77")
		self.assertEqual(untaxed, {"Line": []})


def us_sales_line(tax_code, amount=100.0, qty=2.0):
	return {
		"DetailType": "SalesItemLineDetail",
		"Amount": amount,
		"Description": "ITEM-1 description",
		"SalesItemLineDetail": {
			"ItemRef": {"value": "11"},
			"Qty": qty,
			"UnitPrice": 50.0,
			"TaxCodeRef": {"value": tax_code},
		},
	}


US_TAX_LINE = {
	"DetailType": "AccountBasedExpenseLineDetail",
	"Amount": 20.0,
	"Description": "Sales tax",
	"AccountBasedExpenseLineDetail": {"AccountRef": {"value": "77"}},
}


class TestUSPayloads(unittest.TestCase):
	"""Full payloads from the six builders with qb_region = US."""

	def assertNoGlobalTax(self, payload):
		self.assertNotIn("GlobalTaxCalculation", json.dumps(payload))

	def test_invoice_taxed_ast_off(self):
		payload = run_builder("invoice", make_si(20), us_settings())
		self.assertNoGlobalTax(payload)
		self.assertEqual(payload["Line"], [us_sales_line("TAX")])
		self.assertEqual(payload["TxnTaxDetail"], {"TxnTaxCodeRef": {"value": "5"}})

	def test_invoice_taxed_ast_on(self):
		payload = run_builder("invoice", make_si(20), us_settings(us_uses_ast=1))
		self.assertNoGlobalTax(payload)
		self.assertEqual(payload["Line"], [us_sales_line("TAX")])
		self.assertEqual(payload["TxnTaxDetail"], {"TxnTaxCodeRef": {"value": "5"}, "TotalTax": 20.0})

	def test_invoice_untaxed(self):
		for ast in (0, 1):
			payload = run_builder("invoice", make_si(0), us_settings(us_uses_ast=ast))
			self.assertNoGlobalTax(payload)
			self.assertEqual(payload["Line"], [us_sales_line("NON")])
			self.assertNotIn("TxnTaxDetail", payload)

	def test_invoice_freight_only_is_untaxed(self):
		doc = make_si(0, extra_taxes=[tax_row(0, 15.0, account=FREIGHT_ACCOUNT)])
		payload = run_builder("invoice", doc, us_settings(us_uses_ast=1))
		self.assertEqual(payload["Line"], [us_sales_line("NON")])
		self.assertNotIn("TxnTaxDetail", payload)

	def test_credit_memo_ast_on(self):
		doc = make_return(make_si(20, name="SINV-RET-0001"))
		payload = run_builder("credit_memo", doc, us_settings(us_uses_ast=1))
		self.assertNoGlobalTax(payload)
		self.assertEqual(payload["Line"], [us_sales_line("TAX")])
		self.assertEqual(payload["TxnTaxDetail"], {"TxnTaxCodeRef": {"value": "5"}, "TotalTax": 20.0})

	def test_amend_invoice_ast_off(self):
		doc = make_si(20, name="SINV-0001-1", amended_from="SINV-0001")
		payload = run_builder("amend_invoice", doc, us_settings())
		self.assertNoGlobalTax(payload)
		self.assertEqual(payload["Id"], "900")
		self.assertEqual(payload["TxnTaxDetail"], {"TxnTaxCodeRef": {"value": "5"}})

	def test_bill_taxed(self):
		payload = run_builder("bill", make_pi(20), us_settings())
		self.assertNoGlobalTax(payload)
		self.assertNotIn("TaxCodeRef", json.dumps(payload))
		self.assertEqual(
			payload["Line"],
			[
				{
					"DetailType": "AccountBasedExpenseLineDetail",
					"Amount": 100.0,
					"Description": "ITEM-1: ITEM-1 description",
					"AccountBasedExpenseLineDetail": {"AccountRef": {"value": "77"}},
				},
				US_TAX_LINE,
			],
		)

	def test_bill_untaxed(self):
		payload = run_builder("bill", make_pi(0), us_settings())
		self.assertNoGlobalTax(payload)
		self.assertEqual(len(payload["Line"]), 1)
		self.assertNotIn("TaxCodeRef", json.dumps(payload))

	def test_vendor_credit_taxed(self):
		doc = make_return(make_pi(20, name="PINV-RET-0001"))
		payload = run_builder("vendor_credit", doc, us_settings())
		self.assertNoGlobalTax(payload)
		self.assertNotIn("TaxCodeRef", json.dumps(payload))
		self.assertEqual(payload["Line"][-1], US_TAX_LINE)
		self.assertEqual([line["Amount"] for line in payload["Line"]], [100.0, 20.0])

	def test_amend_bill_taxed(self):
		doc = make_pi(20, name="PINV-0001-1", amended_from="PINV-0001")
		payload = run_builder("amend_bill", doc, us_settings())
		self.assertNoGlobalTax(payload)
		self.assertNotIn("TaxCodeRef", json.dumps(payload))
		self.assertEqual(payload["Line"][-1], US_TAX_LINE)


def qb_entity(entity_id, sync_token, tax_code=None, total_tax=None):
	detail = {}
	if tax_code:
		detail["TxnTaxCodeRef"] = {"value": tax_code}
	if total_tax is not None:
		detail["TotalTax"] = total_tax
	return {"Id": entity_id, "SyncToken": sync_token, "TxnTaxDetail": detail}


class TestUSAstOverride(unittest.TestCase):
	"""AST on, no us_sales_tax_code: finish the override with QuickBooks' TaxCode."""

	settings = staticmethod(lambda: us_settings(us_uses_ast=1, us_sales_tax_code=""))

	def test_invoice_sends_followup_with_returned_code(self):
		from quickbooks_connector import api

		doc = make_si(20)
		with Capture(self.settings()) as cap:
			cap.responses["invoice"] = [qb_entity("100", "0", "8", 18.5), qb_entity("100", "1", "8", 20.0)]
			result = api.create_qb_invoice_from_sales_invoice(doc)
			sent = cap.posted_all("invoice")
		self.assertEqual(result, {"qb_invoice_id": "100"})
		self.assertEqual(sent[0]["TxnTaxDetail"], {"TotalTax": 20.0})
		self.assertEqual(
			sent[1],
			{
				"Id": "100",
				"SyncToken": "0",
				"sparse": True,
				"TxnTaxDetail": {"TxnTaxCodeRef": {"value": "8"}, "TotalTax": 20.0},
			},
		)
		self.assertEqual(doc.quickbooks_id, "100")

	def test_no_followup_when_code_configured(self):
		from quickbooks_connector import api

		with Capture(us_settings(us_uses_ast=1)) as cap:
			cap.responses["invoice"] = [qb_entity("100", "0", "5", 20.0)]
			api.create_qb_invoice_from_sales_invoice(make_si(20))
			self.assertEqual(len(cap.posted_all("invoice")), 1)

	def test_followup_failure_keeps_sync(self):
		from quickbooks_connector import api

		doc = make_si(20)
		with Capture(self.settings()) as cap, mock.patch.object(frappe, "log_error") as log_error:
			cap.api_instance.make_request.side_effect = [
				{"Invoice": qb_entity("100", "0", "8", 18.5)},
				Exception("QuickBooks down"),
			]
			result = api.create_qb_invoice_from_sales_invoice(doc)
		self.assertEqual(result["qb_invoice_id"], "100")
		self.assertEqual(doc.quickbooks_id, "100")
		log_error.assert_called_once()
		# QuickBooks kept its own 18.50, so the sync is flagged for review
		self.assertEqual(doc.quickbooks_sync_status, "Synced with Warning")

	def test_credit_memo_sends_followup_with_returned_code(self):
		doc = make_return(make_si(20, name="SINV-RET-0001"))
		with Capture(self.settings()) as cap:
			cap.docs[("Sales Invoice", doc.name)] = doc
			cap.responses["creditmemo"] = [qb_entity("200", "0", "8", 18.5), qb_entity("200", "1", "8", 20.0)]
			from quickbooks_connector import api

			self.assertTrue(api.manual_create_credit_memo(doc.name)["success"])
			sent = cap.posted_raw_all("/creditmemo")
		self.assertEqual(len(sent), 2)
		self.assertEqual(sent[1]["TxnTaxDetail"], {"TxnTaxCodeRef": {"value": "8"}, "TotalTax": 20.0})
		self.assertEqual(sent[1]["SyncToken"], "0")

	def test_amend_reuses_code_from_existing_invoice(self):
		doc = make_si(20, name="SINV-0001-1", amended_from="SINV-0001")
		with Capture(self.settings()) as cap:
			cap.docs[("Sales Invoice", doc.name)] = doc
			cap.responses["invoice_get"] = qb_entity("900", "4", "8", 18.5)
			cap.responses["invoice_update"] = qb_entity("900", "5", "8", 20.0)
			from quickbooks_connector import api

			self.assertTrue(api.manual_amend_invoice(doc.name)["success"])
			sent = cap.posted_raw_all("/invoice?")
		self.assertEqual(len(sent), 1)
		self.assertEqual(sent[0]["TxnTaxDetail"], {"TxnTaxCodeRef": {"value": "8"}, "TotalTax": 20.0})


class TestUSTaxMismatch(unittest.TestCase):
	"""QuickBooks' TotalTax vs ERPNext's sales tax after create / amend."""

	def _create_invoice(self, doc, settings, qb_total):
		from quickbooks_connector import api

		with Capture(settings) as cap:
			cap.responses["invoice"] = qb_entity("100", "0", "5", qb_total)
			result = api.create_qb_invoice_from_sales_invoice(doc)
			log_calls = [c.args[0] for c in cap.log_action.call_args_list]
		return result, log_calls

	def test_unit_tax_mismatch(self):
		with mock.patch.object(frappe, "db", mock.MagicMock()):
			frappe.db.get_value.side_effect = fake_get_value
			s = us_settings()
			self.assertEqual(qb_tax.tax_mismatch({"TxnTaxDetail": {"TotalTax": 19.0}}, make_si(20), s), (19.0, 20.0))
			self.assertIsNone(qb_tax.tax_mismatch({"TxnTaxDetail": {"TotalTax": 20.01}}, make_si(20), s))
			self.assertIsNone(qb_tax.tax_mismatch({"TxnTaxDetail": {"TotalTax": 19.99}}, make_si(20), s))
			self.assertEqual(qb_tax.tax_mismatch({}, make_si(20), s), (0.0, 20.0))
			self.assertIsNone(qb_tax.tax_mismatch({}, make_si(0), s))
			self.assertEqual(
				qb_tax.tax_mismatch({"TxnTaxDetail": {"TotalTax": -18.0}}, make_return(make_si(20)), s), (18.0, 20.0)
			)
			self.assertIsNone(qb_tax.tax_mismatch({"TxnTaxDetail": {"TotalTax": 1.0}}, make_si(20), make_settings()))

	def test_invoice_mismatch_sets_warning(self):
		doc = make_si(20)
		result, log_calls = self._create_invoice(doc, us_settings(), 19.0)
		self.assertEqual(doc.quickbooks_id, "100")
		self.assertEqual(doc.quickbooks_sync_status, "Synced with Warning")
		self.assertIn("19.00", doc.quickbooks_sync_error)
		self.assertIn("20.00", doc.quickbooks_sync_error)
		self.assertEqual(result["warning"], doc.quickbooks_sync_error)
		self.assertIn("QuickBooks Sales Tax Mismatch", log_calls)

	def test_invoice_within_tolerance_is_synced(self):
		doc = make_si(20)
		result, log_calls = self._create_invoice(doc, us_settings(), 20.01)
		self.assertEqual(doc.quickbooks_sync_status, "Synced")
		self.assertEqual(doc.quickbooks_sync_error, "")
		self.assertEqual(result, {"qb_invoice_id": "100"})
		self.assertNotIn("QuickBooks Sales Tax Mismatch", log_calls)

	def test_untaxed_invoice_no_warning(self):
		doc = make_si(0)
		self._create_invoice(doc, us_settings(), 0)
		self.assertEqual(doc.quickbooks_sync_status, "Synced")

	def test_uk_never_checked(self):
		doc = make_si(20)
		result, log_calls = self._create_invoice(doc, make_settings(), 3.0)
		self.assertEqual(doc.quickbooks_sync_status, "Synced")
		self.assertEqual(result, {"qb_invoice_id": "100"})

	def test_credit_memo_mismatch_sets_warning(self):
		from quickbooks_connector import api

		doc = make_return(make_si(20, name="SINV-RET-0001"))
		with Capture(us_settings()) as cap:
			cap.docs[("Sales Invoice", doc.name)] = doc
			cap.responses["creditmemo"] = qb_entity("200", "0", "5", 17.0)
			result = api.manual_create_credit_memo(doc.name)
			written = cap.set_values("Sales Invoice", doc.name)
		self.assertTrue(result["success"])
		self.assertIn("Warning", result["message"])
		self.assertEqual(written["quickbooks_id"], "200")
		self.assertEqual(written["quickbooks_sync_status"], "Synced with Warning")
		self.assertIn("17.00", written["quickbooks_sync_error"])
		self.assertIn("20.00", written["quickbooks_sync_error"])

	def test_credit_memo_match_is_synced(self):
		from quickbooks_connector import api

		doc = make_return(make_si(20, name="SINV-RET-0001"))
		with Capture(us_settings()) as cap:
			cap.docs[("Sales Invoice", doc.name)] = doc
			cap.responses["creditmemo"] = qb_entity("200", "0", "5", 20.0)
			api.manual_create_credit_memo(doc.name)
			written = cap.set_values("Sales Invoice", doc.name)
		self.assertEqual(written["quickbooks_sync_status"], "Synced")
		self.assertEqual(written["quickbooks_sync_error"], "")

	def test_amend_mismatch_sets_warning(self):
		from quickbooks_connector import api

		doc = make_si(20, name="SINV-0001-1", amended_from="SINV-0001")
		with Capture(us_settings()) as cap:
			cap.docs[("Sales Invoice", doc.name)] = doc
			cap.responses["invoice_update"] = qb_entity("900", "5", "5", 25.0)
			self.assertTrue(api.manual_amend_invoice(doc.name)["success"])
			written = cap.set_values("Sales Invoice", doc.name)
		self.assertEqual(written["quickbooks_sync_status"], "Synced with Warning")


class TestPreferencesAndTaxCodes(unittest.TestCase):
	def test_fetch_us_uses_ast(self):
		from quickbooks_connector.api import fetch_us_uses_ast

		api = mock.MagicMock()
		api.make_request.return_value = {"Preferences": {"TaxPrefs": {"PartnerTaxEnabled": True}}}
		self.assertEqual(fetch_us_uses_ast(api), 1)
		self.assertEqual(api.make_request.call_args.kwargs["params"], {"minorversion": 65})
		api.make_request.return_value = {"Preferences": {"TaxPrefs": {"PartnerTaxEnabled": False}}}
		self.assertEqual(fetch_us_uses_ast(api), 0)

	def test_fetch_us_uses_ast_failure_is_logged_not_raised(self):
		from quickbooks_connector.api import fetch_us_uses_ast

		api = mock.MagicMock()
		api.make_request.side_effect = Exception("boom")
		with mock.patch.object(frappe, "log_error") as log_error:
			self.assertIsNone(fetch_us_uses_ast(api))
		log_error.assert_called_once()

	def test_get_us_tax_codes(self):
		from quickbooks_connector import api

		instance = mock.MagicMock()
		instance.make_request.return_value = {
			"QueryResponse": {
				"TaxCode": [
					{"Id": "5", "Name": "California", "Active": True, "Taxable": True, "SyncToken": "0"},
					{"Id": "NON", "Name": "NON", "Active": True, "Taxable": False},
				]
			}
		}
		with mock.patch.object(api, "QuickBooksAPI", return_value=instance), mock.patch.object(
			frappe, "only_for"
		) as only_for:
			codes = api.get_us_tax_codes()
		only_for.assert_called_once_with("System Manager")
		self.assertIn("TaxCode", instance.make_request.call_args.args[0])
		self.assertEqual(
			codes,
			[
				{"Id": "5", "Name": "California", "Active": True, "Taxable": True},
				{"Id": "NON", "Name": "NON", "Active": True, "Taxable": False},
			],
		)


class TestSettingDefaults(unittest.TestCase):
	"""New fields must default to what getattr() fell back to before they existed."""

	def test_defaults_match_old_fallbacks(self):
		path = os.path.join(
			os.path.dirname(qb_tax.__file__),
			"quickbooks_connector",
			"doctype",
			"quickbooks_setting",
			"quickbooks_setting.json",
		)
		with open(path) as f:
			fields = {df["fieldname"]: df for df in json.load(f)["fields"]}
		expected = {
			"qb_region": "UK",
			"us_uses_ast": "0",
			"sync_accounts": "1",
			"sync_payments": "1",
			"sync_bill_payments": "1",
			"sync_customers": "0",
			"sync_items": "0",
			"sync_suppliers": "0",
			"enable_auto_sync": "0",
			"auto_sync_sales_invoice": "0",
			"auto_sync_purchase_invoice": "0",
		}
		for fieldname, default in expected.items():
			self.assertEqual(fields[fieldname].get("default"), default, fieldname)
		self.assertEqual(fields["us_uses_ast"].get("read_only"), 1)
		self.assertEqual(fields["qb_region"]["options"], "UK\nUS")


class TestRegionCountryCheck(unittest.TestCase):
	def test_region_country_warning(self):
		us, uk = make_settings(qb_region="US"), make_settings()
		self.assertIsNone(qb_tax.region_country_warning(us, "US"))
		self.assertIsNone(qb_tax.region_country_warning(us, "United States"))
		self.assertIsNone(qb_tax.region_country_warning(uk, "GB"))
		self.assertIsNone(qb_tax.region_country_warning(uk, "UK"))
		self.assertIsNone(qb_tax.region_country_warning(uk, None))
		self.assertIn("GB", qb_tax.region_country_warning(us, "GB"))
		self.assertIn("US", qb_tax.region_country_warning(uk, "US"))
		self.assertIsNotNone(qb_tax.region_country_warning(uk, "CA"))

	def _test_connection(self, settings, country):
		from quickbooks_connector import api

		settings.access_token = "token"
		settings.save = mock.MagicMock()
		instance = mock.MagicMock()
		instance.get_company_info.return_value = {"CompanyInfo": {"CompanyName": "Acme", "Country": country}}
		instance.make_request.return_value = {"Preferences": {"TaxPrefs": {"PartnerTaxEnabled": False}}}
		with mock.patch.object(api, "get_settings", return_value=settings), mock.patch.object(
			api, "QuickBooksAPI", return_value=instance
		), mock.patch.object(api, "log_action") as log_action, mock.patch.object(frappe, "db", mock.MagicMock()):
			result = api.test_connection()
		return result, log_action

	def test_connection_warns_on_mismatch_without_changing_region(self):
		settings = make_settings(qb_region="US")
		result, log_action = self._test_connection(settings, "GB")
		self.assertTrue(result["success"])
		self.assertIn("GB", result["warning"])
		self.assertIn("Warning:", result["message"])
		self.assertEqual(settings.qb_region, "US")
		self.assertEqual(log_action.call_args.args[1]["warning"], result["warning"])

	def test_connection_no_warning_when_matching(self):
		result, _ = self._test_connection(make_settings(), "GB")
		self.assertNotIn("warning", result)
		self.assertEqual(result["message"], "Successfully connected to QuickBooks")
		result, _ = self._test_connection(make_settings(qb_region="US"), "US")
		self.assertNotIn("warning", result)
		self.assertEqual(result["us_uses_ast"], 0)


class TestOAuthCallbackSave(unittest.TestCase):
	def test_reloads_settings_before_final_save(self):
		"""test_connection() saves the settings itself; the callback must reload
		before saving, or Frappe raises TimestampMismatchError."""
		from quickbooks_connector import api

		calls = []
		settings = mock.MagicMock()
		settings.reload.side_effect = lambda: calls.append("reload")
		settings.save.side_effect = lambda **k: calls.append("save")
		cache = mock.MagicMock()
		cache.get_value.return_value = {"redirect_uri": "x"}
		with mock.patch.object(frappe, "cache", return_value=cache), mock.patch.object(
			api, "get_settings", return_value=settings
		), mock.patch.object(api, "exchange_code_for_tokens", return_value={"access_token": "a"}), mock.patch.object(
			api, "save_tokens"
		), mock.patch.object(
			api, "test_connection", return_value={"success": True, "company_name": "Acme"}
		), mock.patch.object(api, "log_action"), mock.patch.object(frappe, "db", mock.MagicMock()):
			page = api.oauth_callback(code="c", state="s", realmId="123")
		self.assertIn("Successfully Connected", page)
		self.assertEqual(calls, ["reload", "save"])
		self.assertEqual(settings.is_connected, 1)
		self.assertEqual(settings.company_name, "Acme")


class TestExpenseAccountLookup(unittest.TestCase):
	def test_uses_is_set_filter(self):
		"""["quickbooks_id", "!=", None] made get_value match no account at all."""
		from quickbooks_connector import api

		with mock.patch.object(frappe, "db", mock.MagicMock()):
			frappe.db.get_value.return_value = "80"
			self.assertEqual(api.get_qb_expense_account_id(make_settings(company="US Test Co")), "80")
			filters = frappe.db.get_value.call_args.args[1]
		self.assertIn(["quickbooks_id", "is", "set"], filters)
		self.assertIn(["company", "=", "US Test Co"], filters)
		self.assertNotIn(["quickbooks_id", "!=", None], filters)

	def test_bill_success_sets_synced_status(self):
		"""_mark_purchase_invoice_sync_success used hasattr(meta, ...), always False."""
		from quickbooks_connector import api

		meta = mock.Mock()
		meta.has_field.side_effect = lambda f: f == "quickbooks_sync_status"
		with mock.patch.object(frappe, "db", mock.MagicMock()), mock.patch.object(frappe, "get_meta", return_value=meta):
			api._mark_purchase_invoice_sync_success(SimpleNamespace(name="PINV-0001"), "148", "PINV-0001")
			args = frappe.db.set_value.call_args.args
		self.assertEqual(args[:2], ("Purchase Invoice", "PINV-0001"))
		self.assertEqual(args[2]["quickbooks_sync_status"], "Synced")
		self.assertEqual(args[2]["quickbooks_id"], "148")


class TestCustomerPaymentEntry(unittest.TestCase):
	def test_sets_company_and_ignores_cancelled_invoice(self):
		from quickbooks_connector import api

		lookups = []

		def get_value(doctype, filters=None, fieldname=None, *args, **kwargs):
			lookups.append((doctype, filters))
			return {"Customer": "Cust A", "Company": "Debtors - UTC", "Sales Invoice": "SINV-0001-1"}.get(doctype)

		pe = mock.MagicMock()
		qb_payment = {
			"Id": "150",
			"TotalAmt": 162.38,
			"TxnDate": "2026-11-26",
			"CustomerRef": {"value": "29"},
			"Line": [{"Amount": 162.38, "LinkedTxn": [{"TxnId": "146", "TxnType": "Invoice"}]}],
		}
		settings = make_settings(company="US Test Co", default_account="Cash - UTC")
		with mock.patch.object(api, "get_settings", return_value=settings), mock.patch.object(
			frappe, "db", mock.MagicMock()
		), mock.patch.object(frappe, "get_doc", return_value=pe) as get_doc:
			frappe.db.exists.return_value = False
			frappe.db.get_value.side_effect = get_value
			self.assertEqual(api.create_or_update_payment_entry(qb_payment), "created")
		self.assertEqual(get_doc.call_args.args[0]["company"], "US Test Co")
		self.assertIn(("Sales Invoice", {"quickbooks_id": "146", "docstatus": 1}), lookups)
		pe.append.assert_called_once_with(
			"references",
			{"reference_doctype": "Sales Invoice", "reference_name": "SINV-0001-1", "allocated_amount": 162.38},
		)
		pe.submit.assert_called_once()
