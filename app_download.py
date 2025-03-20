import configparser
import json
import requests
import sys
import os
import time
import logging
from datetime import datetime
from requests.auth import HTTPBasicAuth

# Set up logging
logging.basicConfig(
    filename='xero_download.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger()

# Xero API endpoints
XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_CONNECTIONS_URL = "https://api.xero.com/connections"
XERO_CONTACTS_URL = "https://api.xero.com/api.xro/2.0/Contacts"
XERO_INVOICES_URL = "https://api.xero.com/api.xro/2.0/Invoices"

# Read config.ini
config = configparser.ConfigParser()
config.read('config.ini')

try:
    CLIENT_ID = config['DEFAULT']['CLIENT_ID']
    CLIENT_SECRET = config['DEFAULT']['CLIENT_SECRET']
    SUPPLIER_NAME = config['DEFAULT']['SUPPLIER_NAME']
    START_DATE = config['DEFAULT']['START_DATE']
except KeyError as e:
    print(f"Missing required config key: {e}")
    logger.error(f"Missing required config key: {e}")
    sys.exit(1)

# Validate START_DATE format
try:
    start_date = datetime.strptime(START_DATE, '%Y-%m-%d')
except ValueError:
    print("START_DATE must be in YYYY-MM-DD format (e.g., 2019-07-05)")
    logger.error("START_DATE must be in YYYY-MM-DD format")
    sys.exit(1)

def get_token():
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    data = {'grant_type': "client_credentials",
            'scopes': 'accounting.transactions accounting.attachments accounting.contacts'}
    response = requests.post(XERO_TOKEN_URL, headers=headers, auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET), data=data)
    if response.status_code == 200:
        print("Obtained token successfully")
        logger.info("Obtained token successfully")
        return response.json()['access_token']
    else:
        print(f"Failed to fetch token: {response.status_code} - {response.text}")
        logger.error(f"Failed to fetch token: {response.status_code} - {response.text}")
        sys.exit(1)

def get_tenant_id(token):
    headers = {'Authorization': f"Bearer {token}", 'Accept': 'application/json'}
    response = requests.get(XERO_CONNECTIONS_URL, headers=headers)
    if response.status_code == 200:
        connections = response.json()
        if connections:
            tenant_id = connections[0]['tenantId']
            print(f"Using tenant ID: {tenant_id}")
            logger.info(f"Using tenant ID: {tenant_id}")
            return tenant_id
        else:
            print("No tenant found in connections")
            logger.error("No tenant found in connections")
            sys.exit(1)
    else:
        print(f"Failed to fetch tenant ID: {response.status_code} - {response.text}")
        logger.error(f"Failed to fetch tenant ID: {response.status_code} - {response.text}")
        sys.exit(1)

def get_xero_api(url, token, tenant_id, params=None):
    headers = {'Authorization': f"Bearer {token}", 'Accept': 'application/json', 'Xero-tenant-id': tenant_id}
    response = requests.get(url, headers=headers, params=params)
    return response

def get_contact_id(token, tenant_id, contact_name=SUPPLIER_NAME):
    params = {"where": f'Name=="{contact_name}"'}
    response = get_xero_api(XERO_CONTACTS_URL, token, tenant_id, params=params)
    if response.status_code == 200:
        contacts = response.json().get('Contacts', [])
        if contacts:
            contact_id = contacts[0]['ContactID']
            print(f"Found '{contact_name}' with ContactID: {contact_id}")
            logger.info(f"Found '{contact_name}' with ContactID: {contact_id}")
            return contact_id
        else:
            print(f"No contact found with name '{contact_name}'")
            logger.error(f"No contact found with name '{contact_name}'")
            sys.exit(1)
    else:
        print(f"Failed to fetch contact details: {response.status_code} - {response.text}")
        logger.error(f"Failed to fetch contact details: {response.status_code} - {response.text}")
        sys.exit(1)

def get_invoices_for_contact(token, tenant_id, contact_id):
    year, month, day = start_date.year, start_date.month, start_date.day
    today = datetime(2025, 3, 20)

    invoices = []
    page = 1
    while True:
        params = {
            "where": f'Contact.ContactID==Guid("{contact_id}") AND Date>=DateTime({year},{month:02d},{day:02d})',
            "order": "Date DESC",
            "page": page
        }
        response = get_xero_api(XERO_INVOICES_URL, token, tenant_id, params=params)
        if response.status_code == 200:
            page_invoices = response.json().get('Invoices', [])
            if not page_invoices:
                break
            invoices.extend(page_invoices)
            print(f"Fetched page {page}: {len(page_invoices)} invoices (total so far: {len(invoices)})")
            logger.info(f"Fetched page {page}: {len(page_invoices)} invoices (total so far: {len(invoices)})")
            page += 1
            time.sleep(1)
        else:
            print(f"Failed to fetch invoices: {response.status_code} - {response.text}")
            logger.error(f"Failed to fetch invoices: {response.status_code} - {response.text}")
            sys.exit(1)

    print(f"Found {len(invoices)} invoices for '{SUPPLIER_NAME}' from {start_date.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')}")
    logger.info(f"Found {len(invoices)} invoices for '{SUPPLIER_NAME}' from {start_date.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')}")
    return invoices

def load_processed_invoices():
    """Load previously processed invoice IDs from the log file."""
    processed = set()
    if os.path.exists('xero_download.log'):
        with open('xero_download.log', 'r') as log_file:
            for line in log_file:
                if "Processing invoice:" in line:
                    # Extract the invoice ID from the log line
                    parts = line.split("ID: ")
                    if len(parts) > 1:
                        invoice_id = parts[1].split(',')[0].strip()
                        processed.add(invoice_id)
    return processed

def load_downloaded_attachments():
    """Load previously downloaded attachments from the log file."""
    downloaded = set()
    if os.path.exists('xero_download.log'):
        with open('xero_download.log', 'r') as log_file:
            for line in log_file:
                if "Downloaded attachment:" in line:
                    parts = line.split("Downloaded attachment: ")
                    if len(parts) > 1:
                        unique_file_name = parts[1].strip()
                        downloaded.add(unique_file_name)
    return downloaded

def download_invoice_attachment(token, tenant_id, invoice_id, file_name, inv_num, downloaded_set):
    safe_file_name = file_name.replace('/', '_').replace('\\', '_')
    unique_file_name = f"{inv_num}_{safe_file_name}"

    if unique_file_name in downloaded_set:
        print(f"Skipping {unique_file_name} - already downloaded")
        logger.info(f"Skipped {unique_file_name} - already downloaded")
        return

    url = f"{XERO_INVOICES_URL}/{invoice_id}/Attachments/{file_name}"
    headers = {
        'Authorization': f"Bearer {token}",
        'Accept': 'application/octet-stream',
        'Xero-tenant-id': tenant_id
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        with open(unique_file_name, 'wb') as f:
            f.write(response.content)
        print(f"Downloaded attachment: {unique_file_name}")
        logger.info(f"Downloaded attachment: {unique_file_name}")
    else:
        print(f"Failed to download {file_name}: {response.status_code} - {response.text}")
        logger.error(f"Failed to download {file_name}: {response.status_code} - {response.text}")
    time.sleep(1)

def main():
    try:
        print("Fetching access token from Xero...")
        logger.info("Starting Xero invoice attachment downloader...")
        token = get_token()
        tenant_id = get_tenant_id(token)

        contact_id = get_contact_id(token, tenant_id)
        invoices = get_invoices_for_contact(token, tenant_id, contact_id)

        supplier_folder = os.path.join("invoice_attachments", SUPPLIER_NAME.replace(" ", "_"))
        if not os.path.exists(supplier_folder):
            os.makedirs(supplier_folder)
        os.chdir(supplier_folder)

        # Load previously processed invoices and downloaded attachments
        processed_invoices = load_processed_invoices()
        downloaded_set = load_downloaded_attachments()

        new_invoices_processed = 0
        for inv in invoices:
            inv_id = inv['InvoiceID']
            inv_num = inv.get('InvoiceNumber', inv_id)
            date = inv.get('DateString', 'N/A')

            # Skip if invoice was already processed
            if inv_id in processed_invoices:
                print(f"Skipping invoice: {inv_num} (ID: {inv_id}) - already processed")
                logger.info(f"Skipped invoice: {inv_num} (ID: {inv_id}) - already processed")
                continue

            print(f"Processing invoice: {inv_num} (ID: {inv_id}, Date: {date})")
            logger.info(f"Processing invoice: {inv_num} (ID: {inv_id}, Date: {date})")

            attachments_url = f"{XERO_INVOICES_URL}/{inv_id}/Attachments"
            response = get_xero_api(attachments_url, token, tenant_id)
            if response.status_code == 200:
                attachments = response.json().get('Attachments', [])
                if attachments:
                    for attachment in attachments:
                        file_name = attachment['FileName']
                        print(f"Found attachment: {file_name}")
                        logger.info(f"Found attachment: {file_name}")
                        download_invoice_attachment(token, tenant_id, inv_id, file_name, inv_num, downloaded_set)
                else:
                    print(f"No attachments found for invoice {inv_num}")
                    logger.info(f"No attachments found for invoice {inv_num}")
                new_invoices_processed += 1
            else:
                print(f"Failed to fetch attachments for {inv_num}: {response.status_code} - {response.text}")
                logger.error(f"Failed to fetch attachments for {inv_num}: {response.status_code} - {response.text}")

        print(f"Processed {new_invoices_processed} new invoices.")
        logger.info(f"Processed {new_invoices_processed} new invoices.")

    except Exception as err:
        print(f"Error occurred: {str(err)}")
        logger.error(f"Error occurred: {str(err)}")
        sys.exit(1)

if __name__ == "__main__":
    print("Starting Xero invoice attachment downloader...")
    main()
    print("Download process completed.")
    logger.info("Download process completed.")