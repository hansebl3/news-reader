import mysql.connector
import json
import sys

print("Loading config...")
try:
    with open('config.json', 'r') as f:
        config = json.load(f)
except Exception as e:
    print(f"Config load failed: {e}")
    sys.exit(1)

db_config = config.get('news_db')
print(f"Connecting to {db_config['host']} as {db_config['user']}...")

try:
    conn = mysql.connector.connect(
        host=db_config['host'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database'],
        connection_timeout=5
    )
    print("Connection successful!")
    conn.close()
except mysql.connector.Error as err:
    print(f"Connection failed: {err}")
except Exception as e:
    print(f"Error: {e}")
