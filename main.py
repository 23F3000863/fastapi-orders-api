import time
from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

# ---------------------------------------------------------
# CORS CONFIGURATION
# ---------------------------------------------------------
# This allows the grading website to safely communicate with your API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],  # Allows all headers (Idempotency-Key, X-Client-Id)
)

# ---------------------------------------------------------
# IN-MEMORY DATABASE & STATE
# ---------------------------------------------------------
# 1. Catalog: 50 fixed orders (IDs 1 to 50)
TOTAL_ORDERS = 50
ORDERS_CATALOG = [{"id": i, "item": f"Item {i}", "price": 10.0 * i} for i in range(1, TOTAL_ORDERS + 1)]

# 2. Idempotency Storage: Maps "idempotency_key" -> existing order object
idempotency_store = {}
next_mock_id = 101  # Dynamic IDs for newly created orders start here

# 3. Rate Limiting Storage: Maps "client_id" -> list of request timestamps
rate_limit_store = {}
RATE_LIMIT_MAX = 17
WINDOW_SECONDS = 10.0


# ---------------------------------------------------------
# DATA MODELS
# ---------------------------------------------------------
class OrderCreate(BaseModel):
    item: str
    price: float


# ---------------------------------------------------------
# 1. IDEMPOTENT ORDER CREATION (POST)
# ---------------------------------------------------------
@app.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(
    order_data: OrderCreate,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    global next_mock_id
    
    # If no key is provided, just create a normal transient order
    if not idempotency_key:
        new_id = next_mock_id
        next_mock_id += 1
        return {"id": str(new_id), "item": order_data.item, "price": order_data.price}

    # If key already exists, return the exact same stored order with HTTP 201
    if idempotency_key in idempotency_store:
        return idempotency_store[idempotency_key]

    # First time seeing this key: create it, store it, and return it
    new_id = next_mock_id
    next_mock_id += 1
    
    saved_order = {"id": str(new_id), "item": order_data.item, "price": order_data.price}
    idempotency_store[idempotency_key] = saved_order
    return saved_order


# ---------------------------------------------------------
# 2. CURSOR PAGINATION (GET)
# ---------------------------------------------------------
@app.get("/orders")
def get_orders(
    limit: int = 10, 
    cursor: Optional[str] = None,
    x_client_id: Optional[str] = Header(None, alias="X-Client-Id")
):
    # Enforce rate limit checking on this endpoint if the header is passed
    if x_client_id:
        check_rate_limit(x_client_id)

    # Determine starting index based on the cursor
    start_index = 0
    if cursor:
        try:
            # We use the item ID string as our opaque cursor
            start_index = int(cursor)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid cursor format")

    # Slice the data safely up to the requested limit
    end_index = start_index + limit
    page_items = ORDERS_CATALOG[start_index:end_index]

    # Calculate the next cursor string if there are more items left
    next_cursor = None
    if end_index < TOTAL_ORDERS:
        next_cursor = str(end_index)

    return {
        "items": page_items,
        "next_cursor": next_cursor
    }


# ---------------------------------------------------------
# 3. RATE LIMITING UTILITY
# ---------------------------------------------------------
def check_rate_limit(client_id: str):
    now = time.time()
    
    # Initialize timestamp tracker list for a new client
    if client_id not in rate_limit_store:
        rate_limit_store[client_id] = []
        
    timestamps = rate_limit_store[client_id]
    
    # Evict/remove timestamps older than 10 seconds ago
    valid_window_start = now - WINDOW_SECONDS
    rate_limit_store[client_id] = [t for t in timestamps if t > valid_window_start]
    
    # If the client has used up all 17 allowed slots, block them
    if len(rate_limit_store[client_id]) >= RATE_LIMIT_MAX:
        # Calculate exactly how long until the oldest request falls out of the window
        oldest_request = rate_limit_store[client_id][0]
        retry_after = int((oldest_request + WINDOW_SECONDS) - now) + 1
        
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(max(1, retry_after))}
        )
        
    # If under the limit, record this current request timestamp
    rate_limit_store[client_id].append(now)
