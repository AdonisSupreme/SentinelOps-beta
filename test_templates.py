#!/usr/bin/env python3
import requests
import json

# Test the templates endpoint
url = 'http://127.0.0.1:8000/api/v1/checklists/templates'
headers = {
    'Authorization': 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI3ODVjZmRhOS0zOGM3LTRiOGQtODQ0YS01YzhjNzY3MmExMmIiLCJzaWQiOiJkOGFkMjNhYi1iZTNlLTRhNjYtOGJlMS05NzJkZGIzYjEwODEiLCJyb2xlIjoiYWRtaW4iLCJpYXQiOjE3Njk4NTY2ODksImV4cCI6MTc2OTk0MzA4NywiYXV0aF9zb3VyY2UiOiJzZW50aW5lbCJ9.nj1aQbAXCgKcs9I0Bb-TgvA-pq9Sy2BxEpNV2Ym65tY',
    'Content-Type': 'application/json'
}

try:
    response = requests.get(url, headers=headers)
    print(f'Status Code: {response.status_code}')
    print(f'Response: {response.text}')
except Exception as e:
    print(f'Error: {e}')
