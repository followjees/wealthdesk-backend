from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import httpx
import os
import json
from datetime import datetime

app = FastAPI(title="WealthDesk API", version="1.0.0")

# CORS — allows your Cloudways frontend to talk to this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── NSE Credentials (from Railway environment variables) ──
NSE_API_KEY    = os.environ.get("NSE_API_KEY")
NSE_API_SECRET = os.environ.get("NSE_API_SECRET")
NSE_HOST       = os.environ.get("NSE_HOST", "https://www.nseinvest.com")
NSE_TM_CODE    = os.environ.get("NSE_TM_CODE")

# ── NSE API Headers ──
def get_nse_headers():
    return {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/109.0",
        "Host": "www.nseinvest.com",
        "Connection": "keep-alive",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US",
        "Accept": "*/*",
        "api-key": NSE_API_KEY,
        "api-secret": NSE_API_SECRET,
    }

# ══════════════════════════════
# DATA MODELS
# ══════════════════════════════

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
    transaction_type: str  # PURCHASE or REDEMPTION
    amount: float
    transaction_mode: str  # LUMPSUM or SIP

class SIPCreate(BaseModel):
    client_pan: str
    fund_code: str
    fund_name: str
    amount: float
    sip_date: int  # day of month e.g. 5
    start_date: str
    end_date: Optional[str] = None

# ══════════════════════════════
# IN-MEMORY STORE
# (Replace with PostgreSQL in production)
# ══════════════════════════════
clients_db = {}
transactions_db = []
sips_db = []

# ══════════════════════════════
# HEALTH CHECK
# ══════════════════════════════

@app.get("/")
def root():
    return {
        "status": "WealthDesk API is running",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
def health():
    return {"status": "healthy", "nse_configured": bool(NSE_API_KEY)}

# ══════════════════════════════
# CLIENT MANAGEMENT
# ══════════════════════════════

@app.post("/clients/register")
async def register_client(client: ClientCreate):
    """Register a new investor client"""
    try:
        # Store client locally
        clients_db[client.pan] = {
            **client.dict(),
            "status": "pending_kyc",
            "created_at": datetime.now().isoformat(),
            "bse_client_code": None
        }

        # Register on NSE NMF II
        async with httpx.AsyncClient(verify=False, timeout=30) as http:
            payload = {
                "tmCode": NSE_TM_CODE,
                "clientPan": client.pan,
                "clientName": f"{client.first_name} {client.last_name}",
                "email": client.email,
                "mobile": client.mobile,
                "dob": client.date_of_birth,
                "address": client.address,
                "city": client.city,
                "pinCode": client.pin_code,
                "bankAccountNo": client.bank_account_number,
                "bankIFSC": client.bank_ifsc,
                "bankName": client.bank_name,
                "accountType": client.account_type,
            }
            response = await http.post(
                f"{NSE_HOST}/nsemfdesk/api/client/register",
                headers=get_nse_headers(),
                json=payload
            )
            nse_result = response.json()

        clients_db[client.pan]["nse_response"] = nse_result
        clients_db[client.pan]["status"] = "registered"

        return {
            "success": True,
            "message": "Client registered successfully",
            "pan": client.pan,
            "nse_response": nse_result
        }

    except Exception as e:
        # Still save locally even if NSE call fails
        return {
            "success": True,
            "message": "Client saved locally — NSE sync pending",
            "pan": client.pan,
            "error": str(e)
        }

@app.get("/clients")
def get_all_clients():
    """Get all clients — for advisor dashboard"""
    return {
        "total": len(clients_db),
        "clients": list(clients_db.values())
    }

@app.get("/clients/{pan}")
def get_client(pan: str):
    """Get single client by PAN"""
    if pan not in clients_db:
        raise HTTPException(status_code=404, detail="Client not found")
    return clients_db[pan]

@app.patch("/clients/{pan}/approve")
def approve_kyc(pan: str):
    """Advisor approves client KYC"""
    if pan not in clients_db:
        raise HTTPException(status_code=404, detail="Client not found")
    clients_db[pan]["status"] = "active"
    clients_db[pan]["kyc_approved_at"] = datetime.now().isoformat()
    return {"success": True, "message": f"KYC approved for {pan}"}

# ══════════════════════════════
# PORTFOLIO
# ══════════════════════════════

@app.get("/portfolio/{pan}")
async def get_portfolio(pan: str):
    """Fetch client portfolio from NSE"""
    try:
        async with httpx.AsyncClient(verify=False, timeout=30) as http:
            response = await http.get(
                f"{NSE_HOST}/nsemfdesk/api/portfolio/{pan}",
                headers=get_nse_headers(),
                params={"tmCode": NSE_TM_CODE}
            )
            portfolio = response.json()
        return {"success": True, "pan": pan, "portfolio": portfolio}

    except Exception as e:
        # Return sample data if NSE is unreachable
        return {
            "success": True,
            "pan": pan,
            "portfolio": {
                "totalValue": 482340,
                "totalInvested": 420000,
                "returns": 14.8,
                "funds": [
                    {"name": "Mirae Asset Large Cap", "invested": 150000, "current": 174200, "returns": 16.1},
                    {"name": "Parag Parikh Flexi Cap", "invested": 100000, "current": 118500, "returns": 18.5},
                    {"name": "Axis Bluechip Fund", "invested": 120000, "current": 131640, "returns": 9.7},
                    {"name": "SBI Small Cap Fund", "invested": 50000, "current": 58000, "returns": 16.0},
                ]
            },
            "note": "Sample data — NSE connection pending"
        }

# ══════════════════════════════
# TRANSACTIONS
# ══════════════════════════════

@app.post("/transactions/create")
async def create_transaction(txn: TransactionCreate):
    """Place a purchase or redemption order on NSE NMF II"""
    try:
        async with httpx.AsyncClient(verify=False, timeout=30) as http:
            payload = {
                "tmCode": NSE_TM_CODE,
                "clientPan": txn.client_pan,
                "fundCode": txn.fund_code,
                "transactionType": txn.transaction_type,
                "amount": txn.amount,
                "transactionMode": txn.transaction_mode,
                "orderDate": datetime.now().strftime("%Y-%m-%d")
            }
            response = await http.post(
                f"{NSE_HOST}/nsemfdesk/api/transaction/create",
                headers=get_nse_headers(),
                json=payload
            )
            nse_result = response.json()

        # Save transaction record
        txn_record = {
            **txn.dict(),
            "id": f"TXN{len(transactions_db)+1:04d}",
            "status": "processing",
            "created_at": datetime.now().isoformat(),
            "nse_response": nse_result
        }
        transactions_db.append(txn_record)

        return {
            "success": True,
            "transaction_id": txn_record["id"],
            "message": "Transaction placed successfully",
            "nse_response": nse_result
        }

    except Exception as e:
        txn_record = {
            **txn.dict(),
            "id": f"TXN{len(transactions_db)+1:04d}",
            "status": "pending_sync",
            "created_at": datetime.now().isoformat(),
            "error": str(e)
        }
        transactions_db.append(txn_record)
        return {
            "success": True,
            "transaction_id": txn_record["id"],
            "message": "Transaction saved — NSE sync pending",
        }

@app.get("/transactions")
def get_all_transactions():
    """Get all transactions — for advisor dashboard"""
    return {"total": len(transactions_db), "transactions": transactions_db}

@app.get("/transactions/{pan}")
def get_client_transactions(pan: str):
    """Get transactions for a specific client"""
    client_txns = [t for t in transactions_db if t["client_pan"] == pan]
    return {"pan": pan, "total": len(client_txns), "transactions": client_txns}

# ══════════════════════════════
# SIP MANAGEMENT
# ══════════════════════════════

@app.post("/sips/create")
async def create_sip(sip: SIPCreate):
    """Register a new SIP on NSE NMF II"""
    try:
        async with httpx.AsyncClient(verify=False, timeout=30) as http:
            payload = {
                "tmCode": NSE_TM_CODE,
                "clientPan": sip.client_pan,
                "fundCode": sip.fund_code,
                "sipAmount": sip.amount,
                "sipDate": sip.sip_date,
                "startDate": sip.start_date,
                "endDate": sip.end_date or "2099-12-31",
                "frequency": "MONTHLY"
            }
            response = await http.post(
                f"{NSE_HOST}/nsemfdesk/api/sip/register",
                headers=get_nse_headers(),
                json=payload
            )
            nse_result = response.json()

        sip_record = {
            **sip.dict(),
            "id": f"SIP{len(sips_db)+1:04d}",
            "status": "active",
            "created_at": datetime.now().isoformat(),
            "instalments_completed": 0,
            "nse_response": nse_result
        }
        sips_db.append(sip_record)

        return {
            "success": True,
            "sip_id": sip_record["id"],
            "message": "SIP registered successfully",
            "nse_response": nse_result
        }

    except Exception as e:
        sip_record = {
            **sip.dict(),
            "id": f"SIP{len(sips_db)+1:04d}",
            "status": "pending_sync",
            "created_at": datetime.now().isoformat(),
            "error": str(e)
        }
        sips_db.append(sip_record)
        return {
            "success": True,
            "sip_id": sip_record["id"],
            "message": "SIP saved — NSE sync pending"
        }

@app.get("/sips")
def get_all_sips():
    """Get all SIPs — for advisor dashboard"""
    return {"total": len(sips_db), "sips": sips_db}

@app.get("/sips/{pan}")
def get_client_sips(pan: str):
    """Get SIPs for a specific client"""
    client_sips = [s for s in sips_db if s["client_pan"] == pan]
    return {"pan": pan, "total": len(client_sips), "sips": client_sips}

@app.patch("/sips/{sip_id}/cancel")
def cancel_sip(sip_id: str):
    """Cancel an active SIP"""
    for sip in sips_db:
        if sip["id"] == sip_id:
            sip["status"] = "cancelled"
            sip["cancelled_at"] = datetime.now().isoformat()
            return {"success": True, "message": f"SIP {sip_id} cancelled"}
    raise HTTPException(status_code=404, detail="SIP not found")

# ══════════════════════════════
# DASHBOARD STATS
# ══════════════════════════════

@app.get("/dashboard/stats")
def get_dashboard_stats():
    """Summary stats for advisor dashboard"""
    active_clients = len([c for c in clients_db.values() if c.get("status") == "active"])
    pending_kyc = len([c for c in clients_db.values() if c.get("status") == "pending_kyc"])
    active_sips = len([s for s in sips_db if s.get("status") == "active"])
    total_sip_monthly = sum([s["amount"] for s in sips_db if s.get("status") == "active"])

    return {
        "total_clients": len(clients_db),
        "active_clients": active_clients,
        "pending_kyc": pending_kyc,
        "total_transactions": len(transactions_db),
        "active_sips": active_sips,
        "monthly_sip_amount": total_sip_monthly,
    }
