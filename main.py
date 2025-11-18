import requests
from typing import List, Dict
from dotenv import load_dotenv
import os
import csv
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta

load_dotenv()

realm_id = os.getenv("REALM_ID")
client_id = os.getenv("CLIENT_ID")
client_secret = os.getenv("CLIENT_SECRET")
refresh_token = os.getenv("REFRESH_TOKEN")
smtp_host = os.getenv("SMTP_HOST")
smtp_port = int(os.getenv("SMTP_PORT", 587))
smtp_user = os.getenv("SMTP_USER")
smtp_pass = os.getenv("SMTP_PASS")
to_email = os.getenv("TO_EMAIL")


def get_new_access_token(client_id: str, client_secret: str, refresh_token: str) -> tuple:
    """Refresh QuickBooks access token using refresh token

    Returns:
        tuple: (new_access_token, new_refresh_token)
    """
    try:
        token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        }

        # Use basic auth with client_id and client_secret
        from requests.auth import HTTPBasicAuth
        auth = HTTPBasicAuth(client_id, client_secret)

        response = requests.post(token_url, headers=headers, data=data, auth=auth)
        response.raise_for_status()

        token_data = response.json()
        new_access_token = token_data.get("access_token")
        new_refresh_token = token_data.get("refresh_token")

        print("Successfully refreshed access token")
        print(f"\n{'='*60}")
        print("IMPORTANT: New refresh token generated!")
        print(f"{'='*60}")
        print(f"New Refresh Token: {new_refresh_token}")
        print(f"\nPlease update your GitHub secret 'REFRESH_TOKEN' with this new value")
        print(f"to keep authentication working for the next 100 days.")
        print(f"{'='*60}\n")

        return new_access_token, new_refresh_token
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error refreshing access token: {e}")
        print(f"Response: {e.response.text if hasattr(e, 'response') else 'No response'}")
        raise
    except Exception as e:
        print(f"Failed to refresh access token: {str(e)}")
        raise


def get_qbo_credits(access_token: str, realm_id: str) -> List[Dict]:
    """Get unprocessed QBO payments with Credit Card or ACH payment method"""
    
    base_url = f"https://quickbooks.api.intuit.com/v3/company/{realm_id}/query"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    
    # Get payment methods
    pm_response = requests.get(
        base_url,
        params={"query": "SELECT * FROM PaymentMethod"},
        headers=headers
    )
    payment_methods = {
        pm["Id"]: pm["Name"] 
        for pm in pm_response.json()["QueryResponse"].get("PaymentMethod", [])
    }
    
    # Get all accounts with pagination
    accounts = {}
    start_position = 1
    max_results = 1000
    
    while True:
        query = f"SELECT * FROM Account STARTPOSITION {start_position} MAXRESULTS {max_results}"
        response = requests.get(base_url, params={"query": query}, headers=headers)
        account_list = response.json()["QueryResponse"].get("Account", [])
        
        for account in account_list:
            accounts[account["Id"]] = account.get("Name", account.get("FullyQualifiedName", ""))
        
        if len(account_list) < max_results:
            break
        start_position += max_results
    
    # Get all deposits with pagination and extract payment IDs from them
    # Map payment_id -> deposit info (ID and DocNumber)
    payment_to_deposit = {}
    start_position = 1
    max_results = 1000
    
    while True:
        query = f"SELECT * FROM Deposit STARTPOSITION {start_position} MAXRESULTS {max_results}"
        response = requests.get(base_url, params={"query": query}, headers=headers)
        deposits = response.json()["QueryResponse"].get("Deposit", [])
        
        # Extract payment IDs from deposit lines and map to deposit info
        for deposit in deposits:
            deposit_id = deposit.get("Id", "")
            deposit_number = deposit.get("DocNumber", "")
            deposit_lines = deposit.get("Line", [])
            for line in deposit_lines:
                linked_txns = line.get("LinkedTxn", [])
                if linked_txns:
                    for linked_txn in linked_txns:
                        if linked_txn.get("TxnType") == "Payment":
                            payment_id = linked_txn.get("TxnId", "")
                            if payment_id:
                                # Store deposit ID and number for this payment
                                payment_to_deposit[payment_id] = {
                                    "Deposit_ID": deposit_id,
                                    "Deposit_Number": deposit_number
                                }
        
        if len(deposits) < max_results:
            break
        start_position += max_results
    
    # Get all payments with pagination
    all_payments = []
    start_position = 1
    max_results = 1000
    
    while True:
        query = f"SELECT * FROM Payment STARTPOSITION {start_position} MAXRESULTS {max_results}"
        response = requests.get(base_url, params={"query": query}, headers=headers)
        payments = response.json()["QueryResponse"].get("Payment", [])
        all_payments.extend(payments)
        
        if len(payments) < max_results:
            break
        start_position += max_results
    
    # Filter and format
    credit_list = []
    # Calculate date one month ago
    one_month_ago = (datetime.now() - timedelta(days=30)).date()

    for payment in all_payments:
        # Check if unprocessed and payment method is CC or ACH
        is_unprocessed = payment.get("ProcessPayment") != True

        payment_method_id = payment.get("PaymentMethodRef", {}).get("value")
        payment_method = payment_methods.get(payment_method_id, "")
        is_valid_method = payment_method in ["Credit Card", "ACH"]

        # Check if payment is within the last month
        payment_date_str = payment.get("TxnDate", "")
        try:
            payment_date = datetime.strptime(payment_date_str, "%Y-%m-%d").date()
            is_recent = payment_date >= one_month_ago
        except (ValueError, AttributeError):
            is_recent = False

        # Get deposit account ID and name
        deposit_account_ref = payment.get("DepositToAccountRef", {})
        deposit_account_id = deposit_account_ref.get("value", "")
        deposit_account_name = accounts.get(deposit_account_id, "")

        # Only include if unprocessed, valid payment method, and within last month
        if is_unprocessed and is_valid_method and is_recent:
            # Get customer name
            customer_ref = payment.get("CustomerRef", {})
            customer_id = customer_ref.get("value", "")
            customer_name = customer_ref.get("name", "")
            
            # Get invoice number from payment lines
            invoice_number = ""
            payment_lines = payment.get("Line", [])
            if payment_lines:
                # Look for LinkedTxn in the lines to find invoice references
                for line in payment_lines:
                    linked_txns = line.get("LinkedTxn", [])
                    if linked_txns:
                        # Get the invoice number directly from linked transactions
                        for linked_txn in linked_txns:
                            if linked_txn.get("TxnType") == "Invoice":
                                invoice_number = linked_txn.get("TxnId", "")
                                break
                        if invoice_number:
                            break
            
            # Check if payment has a matching deposit and get deposit info
            payment_id = payment["Id"]
            deposit_info = payment_to_deposit.get(payment_id, {})
            has_matching_deposit = payment_id in payment_to_deposit
            deposit_id = deposit_info.get("Deposit_ID", "")
            deposit_number = deposit_info.get("Deposit_Number", "")
            
            credit_list.append({
                "Payment_ID": payment["Id"],
                "Date": payment["TxnDate"],
                "Total_Amount": payment["TotalAmt"],
                "QBO_Customer_ID": customer_id,
                "Customer_Name": customer_name,
                "Invoice_Number": invoice_number,
                "Payment_Method": payment_method,
                "Payment_Number": payment.get("PaymentRefNum"),
                "Memo": payment.get("PrivateNote"),
                "Deposit_Account_ID": deposit_account_id,
                "Deposit_Account_Name": deposit_account_name,
                "Has_Matching_Deposit": has_matching_deposit,
                "Deposit_ID": deposit_id,
                "Deposit_Number": deposit_number,
                "Unprocessed": is_unprocessed,
            })
    
    return credit_list


def send_email_with_csv(csv_filename: str, record_count: int):
    """Send email with CSV attachment"""
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = smtp_user
        msg['To'] = to_email
        msg['Subject'] = f'Unprocessed Payments Report - {datetime.now().strftime("%B %d, %Y")}'

        # Email body
        body = f"""Hello,

Please find attached the unprocessed payments report for the last 30 days.

Total unprocessed payments: {record_count}

This report includes:
- Payments marked as unprocessed
- Payment methods: Credit Card or ACH
- Dates within the last 30 days

Best regards,
Automated Payment Report System
"""
        msg.attach(MIMEText(body, 'plain'))

        # Attach CSV file
        with open(csv_filename, 'rb') as attachment:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())

        encoders.encode_base64(part)
        part.add_header(
            'Content-Disposition',
            f'attachment; filename= {csv_filename}'
        )
        msg.attach(part)

        # Send email
        server = smtplib.SMTP(smtp_host, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        text = msg.as_string()
        server.sendmail(smtp_user, to_email, text)
        server.quit()

        print(f"Email sent successfully to {to_email}")
        return True
    except Exception as e:
        print(f"Failed to send email: {str(e)}")
        return False


if __name__ == "__main__":
    # Get fresh access token and new refresh token
    access_token, new_refresh_token = get_new_access_token(client_id, client_secret, refresh_token)

    credit_list = get_qbo_credits(access_token, realm_id)

    # Write to CSV file
    if credit_list:
        csv_filename = "unprocessed_payments.csv"
        fieldnames = ["Payment_ID", "Date", "Total_Amount", "QBO_Customer_ID", "Customer_Name", "Invoice_Number", "Payment_Method", "Payment_Number", "Memo", "Deposit_Account_ID", "Deposit_Account_Name", "Has_Matching_Deposit", "Deposit_ID", "Deposit_Number", "Unprocessed"]

        with open(csv_filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(credit_list)

        print(f"Successfully wrote {len(credit_list)} records to {csv_filename}")

        # Send email with CSV attachment
        send_email_with_csv(csv_filename, len(credit_list))
    else:
        print("No unprocessed payments found matching the criteria (valid payment method, unprocessed, last 30 days).")