"""Snapshot of the UK payloads produced before US sales tax support was added.

These expected payloads were captured from the original builders in api.py.
They must keep passing unchanged: UK behaviour is not allowed to change.
"""

import unittest

from quickbooks_connector.tests.utils import item_row, make_pi, make_return, make_si, run_builder


def sales_line(item_id, amount, qty, price, tax_code, description):
	return {
		"DetailType": "SalesItemLineDetail",
		"Amount": amount,
		"Description": description,
		"SalesItemLineDetail": {
			"ItemRef": {"value": item_id},
			"Qty": qty,
			"UnitPrice": price,
			"TaxCodeRef": {"value": tax_code},
		},
	}


def expense_line(amount, description, tax_code):
	return {
		"DetailType": "AccountBasedExpenseLineDetail",
		"Amount": amount,
		"Description": description,
		"AccountBasedExpenseLineDetail": {
			"AccountRef": {"value": "77"},
			"TaxCodeRef": {"value": tax_code},
		},
	}


def invoice_payload(lines, global_tax):
	return {
		"CustomerRef": {"value": "58"},
		"DocNumber": "SINV-0001",
		"TxnDate": "2026-01-15",
		"DueDate": "2026-02-14",
		"Line": lines,
		"BillEmail": {"Address": "cust@example.com"},
		"GlobalTaxCalculation": global_tax,
	}


def bill_payload(tax_code, global_tax):
	return {
		"VendorRef": {"value": "41"},
		"TxnDate": "2026-01-15",
		"Line": [expense_line(100.0, "ITEM-1: ITEM-1 description", tax_code)],
		"GlobalTaxCalculation": global_tax,
		"DocNumber": "PINV-0001",
		"DueDate": "2026-02-14",
	}


class TestUKInvoicePayloads(unittest.TestCase):
	def test_invoice_20_percent_two_items(self):
		doc = make_si(20, items=[item_row("ITEM-1", 2, 50.0), item_row("ITEM-2", 1, 30.0)])
		expected = invoice_payload(
			[
				sales_line("11", 100.0, 2.0, 50.0, "3", "ITEM-1 description"),
				sales_line("12", 30.0, 1.0, 30.0, "3", "ITEM-2 description"),
			],
			"TaxExcluded",
		)
		self.assertEqual(run_builder("invoice", doc), expected)

	def test_invoice_5_percent(self):
		expected = invoice_payload(
			[sales_line("11", 100.0, 2.0, 50.0, "8", "ITEM-1 description")], "TaxExcluded"
		)
		self.assertEqual(run_builder("invoice", make_si(5)), expected)

	def test_invoice_1_percent(self):
		expected = invoice_payload(
			[sales_line("11", 100.0, 2.0, 50.0, "10", "ITEM-1 description")], "TaxExcluded"
		)
		self.assertEqual(run_builder("invoice", make_si(1)), expected)

	def test_invoice_untaxed(self):
		expected = invoice_payload(
			[sales_line("11", 100.0, 2.0, 50.0, "12", "ITEM-1 description")], "NotApplicable"
		)
		self.assertEqual(run_builder("invoice", make_si(0)), expected)


class TestUKBillPayloads(unittest.TestCase):
	def test_bill_20_percent(self):
		self.assertEqual(run_builder("bill", make_pi(20)), bill_payload("3", "TaxExcluded"))

	def test_bill_5_percent(self):
		self.assertEqual(run_builder("bill", make_pi(5)), bill_payload("8", "TaxExcluded"))

	def test_bill_untaxed(self):
		self.assertEqual(run_builder("bill", make_pi(0)), bill_payload("12", "NotApplicable"))


class TestUKManualPayloads(unittest.TestCase):
	def test_credit_memo_20_percent(self):
		doc = make_return(make_si(20, name="SINV-RET-0001"))
		expected = {
			"CustomerRef": {"value": "58"},
			"DocNumber": "SINV-RET-0001",
			"TxnDate": "2026-01-15",
			"Line": [sales_line("11", 100.0, 2.0, 50.0, "3", "ITEM-1 description")],
			"GlobalTaxCalculation": "TaxExcluded",
		}
		self.assertEqual(run_builder("credit_memo", doc), expected)

	def test_credit_memo_untaxed(self):
		doc = make_return(make_si(0, name="SINV-RET-0001"))
		expected = {
			"CustomerRef": {"value": "58"},
			"DocNumber": "SINV-RET-0001",
			"TxnDate": "2026-01-15",
			"Line": [sales_line("11", 100.0, 2.0, 50.0, "12", "ITEM-1 description")],
			"GlobalTaxCalculation": "NotApplicable",
		}
		self.assertEqual(run_builder("credit_memo", doc), expected)

	def test_amend_invoice_5_percent(self):
		doc = make_si(5, name="SINV-0001-1", amended_from="SINV-0001")
		expected = {
			"Id": "900",
			"SyncToken": "4",
			"CustomerRef": {"value": "58"},
			"TxnDate": "2026-01-15",
			"DueDate": "2026-02-14",
			"Line": [sales_line("11", 100.0, 2.0, 50.0, "8", "ITEM-1 description")],
			"GlobalTaxCalculation": "TaxExcluded",
		}
		self.assertEqual(run_builder("amend_invoice", doc), expected)

	def test_amend_invoice_untaxed(self):
		doc = make_si(0, name="SINV-0001-1", amended_from="SINV-0001")
		expected = {
			"Id": "900",
			"SyncToken": "4",
			"CustomerRef": {"value": "58"},
			"TxnDate": "2026-01-15",
			"DueDate": "2026-02-14",
			"Line": [sales_line("11", 100.0, 2.0, 50.0, "12", "ITEM-1 description")],
			"GlobalTaxCalculation": "NotApplicable",
		}
		self.assertEqual(run_builder("amend_invoice", doc), expected)

	def test_vendor_credit_always_code_12(self):
		# Taxed at 20% but the original code still sends "12" / NotApplicable.
		doc = make_return(make_pi(20, name="PINV-RET-0001"))
		expected = {
			"VendorRef": {"value": "41"},
			"TxnDate": "2026-01-15",
			"Line": [expense_line(100.0, "ITEM-1", "12")],
			"GlobalTaxCalculation": "NotApplicable",
		}
		self.assertEqual(run_builder("vendor_credit", doc), expected)

	def test_amend_bill_always_code_12(self):
		doc = make_pi(20, name="PINV-0001-1", amended_from="PINV-0001")
		expected = {
			"Id": "900",
			"SyncToken": "2",
			"VendorRef": {"value": "41"},
			"TxnDate": "2026-01-15",
			"Line": [expense_line(100.0, "ITEM-1", "12")],
			"GlobalTaxCalculation": "NotApplicable",
			"DueDate": "2026-02-14",
		}
		self.assertEqual(run_builder("amend_bill", doc), expected)
