# QuickBooks Connector for ERPNext

Seamlessly integrate **ERPNext** with **QuickBooks Online**, enabling automatic two-way synchronization of customers, suppliers, items, invoices, and payments — for UK (VAT) and US (sales tax) QuickBooks companies.

## Features

- **Secure OAuth 2.0 Connection** — connect ERPNext to QuickBooks Online safely, with automatic token refresh.
- **Master Data Sync** — sync Customers, Suppliers, Items, and Chart of Accounts from QuickBooks into ERPNext.
- **UK and US QuickBooks** — choose the QuickBooks region in settings: UK companies get VAT tax code mapping, US companies get sales tax handling (including Automated Sales Tax).
- **Sales Invoice → QuickBooks Invoice** — push Sales Invoices to QuickBooks with automatic tax handling for the selected region, billing address (US state names converted to 2-letter codes), customer email, and payment terms.
- **Purchase Invoice → QuickBooks Bill** — push Purchase Invoices to QuickBooks as Bills with correct VAT / sales tax handling.
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
   - QuickBooks Region (**UK** or **US**, default UK) — see [US sales tax](#us-sales-tax)
3. Click **Connect to QuickBooks** and complete the OAuth authorization in the popup window.
4. Once connected, use the **Sync** buttons to pull Customers, Suppliers, Items, and Accounts from QuickBooks into ERPNext.
5. Under **Sync Options**, choose what **Sync All** / the daily auto sync includes, and whether Sales / Purchase Invoices are pushed automatically on submit.

## UK VAT

With region **UK** (the default), each line gets a QuickBooks VAT tax code from the invoice's first tax rate above 0: 20% or more → `3`, 5% or more → `8`, above 0 → `10`, otherwise **Default QB Tax Code ID** (`12`). `GlobalTaxCalculation` is `TaxExcluded` when taxed, `NotApplicable` otherwise.

## US sales tax

Set **QuickBooks Region** to **US** for US QuickBooks companies.

1. Click **QuickBooks → Test Connection**. The connector reads your QuickBooks Preferences and ticks **Uses Automated Sales Tax (AST)** if Automated Sales Tax is on.
2. If AST is **off**, click **QuickBooks → Fetch US Tax Codes**, pick the sales tax code (e.g. your state/county rate) and save. It is sent as the invoice's transaction tax code. Taxed invoices fail to sync until it is set.
3. In ERPNext, book sales tax in the Sales/Purchase Taxes table on an account whose **Account Type** is **Tax**.

How documents are sent:

- **Invoices and Credit Memos** — a document is *taxed* when it has a tax row on a **Tax** account with a non-zero amount. Lines are sent with tax code `TAX` (taxed) or `NON` (not taxed).
  - AST off: `TxnTaxDetail.TxnTaxCodeRef` = your US Sales Tax Code; QuickBooks calculates the tax from that code.
  - AST on: `TxnTaxDetail.TotalTax` = ERPNext's tax amount (after discount) together with `TxnTaxDetail.TxnTaxCodeRef`, as Intuit's override needs both. The code is your US Sales Tax Code if set; otherwise the document is created, and the TaxCode QuickBooks assigns is sent back with `TotalTax` in a follow-up update (amendments reuse the code already on the invoice).
  - `GlobalTaxCalculation` is never sent (QuickBooks does not support it for US companies).
  - After an Invoice / Credit Memo is created or amended, QuickBooks' `TotalTax` is compared with ERPNext's sales tax. If they differ by more than 0.01, the sync is kept but the Sales Invoice is marked **Synced with Warning**, both amounts are written to **QuickBooks Sync Error**, and a "QuickBooks Sales Tax Mismatch" entry is added to the Sync Log.
- **Bills and Vendor Credits** — lines carry no tax code; the purchase tax amount is added as one extra expense line on the same expense account.

Limitations:

- Freight and other non-tax charges in the taxes table are not pushed to QuickBooks (in either region).
- Purchase tax is booked to the item expense account; a dedicated tax expense account is not configurable yet.
- With AST off, QuickBooks calculates the tax from the selected code, so make sure its rate matches ERPNext.

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
