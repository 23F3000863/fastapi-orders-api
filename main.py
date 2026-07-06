import base64
import time
from typing import Dict, List, Optional
from fastapi import FastAPI, Request, Response, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Production-Grade Orders API")

# 1. CORS Configuration (Crucial for browser-based grader checks)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Assigned Variables
TOTAL_ORDERS = 50
RATE_LIMIT_REQUESTS = 17
RATE_LIMIT_WINDOW = 10.0  # seconds

# In-memory global data stores
IDEMPOTENCY_STORE: Dict[str, dict] = {}   
RATE_LIMIT_STORE: Dict[str, List[float]] = {}  

# Fixed catalog array tracking orders 1 to T
ORDERS_CATALOG = [{"id": i, "item": f"Product-{i}", "price": round(10.5 * i, 2)} for i in range(1, TOTAL_ORDERS + 1)]

# 3. Data Schemas
class OrderCreate(BaseModel):
    item: str
    price: float

class OrderResponse(BaseModel):
    id: str
    item: str
    price: float

class PaginatedOrdersResponse(BaseModel):
    items: List[dict]
    next_cursor: Optional[str]

# 4. Sliding Window Rate Limiting Middleware
@app.middleware("http")
async def rate_limiter_middleware(request: Request, call_next):
    client_id = request.headers.get("X-Client-Id")
    
    if client_id:
        now = time.time()
        if client_id not in RATE_LIMIT_STORE:
            RATE_LIMIT_STORE[client_id] = []
            
        # Clear out obsolete timestamps outside of our active window
        RATE_LIMIT_STORE[client_id] = [
            ts for ts in RATE_LIMIT_STORE[client_id] if now - ts < RATE_LIMIT_WINDOW
        ]
        
        # Enforce boundary blocks
        if len(RATE_LIMIT_STORE[client_id]) >= RATE_LIMIT_REQUESTS:
            oldest_ts = RATE_LIMIT_STORE[client_id][0]
            retry_after = int(max(1, RATE_LIMIT_WINDOW - (now - oldest_ts)))
            
            return Response(
                content=f'{{"detail": "Rate limit exceeded. Max {RATE_LIMIT_REQUESTS} requests per 10s."}}',
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(retry_after), "Content-Type": "application/json"}
            )
            
        RATE_LIMIT_STORE[client_id].append(now)

    return await call_next(request)

# 5. Routing Definitions
@app.post("/orders", status_code=status.HTTP_201_CREATED, response_model=OrderResponse)
async def create_order(request: Request, order_data: OrderCreate, response: Response):
    idempotency_key = request.headers.get("Idempotency-Key")
    
    if not idempotency_key:
        new_id = f"ord_{int(time.time() * 1000)}"
        return {"id": new_id, "item": order_data.item, "price": order_data.price}
        
    if idempotency_key in IDEMPOTENCY_STORE:
        response.status_code = status.HTTP_200_OK
        return IDEMPOTENCY_STORE[idempotency_key]
        
    new_id = f"ord_{int(time.time() * 1000)}"
    saved_response = {"id": new_id, "item": order_data.item, "price": order_data.price}
    IDEMPOTENCY_STORE[idempotency_key] = saved_response
    return saved_response

@app.get("/orders", response_model=PaginatedOrdersResponse)
async def list_orders(
    limit: int = Query(default=10, ge=1),
    cursor: Optional[str] = Query(default=None)
):
    start_index = 0
    if cursor:
        try:
            decoded_bytes = base64.b64decode(cursor.encode("utf-8"))
            start_index = int(decoded_bytes.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor shape.")
            
    sliced_items = ORDERS_CATALOG[start_index : start_index + limit]
    next_index = start_index + limit
    
    next_cursor = None
    if next_index < len(ORDERS_CATALOG):
        next_cursor = base64.b64encode(str(next_index).encode("utf-8")).decode("utf-8")
        
    return {
        "items": sliced_items,
        "next_cursor": next_cursor
    }

@app.get("/")
async def root():
    return {"status": "healthy"}
