# Test authentication alignment
import requests
import json

# Test login
print("Testing Authentication Alignment...")

# API base URL
base_url = "http://127.0.0.1:8000"

# Test credentials
email = "ashumba@afcholdings.co.zw"
password = "admin123"

try:
    # Test signin
    print(f"\n1. Testing signin with {email}...")
    signin_response = requests.post(
        f"{base_url}/auth/signin",
        json={"email": email, "password": password},
        headers={"Content-Type": "application/json"}
    )
    
    print(f"Status: {signin_response.status_code}")
    
    if signin_response.status_code == 200:
        data = signin_response.json()
        print("✅ Signin successful!")
        print(f"Token received: {data['token'][:50]}...")
        print(f"User: {data['user']['username']} ({data['user']['email']})")
        print(f"Role: {data['user']['role']}")
        
        # Test /me endpoint
        print(f"\n2. Testing /me endpoint...")
        me_response = requests.get(
            f"{base_url}/auth/me",
            headers={"Authorization": f"Bearer {data['token']}"}
        )
        
        print(f"Status: {me_response.status_code}")
        
        if me_response.status_code == 200:
            me_data = me_response.json()
            print("✅ /me endpoint successful!")
            print(f"User: {me_data['username']} ({me_data['email']})")
            print(f"Role: {me_data['role']}")
        else:
            print(f"❌ /me endpoint failed: {me_response.text}")
            
        # Test logout
        print(f"\n3. Testing logout...")
        logout_response = requests.post(
            f"{base_url}/auth/logout",
            headers={"Authorization": f"Bearer {data['token']}"}
        )
        
        print(f"Status: {logout_response.status_code}")
        
        if logout_response.status_code == 200:
            print("✅ Logout successful!")
        else:
            print(f"❌ Logout failed: {logout_response.text}")
            
    else:
        print(f"❌ Signin failed: {signin_response.text}")
        
except requests.exceptions.ConnectionError:
    print("❌ Cannot connect to backend server. Make sure it's running on http://127.0.0.1:8000")
except Exception as e:
    print(f"❌ Error: {e}")

print("\n🎯 Authentication Test Complete!")
