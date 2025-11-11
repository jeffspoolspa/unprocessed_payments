import requests
from typing import List, Dict
from dotenv import load_dotenv
import os
import csv

load_dotenv()

access_token = os.getenv("ACCESS_TOKEN")
realm_id = os.getenv("REALM_ID")


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
    for payment in all_payments:
        # Check if unprocessed and payment method is CC or ACH
        is_unprocessed = payment.get("ProcessPayment") != True
        
        payment_method_id = payment.get("PaymentMethodRef", {}).get("value")
        payment_method = payment_methods.get(payment_method_id, "")
        is_valid_method = payment_method in ["Credit Card", "ACH"]
        
        # Get deposit account ID and name
        deposit_account_ref = payment.get("DepositToAccountRef", {})
        deposit_account_id = deposit_account_ref.get("value", "")
        deposit_account_name = accounts.get(deposit_account_id, "")
        
        if True:
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

if __name__ == "__main__":
    credit_list = get_qbo_credits(access_token, realm_id)
    
    # Write to CSV file
    if credit_list:
        csv_filename = "credit_list.csv"
        fieldnames = ["Invoice_ID", "Date", "Total_Amount", "QBO_Customer_ID", "Customer_Name", "Invoice_Number", "Payment_Method", "Payment_Number", "Memo", "Deposit_Account_ID", "Deposit_Account_Name", "Has_Matching_Deposit", "Deposit_ID", "Deposit_Number", "Unprocessed"]
        
        with open(csv_filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(credit_list)
        
        print(f"Successfully wrote {len(credit_list)} records to {csv_filename}")
    else:
        print("No credit records found to write to CSV.")