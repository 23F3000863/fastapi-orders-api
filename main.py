import time
import uuid
import base64
from typing import Dict, List, Optional
from fastapi import FastAPI, Request, Response, HTTPException, status, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Unified API Engineering Challenges")

# ---------------------------------------------------------------------------
# Assigned Constants & Data Stores
# ---------------------------------------------------------------------------
TOTAL_ORDERS = 50
ORDERS_LIMIT = 17
ORDERS_WINDOW = 10.0
ORDERS_CATALOG = [{"id": i, "item": f"Item-{i}", "price": float(i * 10)} for i in range(1, TOTAL_ORDERS + 1)]

IDEMPOTENCY_STORE: Dict[str, dict] = {}
ORDERS_RATE_STORE: Dict[str, List[float]] = {}

PING_LIMIT = 11
PING_WINDOW = 10.0
PING_RATE_STORE: Dict[str, List[float]] = {}

# ---------------------------------------------------------------------------
# Core Middleware Layer (Rate Limiting & Context Generation)
# ---------------------------------------------------------------------------
@app.middleware("http")
async def context_and_rate_limit_middleware(request: Request, call_next):
    # 1. IMMEDIATELY bypass custom logic for CORS preflight OPTIONS requests
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path

    # 2. Request Context Generation
    req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = req_id
    
    # 3. Per-Client Rate Limiting Buckets
    client_id = request.headers.get("X-Client-Id")
    if client_id:
        now = time.time()
        
        if "/orders" in path:
            if client_id not in ORDERS_RATE_STORE:
                ORDERS_RATE_STORE[client_id] = []
            ORDERS_RATE_STORE[client_id] = [ts for ts in ORDERS_RATE_STORE[client_id] if now - ts < ORDERS_WINDOW]
            
            if len(ORDERS_RATE_STORE[client_id]) >= ORDERS_LIMIT:
                oldest_ts = ORDERS_RATE_STORE[client_id][0]
                retry_after = int(max(1, ORDERS_WINDOW - (now - oldest_ts)))
                res = JSONResponse(
                    status_code=429, 
                    content={"detail": "Orders rate limit exceeded"}
                )
                res.headers["Retry-After"] = str(retry_after)
                res.headers["X-Request-ID"] = req_id
                return res
            ORDERS_RATE_STORE[client_id].append(now)
            
        elif "/ping" in path:
            if client_id not in PING_RATE_STORE:
                PING_RATE_STORE[client_id] = []
            PING_RATE_STORE[client_id] = [ts for ts in PING_RATE_STORE[client_id] if now - ts < PING_WINDOW]
            
            if len(PING_RATE_STORE[client_id]) >= PING_LIMIT:
                res = JSONResponse(
                    status_code=429, 
                    content={"detail": "Ping rate limit exceeded"}
                )
                res.headers["X-Request-ID"] = req_id
                return res
            PING_RATE_STORE[client_id].append(now)

    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response

# ---------------------------------------------------------------------------
# Strict Native CORS Middleware (Must be declared AFTER custom middlewares)
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Essential for cross-origin browser verification pages
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Retry-After", "Idempotency-Key"],
)

# ---------------------------------------------------------------------------
# Problem 1: Orders Routes
# ---------------------------------------------------------------------------
class OrderCreate(BaseModel):
    item: Optional[str] = "Default Item"
    price: Optional[float] = 0.0

@app.post("/orders")
async def create_order(request: Request, response: Response, data: Optional[OrderCreate] = None):
    data = data or OrderCreate()
    idempotency_key = request.headers.get("Idempotency-Key")
    
    if not idempotency_key:
        response.status_code = status.HTTP_201_CREATED
        return {"id": f"ord_{uuid.uuid4().hex[:12]}", "item": data.item, "price": data.price}
        
    if idempotency_key in IDEMPOTENCY_STORE:
        response.status_code = status.HTTP_200_OK
        return IDEMPOTENCY_STORE[idempotency_key]
        
    order_id = f"ord_{uuid.uuid4().hex[:12]}"
    saved = {"id": order_id, "item": data.item, "price": data.price}
    IDEMPOTENCY_STORE[idempotency_key] = saved
    
    response.status_code = status.HTTP_201_CREATED
    return saved

@app.get("/orders")
async def list_orders(limit: int = Query(default=10, ge=1), cursor: Optional[str] = Query(default=None)):
    start_idx = 0
    if cursor:
        try:
            start_idx = int(base64.b64decode(cursor.encode("utf-8")).decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Malformed cursor structure.")
            
    sliced = ORDERS_CATALOG[start_idx : start_idx + limit]
    next_idx = start_idx + limit
    
    next_cursor = None
    if next_idx < len(ORDERS_CATALOG):
        next_cursor = base64.b64encode(str(next_idx).encode("utf-8")).decode("utf-8")
        
    return {"items": sliced, "next_cursor": next_cursor}

# ---------------------------------------------------------------------------
# Problem 2: Context Middleware Route
# ---------------------------------------------------------------------------
@app.get("/ping")
async def ping(request: Request):
    return {
        "email": "your-registered-email@example.com",  # Replace with your actual email string
        "request_id": request.state.request_id
    }

@app.get("/")
async def health():
    return {"status": "healthy"}
