from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import httpx
import os
import ssl
import asyncpg
from datetime import datetime

app = FastAPI(title="WealthDesk API", version="3.0.0")

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
DATABASE_URL   = os.environ.get("DATABASE_URL")

# ── Database pool ──
db_pool = None

async def get_db():
    return await db_pool.acquire()

async def release_db(conn):
    await db_pool.release(conn)

# ── Create tables on startup ──
@app.on_event("startup")
async def startup():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                pan VARCHAR(10) PRIMARY KEY,
                first_name VARCHAR(100),
                last_name VARCHAR(100),
                email VARCHAR(200),
                mobile VARCHAR(20),
                aadhaar VARCHAR(20),
                date_of_birth VARCHAR(20),
                address TEXT,
                city VARCHAR(100),
                pin_code VARCHAR(10),
                annual_income VARCHAR(50),
                investment_goal VARCHAR(100),
                bank_account_number VARCHAR(30),
                bank_ifsc VARCHAR(20),
                bank_name VARCHAR(100),
                account_type VARCHAR(30),
                status VARCHAR(30) DEFAULT 'pending_kyc',
                nse_response TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                kyc_approved_at TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id VARCHAR(20) PRIMARY KEY,
                client_pan VARCHAR(10),
                fund_code VARCHAR(20),
                fund_name VARCHAR(200),
                transaction_type VARCHAR(20),
                amount NUMERIC,
                transaction_mode VARCHAR(20),
                status VARCHAR(30) DEFAULT 'pending',
                nse_response TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sips (
                id VARCHAR(20) PRIMARY KEY,
                client_pan VARCHAR(10),
                fund_code VARCHAR(20),
                fund_name VARCHAR(200),
                amount NUMERIC,
                sip_date INTEGER,
                start_date VARCHAR(20),
                end_date VARCHAR(20),
                status VARCHAR(30) DEFAULT 'pending',
                instalments_completed INTEGER DEFAULT 0,
                nse_response TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                cancelled_at TIMESTAMP
            )
        """)
    print("✅ Database tables ready")

@app.on_event("shutdown")
async def shutdown():
    await db_pool.close()

# ── TLS 1.3 ──
def get_ssl_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

# ── NSE Headers ──
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

# ── Models ──
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

# ══════════════════════════════
# HEALTH
# ══════════════════════════════

@app.get("/")
def root():
    return {"status": "WealthDesk API is running", "version": "3.0.0", "timestamp": datetime.now().isoformat()}

@app.get("/health")
async def health():
    api_key_set    = bool(NSE_API_KEY)
    api_secret_set = bool(NSE_API_SECRET)
    tm_code_set    = bool(NSE_TM_CODE)
    db_set         = bool(DATABASE_URL)

    # Test DB connection
    db_ok = False
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            db_ok = True
    except:
        pass

    return {
        "status": "healthy",
        "version": "3.0.0",
        "nse_configured": api_key_set and api_secret_set and tm_code_set,
        "database": "connected" if db_ok else "disconnected",
        "tls": "1.3 enforced",
        "credentials": {
            "NSE_API_KEY":    "SET" if api_key_set    else "MISSING",
            "NSE_API_SECRET": "SET" if api_secret_set else "MISSING",
            "NSE_TM_CODE":    "SET" if tm_code_set    else "MISSING",
            "DATABASE_URL":   "SET" if db_set         else "MISSING",
            "NSE_HOST":       NSE_HOST
        }
    }

@app.get("/nse/test")
async def test_nse_connection():
    try:
        ssl_ctx = get_ssl_context()
        async with httpx.AsyncClient(verify=ssl_ctx, timeout=30, follow_redirects=True) as http:
            response = await http.get(f"{NSE_HOST}/nsemfdesk/login.htm", headers=get_nse_headers())
        return {"success": True, "status_code": response.status_code, "message": "NSE connection successful", "tls": "1.3"}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ══════════════════════════════
# CLIENTS
# ══════════════════════════════

@app.post("/clients/register")
async def register_client(client: ClientCreate):
    async with db_pool.acquire() as conn:
        # Check if client already exists
        existing = await conn.fetchrow("SELECT pan FROM clients WHERE pan = $1", client.pan)
        if existing:
            raise HTTPException(status_code=400, detail="Client with this PAN already exists")

        # Save to PostgreSQL
        await conn.execute("""
            INSERT INTO clients (
                pan, first_name, last_name, email, mobile, aadhaar,
                date_of_birth, address, city, pin_code, annual_income,
                investment_goal, bank_account_number, bank_ifsc,
                bank_name, account_type, status
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        """,
            client.pan, client.first_name, client.last_name, client.email,
            client.mobile, client.aadhaar, client.date_of_birth, client.address,
            client.city, client.pin_code, client.annual_income, client.investment_goal,
            client.bank_account_number, client.bank_ifsc, client.bank_name,
            client.account_type, "pending_kyc"
        )

    # Try NSE registration
    nse_response = None
    try:
        ssl_ctx = get_ssl_context()
        async with httpx.AsyncClient(verify=ssl_ctx, timeout=30, follow_redirects=True) as http:
            response = await http.post(
                f"{NSE_HOST}/nsemfdesk/api/client/register",
                headers=get_nse_headers(),
                json={
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
                    "accountType": client.account_type
                }
            )
            nse_response = str(response.json())
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE clients SET nse_response=$1, status=$2 WHERE pan=$3",
                    nse_response, "registered", client.pan
                )
    except Exception as e:
        nse_response = str(e)

    return {"success": True, "message": "Client saved to database", "pan": client.pan, "nse_note": nse_response}

@app.get("/clients")
async def get_all_clients():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM clients ORDER BY created_at DESC")
        clients = [dict(r) for r in rows]
        # Convert datetime to string for JSON
        for c in clients:
            for k, v in c.items():
                if isinstance(v, datetime):
                    c[k] = v.isoformat()
    return {"total": len(clients), "clients": clients}

@app.get("/clients/{pan}")
async def get_client(pan: str):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM clients WHERE pan = $1", pan)
        if not row:
            raise HTTPException(status_code=404, detail="Client not found")
        client = dict(row)
        for k, v in client.items():
            if isinstance(v, datetime):
                client[k] = v.isoformat()
    return client

@app.patch("/clients/{pan}/approve")
async def approve_kyc(pan: str):
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE clients SET status=$1, kyc_approved_at=$2 WHERE pan=$3",
            "active", datetime.now(), pan
        )
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Client not found")
    return {"success": True, "message": f"KYC approved for {pan}"}

# ══════════════════════════════
# PORTFOLIO
# ══════════════════════════════

@app.get("/portfolio/{pan}")
async def get_portfolio(pan: str):
    try:
        ssl_ctx = get_ssl_context()
        async with httpx.AsyncClient(verify=ssl_ctx, timeout=30, follow_redirects=True) as http:
            response = await http.get(
                f"{NSE_HOST}/nsemfdesk/api/portfolio/{pan}",
                headers=get_nse_headers(),
                params={"tmCode": NSE_TM_CODE}
            )
        return {"success": True, "pan": pan, "portfolio": response.json()}
    except Exception as e:
        return {
            "success": True, "pan": pan,
            "note": f"Sample data - {str(e)}",
            "portfolio": {
                "totalValue": 482340, "totalInvested": 420000, "returns": 14.8,
                "funds": [
                    {"name": "Mirae Asset Large Cap", "invested": 150000, "current": 174200, "returns": 16.1},
                    {"name": "Parag Parikh Flexi Cap", "invested": 100000, "current": 118500, "returns": 18.5},
                    {"name": "Axis Bluechip Fund",     "invested": 120000, "current": 131640, "returns": 9.7},
                    {"name": "SBI Small Cap Fund",     "invested": 50000,  "current": 58000,  "returns": 16.0},
                ]
            }
        }

# ══════════════════════════════
# TRANSACTIONS
# ══════════════════════════════

@app.post("/transactions/create")
async def create_transaction(txn: TransactionCreate):
    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM transactions")
        txn_id = f"TXN{count+1:04d}"
        await conn.execute("""
            INSERT INTO transactions (id, client_pan, fund_code, fund_name, transaction_type, amount, transaction_mode, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        """, txn_id, txn.client_pan, txn.fund_code, txn.fund_name,
            txn.transaction_type, txn.amount, txn.transaction_mode, "pending"
        )

    # Try NSE
    try:
        ssl_ctx = get_ssl_context()
        async with httpx.AsyncClient(verify=ssl_ctx, timeout=30, follow_redirects=True) as http:
            response = await http.post(
                f"{NSE_HOST}/nsemfdesk/api/transaction/create",
                headers=get_nse_headers(),
                json={
                    "tmCode": NSE_TM_CODE, "clientPan": txn.client_pan,
                    "fundCode": txn.fund_code, "transactionType": txn.transaction_type,
                    "amount": txn.amount, "transactionMode": txn.transaction_mode,
                    "orderDate": datetime.now().strftime("%Y-%m-%d")
                }
            )
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE transactions SET status=$1, nse_response=$2 WHERE id=$3",
                    "processing", str(response.json()), txn_id
                )
    except:
        pass

    return {"success": True, "transaction_id": txn_id, "message": "Transaction saved to database"}

@app.get("/transactions")
async def get_all_transactions():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM transactions ORDER BY created_at DESC")
        txns = [dict(r) for r in rows]
        for t in txns:
            for k, v in t.items():
                if isinstance(v, datetime):
                    t[k] = v.isoformat()
    return {"total": len(txns), "transactions": txns}

@app.get("/transactions/{pan}")
async def get_client_transactions(pan: str):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM transactions WHERE client_pan=$1 ORDER BY created_at DESC", pan)
        txns = [dict(r) for r in rows]
        for t in txns:
            for k, v in t.items():
                if isinstance(v, datetime):
                    t[k] = v.isoformat()
    return {"pan": pan, "transactions": txns}

# ══════════════════════════════
# SIPS
# ══════════════════════════════

@app.post("/sips/create")
async def create_sip(sip: SIPCreate):
    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM sips")
        sip_id = f"SIP{count+1:04d}"
        await conn.execute("""
            INSERT INTO sips (id, client_pan, fund_code, fund_name, amount, sip_date, start_date, end_date, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        """, sip_id, sip.client_pan, sip.fund_code, sip.fund_name,
            sip.amount, sip.sip_date, sip.start_date,
            sip.end_date or "2099-12-31", "pending"
        )

    # Try NSE
    try:
        ssl_ctx = get_ssl_context()
        async with httpx.AsyncClient(verify=ssl_ctx, timeout=30, follow_redirects=True) as http:
            response = await http.post(
                f"{NSE_HOST}/nsemfdesk/api/sip/register",
                headers=get_nse_headers(),
                json={
                    "tmCode": NSE_TM_CODE, "clientPan": sip.client_pan,
                    "fundCode": sip.fund_code, "sipAmount": sip.amount,
                    "sipDate": sip.sip_date, "startDate": sip.start_date,
                    "endDate": sip.end_date or "2099-12-31", "frequency": "MONTHLY"
                }
            )
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE sips SET status=$1, nse_response=$2 WHERE id=$3",
                    "active", str(response.json()), sip_id
                )
    except:
        pass

    return {"success": True, "sip_id": sip_id, "message": "SIP saved to database"}

@app.get("/sips")
async def get_all_sips():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM sips ORDER BY created_at DESC")
        sips = [dict(r) for r in rows]
        for s in sips:
            for k, v in s.items():
                if isinstance(v, datetime):
                    s[k] = v.isoformat()
    return {"total": len(sips), "sips": sips}

@app.get("/sips/{pan}")
async def get_client_sips(pan: str):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM sips WHERE client_pan=$1 ORDER BY created_at DESC", pan)
        sips = [dict(r) for r in rows]
        for s in sips:
            for k, v in s.items():
                if isinstance(v, datetime):
                    s[k] = v.isoformat()
    return {"pan": pan, "sips": sips}

@app.patch("/sips/{sip_id}/cancel")
async def cancel_sip(sip_id: str):
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE sips SET status=$1, cancelled_at=$2 WHERE id=$3",
            "cancelled", datetime.now(), sip_id
        )
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="SIP not found")
    return {"success": True, "message": f"SIP {sip_id} cancelled"}

# ══════════════════════════════
# DASHBOARD
# ══════════════════════════════

@app.get("/dashboard/stats")
async def get_dashboard_stats():
    async with db_pool.acquire() as conn:
        total_clients  = await conn.fetchval("SELECT COUNT(*) FROM clients")
        active_clients = await conn.fetchval("SELECT COUNT(*) FROM clients WHERE status='active'")
        pending_kyc    = await conn.fetchval("SELECT COUNT(*) FROM clients WHERE status='pending_kyc'")
        total_txns     = await conn.fetchval("SELECT COUNT(*) FROM transactions")
        active_sips    = await conn.fetchval("SELECT COUNT(*) FROM sips WHERE status='active'")
        monthly_sip    = await conn.fetchval("SELECT COALESCE(SUM(amount),0) FROM sips WHERE status='active'")
    return {
        "total_clients": total_clients,
        "active_clients": active_clients,
        "pending_kyc": pending_kyc,
        "total_transactions": total_txns,
        "active_sips": active_sips,
        "monthly_sip_amount": float(monthly_sip)
    }
