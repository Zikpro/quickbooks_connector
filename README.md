# QuickBooks Connector for ERPNext

Seamlessly integrate **ERPNext** with **QuickBooks Online**, enabling automatic two-way synchronization of customers, suppliers, items, invoices, and payments — with full UK VAT support.

## Features

- **Secure OAuth 2.0 Connection** — connect ERPNext to QuickBooks Online safely, with automatic token refresh.
- **Master Data Sync** — sync Customers, Suppliers, Items, and Chart of Accounts from QuickBooks into ERPNext.
- **Sales Invoice → QuickBooks Invoice** — push Sales Invoices to QuickBooks with automatic UK VAT tax code mapping, billing address, customer email, and payment terms.
- **Purchase Invoice → QuickBooks Bill** — push Purchase Invoices to QuickBooks as Bills with correct VAT handling.
- **Payment Sync** — sync Payments and Bill Payments from QuickBooks into ERPNext as Payment Entries, automatically allocated against open invoices.
- **Invoice Cancellation → Auto Void** — cancelling a Sales or Purchase Invoice in ERPNext automatically voids the matching record in QuickBooks.
- **Returns → Credit Memo / Vendor Credit** — return invoices in ERPNext create the corresponding Credit Memo (Sales) or Vendor Credit (Purchase) in QuickBooks.
- **Amendments → Update, Not Duplicate** — amending an invoice in ERPNext updates the existing QuickBooks record instead of creating a new one.
- **Customer/Supplier Auto-Sync** — updating a Customer or Supplier's address, email, or phone in ERPNext automatically pushes the change to QuickBooks.
- **Bulk Push** — select multiple invoices from the Sales/Purchase Invoice list view and push them to QuickBooks in one action.
- **Payment Void Detection** — if a payment is voided in QuickBooks, the next sync automatically cancels the corresponding Payment Entry in ERPNext.

## Requirements

- Frappe Framework v15 or v16
- ERPNext installed on the site
- A QuickBooks Online account (Sandbox or Production)
- A registered app on the [Intuit Developer Portal](https://developer.intuit.com/) with Client ID and Client Secret

## Installation

```bash
cd ~/frappe-bench
bench get-app https://github.com/Zikpro/quickbooks_connector
bench --site your-site-name install-app quickbooks_connector
bench --site your-site-name migrate
```

## Setup

1. Go to **QuickBooks Setting** in ERPNext (search in the awesome bar).
2. Enter your QuickBooks app credentials:
   - Client ID
   - Client Secret
   - Redirect URI (must match the one registered on the Intuit Developer Portal)
   - Authorization Endpoint
   - Token Endpoint
   - Environment (Sandbox or Production) and the corresponding API endpoint
3. Click **Connect to QuickBooks** and complete the OAuth authorization in the popup window.
4. Once connected, use the **Sync** buttons to pull Customers, Suppliers, Items, and Accounts from QuickBooks into ERPNext.

## Usage

### Pushing Invoices to QuickBooks

Open a submitted Sales Invoice or Purchase Invoice and click **QuickBooks → Push to QuickBooks**. You can also select multiple invoices from the list view and use **Push to QuickBooks** as a bulk action.

### Returns

Create a Return / Credit Note against a synced invoice, submit it, then click **QuickBooks → Create Credit Memo in QB** (Sales) or **Create Vendor Credit in QB** (Purchase).

### Amendments

Cancel and amend a synced invoice as usual in ERPNext. After submitting the amendment, click **QuickBooks → Update QB Invoice** (Sales) or **Update QB Bill** (Purchase) to push the changes to the existing QuickBooks record.

### Cancellations

Simply cancel a synced Sales Invoice, Payment Entry in ERPNext — the corresponding record in QuickBooks will be voided automatically.

## Support

For issues, questions, or feature requests, please open an issue on [GitHub](https://github.com/Zikpro/quickbooks_connector/issues).

## License

MIT
