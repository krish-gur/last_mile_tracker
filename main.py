import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import models
from database import engine, SessionLocal
import notifications

# Create database tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Last-Mile Delivery Tracker API",
    description="Backend API for managing rates, automated agent assignments, status transitions, and audit logs.",
    version="1.0.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependency for DB Session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Auto-seed database on server startup if empty
@app.on_event("startup")
def startup_event():
    import seed
    db = SessionLocal()
    try:
        if not db.query(models.User).first():
            seed.seed_data()
    except Exception as e:
        print(f"Startup check/seeding note: {e}")
    finally:
        db.close()


# -------------------------------------------------------------------------
# Pydantic Request & Response Schemas
# -------------------------------------------------------------------------

class RateEstimateRequest(BaseModel):
    pickup_zone_id: int
    drop_zone_id: int
    order_type: str = Field(..., description="'B2B' or 'B2C'")
    payment_type: str = Field(..., description="'Prepaid' or 'COD'")
    actual_weight: float
    length: float
    breadth: float
    height: float

class RateEstimateResponse(BaseModel):
    actual_weight: float
    volumetric_weight: float
    billable_weight: float
    rate_per_kg: float
    base_cost: float
    cod_surcharge: float
    final_charge: float

class OrderCreateRequest(RateEstimateRequest):
    customer_id: int
    pickup_address: str
    drop_address: str

class StatusUpdateRequest(BaseModel):
    new_status: str = Field(..., description="'Picked Up', 'In Transit', 'Out for Delivery', 'Delivered', 'Failed'")
    changed_by_user_id: int
    notes: Optional[str] = None

class RescheduleRequest(BaseModel):
    rescheduled_date: str
    customer_id: int

class AdminStatusOverrideRequest(BaseModel):
    admin_id: int
    new_status: str
    override_reason: str


# -------------------------------------------------------------------------
# Pricing Calculation Helper
# -------------------------------------------------------------------------

def compute_pricing(data: RateEstimateRequest, db: Session) -> dict:
    volumetric_weight = (data.length * data.breadth * data.height) + 5000
    billable_weight = max(data.actual_weight, volumetric_weight)

    rate_card = db.query(models.RateCard).filter(
        models.RateCard.source_zone_id == data.pickup_zone_id,
        models.RateCard.dest_zone_id == data.drop_zone_id,
        models.RateCard.order_type == data.order_type.upper()
    ).first()

    if not rate_card:
        raise HTTPException(
            status_code=404, 
            detail=f"Rate card not found for route Zone {data.pickup_zone_id} -> Zone {data.drop_zone_id} with type {data.order_type}"
        )

    base_cost = billable_weight * rate_card.rate_per_kg
    cod_surcharge = rate_card.cod_surcharge if data.payment_type.upper() == "COD" else 0.0
    final_charge = base_cost + cod_surcharge

    return {
        "actual_weight": round(data.actual_weight, 2),
        "volumetric_weight": round(volumetric_weight, 2),
        "billable_weight": round(billable_weight, 2),
        "rate_per_kg": float(rate_card.rate_per_kg),
        "base_cost": round(base_cost, 2),
        "cod_surcharge": float(cod_surcharge),
        "final_charge": round(final_charge, 2)
    }


# -------------------------------------------------------------------------
# API Endpoints
# -------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def serve_dashboard():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"status": "online", "docs_url": "/docs", "message": "Last-Mile Delivery Tracker API is running"}


@app.post("/orders/estimate-rate", response_model=RateEstimateResponse)
def estimate_rate(request: RateEstimateRequest, db: Session = Depends(get_db)):
    return compute_pricing(request, db)


@app.post("/orders/create")
def create_order(request: OrderCreateRequest, db: Session = Depends(get_db)):
    customer = db.query(models.User).filter(models.User.id == request.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer user not found")

    pricing = compute_pricing(request, db)

    new_order = models.Order(
        customer_id=request.customer_id,
        pickup_zone_id=request.pickup_zone_id,
        drop_zone_id=request.drop_zone_id,
        pickup_address=request.pickup_address,
        drop_address=request.drop_address,
        order_type=request.order_type.upper(),
        payment_type=request.payment_type.upper(),
        actual_weight=pricing["actual_weight"],
        volumetric_weight=pricing["volumetric_weight"],
        billable_weight=pricing["billable_weight"],
        final_charge=pricing["final_charge"],
        status="Order Placed"
    )
    db.add(new_order)
    db.commit()
    db.refresh(new_order)

    # Log initial tracking history
    initial_log = models.OrderTrackingHistory(
        order_id=new_order.id,
        status="Order Placed",
        changed_by_user_id=request.customer_id,
        timestamp=datetime.utcnow(),
        notes="Order created and submitted by customer"
    )
    db.add(initial_log)
    db.commit()

    notifications.notify_order_placed(customer.email, new_order.id, new_order.final_charge)

    return {
        "message": "Order created successfully",
        "order_id": new_order.id,
        "status": new_order.status,
        "pricing_summary": pricing
    }


@app.post("/orders/{order_id}/auto-assign")
def auto_assign_agent(order_id: int, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Find available agent in pickup zone
    available_agent = db.query(models.User).filter(
        models.User.role == "agent",
        models.User.current_zone_id == order.pickup_zone_id,
        models.User.is_available == True
    ).first()

    # Fallback to any available agent
    if not available_agent:
        available_agent = db.query(models.User).filter(
            models.User.role == "agent",
            models.User.is_available == True
        ).first()

    if not available_agent:
        raise HTTPException(status_code=400, detail="No delivery agents currently available")

    order.agent_id = available_agent.id
    order.status = "Agent Assigned"
    available_agent.is_available = False

    history_entry = models.OrderTrackingHistory(
        order_id=order.id,
        status="Agent Assigned",
        changed_by_user_id=available_agent.id,
        timestamp=datetime.utcnow(),
        notes=f"Assigned to agent {available_agent.name} (ID: {available_agent.id})"
    )
    db.add(history_entry)
    db.commit()

    customer = db.query(models.User).filter(models.User.id == order.customer_id).first()
    if customer:
        notifications.notify_status_update(customer.email, order.id, "Agent Assigned")

    return {
        "message": f"Agent {available_agent.name} assigned successfully",
        "order_id": order.id,
        "agent_id": available_agent.id,
        "status": order.status
    }


@app.patch("/orders/{order_id}/status")
def update_order_status(order_id: int, request: StatusUpdateRequest, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    valid_statuses = ["Picked Up", "In Transit", "Out for Delivery", "Delivered", "Failed"]
    if request.new_status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of {valid_statuses}")

    order.status = request.new_status

    if request.new_status in ["Delivered", "Failed"] and order.agent_id:
        agent = db.query(models.User).filter(models.User.id == order.agent_id).first()
        if agent:
            agent.is_available = True

    history_entry = models.OrderTrackingHistory(
        order_id=order.id,
        status=request.new_status,
        changed_by_user_id=request.changed_by_user_id,
        timestamp=datetime.utcnow(),
        notes=request.notes or f"Status transitioned to {request.new_status}"
    )
    db.add(history_entry)
    db.commit()

    customer = db.query(models.User).filter(models.User.id == order.customer_id).first()
    if customer:
        notifications.notify_status_update(customer.email, order.id, request.new_status)

    return {
        "message": f"Order status updated to {request.new_status}",
        "order_id": order.id,
        "current_status": order.status
    }


@app.post("/orders/{order_id}/reschedule")
def reschedule_order(order_id: int, request: RescheduleRequest, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != "Failed":
        raise HTTPException(status_code=400, detail="Only failed delivery orders can be rescheduled")

    order.rescheduled_date = request.rescheduled_date
    order.status = "Rescheduled"

    # Auto-assign available agent for rescheduled delivery
    available_agent = db.query(models.User).filter(
        models.User.role == "agent",
        models.User.current_zone_id == order.pickup_zone_id,
        models.User.is_available == True
    ).first()

    if available_agent:
        order.agent_id = available_agent.id
        available_agent.is_available = False
        notes = f"Rescheduled for {request.rescheduled_date}. Reassigned to Agent {available_agent.name} (ID: {available_agent.id})"
    else:
        notes = f"Rescheduled for {request.rescheduled_date}. Awaiting agent reassignment"

    history_entry = models.OrderTrackingHistory(
        order_id=order.id,
        status="Rescheduled",
        changed_by_user_id=request.customer_id,
        timestamp=datetime.utcnow(),
        notes=notes
    )
    db.add(history_entry)
    db.commit()

    customer = db.query(models.User).filter(models.User.id == order.customer_id).first()
    if customer:
        notifications.notify_rescheduled(customer.email, order.id, request.rescheduled_date)

    return {
        "message": "Order rescheduled successfully",
        "order_id": order.id,
        "rescheduled_date": order.rescheduled_date,
        "agent_id": order.agent_id,
        "status": order.status
    }


@app.get("/orders/{order_id}/tracking")
def get_order_tracking(order_id: int, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    history = db.query(models.OrderTrackingHistory).filter(
        models.OrderTrackingHistory.order_id == order_id
    ).order_by(models.OrderTrackingHistory.timestamp.asc()).all()

    timeline = [
        {
            "status": entry.status,
            "changed_by_user_id": entry.changed_by_user_id,
            "timestamp": entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "notes": entry.notes
        }
        for entry in history
    ]

    return {
        "order_id": order.id,
        "current_status": order.status,
        "customer_id": order.customer_id,
        "agent_id": order.agent_id,
        "pickup_address": order.pickup_address,
        "drop_address": order.drop_address,
        "final_charge": order.final_charge,
        "timeline": timeline
    }


@app.get("/admin/orders")
def get_admin_orders(
    status: Optional[str] = Query(None, description="Filter by status"),
    zone_id: Optional[int] = Query(None, description="Filter by pickup/drop zone ID"),
    agent_id: Optional[int] = Query(None, description="Filter by assigned agent ID"),
    db: Session = Depends(get_db)
):
    query = db.query(models.Order)

    if status:
        query = query.filter(models.Order.status == status)
    if zone_id:
        query = query.filter((models.Order.pickup_zone_id == zone_id) | (models.Order.drop_zone_id == zone_id))
    if agent_id:
        query = query.filter(models.Order.agent_id == agent_id)

    orders = query.order_by(models.Order.created_at.desc()).all()

    return {
        "total_results": len(orders),
        "orders": [
            {
                "id": o.id,
                "customer_id": o.customer_id,
                "agent_id": o.agent_id,
                "pickup_zone_id": o.pickup_zone_id,
                "drop_zone_id": o.drop_zone_id,
                "order_type": o.order_type,
                "payment_type": o.payment_type,
                "billable_weight": o.billable_weight,
                "final_charge": o.final_charge,
                "status": o.status,
                "created_at": o.created_at.strftime("%Y-%m-%d %H:%M:%S")
            }
            for o in orders
        ]
    }


@app.post("/admin/orders/{order_id}/override-status")
def admin_override_status(order_id: int, request: AdminStatusOverrideRequest, db: Session = Depends(get_db)):
    admin_user = db.query(models.User).filter(
        models.User.id == request.admin_id,
        models.User.role == "admin"
    ).first()
    if not admin_user:
        raise HTTPException(status_code=403, detail="Unauthorized: Valid Admin ID required")

    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.status = request.new_status

    history_entry = models.OrderTrackingHistory(
        order_id=order.id,
        status=request.new_status,
        changed_by_user_id=request.admin_id,
        timestamp=datetime.utcnow(),
        notes=f"Admin Override: {request.override_reason}"
    )
    db.add(history_entry)
    db.commit()

    customer = db.query(models.User).filter(models.User.id == order.customer_id).first()
    if customer:
        notifications.notify_status_update(customer.email, order.id, f"{request.new_status} (Admin Override)")

    return {
        "message": f"Order status overridden to {request.new_status} by Admin",
        "order_id": order.id,
        "current_status": order.status
    }