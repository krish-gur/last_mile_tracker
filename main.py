from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime

import models
from database import engine, SessionLocal
from notifications import send_status_notification

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# Auto-create tables in SQLite on startup
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Last-Mile Delivery Tracker API",
    description="Backend service managing dynamic rate cards, agent assignment, and tracking.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database session dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Request Schemas
class RateEstimateRequest(BaseModel):
    pickup_zone_id: int
    drop_zone_id: int
    actual_weight: float
    length: float
    breadth: float
    height: float
    order_type: str  # "B2B" or "B2C"
    payment_type: str  # "Prepaid" or "COD"

class OrderCreateRequest(RateEstimateRequest):
    customer_id: int
    pickup_address: str
    drop_address: str

class StatusUpdateRequest(BaseModel):
    status: str  # "Picked Up", "In Transit", "Out for Delivery", "Delivered", "Failed"
    changed_by_user_id: int
    notes: Optional[str] = None

class RescheduleRequest(BaseModel):
    customer_id: int
    new_delivery_date: datetime

# Helper: Pricing Engine
def compute_pricing(data: RateEstimateRequest, db: Session):
    # Volumetric weight formula: (L * B * H) + 5000
    volumetric_weight = (data.length * data.breadth * data.height) + 5000
    billable_weight = max(data.actual_weight, volumetric_weight)

    rate_card = db.query(models.RateCard).filter(
        models.RateCard.source_zone_id == data.pickup_zone_id,
        models.RateCard.dest_zone_id == data.drop_zone_id,
        models.RateCard.order_type == data.order_type
    ).first()

    if not rate_card:
        raise HTTPException(
            status_code=404, 
            detail="Rate card not found for this zone route and order type"
        )

    base_charge = billable_weight * rate_card.rate_per_kg
    cod_fee = rate_card.cod_surcharge if data.payment_type.upper() == "COD" else 0.0
    final_charge = base_charge + cod_fee

    return {
        "volumetric_weight": volumetric_weight,
        "billable_weight": billable_weight,
        "rate_per_kg": rate_card.rate_per_kg,
        "cod_surcharge": cod_fee,
        "final_charge": final_charge
    }

# Helper: Log immutable tracking history
def record_history(db: Session, order_id: int, status: str, user_id: int, notes: str = None):
    entry = models.OrderTrackingHistory(
        order_id=order_id,
        status=status,
        changed_by_user_id=user_id,
        notes=notes
    )
    db.add(entry)

# API Endpoints
@app.get("/", include_in_schema=False)
def serve_dashboard():
    return FileResponse("index.html")
def read_root():
    return {
        "status": "online",
        "docs_url": "http://127.0.0.1:8000/docs",
        "message": "Last-Mile Delivery Tracker API is running"
    }

@app.post("/orders/estimate-rate")
def estimate_rate(request: RateEstimateRequest, db: Session = Depends(get_db)):
    return compute_pricing(request, db)

@app.post("/orders/create")
def create_order(request: OrderCreateRequest, db: Session = Depends(get_db)):
    pricing = compute_pricing(request, db)

    new_order = models.Order(
        customer_id=request.customer_id,
        pickup_zone_id=request.pickup_zone_id,
        drop_zone_id=request.drop_zone_id,
        pickup_address=request.pickup_address,
        drop_address=request.drop_address,
        order_type=request.order_type,
        payment_type=request.payment_type,
        actual_weight=request.actual_weight,
        volumetric_weight=pricing["volumetric_weight"],
        billable_weight=pricing["billable_weight"],
        final_charge=pricing["final_charge"],
        status="Order Placed"
    )
    db.add(new_order)
    db.commit()
    db.refresh(new_order)

    record_history(db, new_order.id, "Order Placed", request.customer_id, "Order created")
    db.commit()

    # Notify customer
    customer = db.query(models.User).filter(models.User.id == new_order.customer_id).first()
    if customer:
        send_status_notification(customer.email, new_order.id, "Order Placed", f"Charge: {new_order.final_charge}")

    return {"order_id": new_order.id, "status": new_order.status, "pricing": pricing}

@app.post("/orders/{order_id}/auto-assign")
def auto_assign_agent(order_id: int, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Find available agent in pickup zone or fallback to any available agent
    agent = db.query(models.User).filter(
        models.User.role == "agent",
        models.User.is_available == True,
        models.User.current_zone_id == order.pickup_zone_id
    ).first()

    if not agent:
        agent = db.query(models.User).filter(
            models.User.role == "agent",
            models.User.is_available == True
        ).first()

    if not agent:
        raise HTTPException(status_code=404, detail="No available delivery agents found")

    order.agent_id = agent.id
    record_history(db, order.id, order.status, agent.id, f"Agent {agent.name} assigned")
    db.commit()

    return {"order_id": order.id, "assigned_agent_id": agent.id, "agent_name": agent.name}

@app.patch("/orders/{order_id}/status")
def update_status(order_id: int, payload: StatusUpdateRequest, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.status = payload.status
    record_history(db, order.id, payload.status, payload.changed_by_user_id, payload.notes)
    db.commit()

    customer = db.query(models.User).filter(models.User.id == order.customer_id).first()
    if customer:
        send_status_notification(
            customer_email=customer.email,
            order_id=order.id,
            new_status=payload.status,
            notes=payload.notes or ""
        )

    return {
        "order_id": order.id,
        "current_status": order.status,
        "notes": payload.notes
    }

@app.post("/orders/{order_id}/reschedule")
def reschedule_delivery(order_id: int, payload: RescheduleRequest, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
        
    if order.status != "Failed":
        raise HTTPException(
            status_code=400, 
            detail="Only failed deliveries can be rescheduled"
        )

    agent = db.query(models.User).filter(
        models.User.role == "agent",
        models.User.is_available == True
    ).first()

    order.rescheduled_date = payload.new_delivery_date
    order.status = "Rescheduled"
    if agent:
        order.agent_id = agent.id

    note_text = f"Rescheduled to {payload.new_delivery_date.strftime('%Y-%m-%d %H:%M')}. Reassigned to {agent.name if agent else 'Pending Agent'}."
    record_history(db, order.id, "Rescheduled", payload.customer_id, note_text)
    db.commit()

    customer = db.query(models.User).filter(models.User.id == order.customer_id).first()
    if customer:
        send_status_notification(
            customer_email=customer.email,
            order_id=order.id,
            new_status="Rescheduled",
            notes=note_text
        )

    return {
        "order_id": order.id,
        "status": order.status,
        "rescheduled_date": order.rescheduled_date,
        "assigned_agent_id": order.agent_id
    }

@app.get("/orders/{order_id}/tracking")
def get_order_tracking(order_id: int, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    history = db.query(models.OrderTrackingHistory).filter(
        models.OrderTrackingHistory.order_id == order_id
    ).order_by(models.OrderTrackingHistory.timestamp.asc()).all()

    return {
        "order_id": order.id,
        "current_status": order.status,
        "payment_type": order.payment_type,
        "final_charge": order.final_charge,
        "history": [
            {
                "status": h.status,
                "changed_by_user_id": h.changed_by_user_id,
                "notes": h.notes,
                "timestamp": h.timestamp
            }
            for h in history
        ]
    }

@app.get("/admin/orders")
def get_admin_orders(
    status: Optional[str] = Query(None, description="Filter by status"),
    zone_id: Optional[int] = Query(None, description="Filter by pickup zone"),
    agent_id: Optional[int] = Query(None, description="Filter by assigned agent"),
    db: Session = Depends(get_db)
):
    query = db.query(models.Order)
    if status:
        query = query.filter(models.Order.status == status)
    if zone_id:
        query = query.filter(models.Order.pickup_zone_id == zone_id)
    if agent_id:
        query = query.filter(models.Order.agent_id == agent_id)
        
    return query.all()

@app.post("/admin/orders/{order_id}/override-status")
def override_order_status(
    order_id: int, 
    new_status: str, 
    admin_id: int, 
    notes: Optional[str] = "Admin manual override", 
    db: Session = Depends(get_db)
):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.status = new_status
    record_history(db, order.id, new_status, admin_id, notes)
    db.commit()

    customer = db.query(models.User).filter(models.User.id == order.customer_id).first()
    if customer:
        send_status_notification(customer.email, order.id, new_status, notes)

    return {"message": "Status overridden successfully", "order_id": order.id, "new_status": order.status}