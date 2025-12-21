import streamlit as st
import queue
from modules.metrics_manager import DataUsageTracker

def render_sidebar(llm_manager, fetcher):
    """
    Renders the sidebar configuration and returns selected settings.
    Returns logic-relevant values like (mode, refresh_interval).
    """
    with st.sidebar:
        st.header("Settings")
        mode = st.radio("View Mode", ["Live News", "Saved News"])
        
        config = llm_manager.get_config()

        if mode == "Live News":
            # 새로고침 간격
            refresh_options = {
                "Manual": 0,
                "1 Minute": 60,
                "3 Minutes": 180,
                "5 Minutes": 300,
                "10 Minutes": 600
            }
            refresh_label = st.selectbox(
                "Refresh Interval",
                list(refresh_options.keys()),
                index=3, # 기본 5분
                key="refresh_interval_label"
            )
            refresh_interval = refresh_options[refresh_label]
            
            st.markdown("---")
            st.caption("AI Configuration")
            
            # 자동 요약 토글
            if 'auto_summary_enabled' not in st.session_state:
                st.session_state.auto_summary_enabled = config.get("auto_summary_enabled", False)

            def on_summary_toggle():
                 llm_manager.update_config("auto_summary_enabled", st.session_state.auto_summary_enabled)

            st.toggle("Auto Summary", key="auto_summary_enabled", on_change=on_summary_toggle)

            # 서버/제공자 선택
            provider_options = llm_manager.providers
            current_provider = llm_manager.selected_provider
            
            p_index = provider_options.index(current_provider) if current_provider in provider_options else 0
            
            selected_provider_label = st.radio(
                "LLM Provider",
                options=provider_options, 
                index=p_index,
                format_func=lambda x: x.upper() if x != "remote" else "Llama.cpp (2080linux)",
                label_visibility="collapsed",
                key="selected_provider_type",
                disabled=not st.session_state.auto_summary_enabled
            )
            
            if selected_provider_label != current_provider:
                 llm_manager.set_provider(selected_provider_label)
                 st.toast(f"Switched provider to {selected_provider_label}")
                 st.session_state.available_models = llm_manager.get_models()
                 st.rerun()

            # 모델 선택
            if 'available_models' not in st.session_state:
                 st.session_state.available_models = llm_manager.get_models()
            
            if st.session_state.available_models:
                default_model = llm_manager.get_context_default_model()
                default_index = 0
                if default_model and default_model in st.session_state.available_models:
                    default_index = st.session_state.available_models.index(default_model)

                def on_model_change():
                    llm_manager.set_context_default_model(st.session_state.selected_model)

                selected_model = st.selectbox(
                    "AI Model", 
                    st.session_state.available_models, 
                    index=default_index,
                    key="selected_model",
                    on_change=on_model_change
                )
                
                if 'result_queue' not in st.session_state:
                    st.session_state.result_queue = queue.Queue()
            else:
                st.warning("AI Models: Not Connected")
                st.caption(f"Host: {llm_manager.current_host_label}")
                if st.button("Retry Connection"):
                    st.session_state.available_models = llm_manager.get_models()
                    st.rerun()
                st.session_state.selected_model = None
    
        st.markdown("---")
        st.caption("**AI Server Status**")
        col_stat1, col_stat2 = st.columns([1,1])
        with col_stat1:
            if st.button("Check Status", key="check_ollama", use_container_width=True):
                with st.spinner("Checking..."):
                    # 1. Update Models
                    st.session_state.available_models = llm_manager.get_models()
                    
                    # 2. Check Connection
                    success, msg = llm_manager.check_connection()
                    if success:
                        st.toast(f"Connected! Found {len(st.session_state.available_models)} models.")
                    else:
                        st.toast(msg)
                    
                    # 3. Check GPU (Only on manual check)
                    st.session_state.gpu_info = llm_manager.get_gpu_info()
        
        with col_stat2:
            st.write("") 

        st.caption(f"**Host:** {llm_manager.current_host_label}")

        # Display Cached GPU Info
        if 'gpu_info' in st.session_state and st.session_state.gpu_info:
            gpu_info = st.session_state.gpu_info
            # Check if error message
            if len(gpu_info) == 1 and any(x in gpu_info[0] for x in ["Error", "SSH", "Key"]):
                 st.caption(f"**GPU Check:** {gpu_info[0]}")
            else:
                count = len(gpu_info)
                names = set(gpu_info)
                name_str = ", ".join(names)
                st.caption(f"**GPU:** {count} Cards ({name_str})")

        st.markdown("---")
        st.caption("**Server Data Usage (Today)**")
        
        tracker = DataUsageTracker()
        stats = tracker.get_stats()
        
        def format_bytes(size):
            for unit in ['B', 'KB', 'MB', 'GB']:
                if size < 1024.0:
                    return f"{size:,.0f} {unit}" if unit == 'B' else f"{size:.2f} {unit}"
                size /= 1024.0
            return f"{size:.2f} TB"

        rx_str = format_bytes(stats['rx_bytes'])
        tx_str = format_bytes(stats['tx_bytes'])
        total_str = format_bytes(stats['total_bytes'])

        st.markdown(f"""
    <div style="font_size: 0.8rem; color: #666;">
        <div style="display: flex; justify-content: space-between;">
            <span>Rx: <b>{rx_str}</b></span>
            <span>Tx: <b>{tx_str}</b></span>
        </div>
        <div style="margin-top: 4px; font-weight: bold;">
            Total: {total_str}
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Return necessary state for the main loop
    refresh_int = refresh_interval if mode == "Live News" else 0
    return mode, refresh_int, config
