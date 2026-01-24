# Quick test to see what's happening at the backend
import urllib.request
import urllib.error
import json

def test_simple_endpoint():
    """Test a simple endpoint to see if backend is working"""
    try:
        print("Testing /auth/status endpoint...")
        req = urllib.request.Request(
            "http://127.0.0.1:8000/auth/status",
            method="GET",
            headers={"User-Agent": "Auth-Test/1.0"}
        )
        
        with urllib.request.urlopen(req, timeout=5) as response:
            data = response.read().decode('utf-8')
            print(f"Status: {response.status}")
            print(f"Response: {data}")
            return True
    except Exception as e:
        print(f"Error: {e}")
        return False

def test_ad_status():
    """Test AD status endpoint"""
    try:
        print("\nTesting /auth/ad/status endpoint...")
        req = urllib.request.Request(
            "http://127.0.0.1:8000/auth/ad/status",
            method="GET",
            headers={"User-Agent": "Auth-Test/1.0"}
        )
        
        with urllib.request.urlopen(req, timeout=5) as response:
            data = response.read().decode('utf-8')
            print(f"Status: {response.status}")
            print(f"Response: {data}")
            return True
    except Exception as e:
        print(f"Error: {e}")
        return False

def test_router_endpoint():
    """Test the new test endpoint"""
    try:
        print("\nTesting /auth/test endpoint...")
        req = urllib.request.Request(
            "http://127.0.0.1:8000/auth/test",
            method="GET",
            headers={"User-Agent": "Auth-Test/1.0"}
        )
        
        with urllib.request.urlopen(req, timeout=5) as response:
            data = response.read().decode('utf-8')
            print(f"Status: {response.status}")
            print(f"Response: {data}")
            return True
    except Exception as e:
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    print("QUICK BACKEND DIAGNOSTIC")
    print("=" * 30)
    
    test_simple_endpoint()
    test_ad_status()
    test_router_endpoint()
    
    print("\nIf endpoints above work but /auth/signin fails,")
    print("the issue is specifically in the signin logic.")
