import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Notifications")

def send_status_notification(customer_email: str, order_id: int, new_status: str, notes: str = ""):
    """
    Simulates sending email and SMS notifications on status changes.
    Can be integrated with free tiers like SendGrid, Twilio, or SMTP.
    """
    subject = f"Order #{order_id} Update: {new_status}"
    body = f"Hello, your order #{order_id} status has been updated to '{new_status}'."
    if notes:
        body += f" Note: {notes}"
        
    logger.info(f"[EMAIL DISPATCH] To: {customer_email} | Subject: {subject} | Body: {body}")
    logger.info(f"[SMS DISPATCH] To Customer of Order #{order_id}: Status is now {new_status}")
    return True