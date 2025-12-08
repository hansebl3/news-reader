import streamlit as st
import json
import os
from datetime import datetime

DATA_USAGE_FILE = "data_usage.json"

class DataUsageTracker:
    def __init__(self):
        # 세션 상태 키가 없으면 초기화
        if 'data_usage_rx' not in st.session_state:
            st.session_state.data_usage_rx = 0
        if 'data_usage_tx' not in st.session_state:
            st.session_state.data_usage_tx = 0
            
    def _load_data(self):
        if os.path.exists(DATA_USAGE_FILE):
            try:
                with open(DATA_USAGE_FILE, 'r') as f:
                    data = json.load(f)
                    # 날짜가 오늘인지 확인
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
        """수신 바이트 추가"""
        if bytes_count:
            # 세션 업데이트 (일시적)
            st.session_state.data_usage_rx += bytes_count
            
            # 파일 업데이트 (영구적)
            data = self._load_data()
            data['rx'] += bytes_count
            self._save_data(data)

    def add_tx(self, bytes_count):
        """송신 바이트 추가"""
        if bytes_count:
            st.session_state.data_usage_tx += bytes_count
            
            # 파일 업데이트
            data = self._load_data()
            data['tx'] += bytes_count
            self._save_data(data)

    def get_stats(self):
        """통계 가져오기 (오늘 전체)"""
        # 이 세션뿐만 아니라 오늘의 전체 사용량을 표시하고 싶습니다.
        data = self._load_data()
        rx_bytes = data['rx']
        tx_bytes = data['tx']
        
        return {
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes,
            "total_bytes": rx_bytes + tx_bytes
        }
