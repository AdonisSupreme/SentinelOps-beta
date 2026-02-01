#!/usr/bin/env python3
import requests
import json

# Test the create instance endpoint
url = 'http://127.0.0.1:8000/api/v1/checklists/instances'
headers = {
    'Authorization': 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI3ODVjZmRhOS0zOGM3LTRiOGQtODQ0YS01YzhjNzY3MmExMmIiLCJzaWQiOiJkOGFkMjNhYi1iZTNlLTRhNjYtOGJlMS05NzJkZGIzYjEwODEiLCJyb2xlIjoiYWRtaW4iLCJpYXQiOjE3Njk4NTY2ODksImV4cCI6MTc2OTk0MzA4NywiYXV0aF9zb3VyY2UiOiJzZW50aW5lbCJ9.nj1aQbAXCgKcs9I0Bb-TgvA-pq9Sy2BxEpNV2Ym65tY',
    'Content-Type': 'application/json'
}

data = {
    "checklist_date": "2026-01-31",
    "shift": "AFTERNOON",
    "template_id": None
}

try:
    response = requests.post(url, headers=headers, json=data)
    print(f'Status Code: {response.status_code}')
    print(f'Headers: {dict(response.headers)}')
    print(f'Response Text: {response.text}')
    
    if response.status_code == 200:
        try:
            response_json = response.json()
            print(f'Parsed JSON: {json.dumps(response_json, indent=2)}')
            
            # Check if it has the expected structure
            if 'instance' in response_json:
                print(f'Instance found: {response_json["instance"]}')
            else:
                print('No "instance" key found in response')
                
        except json.JSONDecodeError as e:
            print(f'JSON decode error: {e}')
            
except Exception as e:
    print(f'Error: {e}')
