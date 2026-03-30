SMTP configuration for SentinelOps backend

Place these values in your environment (recommended) or in `.env` loaded by your app.

Required variables:
- SMTP_HOST: SMTP server hostname (e.g. smtp.sendgrid.net)
- SMTP_PORT: SMTP port (default 587)
- SMTP_USER: SMTP auth username
- SMTP_PASSWORD: SMTP auth password
- SMTP_FROM: Sender email address (e.g. no-reply@example.com)

Optional environment variables for links and behavior:
- FRONTEND_URL: Public frontend base URL used to build task links in emails (default: http://localhost:3000)

Optional flags:
- SMTP_STARTTLS: "true" or "false" (default: true) — use STARTTLS
- SMTP_USE_TLS: "true" or "false" — for implicit TLS (not commonly used)

Notes:
- After setting these, install dependencies and restart the backend:

```bash
pip install -r requirements.txt
# then restart your uvicorn / service
```

- The emailer lives at `app/core/emailer.py` and is used by the Task service to send notifications on:
  - Assignment (`assign_task`)
  - New comments (`add_comment`)
  - New attachments (`add_attachment`)
  - Task created (`create_task`)
  - Task status changes (`update_task` when `status` changes)

- You can customize message content in `app/core/emailer.py` or call `send_email` directly from other services.
