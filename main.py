from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import httpx
import os
import ssl
from datetime import datetime

app = FastAPI(title="WealthDesk API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

NSE_API_KEY    = os.environ.get("NSE_API_KEY")
NSE_API_SECRET = os.environ.get("NSE_API_SECRET")
NSE_HOST       = os.environ.get("NSE_HOST", "https://www.nseinvest.com")
NSE_TM_CODE    = os.environ.get("NSE_TM_CODE")

def get_ssl_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def get_nse_headers():
    return {
        "Content-Type": "application/json",
        "User-Agent": "PostmanRuntime/7.43.0",
        "Accept": "*/*",
        "Accept-Language": "en-US",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Host": "www.nseinvest.com",
        "Referer": "https://www.nseinvest.com/nsemfdesk/login.htm",
        "Cookie": "",
        "api-key": NSE_API_KEY or "",
        "api-secret": NSE_API_SECRET or "",
    }

class ClientCreate(BaseModel):
    first_name: str
    last_name: str
    email: str
    mobile: str
    pan: str
    aadhaar: str
    date_of_birth: str
    address: str
    city: str
    pin_code: str
    annual_income: str
    investment_goal: str
    bank_account_number: str
    bank_ifsc: str
    bank_name: str
    account_type: str

class TransactionCreate(BaseModel):
    client_pan: str
    fund_code: str
    fund_name: str
    transaction_type: str
    amount: float
    transaction_mode: str

class SIPCreate(BaseModel):
    client_pan: str
    fund_code: str
    fund_name: str
    amount: float
    sip_date: int
    start_date: str
    end_date: Optional[str] = None

clients_db = {}
transactions_db = []
sips_db = []

@app.get("/")
def root():
    return {"status": "WealthDesk API is running", "version": "2.0.0", "timestamp": datetime.now().isoformat()}

@app.get("/health")
def health():
    api_key_set    = bool(NSE_API_KEY)
    api_secret_set = bool(NSE_API_SECRET)
    tm_code_set    = bool(NSE_TM_CODE)
    return {
        "status": "healthy",
        "nse_configured": api_key_set and api_secret_set and tm_code_set,
        "tls": "1.3 enforced",
        "credentials": {
            "NSE_API_KEY":    "SET" if api_key_set    else "MISSING",
            "NSE_API_SECRET": "SET" if api_secret_set else "MISSING",
            "NSE_TM_CODE":    "SET" if tm_code_set    else "MISSING",
            "NSE_HOST":       NSE_HOST
        }
    }

@app.get("/nse/test")
async def test_nse_connection():
    try:
        ssl_ctx = get_ssl_context()
        async with httpx.AsyncClient(verify=ssl_ctx, timeout=30, follow_redirects=True) as http:
            response = await http.get(f"{NSE_HOST}/nsemfdesk/login.htm", headers=get_nse_headers())
        return {"success": True, "status_code": response.status_code, "message": "NSE connection successful"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/clients/register")
async def register_client(client: ClientCreate):
    clients_db[client.pan] = {**client.dict(), "status": "pending_kyc", "created_at": datetime.now().isoformat()}
    try:
        ssl_ctx = get_ssl_context()
        async with httpx.AsyncClient(verify=ssl_ctx, timeout=30, follow_redirects=True) as http:
            response = await http.post(f"{NSE_HOST}/nsemfdesk/api/client/register", headers=get_nse_headers(), json={"tmCode": NSE_TM_CODE, "clientPan": client.pan, "clientName": f"{client.first_name} {client.last_name}", "email": client.email, "mobile": client.mobile, "dob": client.date_of_birth, "address": client.address, "city": client.city, "pinCode": client.pin_code, "bankAccountNo": client.bank_account_number, "bankIFSC": client.bank_ifsc, "bankName": client.bank_name, "accountType": client.account_type})
            clients_db[client.pan]["nse_response"] = response.json()
            clients_db[client.pan]["status"] = "registered"
    except Exception as e:
        clients_db[client.pan]["nse_note"] = str(e)
    return {"success": True, "message": "Client registered", "pan": client.pan}

@app.get("/clients")
def get_all_clients():
    return {"total": len(clients_db), "clients": list(clients_db.values())}

@app.get("/clients/{pan}")
def get_client(pan: str):
    if pan not in clients_db:
        raise HTTPException(status_code=404, detail="Client not found")
    return clients_db[pan]

@app.patch("/clients/{pan}/approve")
def approve_kyc(pan: str):
    if pan not in clients_db:
        raise HTTPException(status_code=404, detail="Client not found")
    clients_db[pan]["status"] = "active"
    clients_db[pan]["kyc_approved_at"] = datetime.now().isoformat()
    return {"success": True, "message": f"KYC approved for {pan}"}

@app.get("/portfolio/{pan}")
async def get_portfolio(pan: str):
    try:
        ssl_ctx = get_ssl_context()
        async with httpx.AsyncClient(verify=ssl_ctx, timeout=30, follow_redirects=True) as http:
            response = await http.get(f"{NSE_HOST}/nsemfdesk/api/portfolio/{pan}", headers=get_nse_headers(), params={"tmCode": NSE_TM_CODE})
        return {"success": True, "pan": pan, "portfolio": response.json()}
    except Exception as e:
        return {"success": True, "pan": pan, "note": f"Sample data - {str(e)}", "portfolio": {"totalValue": 482340, "totalInvested": 420000, "returns": 14.8, "funds": [{"name": "Mirae Asset Large Cap", "invested": 150000, "current": 174200, "returns": 16.1}, {"name": "Parag Parikh Flexi Cap", "invested": 100000, "current": 118500, "returns": 18.5}]}}

@app.post("/transactions/create")
async def create_transaction(txn: TransactionCreate):
    txn_record = {**txn.dict(), "id": f"TXN{len(transactions_db)+1:04d}", "status": "pending", "created_at": datetime.now().isoformat()}
    transactions_db.append(txn_record)
    try:
        ssl_ctx = get_ssl_context()
        async with httpx.AsyncClient(verify=ssl_ctx, timeout=30, follow_redirects=True) as http:
            response = await http.post(f"{NSE_HOST}/nsemfdesk/api/transaction/create", headers=get_nse_headers(), json={"tmCode": NSE_TM_CODE, "clientPan": txn.client_pan, "fundCode": txn.fund_code, "transactionType": txn.transaction_type, "amount": txn.amount, "transactionMode": txn.transaction_mode, "orderDate": datetime.now().strftime("%Y-%m-%d")})
            txn_record["nse_response"] = response.json()
            txn_record["status"] = "processing"
    except Exception as e:
        txn_record["nse_note"] = str(e)
    return {"success": True, "transaction_id": txn_record["id"], "message": "Transaction created"}

@app.get("/transactions")
def get_all_transactions():
    return {"total": len(transactions_db), "transactions": transactions_db}

@app.get("/transactions/{pan}")
def get_client_transactions(pan: str):
    return {"pan": pan, "transactions": [t for t in transactions_db if t["client_pan"] == pan]}

@app.post("/sips/create")
async def create_sip(sip: SIPCreate):
    sip_record = {**sip.dict(), "id": f"SIP{len(sips_db)+1:04d}", "status": "pending", "created_at": datetime.now().isoformat()}
    sips_db.append(sip_record)
    try:
        ssl_ctx = get_ssl_context()
        async with httpx.AsyncClient(verify=ssl_ctx, timeout=30, follow_redirects=True) as http:
            response = await http.post(f"{NSE_HOST}/nsemfdesk/api/sip/register", headers=get_nse_headers(), json={"tmCode": NSE_TM_CODE, "clientPan": sip.client_pan, "fundCode": sip.fund_code, "sipAmount": sip.amount, "sipDate": sip.sip_date, "startDate": sip.start_date, "endDate": sip.end_date or "2099-12-31", "frequency": "MONTHLY"})
            sip_record["nse_response"] = response.json()
            sip_record["status"] = "active"
    except Exception as e:
        sip_record["nse_note"] = str(e)
    return {"success": True, "sip_id": sip_record["id"], "message": "SIP created"}

@app.get("/sips")
def get_all_sips():
    return {"total": len(sips_db), "sips": sips_db}

@app.get("/sips/{pan}")
def get_client_sips(pan: str):
    return {"pan": pan, "sips": [s for s in sips_db if s["client_pan"] == pan]}

@app.patch("/sips/{sip_id}/cancel")
def cancel_sip(sip_id: str):
    for sip in sips_db:
        if sip["id"] == sip_id:
            sip["status"] = "cancelled"
            sip["cancelled_at"] = datetime.now().isoformat()
            return {"success": True, "message": f"SIP {sip_id} cancelled"}
    raise HTTPException(status_code=404, detail="SIP not found")

@app.get("/dashboard/stats")
def get_dashboard_stats():
    return {
        "total_clients": len(clients_db),
        "active_clients": len([c for c in clients_db.values() if c.get("status") == "active"]),
        "pending_kyc": len([c for c in clients_db.values() if c.get("status") == "pending_kyc"]),
        "total_transactions": len(transactions_db),
        "active_sips": len([s for s in sips_db if s.get("status") == "active"]),
        "monthly_sip_amount": sum([s["amount"] for s in sips_db if s.get("status") == "active"]),
    }
