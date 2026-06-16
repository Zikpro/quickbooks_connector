import frappe
import requests
import json
import base64
from frappe import _
from frappe.utils import now_datetime, get_datetime, getdate, cstr
from urllib.parse import urlencode, quote
import secrets
from datetime import timedelta
from frappe.utils import flt

# ============ CONSTANTS ============
DEFAULT_CUSTOMER_TYPE = "Company"
DEFAULT_CUSTOMER_GROUP = "Commercial"
DEFAULT_TERRITORY = "All Territories"
DEFAULT_ITEM_GROUP = "All Item Groups"
DEFAULT_STOCK_UOM = "Nos"
STATE_TIMEOUT = 300  # 5 minutes
TOKEN_REFRESH_BUFFER = 300  # 5 minutes

# ============ TASK 1: OAuth Functions ============

def get_settings():
    """Get QuickBooks Settings document"""
    try:
        settings = frappe.get_all(
            "Quickbooks Setting",
            filters={"name": ["!=", ""]},
            limit=1,
            ignore_permissions=True
        )
        if not settings:
            frappe.throw(
                _("Please create a Quickbooks Setting record first.")
            )
        return frappe.get_doc(
            "Quickbooks Setting",
            settings[0].name,
            ignore_permissions=True
        )
    except Exception as e:
        frappe.log_error(
            title="QuickBooks Settings Error",
            message=f"Failed to get settings: {str(e)}"
        )
        raise

@frappe.whitelist()
def get_authorization_url():
    """Generate QuickBooks OAuth 2.0 authorization URL"""
    try:
        settings = get_settings()
        
        # Validate required fields
        required_fields = ['client_id', 'redirect_uri']
        for field in required_fields:
            if not getattr(settings, field, None):
                frappe.throw(_(f"{field.replace('_', ' ').title()} is required"))

        scopes = [
            "com.intuit.quickbooks.accounting",
            "openid",
            "profile",
            "email",
            "phone",
            "address"
        ]

      
        state = secrets.token_urlsafe(16)

        state_data = {
            "timestamp": now_datetime(),
            "redirect_uri": settings.redirect_uri
        }
        frappe.cache().set_value(
            f"quickbooks_state:{state}",
            state_data,
            expires_in_sec=STATE_TIMEOUT
        )

        
        params = {
            'client_id': settings.client_id,
            'response_type': 'code',
            'scope': ' '.join(scopes),
            'redirect_uri': settings.redirect_uri,
            'state': state
        }

        auth_url = settings.authorization_endpoint or "https://appcenter.intuit.com/connect/oauth2"
        authorization_url = f"{auth_url}?{urlencode(params)}"

   
        frappe.logger().debug(
            f"QuickBooks Auth URL generated with state: {state}"
        )

        return {
            "success": True,
            "authorization_url": authorization_url,
            "state": state
        }

    except Exception as e:
        frappe.log_error(
            title="QuickBooks Authorization Error",
            message=f"Error generating authorization URL: {str(e)}"
        )
        return {
            "success": False,
            "error": f"Failed to generate authorization URL: {str(e)}"
        }

@frappe.whitelist(allow_guest=True)
def oauth_callback(code=None, state=None, realmId=None, error=None):
    """Handle OAuth 2.0 callback from QuickBooks"""
    

    frappe.set_user("Administrator")
    
    try:
        if error:
            error_msg = f"OAuth Error: {error}"
            frappe.log_error("QuickBooks OAuth Error", error_msg)
            return render_error_page("OAuth Error", error)

        if not code:
            return render_error_page("Missing Code", "No authorization code received")

        if not state:
            return render_error_page("Missing State", "No state parameter received")

        stored_state = frappe.cache().get_value(f"quickbooks_state:{state}")
        if not stored_state:
            return render_error_page("Invalid State", "State parameter verification failed or expired")

        frappe.cache().delete_key(f"quickbooks_state:{state}")

        frappe.logger().debug(f"QuickBooks Callback - Code: {code[:10]}..., RealmId: {realmId}")

        settings = get_settings()

        token_data = exchange_code_for_tokens(code, settings)
        if not token_data:
            return render_error_page("Token Exchange Failed", "Failed to exchange authorization code for tokens")

        save_tokens(settings, token_data, realmId)

        test_result = test_connection()
        if test_result.get("success"):
            settings.is_connected = 1
            settings.company_name = test_result.get("company_name", "")
            settings.save(ignore_permissions=True)
            frappe.db.commit()

            log_action("Connection Established", {
                "company": settings.company_name,
                "realm_id": realmId,
                "timestamp": now_datetime()
            })

            return render_success_page()
        else:
            return render_error_page("Connection Test Failed", test_result.get('error', 'Unknown error'))

    except Exception as e:
        frappe.log_error(title="QuickBooks OAuth Callback Error", message=str(e))
        return render_error_page("Connection Error", str(e))

def exchange_code_for_tokens(code, settings):
    """Exchange authorization code for tokens"""
    try:
        token_url = settings.token_endpoint
        
     
        client_secret = settings.get_password('client_secret')
        if not client_secret:
            frappe.throw(_("Client secret not found in settings"))

        auth_string = f"{settings.client_id}:{client_secret}"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()

        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': f'Basic {encoded_auth}'
        }

        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': settings.redirect_uri
        }

        response = requests.post(token_url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        
        token_data = response.json()
        
      
        required_token_fields = ['access_token', 'refresh_token', 'expires_in']
        for field in required_token_fields:
            if field not in token_data:
                frappe.throw(_(f"Missing required token field: {field}"))

        return token_data

    except requests.exceptions.RequestException as e:
        frappe.log_error(
            title="Token Exchange HTTP Error",
            message=f"HTTP Error: {str(e)}"
        )
        frappe.throw(_(f"Token exchange failed: {str(e)}"))
    except Exception as e:
        frappe.log_error(
            title="Token Exchange Error",
            message=str(e)
        )
        frappe.throw(_(f"Token exchange failed: {str(e)}"))

def save_tokens(settings, token_data, realm_id=None):
    """Save tokens to settings"""
    try:
      
        settings.access_token = token_data.get("access_token")
        settings.refresh_token = token_data.get("refresh_token")
        
        expires_in = token_data.get('expires_in', 3600)
        settings.token_expiry = now_datetime() + timedelta(
            seconds=expires_in - TOKEN_REFRESH_BUFFER
        )

        if realm_id:
            settings.realm_id_company_id = realm_id

        settings.save(ignore_permissions=True)
        frappe.db.commit()

        log_action(
            "Tokens Saved",
            {
                "realm_id": realm_id,
                "expires_at": settings.token_expiry
            }
        )

    except Exception as e:
        frappe.log_error("Save Tokens Error", str(e))
        frappe.throw(_(f"Failed to save tokens: {str(e)}"))

# ============ TASK 2: Token Management ============

@frappe.whitelist()
def get_company_info():
    api = QuickBooksAPI()
    return api.get_company_info()

@frappe.whitelist()
def refresh_tokens():
    """Refresh access token"""
    try:
        settings = get_settings()
        
        if not settings.refresh_token:
            return {
                "success": False,
                "error": "No refresh token available"
            }

    
        client_secret = settings.get_password('client_secret')
        refresh_token = settings.get_password('refresh_token')
        
        if not client_secret or not refresh_token:
            return {
                "success": False,
                "error": "Missing client secret or refresh token"
            }

        token_url = settings.token_endpoint
        auth_string = f"{settings.client_id}:{client_secret}"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()

        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': f'Basic {encoded_auth}'
        }

        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token
        }

        response = requests.post(token_url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        
        token_data = response.json()
        save_tokens(settings, token_data)

        log_action("Tokens Refreshed", {"success": True})
        
        return {
            "success": True,
            "message": "Tokens refreshed successfully"
        }

    except requests.exceptions.RequestException as e:
        error_msg = f"Token refresh failed: {str(e)}"
        log_action("Token Refresh Failed", {"error": error_msg})
        return {"success": False, "error": error_msg}
    except Exception as e:
        error_msg = f"Token refresh error: {str(e)}"
        log_action("Token Refresh Failed", {"error": error_msg})
        return {"success": False, "error": error_msg}

def get_valid_access_token():
    """Get valid token (refresh if expired)"""
    try:
        settings = get_settings()
        
        if not settings.access_token:
            frappe.throw(_("Not connected to QuickBooks"))

        
        if settings.token_expiry:
            expiry_time = get_datetime(settings.token_expiry)
            current_time = now_datetime()
            
            
            if current_time >= expiry_time:
                refresh_result = refresh_tokens()
                if not refresh_result.get("success"):
                    frappe.throw(_(f"Failed to refresh token: {refresh_result.get('error')}"))

        access_token = settings.get_password('access_token')
        if not access_token:
            frappe.throw(_("Access token not found"))
            
        return access_token

    except Exception as e:
        frappe.log_error(
            title="Get Access Token Error",
            message=str(e)
        )
        raise

# ============ TASK 3: API Client ============

class QuickBooksAPI:
    """QuickBooks API Client"""
    
    def __init__(self):
        self.settings = get_settings()
        self.max_retries = 3
        self.timeout = 60

    def get_api_endpoint(self):
        """Get correct API endpoint"""
        if self.settings.environment == "Sandbox":
            return self.settings.api_endpoint_sandbox or "https://sandbox-quickbooks.api.intuit.com/v3"
        else:
            return self.settings.api_endpoint_production or "https://quickbooks.api.intuit.com/v3"

    def make_request(self, endpoint, method='GET', data=None, params=None, retry_count=0):
        try:
            access_token = get_valid_access_token()
            
            if not self.settings.realm_id_company_id:
                frappe.throw(_("Realm ID not found. Please connect first."))

            base_url = self.get_api_endpoint()
            url = f"{base_url}/company/{self.settings.realm_id_company_id}/{endpoint}"

            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'User-Agent': 'ERPNext-QuickBooks-Integration/1.0'
            }

            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=data,
                params=params,
                timeout=self.timeout
            )

            if response.status_code == 429 and retry_count < self.max_retries:
                import time
                time.sleep(60)
                return self.make_request(endpoint, method, data, params, retry_count + 1)

            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            details = e.response.text if e.response else ""
            frappe.log_error("QuickBooks HTTP Error", f"{url}\n{details}")
            frappe.throw(_(details or str(e)))
        except requests.exceptions.RequestException as e:
            frappe.log_error("QuickBooks Network Error", str(e))
            frappe.throw(_("Network error while calling QuickBooks"))
        except Exception as e:
            frappe.log_error("QuickBooks API Error", str(e))
            frappe.throw(_("Unexpected QuickBooks API error"))


    def get_accounts(self, start_position=1, max_results=1000):
        """Get accounts with pagination"""
        query = f"SELECT * FROM Account STARTPOSITION {start_position} MAXRESULTS {max_results}"
        return self.make_request(f'query?query={quote(query)}')
    
    # ============ QUICKBOOKS BILL PAYMENT SYNC ============

    def get_bills(self, start_position=1, max_results=1000):
        """Get bills from QuickBooks with pagination"""
        query = f"SELECT * FROM Bill STARTPOSITION {start_position} MAXRESULTS {max_results}"
        return self.make_request(f'query?query={quote(query)}')

   

    def get_bill_payments(self, start_position=1, max_results=1000):
        """Get bill payments from QuickBooks"""
        query = f"SELECT * FROM BillPayment STARTPOSITION {start_position} MAXRESULTS {max_results}"
        return self.make_request(f'query?query={quote(query)}')

    # ============ API Methods ============
    
    def get_company_info(self):
        """Get company info"""
        realm_id = self.settings.realm_id_company_id
        if not realm_id:
            frappe.throw(_("Realm ID not found"))
        
        return self.make_request(
            f"companyinfo/{realm_id}",
            params={"minorversion": 65}
        )

    def get_customers(self, start_position=1, max_results=1000):
        """Get customers with pagination"""
        query = f"SELECT * FROM Customer STARTPOSITION {start_position} MAXRESULTS {max_results}"
        return self.make_request(f'query?query={quote(query)}')

    def get_items(self, start_position=1, max_results=1000):
        """Get items with pagination"""
        query = f"SELECT * FROM Item STARTPOSITION {start_position} MAXRESULTS {max_results}"
        return self.make_request(f'query?query={quote(query)}')

    def get_payments(self, start_position=1, max_results=1000):
        """Get payments from QuickBooks with pagination"""
        query = f"SELECT * FROM Payment STARTPOSITION {start_position} MAXRESULTS {max_results}"
        return self.make_request(f'query?query={quote(query)}')

    def get_payment_by_id(self, payment_id):
        """Get specific payment by ID"""
        return self.make_request(f'payment/{payment_id}')

    def get_payments_by_date_range(self, start_date, end_date, start_position=1, max_results=1000):
        """Get payments within date range"""
        query = f"SELECT * FROM Payment WHERE TxnDate >= '{start_date}' AND TxnDate <= '{end_date}' STARTPOSITION {start_position} MAXRESULTS {max_results}"
        return self.make_request(f'query?query={quote(query)}')

    def query_all(self, entity, conditions=None):
        """Query all records of an entity with optional conditions"""
        query = f"SELECT * FROM {entity}"
        if conditions:
            query += f" WHERE {conditions}"
        query += f" STARTPOSITION 1 MAXRESULTS 1000"
        return self.make_request(f'query?query={quote(query)}')
    
    def get_vendors(self, start_position=1, max_results=1000):
        query = f"SELECT * FROM Vendor STARTPOSITION {start_position} MAXRESULTS {max_results}"
        return self.make_request(f'query?query={quote(query)}')


# ============ TASK 4: Connection Functions ============

@frappe.whitelist()
def test_connection():
    """Test QuickBooks connection"""
    try:
        settings = get_settings()
        
        if not settings.access_token:
            return {
                "success": False,
                "error": "Not connected to QuickBooks"
            }

        api = QuickBooksAPI()
        company_info = api.get_company_info()

        if not isinstance(company_info, dict):
            frappe.throw(_("Invalid response from QuickBooks (not JSON)"))
        
        if "CompanyInfo" not in company_info:
            frappe.throw(_("CompanyInfo missing in QuickBooks response"))

        company_name = company_info.get('CompanyInfo', {}).get('CompanyName', 'Unknown')

        # Update settings
        settings.is_connected = 1
        settings.company_name = company_name
        settings.save()
        frappe.db.commit()

        log_action(
            "Connection Test Success",
            {
                "company": company_name,
                "timestamp": now_datetime()
            }
        )

        return {
            "success": True,
            "company_name": company_name,
            "message": "Successfully connected to QuickBooks"
        }

    except Exception as e:
        error_msg = str(e)
        log_action(
            "Connection Test Failed",
            {
                "error": error_msg,
                "timestamp": now_datetime()
            }
        )
        
        if 'settings' in locals():
            settings.is_connected = 0
            settings.save()
            frappe.db.commit()

        return {
            "success": False,
            "error": error_msg
        }

@frappe.whitelist()
def disconnect():
    """Disconnect from QuickBooks"""
    try:
        settings = get_settings()
        
       
        settings.access_token = ""
        settings.refresh_token = ""
        settings.realm_id_company_id = ""
        settings.token_expiry = None
        settings.is_connected = 0
        settings.company_name = ""
        settings.save()
        frappe.db.commit()

        log_action("Disconnected", {"timestamp": now_datetime()})
        
        return {
            "success": True,
            "message": "Disconnected from QuickBooks successfully"
        }

    except Exception as e:
        error_msg = str(e)
        log_action("Disconnect Failed", {"error": error_msg})


@frappe.whitelist()
def sync_all():
    """Sync all data based on settings"""
    try:
        settings = get_settings()
        if not settings.is_connected:
            return {
                "success": False,
                "error": "Not connected to QuickBooks"
            }

        results = {
            "accounts": {"success": False},
            "customers": {"success": False},
            "items": {"success": False},
            "suppliers": {"success": False},
            "payments": {"success": False},
            "bill_payments": {"success": False}
        }

    
        if getattr(settings, 'sync_accounts', True):
            results["accounts"] = sync_accounts()

        
        if getattr(settings, 'sync_customers', False):
            results["customers"] = sync_customers()

        if getattr(settings, 'sync_items', False):
            results["items"] = sync_items()

    
        if getattr(settings, 'sync_suppliers', False):
            results["suppliers"] = sync_suppliers()

        if getattr(settings, 'sync_payments', True):
            results["payments"] = sync_payments()

        
        if getattr(settings, 'sync_bill_payments', True):
            results["bill_payments"] = sync_bill_payments()


        settings.last_sync = now_datetime()
        settings.save()
        frappe.db.commit()

      
        all_success = all(
            result.get("success", False) for result in results.values()
            if isinstance(result, dict)
        )

        log_action(
            "Full Sync Completed",
            {
                "results": results,
                "timestamp": now_datetime()
            }
        )

        return {
            "success": all_success,
            "results": results,
            "message": "Sync completed"
        }

    except Exception as e:
        error_msg = str(e)
        log_action(
            "Full Sync Failed",
            {
                "error": error_msg,
                "timestamp": now_datetime()
            }
        )
        return {
            "success": False,
            "error": error_msg
        }
    
def sync_with_pagination(api_method, process_func, entity_name):
    """Generic sync function with pagination"""
    try:
        start_position = 1
        max_results = 1000
        total_created = 0
        total_updated = 0
        total_processed = 0

        while True:
         
            response = api_method(start_position, max_results)
            query_response = response.get('QueryResponse', {})
            entities = query_response.get(entity_name, [])

            if not entities:
                break

 
            created, updated = process_func(entities)
            total_created += created
            total_updated += updated
            total_processed += len(entities)

            if len(entities) < max_results:
                break

            start_position += max_results

        return total_created, total_updated, total_processed

    except Exception as e:
        frappe.log_error(
            title=f"Pagination Sync Error - {entity_name}",
            message=str(e)
        )
        raise

@frappe.whitelist()
def sync_customers():
    """Sync customers from QuickBooks"""
    try:
        api = QuickBooksAPI()

        def process_customers_batch(customers_batch):
            created = 0
            updated = 0
            for qb_customer in customers_batch:
                result = create_or_update_customer(qb_customer)
                if result == "created":
                    created += 1
                elif result == "updated":
                    updated += 1
            return created, updated

        total_created, total_updated, total_processed = sync_with_pagination(
            api.get_customers,
            process_customers_batch,
            "Customer"
        )

        log_action(
            "Customers Synced",
            {
                "created": total_created,
                "updated": total_updated,
                "processed": total_processed,
                "timestamp": now_datetime()
            }
        )

        return {
            "success": True,
            "message": f"Synced {total_created} new, {total_updated} updated customers",
            "created": total_created,
            "updated": total_updated,
            "processed": total_processed
        }

    except Exception as e:
        error_msg = str(e)
        log_action(
            "Customer Sync Failed",
            {
                "error": error_msg,
                "timestamp": now_datetime()
            }
        )
        return {
            "success": False,
            "error": error_msg
        }

def create_or_update_customer(qb_customer):
    """Create or update customer in ERPNext"""
    try:
        customer_id = qb_customer.get('Id')
        if not customer_id:
            return "skipped"
        
        settings = get_settings() 
        
        

      
        existing = frappe.db.get_value(
            "Customer",
            {"quickbooks_id": customer_id},
            ["name", "customer_name"]
        )

     
        customer_group = DEFAULT_CUSTOMER_GROUP
        if not frappe.db.exists("Customer Group", customer_group):
            # Sirf non-group type groups dhundo
            customer_group = frappe.db.get_value(
                "Customer Group",
                {"is_group": 0},
                "name",
                order_by="creation asc"
            )
            if not customer_group:
                frappe.throw(_("No valid Customer Group found. Please create a non-group Customer Group in ERPNext."))

        territory = DEFAULT_TERRITORY
        if not frappe.db.exists("Territory", territory):
            territory = frappe.db.get_value("Territory", 
                {}, "name") or "All Territories"

        customer_data = {
            "customer_name": qb_customer.get('DisplayName') or qb_customer.get('FullyQualifiedName'),
            "customer_type": DEFAULT_CUSTOMER_TYPE,
            "customer_group": customer_group,
            "territory": territory,
            "quickbooks_id": customer_id,
            "disabled": qb_customer.get('Active') == False,
            "custom_company": settings.company         
        }

      
        primary_email = qb_customer.get('PrimaryEmailAddr', {})
        if primary_email.get('Address'):
            customer_data["email_id"] = primary_email.get('Address')

        primary_phone = qb_customer.get('PrimaryPhone', {})
        if primary_phone.get('FreeFormNumber'):
            customer_data["mobile_no"] = primary_phone.get('FreeFormNumber')

        billing_address = qb_customer.get('BillAddr', {})
        if billing_address and billing_address.get('Country'):
            address_fields = {
                'Line1': 'address_line1',
                'Line2': 'address_line2',
                'City': 'city',
                'Country': 'country',
                'PostalCode': 'pincode'
            }
            for qb_field, erp_field in address_fields.items():
                if billing_address.get(qb_field):
                    customer_data[erp_field] = billing_address.get(qb_field)

        if existing:
            customer = frappe.get_doc("Customer", existing[0])
            customer.update(customer_data)
            customer.save(ignore_permissions=True)
            _update_party_address(qb_customer.get('BillAddr', {}), "Customer", existing[0])
            return "updated"
        else:
            customer = frappe.get_doc({
                "doctype": "Customer",
                **customer_data
            })
            customer.insert(ignore_permissions=True)
            _update_party_address(qb_customer.get('BillAddr', {}), "Customer", customer.name)
            return "created"

    except Exception as e:
        frappe.log_error(
            title="Customer Processing Error",
            message=f"Customer ID: {qb_customer.get('Id')}, Error: {str(e)}"
        )
        return "error"
    

def create_or_update_supplier(qb_vendor):
    try:
        vendor_id = qb_vendor.get("Id")
        if not vendor_id:
            return "skipped"

        supplier_name = qb_vendor.get("DisplayName") or qb_vendor.get("CompanyName")
        if not supplier_name:
            return "skipped"

        settings = get_settings()

        existing = frappe.db.get_value(
            "Supplier",
            {"quickbooks_id": vendor_id},
            "name"
        )

        supplier_data = {
            "supplier_name": supplier_name,
            "supplier_type": "Company",
            "quickbooks_id": vendor_id,
            "disabled": qb_vendor.get("Active") is False,
            "custom_company": settings.company
        }

        primary_email = qb_vendor.get('PrimaryEmailAddr', {})
        if primary_email.get('Address'):
            supplier_data["email_id"] = primary_email.get('Address')

        primary_phone = qb_vendor.get('PrimaryPhone', {})
        if primary_phone.get('FreeFormNumber'):
            supplier_data["mobile_no"] = primary_phone.get('FreeFormNumber')

        if existing:
            sup = frappe.get_doc("Supplier", existing)
            sup.update(supplier_data)
            sup.save(ignore_permissions=True)
            _update_party_address(qb_vendor.get('BillAddr', {}), "Supplier", existing)
            return "updated"

        sup = frappe.get_doc({
            "doctype": "Supplier",
            **supplier_data
        })
        sup.insert(ignore_permissions=True)
        _update_party_address(qb_vendor.get('BillAddr', {}), "Supplier", sup.name)
        return "created"

    except Exception as e:
        frappe.log_error("Supplier Sync Error", str(e))
        return "error"

def _update_party_address(bill_addr, party_type, party_name):
    """Create or update billing address for Customer or Supplier"""
    try:
        if not bill_addr or not bill_addr.get('Line1'):
            return

        # City required hai ERPNext Address mein - agar missing hai toh skip karo
        if not bill_addr.get('City'):
            frappe.logger().debug(
                f"Skipping address sync for {party_type} '{party_name}': City missing from QB data"
            )
            return

        address_name = frappe.db.get_value(
            "Dynamic Link",
            {
                "link_doctype": party_type,
                "link_name": party_name,
                "parenttype": "Address"
            },
            "parent"
        )

        addr_data = {
            "address_line1": bill_addr.get('Line1', ''),
            "address_line2": bill_addr.get('Line2', ''),
            "city": bill_addr.get('City', ''),
            "state": bill_addr.get('CountrySubDivisionCode', ''),
            "pincode": bill_addr.get('PostalCode', ''),
            "country": bill_addr.get('Country', 'United Kingdom'),
            "address_type": "Billing"
        }

        if address_name and frappe.db.exists("Address", address_name):
            addr = frappe.get_doc("Address", address_name)
            addr.update(addr_data)
            addr.save(ignore_permissions=True)
        else:
            addr = frappe.get_doc({
                "doctype": "Address",
                "address_title": party_name,
                "address_type": "Billing",
                **addr_data,
                "links": [{
                    "link_doctype": party_type,
                    "link_name": party_name
                }]
            })
            addr.insert(ignore_permissions=True)

    except Exception as e:
        frappe.log_error(
            f"{party_type} Address Sync Error",
            f"{party_name}: {str(e)}"
        )


@frappe.whitelist()
def sync_items():
    """Sync items from QuickBooks"""
    try:
        api = QuickBooksAPI()

        def process_items_batch(items_batch):
            created = 0
            updated = 0
            for qb_item in items_batch:
                result = create_or_update_item(qb_item)
                if result == "created":
                    created += 1
                elif result == "updated":
                    updated += 1
            return created, updated

        total_created, total_updated, total_processed = sync_with_pagination(
            api.get_items,
            process_items_batch,
            "Item"
        )

        log_action(
            "Items Synced",
            {
                "created": total_created,
                "updated": total_updated,
                "processed": total_processed,
                "timestamp": now_datetime()
            }
        )

        return {
            "success": True,
            "message": f"Synced {total_created} new, {total_updated} updated items",
            "created": total_created,
            "updated": total_updated,
            "processed": total_processed
        }

    except Exception as e:
        error_msg = str(e)
        log_action(
            "Item Sync Failed",
            {
                "error": error_msg,
                "timestamp": now_datetime()
            }
        )
        return {
            "success": False,
            "error": error_msg
        }
    
@frappe.whitelist()
def sync_suppliers():
    try:
        api = QuickBooksAPI()

        def process_batch(vendors):
            created = updated = 0
            for v in vendors:
                res = create_or_update_supplier(v)
                if res == "created":
                    created += 1
                elif res == "updated":
                    updated += 1
            return created, updated

        total_created, total_updated, total_processed = sync_with_pagination(
            api.get_vendors,
            process_batch,
            "Vendor"
        )

        log_action(
            "Suppliers Synced",
            {
                "created": total_created,
                "updated": total_updated,
                "processed": total_processed
            }
        )

        return {
            "success": True,
            "message": f"Suppliers synced: {total_created} created, {total_updated} updated"
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

    


@frappe.whitelist()
def push_sales_invoice_to_quickbooks(sales_invoice_name: str):
    """
    Manual push button: ERPNext Sales Invoice -> QuickBooks Invoice
    """
    try:
        si = frappe.get_doc("Sales Invoice", sales_invoice_name)

        if si.docstatus != 1:
            frappe.throw(_("Sales Invoice must be Submitted before syncing to QuickBooks."))

        result = create_qb_invoice_from_sales_invoice(si)

        return {
            "success": True,
            "message": f"Invoice synced to QuickBooks. QB Invoice ID: {result.get('qb_invoice_id')}",
            "qb_invoice_id": result.get("qb_invoice_id")
        }

    except Exception as e:
        frappe.log_error("QuickBooks Invoice Push Error", f"{sales_invoice_name}: {str(e)}")
        return {"success": False, "error": str(e)}

def create_qb_invoice_from_sales_invoice(si):
    """
    Core logic:
    - Prevent duplicates
    - Build payload
    - POST to QuickBooks /invoice endpoint
    - Save QB Invoice ID back on ERPNext Sales Invoice
    """

    if getattr(si, "quickbooks_id", None):
        return {"qb_invoice_id": si.quickbooks_id, "skipped": True}

    settings = get_settings()
    api = QuickBooksAPI()


    qb_customer_id = frappe.db.get_value("Customer", si.customer, "quickbooks_id")
    if not qb_customer_id:
        msg = f"Customer '{si.customer}' does not have QuickBooks ID. Sync customers first."
        _mark_sales_invoice_sync_error(si, msg)
        frappe.throw(_(msg))


    default_tax_code = getattr(settings, 'default_tax_code', '12') or '12'


    def get_qb_tax_code(tax_rate):
        rate = float(tax_rate or 0)
        if rate >= 20:
            return "3"   
        elif rate >= 5:
            return "8"   
        elif rate > 0:
            return "10"   
        else:
            return default_tax_code 

    
    invoice_tax_rate = 0
    if si.taxes:
        for tax in si.taxes:
            if float(tax.rate or 0) > 0:
                invoice_tax_rate = float(tax.rate)
                break

    
    lines = []
    for row in si.items:
        qb_item_id = frappe.db.get_value("Item", row.item_code, "quickbooks_id")
        if not qb_item_id:
            msg = f"Item '{row.item_code}' missing QuickBooks ID. Sync items first."
            _mark_sales_invoice_sync_error(si, msg)
            frappe.throw(_(msg))

        tax_code_id = get_qb_tax_code(invoice_tax_rate)

        line = {
            "DetailType": "SalesItemLineDetail",
            "Amount": float(row.amount),
            "Description": row.description or row.item_name or row.item_code,
            "SalesItemLineDetail": {
                "ItemRef": {"value": str(qb_item_id)},
                "Qty": float(row.qty),
                "UnitPrice": float(row.rate),
                "TaxCodeRef": {"value": tax_code_id}
            }
        }
        lines.append(line)

    if not lines:
        msg = "Sales Invoice has no items. Cannot sync."
        _mark_sales_invoice_sync_error(si, msg)
        frappe.throw(_(msg))

    doc_number = si.name

    payload = {
        "CustomerRef": {"value": str(qb_customer_id)},
        "DocNumber": str(si.name),
        "TxnDate": str(si.posting_date),
        "DueDate": str(si.due_date),
        "Line": lines
    }

    if si.customer_address:
        try:
            addr = frappe.get_doc("Address", si.customer_address)
            bill_addr = {}
            if addr.address_line1:
                bill_addr["Line1"] = addr.address_line1
            if addr.address_line2:
                bill_addr["Line2"] = addr.address_line2
            if addr.city:
                bill_addr["City"] = addr.city
            if addr.state:
                bill_addr["CountrySubDivisionCode"] = addr.state
            if addr.pincode:
                bill_addr["PostalCode"] = addr.pincode
            if addr.country:
                bill_addr["Country"] = addr.country
            if bill_addr:
                payload["BillAddr"] = bill_addr
        except Exception:
            pass


    try:
        customer_email = None
        
        if si.customer_address:
            customer_email = frappe.db.get_value(
                "Address", si.customer_address, "email_id"
            )

        if not customer_email and getattr(si, 'contact_email', None):
            customer_email = si.contact_email
    
        if not customer_email:
            customer_email = frappe.db.get_value(
                "Customer", si.customer, "email_id"
            )
        if customer_email:
            payload["BillEmail"] = {"Address": customer_email}
    except Exception:
        pass

    if si.payment_terms_template:
        try:
            terms_response = api.make_request(
                "query?query=SELECT * FROM Term",
                params={"minorversion": 65}
            )
            terms_list = terms_response.get('QueryResponse', {}).get('Term', [])
            template_name = si.payment_terms_template.lower().strip()
            matched_term = None
            for term in terms_list:
                qb_term_name = term.get('Name', '').lower().strip()
    
                if qb_term_name == template_name:
                    matched_term = term
                    break
                if template_name.replace(' ', '') == qb_term_name.replace(' ', ''):
                    matched_term = term
                    break
                import re
                erp_numbers = re.findall(r'\d+', template_name)
                qb_numbers = re.findall(r'\d+', qb_term_name)
                if erp_numbers and qb_numbers and erp_numbers[0] == qb_numbers[0]:
                    matched_term = term
                    break
            if matched_term:
                payload["SalesTermRef"] = {"value": str(matched_term.get('Id'))}
        except Exception:
            pass
    elif si.due_date:
        payload["DueDate"] = str(si.due_date)

    # -------------------------------
    # TAX HANDLING (QB UK FORMAT)
    # QB automatically calculates VAT based on TaxCodeRef in line items
    # -------------------------------
    if invoice_tax_rate > 0:
        payload["GlobalTaxCalculation"] = "TaxExcluded"
    else:
        payload["GlobalTaxCalculation"] = "NotApplicable"

    # Create invoice in QB
    qb_response = api.make_request("invoice", method="POST", data=payload, params={"minorversion": 65})

    qb_invoice = qb_response.get("Invoice") or qb_response
    qb_invoice_id = None

    if isinstance(qb_invoice, dict):
        qb_invoice_id = qb_invoice.get("Id")

    if not qb_invoice_id:
        msg = f"QuickBooks invoice creation failed. Response: {frappe.as_json(qb_response)}"
        _mark_sales_invoice_sync_error(si, msg)
        frappe.throw(_(msg))


    si.db_set("quickbooks_id", qb_invoice_id)
    si.db_set("quickbooks_doc_number", doc_number)
    si.db_set("quickbooks_last_sync", now_datetime())
    si.db_set("quickbooks_sync_status", "Synced")
    si.db_set("quickbooks_sync_error", "")

    log_action(
        "ERPNext Invoice -> QuickBooks Created",
        {
            "sales_invoice": si.name,
            "qb_invoice_id": qb_invoice_id,
            "qb_doc_number": doc_number
        },
        entity_type="Invoice",
        entity_id=qb_invoice_id
    )

    return {"qb_invoice_id": qb_invoice_id}



# ============ PURCHASE INVOICE SYNC FUNCTIONS ============

@frappe.whitelist()
def push_purchase_invoice_to_quickbooks(purchase_invoice_name: str):
    """
    Manual push button: ERPNext Purchase Invoice -> QuickBooks Bill
    """
    try:
        pi = frappe.get_doc("Purchase Invoice", purchase_invoice_name)

        if pi.docstatus != 1:
            frappe.throw(_("Purchase Invoice must be Submitted before syncing to QuickBooks."))

        result = create_qb_bill_from_purchase_invoice(pi)

        return {
            "success": True,
            "message": f"Bill synced to QuickBooks. QB Bill ID: {result.get('qb_bill_id')}",
            "qb_bill_id": result.get("qb_bill_id")
        }

    except Exception as e:
        frappe.log_error("QuickBooks Bill Push Error", f"{purchase_invoice_name}: {str(e)}")
        return {"success": False, "error": str(e)}

def create_qb_bill_from_purchase_invoice(pi):
    """
    Purchase Invoice -> QuickBooks Bill
    With UK VAT tax handling
    """

    if getattr(pi, "quickbooks_id", None):
        return {"qb_bill_id": pi.quickbooks_id, "skipped": True}

    settings = get_settings()
    api = QuickBooksAPI()


    qb_vendor_id = frappe.db.get_value("Supplier", pi.supplier, "quickbooks_id")
    if not qb_vendor_id:
        msg = f"Supplier '{pi.supplier}' does not have QuickBooks ID. Sync suppliers first."
        _mark_purchase_invoice_sync_error(pi, msg)
        frappe.throw(_(msg))

    frappe.logger().info(f"Supplier: {pi.supplier}, QB Vendor ID: {qb_vendor_id}")

    default_tax_code = getattr(settings, 'default_tax_code', '12') or '12'

    def get_qb_tax_code(tax_rate):
        rate = float(tax_rate or 0)
        if rate >= 20:
            return "3"   
        elif rate >= 5:
            return "8"   
        elif rate > 0:
            return "10"  
        else:
            return default_tax_code  


    invoice_tax_rate = 0
    if pi.taxes:
        for tax in pi.taxes:
            if float(tax.rate or 0) > 0:
                invoice_tax_rate = float(tax.rate)
                break

    
    qb_account_id = frappe.db.get_value(
        "Account",
        [
            ["company", "=", settings.company],
            ["quickbooks_id", "!=", ""],
            ["quickbooks_id", "!=", None],
            ["account_type", "in", ["Expense Account", "Cost of Goods Sold"]]
        ],
        "quickbooks_id"
    )


    if not qb_account_id:
        frappe.throw(_("No expense account mapped to QuickBooks. Please sync accounts first via Quickbooks Setting → Sync Accounts."))

    frappe.logger().info(f"Using QB account ID: {qb_account_id} for purchase invoice {pi.name}")

    
    tax_code_id = get_qb_tax_code(invoice_tax_rate)


    lines = []
    for idx, row in enumerate(pi.items):
        line = {
            "DetailType": "AccountBasedExpenseLineDetail",
            "Amount": float(row.amount or 0),
            "Description": f"{row.item_code}: {row.description or row.item_name or ''}",
            "AccountBasedExpenseLineDetail": {
                "AccountRef": {
                    "value": qb_account_id
                },
                "TaxCodeRef": {
                    "value": tax_code_id
                }
            }
        }
        lines.append(line)

    if not lines:
        msg = "Purchase Invoice has no items. Cannot sync."
        _mark_purchase_invoice_sync_error(pi, msg)
        frappe.throw(_(msg))

    
    payload = {
        "VendorRef": {
            "value": qb_vendor_id
        },
        "TxnDate": getdate(pi.posting_date).strftime('%Y-%m-%d') if pi.posting_date else getdate(now_datetime()).strftime('%Y-%m-%d'),
        "Line": lines
    }

    # -------------------------------
    # TAX HANDLING (QB UK FORMAT)
    # QB automatically calculates VAT based on TaxCodeRef in line items
    # -------------------------------
    if invoice_tax_rate > 0:
        payload["GlobalTaxCalculation"] = "TaxExcluded"
    else:
        payload["GlobalTaxCalculation"] = "NotApplicable"

    
    if pi.name:
        payload["DocNumber"] = pi.name

    if getattr(pi, "due_date", None):
        payload["DueDate"] = getdate(pi.due_date).strftime('%Y-%m-%d')

    frappe.logger().debug(f"Bill Payload: {frappe.as_json(payload)}")

    try:
        # Create the bill
        qb_response = api.make_request("bill", method="POST", data=payload, params={"minorversion": 65})

        qb_bill = qb_response.get("Bill") or qb_response
        qb_bill_id = qb_bill.get("Id") if isinstance(qb_bill, dict) else None

        if not qb_bill_id:
            msg = f"QuickBooks bill creation failed. Response: {frappe.as_json(qb_response)}"
            _mark_purchase_invoice_sync_error(pi, msg)
            frappe.throw(_(msg))

        
        _mark_purchase_invoice_sync_success(pi, qb_bill_id, pi.name)

        log_action(
            "ERPNext Purchase Invoice -> QuickBooks Bill Created",
            {
                "purchase_invoice": pi.name,
                "qb_bill_id": qb_bill_id,
                "supplier": pi.supplier,
                "total": pi.grand_total,
                "tax_rate": invoice_tax_rate,
                "tax_code_id": tax_code_id,
                "qb_account_id": qb_account_id,
                "vendor_qb_id": qb_vendor_id
            },
            entity_type="Bill",
            entity_id=qb_bill_id
        )

        return {"qb_bill_id": qb_bill_id}

    except requests.exceptions.HTTPError as e:
        error_details = "Unknown error"
        try:
            if e.response and e.response.text:
                error_json = json.loads(e.response.text)
                fault = error_json.get("Fault", {})
                errors = fault.get("Error", [{}])
                if errors:
                    error_details = errors[0].get("Message", "Unknown error")
                    frappe.logger().error(f"QB Error: {error_details}")
                    frappe.logger().error(f"Full Error: {e.response.text}")
        except Exception:
            error_details = str(e)

        msg = f"QuickBooks API Error: {error_details}"
        _mark_purchase_invoice_sync_error(pi, msg)
        frappe.throw(_(msg))

    except Exception as e:
        error_msg = f"QuickBooks API Error: {str(e)}"
        _mark_purchase_invoice_sync_error(pi, error_msg)
        frappe.throw(_(error_msg))



def _mark_purchase_invoice_sync_success(pi, qb_bill_id=None, doc_number=None):
    """Mark purchase invoice as successfully synced - SAFE VERSION"""
    try:
        update_fields = {
            "quickbooks_last_sync": now_datetime(),
            "quickbooks_sync_error": ""
        }
        
    
        try:
            meta = frappe.get_meta("Purchase Invoice")
            if hasattr(meta, 'quickbooks_sync_status'):
                update_fields["quickbooks_sync_status"] = "Synced"
        except Exception:
            pass
        
        if qb_bill_id:
            update_fields["quickbooks_id"] = qb_bill_id
        if doc_number:
            update_fields["quickbooks_doc_number"] = doc_number
        
        
        frappe.db.set_value("Purchase Invoice", pi.name, update_fields)
        frappe.db.commit()
        
    except Exception as e:
        frappe.log_error("Mark Purchase Invoice Sync Success Error", str(e))

def _mark_purchase_invoice_sync_error(pi, error_message: str):
    """Mark purchase invoice as sync error - SAFE VERSION"""
    try:
        update_fields = {
            "quickbooks_last_sync": now_datetime(),
            "quickbooks_sync_error": error_message
        }
        
    
        try:
            meta = frappe.get_meta("Purchase Invoice")
            if hasattr(meta, 'quickbooks_sync_status'):
                update_fields["quickbooks_sync_status"] = "Error"
        except Exception:
            pass
        
        frappe.db.set_value("Purchase Invoice", pi.name, update_fields)
        frappe.db.commit()
    except Exception as e:
        frappe.log_error("Mark Purchase Invoice Sync Error", str(e))


    


# ============ BULK PUSH FOR PURCHASE INVOICES ============

@frappe.whitelist()
def bulk_push_purchase_invoices(invoice_names=None, force_sync=False):
    """
    Bulk push purchase invoices to QuickBooks
    """
    try:
        settings = get_settings()
        if not settings.is_connected:
            return {
                "success": False,
                "error": "Not connected to QuickBooks"
            }


        if isinstance(invoice_names, str):
            try:
                invoice_names = json.loads(invoice_names)
            except Exception:
                if ',' in invoice_names:
                    invoice_names = [name.strip() for name in invoice_names.split(',') if name.strip()]
                else:
                    invoice_names = [invoice_names] if invoice_names else []
        
        if not isinstance(invoice_names, list):
            invoice_names = []
        
        
        if not invoice_names:
            invoice_names = get_unsynced_purchase_invoices()
        
        if not invoice_names:
            return {
                "success": True,
                "message": "No unsynced invoices found",
                "synced": 0,
                "skipped": 0,
                "failed": 0
            }

        
        batch_size = 10
        batches = [invoice_names[i:i + batch_size] for i in range(0, len(invoice_names), batch_size)]
        
        total_success = 0
        total_skipped = 0
        total_failed = 0
        success_list = []
        failed_list = []
        skipped_list = []

        for batch_index, batch in enumerate(batches):
            frappe.publish_progress(
                batch_index * 100 / len(batches),
                title="Pushing purchase invoices to QuickBooks",
                description=f"Processing batch {batch_index + 1} of {len(batches)}"
            )
            
            for invoice_name in batch:
                try:
                    result = process_single_purchase_invoice_push(invoice_name, force_sync)
                    
                    if result["status"] == "success":
                        total_success += 1
                        success_list.append({
                            "name": invoice_name,
                            "qb_id": result.get("qb_bill_id")
                        })
                    elif result["status"] == "skipped":
                        total_skipped += 1
                        skipped_list.append({
                            "name": invoice_name,
                            "reason": result.get("reason")
                        })
                    else:
                        total_failed += 1
                        failed_list.append({
                            "name": invoice_name,
                            "error": result.get("error")
                        })
                        
                except Exception as e:
                    total_failed += 1
                    failed_list.append({
                        "name": invoice_name,
                        "error": str(e)
                    })
                    frappe.log_error(
                        title="Bulk Push Purchase Invoice Error",
                        message=f"Purchase Invoice {invoice_name}: {str(e)}"
                    )
            
    
            if batch_index < len(batches) - 1:
                import time
                time.sleep(1)

    
        log_action(
            "Bulk Purchase Invoice Push Completed",
            {
                "total_processed": len(invoice_names),
                "success": total_success,
                "skipped": total_skipped,
                "failed": total_failed,
                "timestamp": now_datetime()
            },
            entity_type="Bill",
            entity_id=f"PURCHASE_BATCH_{len(invoice_names)}"
        )

        return {
            "success": True,
            "message": f"Processed {len(invoice_names)} purchase invoices",
            "total": len(invoice_names),
            "success": total_success,
            "skipped": total_skipped,
            "failed": total_failed,
            "success_list": success_list[:20],
            "failed_list": failed_list[:20],
            "skipped_list": skipped_list[:20]
        }

    except Exception as e:
        error_msg = str(e)
        log_action(
            "Bulk Purchase Invoice Push Failed",
            {
                "error": error_msg,
                "timestamp": now_datetime()
            }
        )
        return {
            "success": False,
            "error": error_msg,
            "synced": 0,
            "skipped": 0,
            "failed": 0
        }

def get_unsynced_purchase_invoices(limit=1000):
    """
    Get all unsynced purchase invoices ready for QuickBooks
    """
    try:
        invoices = frappe.get_all(
            "Purchase Invoice",
            filters={
                "docstatus": 1,
                "quickbooks_id": ["in", ["", None]]
            },
            fields=["name"],
            limit=limit,
            order_by="posting_date desc"
        )
        return [invoice.name for invoice in invoices]
    except Exception as e:
        frappe.log_error("Get Unsynced Purchase Invoices Error", str(e))
        return []

def process_single_purchase_invoice_push(invoice_name, force_sync=False):
    """
    Process single purchase invoice push with validation
    """
    try:
    
        pi = frappe.get_doc("Purchase Invoice", invoice_name)
        
        
        if pi.docstatus != 1:
            return {
                "status": "skipped",
                "reason": "Invoice not submitted"
            }
        
    
        if pi.quickbooks_id and not force_sync:
            return {
                "status": "skipped", 
                "reason": "Already synced",
                "qb_id": pi.quickbooks_id
            }
        
    
        if not pi.supplier:
            return {
                "status": "failed",
                "error": "Supplier not specified"
            }
        
    
        qb_vendor_id = frappe.db.get_value("Supplier", pi.supplier, "quickbooks_id")
        if not qb_vendor_id:
            return {
                "status": "failed",
                "error": f"Supplier '{pi.supplier}' not synced to QuickBooks"
            }
        

        for item in pi.items:
            qb_item_id = frappe.db.get_value("Item", item.item_code, "quickbooks_id")
            if not qb_item_id:
                return {
                    "status": "failed",
                    "error": f"Item '{item.item_code}' not synced to QuickBooks"
                }
        
        
        result = create_qb_bill_from_purchase_invoice(pi)
        
        if result.get("qb_bill_id"):
            return {
                "status": "success",
                "qb_bill_id": result.get("qb_bill_id")
            }
        else:
            return {
                "status": "failed",
                "error": result.get("error", "Unknown error")
            }
            
    except Exception as e:
        return {
            "status": "failed",
            "error": str(e)
        }
    





@frappe.whitelist()
def sync_accounts():
    """Sync accounts from QuickBooks"""
    try:
        api = QuickBooksAPI()

        def process_accounts_batch(accounts_batch):
            created = 0
            updated = 0
            for qb_account in accounts_batch:
                result = create_or_update_account(qb_account)
                if result == "created":
                    created += 1
                elif result == "updated":
                    updated += 1
            return created, updated

        total_created, total_updated, total_processed = sync_with_pagination(
            api.get_accounts,
            process_accounts_batch,
            "Account"
        )

        log_action(
            "Accounts Synced",
            {
                "created": total_created,
                "updated": total_updated,
                "processed": total_processed,
                "timestamp": now_datetime()
            }
        )

        return {
            "success": True,
            "message": f"Synced {total_created} new, {total_updated} updated accounts",
            "created": total_created,
            "updated": total_updated,
            "processed": total_processed
        }

    except Exception as e:
        error_msg = str(e)
        log_action(
            "Account Sync Failed",
            {
                "error": error_msg,
                "timestamp": now_datetime()
            }
        )
        return {
            "success": False,
            "error": error_msg
        }
    

def create_or_update_account(qb_account):
    """Map QuickBooks accounts to ERPNext accounts"""
    try:
        account_id = qb_account.get('Id')
        account_name = qb_account.get('Name')
        account_type = qb_account.get('AccountType', '')

        if not account_id or not account_name:
            return "skipped"

        company = get_settings().company
        if not company:
            return "skipped"

        type_mapping = {
            'Bank': ['Bank', 'Cash'],
            'Accounts Receivable': ['Receivable'],
            'Accounts Payable': ['Payable'],
            'Credit Card': ['Bank'],
            'Income': ['Income Account'],
            'Expense': ['Expense Account'],
            'Cost of Goods Sold': ['Cost of Goods Sold'],
            'Other Income': ['Income Account'],
            'Other Expense': ['Expense Account'],
            'Equity': ['Equity'],
            'Fixed Asset': ['Fixed Asset'],
            'Other Current Asset': ['Current Asset'],
            'Other Asset': ['Current Asset', 'Stock'],
            'Other Current Liability': ['Current Liability'],
            'Long Term Liability': ['Current Liability']
        }

        erp_account_types = type_mapping.get(account_type, [])
        matched_account = None

        for erp_type in erp_account_types:
            type_accounts = frappe.get_all(
                "Account",
                filters={
                    "account_type": erp_type,
                    "company": company,
                    "is_group": 0,
                    "quickbooks_id": ["in", ["", None]]
                },
                fields=["name", "account_name", "account_type"],
                limit=10
            )

            if type_accounts:
                for acc in type_accounts:
                    if not acc.get('quickbooks_id'):
                        matched_account = acc
                        break
                if matched_account:
                    break

        if matched_account:
            account = frappe.get_doc("Account", matched_account.name)
            account.quickbooks_id = account_id

            qb_info = f"QB: {account_name} ({account_type})"
            if hasattr(account, "remarks"):
                if not account.remarks:
                    account.remarks = qb_info
                elif qb_info not in account.remarks:
                    account.remarks = f"{account.remarks} | {qb_info}"

            account.save(ignore_permissions=True)
            frappe.db.commit()

            frappe.logger().info(
                f"Mapped: QB '{account_name}' ({account_type}, ID: {account_id}) -> "
                f"ERP '{account.account_name}' ({account.account_type})"
            )
            return "updated"
        else:
            frappe.logger().debug(
                f"No match for QB Account: '{account_name}' (Type: {account_type}, ID: {account_id})"
            )
            return "skipped"

    except Exception as e:
        frappe.log_error(
            title="Account Mapping Error",
            message=f"QB Account: {account_name}, Error: {str(e)}"
        )
        return "error"


@frappe.whitelist()
def sync_bill_payments():
    """Sync bill payments from QuickBooks to ERPNext - ENHANCED"""
    try:
        api = QuickBooksAPI()
        created = 0
        skipped = 0
        errors = 0
        submitted = 0
        drafts = 0
        
    
        start_position = 1
        max_results = 500 
        
        # Log start of sync
        log_action(
            "Bill Payment Sync Started",
            {
                "timestamp": now_datetime(),
                "max_results_per_page": max_results
            }
        )
        
        while True:
            frappe.logger().info(f"Fetching bill payments batch: start_position={start_position}")
            
            response = api.get_bill_payments(start_position, max_results)
            query_response = response.get('QueryResponse', {})
            bill_payments = query_response.get('BillPayment', [])
            
            if not bill_payments:
                frappe.logger().info("No more bill payments found")
                break
            
            frappe.logger().info(f"Processing {len(bill_payments)} bill payments")
            
            # Process batch
            for qb_bill_payment in bill_payments:
                try:
                    result = create_or_update_bill_payment_entry(qb_bill_payment)
                    
                    if result == "created":
                        created += 1
                        
                    elif result == "skipped":
                        skipped += 1
                    elif result == "error":
                        errors += 1
                        
                except Exception as e:
                    errors += 1
                    frappe.log_error(
                        f"Bill Payment Processing Error - ID: {qb_bill_payment.get('Id')}",
                        str(e)
                    )
            
            if len(bill_payments) < max_results:
                break
            
            start_position += max_results
            
            import time
            time.sleep(1)
        
        recent_payments = frappe.get_all(
            "Payment Entry",
            filters={
                "quickbooks_bill_payment_id": ["!=", ""],
                "creation": [">", frappe.utils.add_to_date(now_datetime(), hours=-1)]
            },
            fields=["name", "docstatus"]
        )
        
        submitted = sum(1 for p in recent_payments if p.docstatus == 1)
        drafts = sum(1 for p in recent_payments if p.docstatus == 0)
        
        log_action(
            "Bill Payments Synced - COMPLETE",
            {
                "created": created,
                "skipped": skipped,
                "errors": errors,
                "submitted": submitted,
                "drafts": drafts,
                "timestamp": now_datetime(),
                "total_processed": created + skipped + errors
            },
            entity_type="", 
            entity_id=f"BILL_PAYMENTS_SYNC_{now_datetime().strftime('%Y%m%d_%H%M%S')}"
        )
        
        message_parts = []
        if created > 0:
            message_parts.append(f"Created {created} payment entries")
        if submitted > 0:
            message_parts.append(f"{submitted} auto-submitted")
        if drafts > 0:
            message_parts.append(f"{drafts} kept as draft (need manual allocation)")
        if skipped > 0:
            message_parts.append(f"{skipped} skipped (already synced)")
        if errors > 0:
            message_parts.append(f"{errors} errors")
        
        full_message = f"Bill payment sync completed: " + ", ".join(message_parts)
        
        return {
            "success": True,
            "message": full_message,
            "created": created,
            "submitted": submitted,
            "drafts": drafts,
            "skipped": skipped,
            "errors": errors
        }
        
    except Exception as e:
        error_msg = str(e)
        log_action(
            "Bill Payment Sync Failed",
            {
                "error": error_msg,
                "timestamp": now_datetime()
            }
        )
        return {
            "success": False,
            "error": error_msg,
            "created": 0,
            "submitted": 0,
            "drafts": 0,
            "skipped": 0,
            "errors": 1
        }

def create_or_update_bill_payment_entry(qb_bill_payment):
    """
    Create or update payment entry for bill payment in ERPNext - CORRECTED ACCOUNTS
    """
    try:
        payment_id = qb_bill_payment.get("Id")
        if not payment_id:
            return "skipped"

        if frappe.db.exists(
            "Payment Entry",
            {"quickbooks_bill_payment_id": payment_id}
        ):
            return "skipped"

        total_amount = flt(qb_bill_payment.get("TotalAmt", 0))
        if total_amount <= 0:
            return "skipped"

        # -----------------------------
        # SUPPLIER/VENDOR
        # -----------------------------
        vendor_ref = qb_bill_payment.get("VendorRef", {})
        vendor_id = vendor_ref.get("value")
        vendor_name = vendor_ref.get("name", "")
        
        if not vendor_id:
            return "skipped"

    
        supplier = frappe.db.get_value(
            "Supplier",
            {"quickbooks_id": vendor_id},
            "name"
        )
        
        if not supplier:
            
            if vendor_name:
                supplier = frappe.db.get_value(
                    "Supplier",
                    {"supplier_name": vendor_name},
                    "name"
                )
            
            if not supplier:
                frappe.logger().debug(f"Supplier not found for QB Vendor: {vendor_name} (ID: {vendor_id})")
                return "skipped"

        settings = get_settings()
        company = settings.company
        
        if not company:
            return "skipped"

        payable_account = frappe.db.get_value(
            "Company",
            company,
            "default_payable_account"
        )

        bank_account = settings.default_account
        if not bank_account:
            bank_account = frappe.db.get_value(
                "Company",
                company,
                "default_bank_account"
            )
        
        if not payable_account:
            frappe.throw(f"Default Payable Account not set for company: {company}")
        
        if not bank_account:
            frappe.throw(f"Default Bank Account not set. Please set in QuickBooks Settings or Company.")

        payable_account_to_use = payable_account
        
        # -----------------------------
        # FIND AND LINK PURCHASE INVOICES
        # -----------------------------
        references = []
        linked_bills_info = []
        
        lines = qb_bill_payment.get("Line", [])
        if not isinstance(lines, list):
            lines = [lines] if lines else []
        
        for line in lines:
            line_amount = flt(line.get("Amount", 0))
            if line_amount <= 0:
                continue
            
            linked_txns = line.get("LinkedTxn", [])
            if not isinstance(linked_txns, list):
                linked_txns = [linked_txns] if linked_txns else []
            
            for txn in linked_txns:
                if isinstance(txn, dict) and txn.get("TxnType") == "Bill":
                    qb_bill_id = txn.get("TxnId")
                    
                    if qb_bill_id:
                        purchase_invoice = frappe.db.get_value(
                            "Purchase Invoice",
                            {"quickbooks_id": qb_bill_id},
                            ["name", "outstanding_amount", "grand_total", "credit_to"],
                            as_dict=True
                        )
                        
                        if purchase_invoice:
                            frappe.logger().info(f"Found Purchase Invoice: {purchase_invoice.name}")
                            
                            if purchase_invoice.credit_to:
                                payable_account_to_use = purchase_invoice.credit_to
                                frappe.logger().info(f"Using PI's payable account: {payable_account_to_use}")
                            
                            
                            pi_doc = frappe.get_doc("Purchase Invoice", purchase_invoice.name)
                            
                            outstanding = flt(purchase_invoice.outstanding_amount) if purchase_invoice.outstanding_amount else flt(pi_doc.grand_total)
                            
                            allocate_amount = min(line_amount, outstanding)
                            
                            references.append({
                                "reference_doctype": "Purchase Invoice",
                                "reference_name": purchase_invoice.name,
                                "allocated_amount": allocate_amount,
                                "outstanding_amount": outstanding,
                                "total_amount": flt(pi_doc.grand_total),
                                "payable_account": payable_account_to_use
                            })
                            
                            linked_bills_info.append({
                                "qb_bill_id": qb_bill_id,
                                "purchase_invoice": purchase_invoice.name,
                                "amount": allocate_amount,
                                "outstanding": outstanding,
                                "payable_account": payable_account_to_use
                            })
                            
                            line_amount -= allocate_amount
                            
                            frappe.logger().info(
                                f"Linked payment {allocate_amount} to PI: {purchase_invoice.name} "
                                f"(QB Bill: {qb_bill_id}, Payable: {payable_account_to_use})"
                            )
                        else:
                            frappe.logger().warning(
                                f"No Purchase Invoice found with QB Bill ID: {qb_bill_id}"
                            )
        
        if not references:
            frappe.logger().warning(
                f"No purchase invoices linked to QB Bill Payment {payment_id}. "
                f"Cannot create payment entry."
            )
            return "skipped"
        
        # Dates
        posting_date = qb_bill_payment.get("TxnDate")
        if not posting_date:
            posting_date = now_datetime().date()
        
        # Reference number
        ref_no = qb_bill_payment.get("PaymentRefNum") or f"QB-BP-{payment_id}"

        # Payment method
        payment_method_ref = qb_bill_payment.get("PaymentMethodRef", {})
        payment_method = payment_method_ref.get("name", "Check")
        
        # Mode of Payment
        mode_of_payment = get_or_create_mode_of_payment(payment_method, company)

        # -----------------------------
        # CREATE PAYMENT ENTRY - CORRECT ACCOUNTS
        # -----------------------------
        pe_data = {
            "doctype": "Payment Entry",
            "payment_type": "Pay",  
            "party_type": "Supplier",
            "party": supplier,
            "company": company,
            "paid_from": bank_account,     
            "paid_to": payable_account_to_use,  
            "paid_amount": total_amount,
            "received_amount": total_amount,
            "posting_date": posting_date,
            "mode_of_payment": mode_of_payment,
            "reference_no": ref_no,
            "reference_date": posting_date,
            "remarks": f"QuickBooks Bill Payment {ref_no} - {payment_method}",
            "quickbooks_bill_payment_id": payment_id,
        }
        
        pe = frappe.get_doc(pe_data)

        for ref in references:
            pe.append("references", {
                "reference_doctype": ref["reference_doctype"],
                "reference_name": ref["reference_name"],
                "allocated_amount": ref["allocated_amount"],
                "outstanding_amount": ref["outstanding_amount"],
                "total_amount": ref["total_amount"]
            })
        
        pe.insert(ignore_permissions=True)
        
        # -----------------------------
        # AUTO-SUBMIT
        # -----------------------------
        try:
            if pe.references and len(pe.references) > 0:
                pe.submit()
                status = "Submitted"
                frappe.logger().info(f"Payment Entry {pe.name} submitted successfully")
                
   
                for ref in pe.references:
                    pi = frappe.get_doc("Purchase Invoice", ref.reference_name)
                    new_outstanding = flt(pi.outstanding_amount) - flt(ref.allocated_amount)
                    frappe.logger().info(
                        f"Updated PI {ref.reference_name} outstanding: "
                        f"{pi.outstanding_amount} → {new_outstanding}"
                    )
                    
            else:
                status = "Draft (No references)"
                pe.db_set("remarks", f"{pe.remarks} [UNALLOCATED]")
                frappe.logger().warning(f"Payment Entry {pe.name} kept as draft")
                
        except Exception as submit_error:
            status = "Draft (Submit Failed)"
            error_msg = str(submit_error)
            pe.db_set("remarks", f"{pe.remarks} [SUBMIT FAILED: {error_msg}]")
            frappe.logger().error(f"Failed to submit Payment Entry {pe.name}: {error_msg}")
        
        frappe.db.commit()
        
        log_action(
            "Bill Payment Created",
            {
                "payment_entry": pe.name,
                "qb_payment_id": payment_id,
                "supplier": supplier,
                "amount": total_amount,
                "status": status,
                "references_count": len(references),
                "linked_bills": linked_bills_info,
                "paid_from": bank_account,
                "paid_to": payable_account_to_use,
                "mode_of_payment": mode_of_payment
            },
            entity_type="Payment Entry",
            entity_id=pe.name
        )

        return "created"

    except Exception as e:
        frappe.log_error(
            "QuickBooks Bill Payment Processing Error",
            f"Bill Payment ID {qb_bill_payment.get('Id')}: {str(e)}\nTraceback: {frappe.get_traceback()}"
        )
        return "error"    


def get_or_create_mode_of_payment(qb_payment_method, company):
    """
    Get existing Mode of Payment or create if doesn't exist - SAFE VERSION
    """
    try:
        # Default mapping
        erp_mode_name = "Bank"
        
        if qb_payment_method:
            qb_payment_method = str(qb_payment_method).strip()
            
            # Simple mapping
            if "cash" in qb_payment_method.lower():
                erp_mode_name = "Cash"
            elif "check" in qb_payment_method.lower() or "cheque" in qb_payment_method.lower():
                erp_mode_name = "Cheque"  
            elif "credit" in qb_payment_method.lower():
                erp_mode_name = "Credit Card"
            elif "debit" in qb_payment_method.lower():
                erp_mode_name = "Debit Card"
            elif "paypal" in qb_payment_method.lower():
                erp_mode_name = "PayPal"
        
     
        mode_exists = frappe.db.exists("Mode of Payment", erp_mode_name)
        
        if not mode_exists:
          
            alternative_names = {
                "Cheque": ["Check", "Bank"],
                "Check": ["Cheque", "Bank"],
                "Bank": ["Cash", "Cheque"],
                "Cash": ["Bank"]
            }
            
            if erp_mode_name in alternative_names:
                for alt_name in alternative_names[erp_mode_name]:
                    if frappe.db.exists("Mode of Payment", alt_name):
                        return alt_name
            
            
            if frappe.db.exists("Mode of Payment", "Cash"):
                return "Cash"
            
  
            try:
                mode_doc = frappe.get_doc({
                    "doctype": "Mode of Payment",
                    "mode_of_payment": erp_mode_name,
                    "enabled": 1,
                    "type": "Bank" if erp_mode_name in ["Cheque", "Check", "Bank"] else "Cash"
                })
                mode_doc.insert(ignore_permissions=True)
                frappe.db.commit()
            except Exception:
              
                erp_mode_name = "Cash"
        
        return erp_mode_name
        
    except Exception as e:
        frappe.log_error(
            "Mode of Payment Error",
            f"Payment Method: {qb_payment_method}, Error: {str(e)}"
        )
        return "Cash" 
    


def extract_erp_taxes(doc):
    """
    Extract ERPNext tax rows safely
    Works for Sales Invoice and Purchase Invoice
    """
    taxes = []
    if hasattr(doc, "taxes") and doc.taxes:
        for t in doc.taxes:
            if flt(t.tax_amount) != 0 and t.account_head:
                taxes.append({
                    "account": t.account_head,
                    "amount": flt(t.tax_amount),
                    "rate": flt(t.rate),
                    "description": t.description or t.account_head
                })
    return taxes


def map_erp_tax_to_qb_taxcode(erp_tax_account):
    """
    Map ERP tax account → QuickBooks TaxRate → TaxCode
    Uses Account.quickbooks_id as TaxRate ID
    """
    qb_taxrate_id = frappe.db.get_value(
        "Account",
        erp_tax_account,
        "quickbooks_id"
    )

    if not qb_taxrate_id:
        frappe.throw(
            f"Tax Account '{erp_tax_account}' not mapped to QuickBooks "
            f"(missing quickbooks_id on Account)."
        )


    return {
        "TaxRateRef": {
            "value": str(qb_taxrate_id)
        }
    }


def build_qb_tax_detail(doc):
    """
    Build QuickBooks TxnTaxDetail object from ERP taxes
    """
    erp_taxes = extract_erp_taxes(doc)

    if not erp_taxes:
        return None

    tax_lines = []
    total_tax = 0

    for tax in erp_taxes:
        tax_code = map_erp_tax_to_qb_taxcode(tax["account"])
        total_tax += tax["amount"]

        tax_lines.append({
            "Amount": float(tax["amount"]),
            "DetailType": "TaxLineDetail",
            "TaxLineDetail": {
                "TaxRateRef": tax_code["TaxRateRef"],
                "PercentBased": True,
                "TaxPercent": float(tax["rate"])
            }
        })

    return {
        "TxnTaxCodeRef": {"value": "TAX"},  
        "TotalTax": float(total_tax),
        "TaxLine": tax_lines
    }













# ============ ENHANCED BULK PUSH FUNCTIONS ============

@frappe.whitelist()
def bulk_push_sales_invoices(invoice_names=None, force_sync=False):
    """
    Enhanced bulk push with better error handling and reporting
    """
    try:
        settings = get_settings()
        if not settings.is_connected:
            return {
                "success": False,
                "error": "Not connected to QuickBooks"
            }

       
        if isinstance(invoice_names, str):
            try:
              
                invoice_names = json.loads(invoice_names)
            except Exception:
             
                if ',' in invoice_names:
                    invoice_names = [name.strip() for name in invoice_names.split(',') if name.strip()]
                else:
              
                    invoice_names = [invoice_names] if invoice_names else []
        
    
        if not isinstance(invoice_names, list):
            invoice_names = []
        
      
        if not invoice_names:
            invoice_names = get_unsynced_invoices()
        
        if not invoice_names:
            return {
                "success": True,
                "message": "No unsynced invoices found",
                "synced": 0,
                "skipped": 0,
                "failed": 0
            }

        batch_size = 10
        batches = [invoice_names[i:i + batch_size] for i in range(0, len(invoice_names), batch_size)]
        
        total_success = 0
        total_skipped = 0
        total_failed = 0
        success_list = []
        failed_list = []
        skipped_list = []

        for batch_index, batch in enumerate(batches):
            frappe.publish_progress(
                batch_index * 100 / len(batches),
                title="Pushing invoices to QuickBooks",
                description=f"Processing batch {batch_index + 1} of {len(batches)}"
            )
            
            for invoice_name in batch:
                try:
                    result = process_single_invoice_push(invoice_name, force_sync)
                    
                    if result["status"] == "success":
                        total_success += 1
                        success_list.append({
                            "name": invoice_name,
                            "qb_id": result.get("qb_invoice_id")
                        })
                    elif result["status"] == "skipped":
                        total_skipped += 1
                        skipped_list.append({
                            "name": invoice_name,
                            "reason": result.get("reason")
                        })
                    else:
                        total_failed += 1
                        failed_list.append({
                            "name": invoice_name,
                            "error": result.get("error")
                        })
                        
                except Exception as e:
                    total_failed += 1
                    failed_list.append({
                        "name": invoice_name,
                        "error": str(e)
                    })
                    frappe.log_error(
                        title="Bulk Push Error",
                        message=f"Invoice {invoice_name}: {str(e)}"
                    )
            
        
            if batch_index < len(batches) - 1:
                import time
                time.sleep(1)

   
        log_action(
            "Bulk Invoice Push Completed",
            {
                "total_processed": len(invoice_names),
                "success": total_success,
                "skipped": total_skipped,
                "failed": total_failed,
                "timestamp": now_datetime()
            },
            entity_type="Invoice",
            entity_id=f"BATCH_{len(invoice_names)}"
        )

        return {
            "success": True,
            "message": f"Processed {len(invoice_names)} invoices",
            "total": len(invoice_names),
            "success": total_success,
            "skipped": total_skipped,
            "failed": total_failed,
            "success_list": success_list[:20], 
            "failed_list": failed_list[:20],
            "skipped_list": skipped_list[:20]
        }

    except Exception as e:
        error_msg = str(e)
        log_action(
            "Bulk Invoice Push Failed",
            {
                "error": error_msg,
                "timestamp": now_datetime()
            }
        )
        return {
            "success": False,
            "error": error_msg,
            "synced": 0,
            "skipped": 0,
            "failed": 0
        }

def get_unsynced_invoices(limit=1000):
    """
    Get all unsynced invoices ready for QuickBooks
    """
    try:
        invoices = frappe.get_all(
            "Sales Invoice",
            filters={
                "docstatus": 1,
                "quickbooks_id": ["in", ["", None]]
            },
            fields=["name"],
            limit=limit,
            order_by="posting_date desc"
        )
        return [invoice.name for invoice in invoices]
    except Exception as e:
        frappe.log_error("Get Unsynced Invoices Error", str(e))
        return []

def process_single_invoice_push(invoice_name, force_sync=False):
    """
    Process single invoice push with validation
    """
    try:
        # Get invoice
        si = frappe.get_doc("Sales Invoice", invoice_name)
        
     
        if si.docstatus != 1:
            return {
                "status": "skipped",
                "reason": "Invoice not submitted"
            }
      
        if si.quickbooks_id and not force_sync:
            return {
                "status": "skipped", 
                "reason": "Already synced",
                "qb_id": si.quickbooks_id
            }
        
   
        if not si.customer:
            return {
                "status": "failed",
                "error": "Customer not specified"
            }
        
        
        qb_customer_id = frappe.db.get_value("Customer", si.customer, "quickbooks_id")
        if not qb_customer_id:
            return {
                "status": "failed",
                "error": f"Customer '{si.customer}' not synced to QuickBooks"
            }
        
    
        for item in si.items:
            qb_item_id = frappe.db.get_value("Item", item.item_code, "quickbooks_id")
            if not qb_item_id:
                return {
                    "status": "failed",
                    "error": f"Item '{item.item_code}' not synced to QuickBooks"
                }
        

        result = create_qb_invoice_from_sales_invoice(si)
        
        if result.get("qb_invoice_id"):
            return {
                "status": "success",
                "qb_invoice_id": result.get("qb_invoice_id")
            }
        else:
            return {
                "status": "failed",
                "error": result.get("error", "Unknown error")
            }
            
    except Exception as e:
        return {
            "status": "failed",
            "error": str(e)
        }

@frappe.whitelist()
def get_unsynced_invoice_count():
    """
    Get count of unsynced invoices for UI display
    """
    try:
        count = frappe.db.count("Sales Invoice", {
            "docstatus": 1,
            "quickbooks_id": ["in", ["", None]]
        })
        return {
            "success": True,
            "count": count
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "count": 0
        }

@frappe.whitelist()
def retry_failed_syncs():
    """
    Retry invoices that failed to sync
    """
    try:
      
        failed_invoices = frappe.get_all(
            "Sales Invoice",
            filters={
                "docstatus": 1,
                "quickbooks_sync_status": "Error",
                "quickbooks_sync_error": ["!=", ""]
            },
            fields=["name", "quickbooks_sync_error"],
            limit=100
        )
        
        if not failed_invoices:
            return {
                "success": True,
                "message": "No failed invoices found",
                "retried": 0
            }
        
        retried = 0
        for invoice in failed_invoices:
            try:
                si = frappe.get_doc("Sales Invoice", invoice.name)
                
               
                si.db_set("quickbooks_sync_error", "")
                si.db_set("quickbooks_sync_status", "Pending")
                
                # Retry push
                result = create_qb_invoice_from_sales_invoice(si)
                if result.get("qb_invoice_id"):
                    retried += 1
                    
            except Exception as e:
                frappe.log_error(
                    f"Retry Failed for {invoice.name}",
                    str(e)
                )
        
        return {
            "success": True,
            "message": f"Retried {retried} of {len(failed_invoices)} failed invoices",
            "retried": retried,
            "total": len(failed_invoices)
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }



def _mark_sales_invoice_sync_error(si, error_message: str):
    try:
        si.db_set("quickbooks_last_sync", now_datetime())
        si.db_set("quickbooks_sync_status", "Error")
        si.db_set("quickbooks_sync_error", error_message)
    except Exception:
        pass

def create_or_update_item(qb_item):
    """Create or update item in ERPNext"""
    try:
        item_id = qb_item.get('Id')
        item_name = qb_item.get('Name')
        if not item_id or not item_name:
            return "skipped"
        
        settings = get_settings()

      
        existing = frappe.db.get_value(
            "Item",
            {"quickbooks_id": item_id},
            ["name", "item_name"]
        )

        item_data = {
            "item_code": item_name[:140], 
            "item_name": item_name,
            "description": qb_item.get('Description', '')[:255],
            "item_group": DEFAULT_ITEM_GROUP,
            "stock_uom": DEFAULT_STOCK_UOM,
            "is_stock_item": 0,
            "quickbooks_id": item_id,
            "disabled": qb_item.get('Active') == False,
            "custom_company": settings.company
        }

        item_type = qb_item.get('Type', '').lower()
        if item_type == 'inventory':
            item_data["is_stock_item"] = 1
        elif item_type == 'service':
            item_data["is_stock_item"] = 0

        unit_price = qb_item.get('UnitPrice')
        if unit_price is not None:
            item_data["standard_rate"] = float(unit_price)

        if getattr(settings, 'default_account', None):
            item_data["income_account"] = settings.default_account

        if existing:
            item = frappe.get_doc("Item", existing[0])
            item.update(item_data)
            item.save(ignore_permissions=True)
            return "updated"
        else:
            item = frappe.get_doc({
                "doctype": "Item",
                **item_data
            })
            item.insert(ignore_permissions=True)
            return "created"

    except Exception as e:
        frappe.log_error(
            title="Item Processing Error",
            message=f"Item ID: {qb_item.get('Id')}, Error: {str(e)}"
        )
        return "error"

# ============ PAYMENT ENTRY SYNC FUNCTIONS ============

@frappe.whitelist()
def sync_payments():
    """Sync payment entries from QuickBooks to ERPNext"""
    try:
        api = QuickBooksAPI()
        created = 0
        updated = 0
        skipped = 0
        errors = 0
        qb_payment_ids = set()

        start_position = 1
        max_results = 1000

        while True:
            response = api.get_payments(start_position, max_results)
            query_response = response.get('QueryResponse', {})
            payments = query_response.get('Payment', [])

            if not payments:
                break

            for qb_payment in payments:
                qb_payment_ids.add(str(qb_payment.get("Id", "")))
                result = create_or_update_payment_entry(qb_payment)
                if result == "created":
                    created += 1
                elif result == "updated":
                    updated += 1
                elif result == "skipped":
                    skipped += 1
                elif result == "error":
                    errors += 1

            if len(payments) < max_results:
                break

            start_position += max_results

        # QB mein deleted payments ko ERP mein flag karo
        try:
            erp_payments = frappe.get_all(
                "Payment Entry",
                filters={
                    "quickbooks_payment_id": ["!=", ""],
                    "docstatus": 1
                },
                fields=["name", "quickbooks_payment_id"]
            )
            for pe in erp_payments:
                if str(pe.quickbooks_payment_id) not in qb_payment_ids:
                    frappe.log_error(
                        "QB Payment Deleted",
                        f"Payment {pe.name} (QB ID: {pe.quickbooks_payment_id}) no longer exists in QB. Please review manually."
                    )
        except Exception:
            pass

        log_action(
            "Payments Synced",
            {
                "created": created,
                "updated": updated,
                "skipped": skipped,
                "errors": errors,
                "timestamp": now_datetime()
            }
        )

        return {
            "success": True,
            "message": f"Synced {created} new, {updated} updated payments",
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "errors": errors
        }

    except Exception as e:
        error_msg = str(e)
        log_action(
            "Payment Sync Failed",
            {
                "error": error_msg,
                "timestamp": now_datetime()
            }
        )
        return {
            "success": False,
            "error": error_msg
        }

def create_or_update_payment_entry(qb_payment):

    try:
        payment_id = qb_payment.get("Id")
        if not payment_id:
            return "skipped"

        
        if frappe.db.exists(
            "Payment Entry",
            {"quickbooks_payment_id": payment_id}
        ):
            return "skipped"

        total_amount = flt(qb_payment.get("TotalAmt", 0))
        if total_amount <= 0:
            return "skipped"

        # -----------------------------
        # CUSTOMER
        # -----------------------------
        customer_id = qb_payment.get("CustomerRef", {}).get("value")
        customer = frappe.db.get_value(
            "Customer",
            {"quickbooks_id": customer_id},
            "name"
        )
        if not customer:
            return "skipped"

        settings = get_settings()

        receivable_account = frappe.db.get_value(
            "Company",
            settings.company,
            "default_receivable_account"
        )

        bank_account = settings.default_account

        if not receivable_account or not bank_account:
            frappe.throw("Missing default accounts")

        posting_date = qb_payment.get("TxnDate")
        ref_no = qb_payment.get("PaymentRefNum") or f"QB-{payment_id}"

        # -----------------------------
        # CREATE PAYMENT ENTRY
        # -----------------------------
        pe = frappe.get_doc({
            "doctype": "Payment Entry",
            "payment_type": "Receive",
            "party_type": "Customer",
            "party": customer,
            "paid_from": receivable_account,
            "paid_to": bank_account,
            "paid_amount": total_amount,
            "received_amount": total_amount,
            "posting_date": posting_date,
            "reference_no": ref_no,
            "reference_date": posting_date,
            "quickbooks_payment_id": payment_id,
            "remarks": f"QuickBooks Payment {ref_no}",
            "references": []
        })

        # -----------------------------
        # ALLOCATE AGAINST INVOICE
        # -----------------------------
        for line in qb_payment.get("Line", []):
            for txn in line.get("LinkedTxn", []):
                if txn.get("TxnType") == "Invoice":
                    qb_invoice_id = txn.get("TxnId")

                    erp_invoice = frappe.db.get_value(
                        "Sales Invoice",
                        {"quickbooks_id": qb_invoice_id},
                        "name"
                    )

                    if erp_invoice:
                        pe.append("references", {
                            "reference_doctype": "Sales Invoice",
                            "reference_name": erp_invoice,
                            "allocated_amount": flt(line.get("Amount"))
                        })

        pe.insert(ignore_permissions=True)
        pe.submit()

        return "created"

    except Exception as e:
        frappe.log_error(
            "QuickBooks Payment Processing Error",
            f"Payment ID {qb_payment.get('Id')}: {str(e)}"
        )
        return "error"


def map_payment_method(qb_payment_method):
    """Map QuickBooks payment method to ERPNext mode of payment"""
    payment_method_map = {
        "Cash": "Cash",
        "Check": "Check",
        "Cheque": "Check",
        "Credit Card": "Credit Card",
        "CreditCard": "Credit Card",
        "Bank Transfer": "Bank Transfer",
        "BankTransfer": "Bank Transfer",
        "Debit Card": "Debit Card",
        "DebitCard": "Debit Card",
        "PayPal": "PayPal",
        "ACH": "Bank Transfer",
        "Direct Debit": "Direct Debit"
    }
    
    
    if not qb_payment_method:
        return "Check"
    
    qb_payment_method = str(qb_payment_method).strip()
    

    if qb_payment_method in payment_method_map:
        return payment_method_map[qb_payment_method]
    

    for qb_method, erp_method in payment_method_map.items():
        if qb_payment_method.lower() == qb_method.lower():
            return erp_method
    
    
    qb_lower = qb_payment_method.lower()
    if "check" in qb_lower or "cheque" in qb_lower:
        return "Check"
    elif "cash" in qb_lower:
        return "Cash"
    elif "credit" in qb_lower:
        return "Credit Card"
    elif "debit" in qb_lower:
        return "Debit Card"
    elif "bank" in qb_lower or "transfer" in qb_lower or "ach" in qb_lower:
        return "Bank Transfer"
    elif "paypal" in qb_lower:
        return "PayPal"
    
    return "Check"  

@frappe.whitelist()
def sync_recent_payments(days=7):
    """Sync payments from last N days"""
    try:
        from datetime import datetime, timedelta
        
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)
        
        api = QuickBooksAPI()
        created = 0
        skipped = 0
        
        start_position = 1
        max_results = 1000
        
        while True:
            response = api.get_payments_by_date_range(
                start_date.strftime('%Y-%m-%d'),
                end_date.strftime('%Y-%m-%d'),
                start_position,
                max_results
            )
            
            query_response = response.get('QueryResponse', {})
            payments = query_response.get('Payment', [])
            
            if not payments:
                break
            
            for qb_payment in payments:
                result = create_or_update_payment_entry(qb_payment)
                if result == "created":
                    created += 1
                elif result == "skipped":
                    skipped += 1
            
            if len(payments) < max_results:
                break
            
            start_position += max_results
        
        return {
            "success": True,
            "message": f"Synced {created} payments from last {days} days",
            "created": created,
            "skipped": skipped
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

# ============ TASK 6: Logging ============

def log_action(action, details, status="Info", settings_name=None, entity_type=None, entity_id=None, error=None):
    """Log actions to Quickbooks Sync Log"""
    try:
        if not settings_name:
            try:
                settings = frappe.get_all(
                    "Quickbooks Setting",
                    limit=1,
                    ignore_permissions=True
                )
                settings_name = settings[0].name if settings else None
            except Exception:
                settings_name = None

        log_entry = {
            "doctype": "Quickbooks Sync Log",
            "action": action,
            "details": json.dumps(details, indent=2, default=str),
            "timestamp": now_datetime(),
            "status": status,
            "quickbook_settings": settings_name,
            "entity_type": entity_type or "",
            "entity_id": entity_id,
            "error": error
        }

        log_doc = frappe.get_doc(log_entry)
        log_doc.insert(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(title="QuickBooks Logging Error", message=str(e))

# ============ TASK 7: Scheduled Jobs ============

def scheduled_token_refresh():
    """Auto refresh tokens"""
    try:
        settings = get_settings()
        if settings.is_connected and settings.token_expiry:
            expiry_time = get_datetime(settings.token_expiry)
            current_time = now_datetime()
            
            # Refresh if expires in less than 1 hour
            if (expiry_time - current_time).total_seconds() < 3600:
                refresh_result = refresh_tokens()
                if refresh_result.get("success"):
                    log_action("Scheduled Token Refresh", {"success": True})
                else:
                    log_action("Scheduled Token Refresh Failed", refresh_result)
    except Exception as e:
        log_action(
            "Scheduled Refresh Error",
            {"error": str(e)}
        )

def scheduled_sync():
    """Auto sync data"""
    try:
        settings = get_settings()
        if settings.is_connected and getattr(settings, 'enable_auto_sync', False):
            sync_result = sync_all()
            log_action(
                "Scheduled Sync Completed",
                sync_result
            )
    except Exception as e:
        log_action(
            "Scheduled Sync Error",
            {"error": str(e)}
        )

# ============ TASK 8: Utility Functions ============

@frappe.whitelist()
def get_sync_status():
    """Get sync status"""
    try:
        settings = get_settings()
        return {
            "success": True,
            "is_connected": settings.is_connected,
            "last_sync": settings.last_sync,
            "company_name": settings.company_name,
            "realm_id": settings.realm_id_company_id
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@frappe.whitelist()
def clear_sync_logs(settings_name=None):
    settings = frappe.get_doc("Quickbooks Setting", settings_name) if settings_name else get_settings()
    frappe.db.delete("Quickbooks Sync Log", {"quickbooks_setting": settings.name})
    frappe.db.commit()
    return {"success": True, "message": "Sync logs cleared"}

@frappe.whitelist()
def get_sync_settings():
    """Get sync configuration"""
    try:
        settings = get_settings()
        return {
            "success": True,
            "sync_customers": getattr(settings, 'sync_customers', False),
            "sync_items": getattr(settings, 'sync_items', False),
            "sync_payments": getattr(settings, 'sync_payments', True),
            "enable_auto_sync": getattr(settings, 'enable_auto_sync', False)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

def render_error_page(title, message):
    """Render error page HTML"""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
    <title>QuickBooks Connection Error</title>
    <style>
    body {{
        font-family: Arial, sans-serif;
        text-align: center;
        padding: 50px;
        background-color: #f5f5f5;
    }}
    .container {{
        background: white;
        padding: 30px;
        border-radius: 10px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        max-width: 500px;
        margin: 0 auto;
    }}
    .error {{
        color: #ff5858;
        font-size: 24px;
        margin-bottom: 20px;
    }}
    .message {{
        margin: 20px 0;
        color: #666;
        background: #fff5f5;
        padding: 15px;
        border-radius: 5px;
        border-left: 4px solid #ff5858;
    }}
    button {{
        background: #ff5858;
        color: white;
        border: none;
        padding: 12px 24px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 16px;
        margin-top: 20px;
    }}
    button:hover {{
        background: #e04a4a;
    }}
    </style>
    </head>
    <body>
    <div class="container">
        <div class="error">❌ {title}</div>
        <div class="message">{message}</div>
        <button onclick="closeWindow()">Close Window</button>
    </div>
    <script>
    function closeWindow() {{
        window.close();
    }}
    </script>
    </body>
    </html>
    """

def render_success_page():
    """Render success page HTML"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
    <title>QuickBooks Connected</title>
    <style>
    body {
        font-family: Arial, sans-serif;
        text-align: center;
        padding: 50px;
        background-color: #f5f5f5;
    }
    .container {
        background: white;
        padding: 30px;
        border-radius: 10px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        max-width: 500px;
        margin: 0 auto;
    }
    .success {
        color: #2CA01C;
        font-size: 24px;
        margin-bottom: 20px;
    }
    .message {
        margin: 20px 0;
        color: #666;
    }
    button {
        background: #2CA01C;
        color: white;
        border: none;
        padding: 12px 24px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 16px;
        margin-top: 20px;
    }
    button:hover {
        background: #248C16;
    }
    </style>
    </head>
    <body>
    <div class="container">
        <div class="success">✅ Successfully Connected!</div>
        <div class="message">QuickBooks has been connected to ERPNext.</div>
        <div class="message">You can now close this window and return to ERPNext.</div>
        <button onclick="closeWindow()">Close Window</button>
    </div>
    <script>
    function closeWindow() {
        if (window.opener) {
            window.opener.location.reload();
        }
        window.close();
    }
    // Auto-close after 5 seconds
    setTimeout(closeWindow, 5000);
    </script>
    </body>
    </html>
    """

# ============ INITIALIZATION ============

def initialize():
    """Initialize the QuickBooks integration"""
    try:
        # Check if settings exist
        settings = get_settings()
        log_action(
            "Integration Initialized",
            {
                "version": "1.0",
                "timestamp": now_datetime()
            }
        )
        return True
    except Exception:
        return False






    



@frappe.whitelist()
def manual_create_credit_memo(sales_invoice_name):
    """Manually create Credit Memo in QB for a return Sales Invoice"""
    try:
        

        si = frappe.get_doc("Sales Invoice", sales_invoice_name)

        if not si.is_return:
            return {"success": False, "error": "This is not a return invoice"}

        settings = get_settings()
        access_token = get_valid_access_token()

        qb_customer_id = frappe.db.get_value("Customer", si.customer, "quickbooks_id")
        if not qb_customer_id:
            return {"success": False, "error": f"Customer '{si.customer}' missing QB ID"}

        default_tax_code = getattr(settings, 'default_tax_code', '12') or '12'

        def get_qb_tax_code(tax_rate):
            rate = float(tax_rate or 0)
            if rate >= 20: return "3"
            elif rate >= 5: return "8"
            elif rate > 0: return "10"
            return default_tax_code

        invoice_tax_rate = 0
        if si.taxes:
            for tax in si.taxes:
                if float(tax.rate or 0) > 0:
                    invoice_tax_rate = float(tax.rate)
                    break

        lines = []
        for row in si.items:
            qb_item_id = frappe.db.get_value("Item", row.item_code, "quickbooks_id")
            if not qb_item_id:
                return {"success": False, "error": f"Item '{row.item_code}' missing QB ID"}
            lines.append({
                "DetailType": "SalesItemLineDetail",
                "Amount": abs(float(row.amount)),
                "Description": row.description or row.item_name or row.item_code,
                "SalesItemLineDetail": {
                    "ItemRef": {"value": str(qb_item_id)},
                    "Qty": abs(float(row.qty)),
                    "UnitPrice": float(row.rate),
                    "TaxCodeRef": {"value": get_qb_tax_code(invoice_tax_rate)}
                }
            })

        payload = {
            "CustomerRef": {"value": str(qb_customer_id)},
            "DocNumber": str(si.name),
            "TxnDate": str(si.posting_date),
            "Line": lines,
            "GlobalTaxCalculation": "TaxExcluded" if invoice_tax_rate > 0 else "NotApplicable"
        }

        api_obj = QuickBooksAPI()
        base_url = api_obj.get_api_endpoint()
        url = f"{base_url}/company/{settings.realm_id_company_id}/creditmemo?minorversion=65"
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }

        response = requests.post(url, headers=headers, json=payload)

        if response.status_code != 200:
            return {"success": False, "error": f"QB Error: {response.text}"}

        credit_memo_id = response.json().get("CreditMemo", {}).get("Id")
        if not credit_memo_id:
            return {"success": False, "error": "No ID in response"}

        frappe.db.set_value("Sales Invoice", sales_invoice_name, "quickbooks_id", credit_memo_id)
        frappe.db.set_value("Sales Invoice", sales_invoice_name, "quickbooks_sync_status", "Synced")
        frappe.db.set_value("Sales Invoice", sales_invoice_name, "quickbooks_sync_error", "")
        frappe.db.set_value("Sales Invoice", sales_invoice_name, "quickbooks_last_sync", frappe.utils.now_datetime())

        return {"success": True, "message": f"Credit Memo created in QB. ID: {credit_memo_id}"}

    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def manual_create_vendor_credit(purchase_invoice_name):
    """Manually create Vendor Credit in QB for a return Purchase Invoice"""
    try:
     
        
        pi = frappe.get_doc("Purchase Invoice", purchase_invoice_name)
        
        if not pi.is_return:
            return {"success": False, "error": "This is not a return invoice"}

        settings = get_settings()
        access_token = get_valid_access_token()

        qb_vendor_id = frappe.db.get_value("Supplier", pi.supplier, "quickbooks_id")
        if not qb_vendor_id:
            return {"success": False, "error": f"Supplier '{pi.supplier}' missing QB ID"}

        qb_account_id = frappe.db.get_value(
            "Account",
            [
                ["company", "=", settings.company],
                ["quickbooks_id", "!=", ""],
                ["quickbooks_id", "!=", None],
                ["account_type", "in", ["Expense Account", "Cost of Goods Sold"]]
            ],
            "quickbooks_id"
        )
        if not qb_account_id:
            return {"success": False, "error": "No expense account mapped to QuickBooks. Please sync accounts first via Quickbooks Setting → Sync Accounts."}

        lines = []
        for row in pi.items:
            amount = abs(float(row.amount or 0))
            if amount <= 0:
                continue
            lines.append({
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": amount,
                "Description": str(row.item_code),
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": str(qb_account_id)},
                    "TaxCodeRef": {"value": "12"}
                }
            })

        if not lines:
            return {"success": False, "error": "No valid lines found"}

        payload = {
            "VendorRef": {"value": str(qb_vendor_id)},
            "TxnDate": str(pi.posting_date),
            "Line": lines,
            "GlobalTaxCalculation": "NotApplicable"
        }

        api_obj = QuickBooksAPI()
        base_url = api_obj.get_api_endpoint()
        url = f"{base_url}/company/{settings.realm_id_company_id}/vendorcredit?minorversion=65"
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }

        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return {"success": False, "error": f"QB Error: {response.text}"}

        data = response.json()
        vendor_credit_id = data.get("VendorCredit", {}).get("Id")

        if not vendor_credit_id:
            return {"success": False, "error": f"No ID in response: {data}"}

        frappe.db.set_value("Purchase Invoice", purchase_invoice_name, "quickbooks_id", vendor_credit_id)
        frappe.db.set_value("Purchase Invoice", purchase_invoice_name, "quickbooks_sync_status", "Synced")
        frappe.db.set_value("Purchase Invoice", purchase_invoice_name, "quickbooks_sync_error", "")
        frappe.db.set_value("Purchase Invoice", purchase_invoice_name, "quickbooks_last_sync", frappe.utils.now_datetime())

        return {"success": True, "message": f"Vendor Credit created. ID: {vendor_credit_id}", "id": vendor_credit_id}

    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def manual_void_bill(purchase_invoice_name):
    """Manually void a Bill in QB"""
    try:
        pi = frappe.get_doc("Purchase Invoice", purchase_invoice_name)
        from quickbooks_connector.qb_purchase_hooks import on_purchase_invoice_cancel
        on_purchase_invoice_cancel(pi)
        return {"success": True, "message": "Bill voided successfully"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def manual_void_payment(payment_entry_name):
    """Manually void a Payment in QB"""
    try:
      

        pe = frappe.get_doc("Payment Entry", payment_entry_name)
        settings = get_settings()
        access_token = get_valid_access_token()

        qb_payment_id = getattr(pe, "quickbooks_payment_id", None)
        qb_bill_payment_id = getattr(pe, "quickbooks_bill_payment_id", None)

        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }

        api_obj = QuickBooksAPI()
        base_url = f"{api_obj.get_api_endpoint()}/company/{settings.realm_id_company_id}"

        if qb_payment_id:
            # Fetch SyncToken
            r = requests.get(
                f"{base_url}/payment/{qb_payment_id}?minorversion=65",
                headers=headers
            )
            payment_data = r.json().get("Payment", {})
            sync_token = payment_data.get("SyncToken")

            if not sync_token:
                return {"success": False, "error": f"No SyncToken for Payment ID: {qb_payment_id}"}

            payload = {
                "Id": str(qb_payment_id),
                "SyncToken": str(sync_token),
                "sparse": True,
                "TotalAmt": 0
            }

            response = requests.post(
                f"{base_url}/payment?minorversion=65&operation=void",
                headers=headers,
                json={"Id": str(qb_payment_id), "SyncToken": str(sync_token), "sparse": True}
            )

            if response.status_code != 200:
                response = requests.post(
                    f"{base_url}/payment?minorversion=65",
                    headers=headers,
                    json={
                        "Id": str(qb_payment_id),
                        "SyncToken": str(sync_token),
                        "TotalAmt": 0,
                        "Line": [],
                        "sparse": True
                    }
                )

            if response.status_code != 200:
                return {"success": False, "error": f"QB Error: {response.text}"}

            frappe.db.set_value("Payment Entry", payment_entry_name, "quickbooks_payment_id", "")
            return {"success": True, "message": f"Payment {qb_payment_id} voided successfully"}

        elif qb_bill_payment_id:
            r = requests.get(
                f"{base_url}/billpayment/{qb_bill_payment_id}?minorversion=65",
                headers=headers
            )
            bill_payment_data = r.json().get("BillPayment", {})
            sync_token = bill_payment_data.get("SyncToken")

            if not sync_token:
                return {"success": False, "error": f"No SyncToken for Bill Payment ID: {qb_bill_payment_id}"}

            response = requests.post(
                f"{base_url}/billpayment?minorversion=65&operation=void",
                headers=headers,
                json={"Id": str(qb_bill_payment_id), "SyncToken": str(sync_token), "sparse": True}
            )

            if response.status_code != 200:
                return {"success": False, "error": f"QB Error: {response.text}"}

            frappe.db.set_value("Payment Entry", payment_entry_name, "quickbooks_bill_payment_id", "")
            return {"success": True, "message": f"Bill Payment {qb_bill_payment_id} voided successfully"}

        return {"success": False, "error": "No QB Payment ID found"}

    except Exception as e:
        return {"success": False, "error": str(e)}




    


@frappe.whitelist()
def manual_amend_invoice(sales_invoice_name):
    """Manually update QB Invoice for amended Sales Invoice"""
    try:
        
        
        si = frappe.get_doc("Sales Invoice", sales_invoice_name)
        
        if not si.amended_from:
            return {"success": False, "error": "This is not an amended invoice"}

        settings = get_settings()
        access_token = get_valid_access_token()

        # Original QB ID from amended_from
        original_qb_id = frappe.db.get_value("Sales Invoice", si.amended_from, "quickbooks_id")
        if not original_qb_id:
            return {"success": False, "error": f"Original invoice '{si.amended_from}' has no QB ID"}

        # Fetch SyncToken
        api_obj = QuickBooksAPI()
        base_url = api_obj.get_api_endpoint()
        url_get = f"{base_url}/company/{settings.realm_id_company_id}/invoice/{original_qb_id}?minorversion=65"
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        r = requests.get(url_get, headers=headers)
        invoice_data = r.json().get("Invoice", {})
        sync_token = invoice_data.get("SyncToken")

        if not sync_token:
            return {"success": False, "error": f"No SyncToken for QB Invoice: {original_qb_id}. Invoice may be voided."}

        qb_customer_id = frappe.db.get_value("Customer", si.customer, "quickbooks_id")
        if not qb_customer_id:
            return {"success": False, "error": f"Customer '{si.customer}' missing QB ID"}

        default_tax_code = getattr(settings, 'default_tax_code', '12') or '12'

        def get_qb_tax_code(tax_rate):
            rate = float(tax_rate or 0)
            if rate >= 20: return "3"
            elif rate >= 5: return "8"
            elif rate > 0: return "10"
            return default_tax_code

        invoice_tax_rate = 0
        if si.taxes:
            for tax in si.taxes:
                if float(tax.rate or 0) > 0:
                    invoice_tax_rate = float(tax.rate)
                    break

        lines = []
        for row in si.items:
            qb_item_id = frappe.db.get_value("Item", row.item_code, "quickbooks_id")
            if not qb_item_id:
                continue
            lines.append({
                "DetailType": "SalesItemLineDetail",
                "Amount": float(row.amount),
                "Description": row.description or row.item_name or row.item_code,
                "SalesItemLineDetail": {
                    "ItemRef": {"value": str(qb_item_id)},
                    "Qty": float(row.qty),
                    "UnitPrice": float(row.rate),
                    "TaxCodeRef": {"value": get_qb_tax_code(invoice_tax_rate)}
                }
            })

        payload = {
            "Id": str(original_qb_id),
            "SyncToken": str(sync_token),
            "CustomerRef": {"value": str(qb_customer_id)},
            "TxnDate": str(si.posting_date),
            "DueDate": str(si.due_date),
            "Line": lines
        }

        if invoice_tax_rate > 0:
            payload["GlobalTaxCalculation"] = "TaxExcluded"
        else:
            payload["GlobalTaxCalculation"] = "NotApplicable"

        url_update = f"{base_url}/company/{settings.realm_id_company_id}/invoice?minorversion=65"
        response = requests.post(url_update, headers=headers, json=payload)

        if response.status_code != 200:
            return {"success": False, "error": f"QB Error: {response.text}"}

        frappe.db.set_value("Sales Invoice", sales_invoice_name, "quickbooks_id", original_qb_id)
        frappe.db.set_value("Sales Invoice", sales_invoice_name, "quickbooks_sync_status", "Synced")
        frappe.db.set_value("Sales Invoice", sales_invoice_name, "quickbooks_sync_error", "")
        frappe.db.set_value("Sales Invoice", sales_invoice_name, "quickbooks_last_sync", frappe.utils.now_datetime())

        return {"success": True, "message": f"QB Invoice {original_qb_id} updated successfully"}

    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def manual_amend_bill(purchase_invoice_name):
    """Manually update QB Bill for amended Purchase Invoice"""
    try:


        pi = frappe.get_doc("Purchase Invoice", purchase_invoice_name)

        if not pi.amended_from:
            return {"success": False, "error": "This is not an amended invoice"}

        settings = get_settings()
        access_token = get_valid_access_token()

        original_qb_id = frappe.db.get_value("Purchase Invoice", pi.amended_from, "quickbooks_id")
        if not original_qb_id:
            return {"success": False, "error": f"Original invoice '{pi.amended_from}' has no QB ID"}

        # Fetch SyncToken
        api_obj = QuickBooksAPI()
        base_url = api_obj.get_api_endpoint()
        url_get = f"{base_url}/company/{settings.realm_id_company_id}/bill/{original_qb_id}?minorversion=65"
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        r = requests.get(url_get, headers=headers)
        bill_data = r.json().get("Bill", {})
        sync_token = bill_data.get("SyncToken")

        if not sync_token:
            return {"success": False, "error": f"No SyncToken for QB Bill: {original_qb_id}. Bill may be voided."}

        qb_vendor_id = frappe.db.get_value("Supplier", pi.supplier, "quickbooks_id")
        if not qb_vendor_id:
            return {"success": False, "error": f"Supplier '{pi.supplier}' missing QB ID"}

        qb_account_id = frappe.db.get_value(
            "Account",
            [
                ["company", "=", settings.company],
                ["quickbooks_id", "!=", ""],
                ["quickbooks_id", "!=", None],
                ["account_type", "in", ["Expense Account", "Cost of Goods Sold"]]
            ],
            "quickbooks_id"
        )
        if not qb_account_id:
            return {"success": False, "error": "No expense account mapped to QuickBooks. Please sync accounts first via Quickbooks Setting → Sync Accounts."}

        lines = []
        for row in pi.items:
            amount = float(row.amount or 0)
            if amount <= 0:
                continue
            lines.append({
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": amount,
                "Description": str(row.item_code),
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": str(qb_account_id)},
                    "TaxCodeRef": {"value": "12"}
                }
            })

        payload = {
            "Id": str(original_qb_id),
            "SyncToken": str(sync_token),
            "VendorRef": {"value": str(qb_vendor_id)},
            "TxnDate": str(pi.posting_date),
            "Line": lines,
            "GlobalTaxCalculation": "NotApplicable"
        }

        if getattr(pi, "due_date", None):
            payload["DueDate"] = str(pi.due_date)

        url_update = f"{base_url}/company/{settings.realm_id_company_id}/bill?minorversion=65"
        response = requests.post(url_update, headers=headers, json=payload)

        if response.status_code != 200:
            return {"success": False, "error": f"QB Error: {response.text}"}

        frappe.db.set_value("Purchase Invoice", purchase_invoice_name, "quickbooks_id", original_qb_id)
        frappe.db.set_value("Purchase Invoice", purchase_invoice_name, "quickbooks_sync_status", "Synced")
        frappe.db.set_value("Purchase Invoice", purchase_invoice_name, "quickbooks_sync_error", "")
        frappe.db.set_value("Purchase Invoice", purchase_invoice_name, "quickbooks_last_sync", frappe.utils.now_datetime())

        return {"success": True, "message": f"QB Bill {original_qb_id} updated successfully"}

    except Exception as e:
        return {"success": False, "error": str(e)}