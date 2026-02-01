# app/checklists/email_service.py
"""
Email service for sending escalation notifications when items are skipped or failed.
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from datetime import datetime

log = logging.getLogger(__name__)

# Email configuration from environment variables
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_TO = [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]
EMAIL_CC = [e.strip() for e in os.getenv("EMAIL_CC", "").split(",") if e.strip()]
SMTP_SERVER = os.getenv("SMTP_SERVER", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")


class EmailService:
    """Service for sending escalation emails"""
    
    @staticmethod
    def send_escalation_email(
        item_title: str,
        action_type: str,  # 'SKIPPED' or 'FAILED'
        reason: str,
        checklist_date: str,
        shift: str,
        operator_name: str,
        instance_id: Optional[str] = None
    ) -> bool:
        """
        Send escalation email when an item is skipped or failed.
        
        Args:
            item_title: The title of the checklist item
            action_type: Either 'SKIPPED' or 'FAILED'
            reason: The reason/description provided
            checklist_date: Date of the checklist instance
            shift: Shift type (MORNING, AFTERNOON, NIGHT)
            operator_name: Name of the operator who performed the action
            instance_id: Optional checklist instance ID
            
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        # Check if email is configured
        if not all([EMAIL_FROM, SMTP_SERVER, SMTP_PASSWORD]) or not EMAIL_TO:
            log.warning("Email not configured - skipping escalation email")
            return False
        
        try:
            # Build email subject
            status_label = "SKIPPED" if action_type == "SKIPPED" else "ISSUE REPORTED"
            subject = f"[SentinelOps] {status_label}: {item_title}"
            
            # Build email body
            body = f"""
Escalation Notification - SentinelOps Checklist

Item: {item_title}
Action: {status_label}
Date: {checklist_date}
Shift: {shift}
Operator: {operator_name}
"""
            if instance_id:
                body += f"Instance ID: {instance_id}\n"
            
            body += f"""
Reason/Description:
{reason}

---
This is an automated notification from SentinelOps.
Please review and take appropriate action.
"""
            
            # Create message
            msg = MIMEMultipart()
            msg['From'] = EMAIL_FROM
            msg['To'] = ", ".join(EMAIL_TO)
            if EMAIL_CC:
                msg['Cc'] = ", ".join(EMAIL_CC)
            msg['Subject'] = subject
            msg['Date'] = datetime.now().strftime('%a, %d %b %Y %H:%M:%S %z')
            
            # Attach body
            msg.attach(MIMEText(body, 'plain'))
            
            # Send email
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_FROM, SMTP_PASSWORD)
                
                recipients = EMAIL_TO + EMAIL_CC
                server.sendmail(EMAIL_FROM, recipients, msg.as_string())
            
            log.info(f"Escalation email sent for {item_title} - {action_type}")
            return True
            
        except Exception as e:
            log.error(f"Failed to send escalation email: {e}")
            return False
    
    @staticmethod
    def is_configured() -> bool:
        """Check if email service is properly configured"""
        return all([
            EMAIL_FROM,
            SMTP_SERVER,
            SMTP_PASSWORD,
            len(EMAIL_TO) > 0
        ])
