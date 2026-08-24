from database import engine, SessionLocal, Base
import models

# Recreate all tables
Base.metadata.create_all(bind=engine)

db = SessionLocal()

# Check if data already exists
if not db.query(models.User).first():
    # 1. Create Users
    admin = models.User(name="Admin User", email="admin@delivery.com", role="admin")
    customer = models.User(name="Customer User", email="customer@delivery.com", role="customer")
    agent = models.User(name="Delivery Agent 1", email="agent1@delivery.com", role="agent")
    
    db.add_all([admin, customer, agent])
    db.commit()

    # 2. Create Zones
    zone_a = models.Zone(name="Zone A", area_names="Downtown, North City, 110001")
    zone_b = models.Zone(name="Zone B", area_names="Suburbs, South City, 110002")
    
    db.add_all([zone_a, zone_b])
    db.commit()

    # 3. Create Rate Cards
    # Zone A to Zone A (Intra-zone)
    rc_intra_b2c = models.RateCard(source_zone_id=zone_a.id, dest_zone_id=zone_a.id, order_type="B2C", rate_per_kg=40.0, cod_surcharge=30.0)
    rc_intra_b2b = models.RateCard(source_zone_id=zone_a.id, dest_zone_id=zone_a.id, order_type="B2B", rate_per_kg=25.0, cod_surcharge=50.0)
    
    # Zone A to Zone B (Inter-zone)
    rc_inter_b2c = models.RateCard(source_zone_id=zone_a.id, dest_zone_id=zone_b.id, order_type="B2C", rate_per_kg=70.0, cod_surcharge=40.0)
    rc_inter_b2b = models.RateCard(source_zone_id=zone_a.id, dest_zone_id=zone_b.id, order_type="B2B", rate_per_kg=50.0, cod_surcharge=60.0)

    db.add_all([rc_intra_b2c, rc_intra_b2b, rc_inter_b2c, rc_inter_b2b])
    db.commit()
    print("Database seeded successfully with initial users, zones, and rate cards.")
else:
    print("Database already contains data.")

db.close()