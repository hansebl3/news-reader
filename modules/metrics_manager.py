import streamlit as st
import json
import os
from datetime import datetime

DATA_USAGE_FILE = "data_usage.json"

class DataUsageTracker:
    def __init__(self):
        # Initialize session state keys if not present
        if 'data_usage_rx' not in st.session_state:
            st.session_state.data_usage_rx = 0
        if 'data_usage_tx' not in st.session_state:
            st.session_state.data_usage_tx = 0
            
    def _load_data(self):
        if os.path.exists(DATA_USAGE_FILE):
            try:
                with open(DATA_USAGE_FILE, 'r') as f:
                    data = json.load(f)
                    # Check if date matches today
                    if data.get('date') == datetime.now().strftime('%Y-%m-%d'):
                        return data
            except:
                pass
        return {'date': datetime.now().strftime('%Y-%m-%d'), 'rx': 0, 'tx': 0}

    def _save_data(self, data):
        try:
            with open(DATA_USAGE_FILE, 'w') as f:
                json.dump(data, f)
        except:
            pass

    def add_rx(self, bytes_count):
        """Add received bytes"""
        if bytes_count:
            # Update Session (Transient)
            st.session_state.data_usage_rx += bytes_count
            
            # Update File (Persistent)
            data = self._load_data()
            data['rx'] += bytes_count
            self._save_data(data)

    def add_tx(self, bytes_count):
        """Add transmitted bytes"""
        if bytes_count:
            st.session_state.data_usage_tx += bytes_count
            
            # Update File
            data = self._load_data()
            data['tx'] += bytes_count
            self._save_data(data)

    def get_stats(self):
        """Get stats (Today's Total)"""
        # We want to display TOTAL usage for the day, not just this session
        data = self._load_data()
        rx_bytes = data['rx']
        tx_bytes = data['tx']
        
        return {
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes,
            "total_bytes": rx_bytes + tx_bytes
        }
