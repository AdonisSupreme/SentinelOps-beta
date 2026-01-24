#!/usr/bin/env python3
"""
Setup authentication database for SentinelOps
Run this script to create the required authentication tables and default user
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.db.database import get_connection
from app.core.security import hash_password

def setup_auth_db():
    """Setup authentication database with required tables and default user"""
    print("🔧 Setting up authentication database...")
    
    try:
        # Read and execute the auth schema migration
        with open('migrations/001_auth_schema.sql', 'r') as f:
            schema_sql = f.read()
        
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Execute the schema
                cur.execute(schema_sql)
                conn.commit()
                
                print("✅ Authentication schema created successfully")
                
                # Verify admin user exists
                cur.execute("""
                    SELECT u.id, u.username, u.email, r.name as role
                    FROM users u
                    JOIN user_roles ur ON ur.user_id = u.id
                    JOIN roles r ON r.id = ur.role_id
                    WHERE u.email = %s
                """, ('ashumba@afcholdings.co.zw',))
                
                admin_user = cur.fetchone()
                if admin_user:
                    print(f"✅ Admin user verified: {admin_user[1]} ({admin_user[2]}) with role {admin_user[3]}")
                    print(f"🔑 Login credentials:")
                    print(f"   Email: {admin_user[2]}")
                    print(f"   Password: admin123")
                    print(f"⚠️  IMPORTANT: Change the default password in production!")
                else:
                    print("❌ Admin user not found after creation")
        
        print("🎉 Authentication database setup completed!")
        
    except Exception as e:
        print(f"❌ Error setting up authentication database: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = setup_auth_db()
    sys.exit(0 if success else 1)
