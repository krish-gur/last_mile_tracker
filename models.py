from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String, unique=True, index=True)
    role = Column(String)  # 'admin', 'customer', 'agent'
    current_zone_id = Column(Integer, ForeignKey("zones.id"), nullable=True)
    is_available = Column(Boolean, default=True)

class Zone(Base):
    __tablename__ = "zones"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True)
    area_names = Column(String)

class RateCard(Base):
    __tablename__ = "rate_cards"
    
    id = Column(Integer, primary_key=True, index=True)
    source_zone_id = Column(Integer, ForeignKey("zones.id"))
    dest_zone_id = Column(Integer, ForeignKey("zones.id"))
    order_type = Column(String)  # 'B2B' or 'B2C'
    rate_per_kg = Column(Float)
    cod_surcharge = Column(Float, default=0.0)

class Order(Base):
    __tablename__ = "orders"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("users.id"))
    agent_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    pickup_zone_id = Column(Integer, ForeignKey("zones.id"))
    drop_zone_id = Column(Integer, ForeignKey("zones.id"))
    
    pickup_address = Column(String)
    drop_address = Column(String)
    order_type = Column(String)  # 'B2B' or 'B2C'
    payment_type = Column(String)  # 'Prepaid' or 'COD'
    
    actual_weight = Column(Float)
    volumetric_weight = Column(Float)
    billable_weight = Column(Float)
    final_charge = Column(Float)
    
    status = Column(String, default="Order Placed")  # Picked Up, In Transit, Out for Delivery, Delivered, Failed
    rescheduled_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    tracking_history = relationship("OrderTrackingHistory", back_populates="order")

class OrderTrackingHistory(Base):
    __tablename__ = "order_tracking_history"
    
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    status = Column(String)
    changed_by_user_id = Column(Integer, ForeignKey("users.id"))
    timestamp = Column(DateTime, default=datetime.utcnow)
    notes = Column(String, nullable=True)
    
    order = relationship("Order", back_populates="tracking_history")