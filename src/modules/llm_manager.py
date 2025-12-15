import requests
import logging
import json
import os
import subprocess
from modules.metrics_manager import DataUsageTracker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LLMManager:
    def __init__(self):
        # Providers: 'remote' (Ollama 2080ti), 'openai', 'gemini'
        self.providers = ["remote", "openai", "gemini"]
        self.ollama_host = "http://2080ti.tail8b1392.ts.net:11434"
        self.ssh_key_path = os.path.expanduser('~/.ssh/id_ed25519')
        self.ssh_host = 'ross@2080ti.tail8b1392.ts.net'
        
        # Load Config
        self.config = self.get_config()
        self.selected_provider = self.config.get("selected_provider", "remote")
        if self.selected_provider not in self.providers:
            self.selected_provider = "remote"

    def get_config(self):
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "llm_config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading config: {e}")
        return {}

    def update_config(self, key, value):
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "llm_config.json")
        try:
            data = self.get_config()
            data[key] = value
            with open(config_path, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving config: {e}")

    def set_provider(self, provider):
        """Sets the current LLM provider."""
        if provider in self.providers:
            self.selected_provider = provider
            self.update_config("selected_provider", provider)
            logger.info(f"Provider switched to: {provider}")
            return True
        return False

    @property
    def current_host_label(self):
        if self.selected_provider == "remote":
            return f"Ollama ({self.ollama_host})"
        return f"Cloud API ({self.selected_provider})"

    def get_context_default_model(self):
        """Returns default model for current provider."""
        config = self.get_config()
        # Fallback for old config keys
        if self.selected_provider == "remote":
             return config.get("default_model_remote")
        return config.get(f"default_model_{self.selected_provider}")

    def set_context_default_model(self, model_name):
        """Sets default model for current provider."""
        key = f"default_model_{self.selected_provider}"
        if self.selected_provider == "remote": key = "default_model_remote"
        self.update_config(key, model_name)

    def check_connection(self):
        """Checks connection to current provider."""
        if self.selected_provider == "remote":
            try:
                resp = requests.get(f"{self.ollama_host}/api/tags", timeout=2)
                if resp.status_code == 200:
                    return True, f"Connected to {self.ollama_host}"
                return False, f"Status: {resp.status_code}"
            except Exception as e:
                return False, f"Connection Failed: {e}"
        else:
            # For Cloud, just check if key exists
            keys = self.get_config().get("api_keys", {})
            if keys.get(self.selected_provider):
                return True, f"API Key found for {self.selected_provider}"
            return False, f"Missing API Key for {self.selected_provider}"

    def get_models(self):
        """Returns available models for current provider."""
        if self.selected_provider == "remote":
            try:
                resp = requests.get(f"{self.ollama_host}/api/tags", timeout=5)
                if resp.status_code == 200:
                    return [m['name'] for m in resp.json().get('models', [])]
            except Exception:
                pass
            return []
        
        elif self.selected_provider == "openai":
            return self.get_config().get("models", {}).get("openai", ["gpt-4o", "gpt-3.5-turbo"])
        
        elif self.selected_provider == "gemini":
            return self.get_config().get("models", {}).get("gemini", ["gemini-1.5-flash", "gemini-1.5-pro"])
            
        return []

    def generate_response(self, prompt, model, stream=False):
        """Generates response based on selected provider."""
        tracker = DataUsageTracker()
        
        try:
            if self.selected_provider == "remote":
                return self._call_ollama(prompt, model, stream, tracker)
            elif self.selected_provider == "openai":
                return self._call_openai(prompt, model, stream, tracker)
            elif self.selected_provider == "gemini":
                # For stability, use non-streaming for now unless requested otherwise
                return self._call_gemini(prompt, model, stream, tracker)
        except Exception as e:
            logger.error(f"Generate Error ({self.selected_provider}): {e}")
            return f"Error: {e}"
        
        return "Error: Unknown Provider"

    def _call_ollama(self, prompt, model, stream, tracker):
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "context": [] # Stateless
        }
        tracker.add_tx(len(json.dumps(payload)))
        
        response = requests.post(f"{self.ollama_host}/api/generate", json=payload, stream=stream, timeout=120)
        response.raise_for_status()
        
        if stream:
             tracker.add_rx(len(response.content)) # Approx
             # Return generator? The caller (News_Reader) currently expects text or handles stream?
             # News_Reader `generate_summary` (worker) might expect text. 
             # Looking at News_Reader.py: `auto_sum_worker` -> `fetcher.generate_summary` -> `llm.generate_response`.
             # It seems it handles string return. Stream support in News Reader is not main priority or is handled by gathering lines.
             # The original code gathered text if stream=True: "res_text = result.get('response')..."
             # Wait, original code for stream=True returned `res_text` from ONE chunk? 
             # No, loop was missing in `generate_response` for `stream=True` in original code (lines 145-148).
             # It just returned `response.json().get("response")`. This implies it wasn't really streaming or I misread.
             # Actually `News_Reader` only uses it for summary which is usually non-streamed (batch).
             # I will stick to non-streaming return for simplicity unless forced.
             pass
        
        # Non-streaming handling (or aggregating stream)
        # If stream=True was passed but we digest it here:
        full_text = ""
        if stream:
             for line in response.iter_lines():
                 if line:
                     body = json.loads(line)
                     full_text += body.get("response", "")
        else:
             full_text = response.json().get("response", "")
             
        tracker.add_rx(len(full_text))
        return full_text

    def _call_openai(self, prompt, model, stream, tracker):
        api_key = self.get_config().get("api_keys", {}).get("openai")
        if not api_key: raise ValueError("OpenAI API Key missing")
        
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        messages = [{"role": "user", "content": prompt}]
        payload = {"model": model, "messages": messages, "stream": False} # Force False for now
        
        tracker.add_tx(len(json.dumps(payload)))
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        
        res = r.json()
        text = res['choices'][0]['message']['content']
        tracker.add_rx(len(r.content))
        return text

    def _call_gemini(self, prompt, model, stream, tracker):
        api_key = self.get_config().get("api_keys", {}).get("gemini")
        if not api_key: raise ValueError("Gemini API Key missing")
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        
        tracker.add_tx(len(json.dumps(payload)))
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        
        data = r.json()
        try:
            text = data['candidates'][0]['content']['parts'][0]['text']
        except:
            text = "Error parsing Gemini response"
            
        tracker.add_rx(len(r.content))
        return text

    def get_gpu_info(self):
        if self.selected_provider != "remote":
            return [f"Cloud Provider: {self.selected_provider.upper()}", "No GPU Info"]
            
        if not os.path.exists(self.ssh_key_path):
            return [f"SSH Key Not Found"]

        try:
            cmd = [
                'ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=2',
                '-i', self.ssh_key_path, self.ssh_host, 
                'nvidia-smi --query-gpu=name --format=csv,noheader'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                return [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
            return [f"SSH Error"]
        except Exception:
            return ["Check Failed"]

