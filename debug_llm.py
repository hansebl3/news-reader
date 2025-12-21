import sys
import os
import requests
import json
import logging

# Add src to path
sys.path.append(os.path.abspath("src"))

logging.basicConfig(level=logging.INFO)
from modules.llm_manager import LLMManager

try:
    mgr = LLMManager()
    p = mgr.provider_map.get(mgr.selected_provider)
    if p:
        print(f"Provider: {mgr.selected_provider}")
        print(f"URL: {p.get('url')}")
    
        print("Testing check_connection()...")
        status, msg = mgr.check_connection()
        print(f"Check Result: {status}, {msg}")

        print("Testing get_models()...")
        models = mgr.get_models()
        print(f"Models: {models}")
        
        # Manual timeout test
        print("Manual Timeout Test (5s)...")
        try:
             url = p.get('url') + "/models"
             resp = requests.get(url, timeout=5)
             print(f"Manual 5s Resp: {resp.status_code}")
        except Exception as e:
             print(f"Manual 5s Error: {e}")

    else:
        print(f"Selected provider {mgr.selected_provider} not found in map")

except Exception as e:
    print(f"Error: {e}")
