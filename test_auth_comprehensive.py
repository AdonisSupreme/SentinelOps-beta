# Comprehensive authentication test with detailed logging
import time
import json

def test_backend_connectivity():
    """Test if backend is running and accessible"""
    print("Testing Backend Connectivity...")
    
    try:
        import urllib.request
        import urllib.error
        
        # Test basic connectivity
        print("Testing basic HTTP connection...")
        req = urllib.request.Request(
            "http://127.0.0.1:8000/",
            method="GET",
            headers={"User-Agent": "Auth-Test/1.0"}
        )
        
        with urllib.request.urlopen(req, timeout=5) as response:
            print(f"SUCCESS: Backend reachable! Status: {response.status}")
            print(f"Response headers: {dict(response.headers)}")
            return True
            
    except urllib.error.URLError as e:
        print(f"ERROR: Backend not reachable: {e}")
        print(f"Possible causes:")
        print(f"   - Backend server not running")
        print(f"   - Wrong port (expected 8000)")
        print(f"   - Firewall blocking connection")
        print(f"   - Backend crashed")
        return False
    except Exception as e:
        print(f"ERROR: Unexpected error: {e}")
        return False

def test_auth_endpoint():
    """Test the auth signin endpoint directly"""
    print("\nTesting Auth Endpoint...")
    
    try:
        import urllib.request
        import urllib.error
        
        # Prepare test data
        test_data = {
            "email": "ashumba@afcholdings.co.zw",
            "password": "admin123"
        }
        
        json_data = json.dumps(test_data).encode('utf-8')
        
        print(f"Sending request to /auth/signin")
        print(f"Email: {test_data['email']}")
        print(f"Password: {'*' * len(test_data['password'])}")
        
        req = urllib.request.Request(
            "http://127.0.0.1:8000/auth/signin",
            method="POST",
            data=json_data,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(json_data)),
                "User-Agent": "Auth-Test/1.0"
            }
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            response_data = response.read().decode('utf-8')
            print(f"SUCCESS: Auth endpoint responded! Status: {response.status}")
            print(f"Response data: {response_data}")
            
            try:
                parsed = json.loads(response_data)
                if 'token' in parsed and 'user' in parsed:
                    print(f"Authentication successful!")
                    print(f"User: {parsed['user']['username']} ({parsed['user']['email']})")
                    print(f"Role: {parsed['user']['role']}")
                    print(f"Token length: {len(parsed['token'])}")
                    return True
                else:
                    print(f"WARNING: Unexpected response format")
                    return False
            except json.JSONDecodeError as e:
                print(f"ERROR: Invalid JSON response: {e}")
                return False
                
    except urllib.error.HTTPError as e:
        print(f"ERROR: HTTP Error: {e.code} {e.reason}")
        try:
            error_data = e.read().decode('utf-8')
            print(f"Error response: {error_data}")
        except:
            pass
        return False
    except Exception as e:
        print(f"ERROR: Request failed: {e}")
        return False

def main():
    print("COMPREHENSIVE AUTHENTICATION TEST")
    print("=" * 50)
    
    # Test 1: Basic connectivity
    if not test_backend_connectivity():
        print("\nBACKEND NOT RUNNING - Start the backend first!")
        print("To start backend:")
        print("   cd c:\\Users\\ashumba\\Documents\\Sentinel\\SentinelOps-beta")
        print("   python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000")
        return
    
    # Test 2: Auth endpoint
    if test_auth_endpoint():
        print("\nAUTHENTICATION WORKING!")
        print("Frontend should be able to login now")
    else:
        print("\nAUTHENTICATION FAILED")
        print("Check backend logs for detailed error information")

if __name__ == "__main__":
    main()
