"""Tests for qb_address.py and where it is used. QuickBooks is never called."""

import unittest
from types import SimpleNamespace
from unittest import mock

import frappe

from quickbooks_connector import qb_address
from quickbooks_connector.tests.utils import Capture, make_settings, make_si


class TestUSStateCode(unittest.TestCase):
	def test_all_states_and_dc(self):
		self.assertEqual(len(qb_address.US_STATE_CODES), 51)
		self.assertEqual(len(set(qb_address.US_STATE_CODES.values())), 51)
		self.assertIn("DC", qb_address.US_STATE_CODES.values())

	def test_names(self):
		self.assertEqual(qb_address.us_state_code("California"), "CA")
		self.assertEqual(qb_address.us_state_code("new  york"), "NY")
		self.assertEqual(qb_address.us_state_code(" West Virginia "), "WV")
		self.assertEqual(qb_address.us_state_code("District of Columbia"), "DC")
		self.assertEqual(qb_address.us_state_code("Washington"), "WA")

	def test_two_letters_pass_through(self):
		self.assertEqual(qb_address.us_state_code("TX"), "TX")
		self.assertEqual(qb_address.us_state_code("tx"), "TX")

	def test_unknown_and_empty_unchanged(self):
		self.assertEqual(qb_address.us_state_code("Ontario"), "Ontario")
		self.assertEqual(qb_address.us_state_code(""), "")
		self.assertIsNone(qb_address.us_state_code(None))

	def test_subdivision_code_by_region(self):
		self.assertEqual(qb_address.subdivision_code("California", make_settings(qb_region="US")), "CA")
		self.assertEqual(qb_address.subdivision_code("Greater London", make_settings()), "Greater London")


class TestNormalizeCountry(unittest.TestCase):
	def test_us_aliases(self):
		for value in ("USA", "US", "us", "United States of America", "United States"):
			self.assertEqual(qb_address.normalize_country(value), "United States", value)

	def test_other_countries_unchanged(self):
		self.assertEqual(qb_address.normalize_country("United Kingdom"), "United Kingdom")
		self.assertEqual(qb_address.normalize_country("Canada"), "Canada")

	def test_empty_uses_default_country_then_uk(self):
		self.assertEqual(
			qb_address.normalize_country(None, make_settings(default_country="United States")),
			"United States",
		)
		self.assertEqual(qb_address.normalize_country("", make_settings()), "United Kingdom")
		self.assertEqual(qb_address.normalize_country(None, None), "United Kingdom")


def us_address():
	return SimpleNamespace(
		address_line1="1 Market St",
		address_line2=None,
		city="San Francisco",
		state="California",
		pincode="94105",
		country="United States",
		email_id=None,
	)


class TestOutboundAddresses(unittest.TestCase):
	def _invoice_bill_addr(self, settings):
		from quickbooks_connector import api

		doc = make_si(0, customer_address="ADDR-1")
		with Capture(settings) as cap:
			cap.docs[("Address", "ADDR-1")] = us_address()
			api.create_qb_invoice_from_sales_invoice(doc)
			return cap.posted("invoice")["BillAddr"]

	def test_invoice_us_state_code(self):
		addr = self._invoice_bill_addr(make_settings(qb_region="US", us_uses_ast=1))
		self.assertEqual(addr["CountrySubDivisionCode"], "CA")

	def test_invoice_uk_state_unchanged(self):
		addr = self._invoice_bill_addr(make_settings())
		self.assertEqual(addr["CountrySubDivisionCode"], "California")

	def _customer_payload(self, settings):
		from quickbooks_connector import qb_customer_hooks

		api_instance = mock.MagicMock()
		api_instance.settings = settings
		api_instance.make_request.return_value = {"Customer": {"SyncToken": "3"}}

		def get_value(doctype, filters=None, fieldname=None, *args, **kwargs):
			if doctype == "Address":
				return frappe._dict(vars(us_address()))
			return None

		customer = SimpleNamespace(
			name="Cust A", customer_name="Cust A", email_id=None, mobile_no=None,
			customer_primary_address="ADDR-1",
		)
		with mock.patch("quickbooks_connector.api.QuickBooksAPI", return_value=api_instance), mock.patch.object(
			frappe, "db", mock.MagicMock()
		):
			frappe.db.get_value.side_effect = get_value
			qb_customer_hooks.sync_customer_to_qb(customer, "58")
		return api_instance.make_request.call_args.kwargs["data"]

	def test_customer_sync_us_state_code(self):
		payload = self._customer_payload(make_settings(qb_region="US"))
		self.assertEqual(payload["BillAddr"]["CountrySubDivisionCode"], "CA")

	def test_customer_sync_uk_state_unchanged(self):
		payload = self._customer_payload(make_settings())
		self.assertEqual(payload["BillAddr"]["CountrySubDivisionCode"], "California")


class TestInboundAddress(unittest.TestCase):
	def _address_country(self, qb_country, settings):
		from quickbooks_connector import api

		bill_addr = {"Line1": "1 Market St", "City": "San Francisco", "CountrySubDivisionCode": "CA"}
		if qb_country is not None:
			bill_addr["Country"] = qb_country
		new_doc = mock.MagicMock()
		with mock.patch.object(api, "get_settings", return_value=settings), mock.patch.object(
			frappe, "db", mock.MagicMock()
		), mock.patch.object(frappe, "get_doc", return_value=new_doc) as get_doc:
			frappe.db.get_value.return_value = None  # no existing address
			api._update_party_address(bill_addr, "Customer", "Cust A")
		new_doc.insert.assert_called_once()
		return get_doc.call_args.args[0]["country"]

	def test_usa_maps_to_united_states(self):
		self.assertEqual(self._address_country("USA", make_settings()), "United States")

	def test_missing_country_uses_default_country(self):
		self.assertEqual(
			self._address_country(None, make_settings(default_country="United States")), "United States"
		)

	def test_missing_country_without_default_is_uk(self):
		self.assertEqual(self._address_country(None, make_settings()), "United Kingdom")


class TestInboundCustomerSync(unittest.TestCase):
	"""QuickBooks -> ERPNext customer sync."""

	def _create(self, qb_customer, settings=None):
		from quickbooks_connector import api

		new_doc = mock.MagicMock()
		new_doc.name = "Cust A"
		with mock.patch.object(api, "get_settings", return_value=settings or make_settings(company="US Test Co")), mock.patch.object(
			frappe, "db", mock.MagicMock()
		), mock.patch.object(frappe, "get_doc", return_value=new_doc) as get_doc, mock.patch.object(
			api, "_update_party_address"
		):
			frappe.db.get_value.return_value = None  # not synced before
			frappe.db.exists.return_value = True  # default group / territory exist
			result = api.create_or_update_customer(qb_customer)
		return result, get_doc.call_args.args[0], new_doc

	def test_usa_country_is_normalised(self):
		qb_customer = {
			"Id": "14",
			"DisplayName": "Amy's Bird Sanctuary",
			"BillAddr": {"Line1": "4581 Finch St.", "City": "Bayshore", "Country": "USA", "PostalCode": "94326"},
		}
		result, data, _ = self._create(qb_customer)
		self.assertEqual(result, "created")
		self.assertEqual(data["country"], "United States")
		self.assertEqual(data["city"], "Bayshore")

	def test_no_country_key_when_qb_sends_none(self):
		_, data, _ = self._create({"Id": "15", "DisplayName": "No Address"})
		self.assertNotIn("country", data)

	def test_synced_customer_is_flagged_so_it_is_not_pushed_back(self):
		_, _, new_doc = self._create({"Id": "15", "DisplayName": "No Address"})
		self.assertTrue(new_doc.flags.from_quickbooks)
		new_doc.insert.assert_called_once()

	def test_on_customer_update_skips_flagged_docs(self):
		from quickbooks_connector import qb_customer_hooks

		doc = SimpleNamespace(name="Cust A", quickbooks_id="14", flags=frappe._dict(from_quickbooks=True))
		with mock.patch.object(qb_customer_hooks, "sync_customer_to_qb") as push, mock.patch(
			"quickbooks_connector.api.get_settings"
		) as get_settings:
			qb_customer_hooks.on_customer_update(doc)
		push.assert_not_called()
		get_settings.assert_not_called()

	def test_on_customer_update_still_pushes_user_edits(self):
		from quickbooks_connector import qb_customer_hooks

		doc = SimpleNamespace(name="Cust A", quickbooks_id="14", flags=frappe._dict())
		with mock.patch.object(qb_customer_hooks, "sync_customer_to_qb") as push, mock.patch(
			"quickbooks_connector.api.get_settings", return_value=make_settings()
		):
			qb_customer_hooks.on_customer_update(doc)
		push.assert_called_once_with(doc, "14")
