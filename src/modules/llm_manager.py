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
        self.ssh_key_path = os.path.expanduser('~/.ssh/id_ed25519')
        self.ssh_host = 'ross@2080ti.tail8b1392.ts.net'
        
        # Load Config & Providers
        self.config = self.get_config()
        self._load_providers()
        
        self.selected_provider = self.config.get("selected_provider", "remote")
        if self.selected_provider not in self.providers:
            # Fallback to first available or remote
            if "remote" in self.providers:
                self.selected_provider = "remote"
            else:
                 self.selected_provider = self.providers[0] if self.providers else "openai"

    def _load_providers(self):
        self.config = self.get_config()
        self.custom_providers = self.config.get("custom_providers", [])
        
        # Helper to find provider by name
        self.provider_map = {p['name']: p for p in self.custom_providers}
        
        # Basic Cloud Providers
        self.cloud_providers = ["openai", "gemini"]
        
        self.providers = list(self.provider_map.keys()) + self.cloud_providers

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
        if self.selected_provider in self.provider_map:
            p = self.provider_map[self.selected_provider]
            return p.get('display_name', f"{p['name']} ({p['url']})")
        return f"Cloud API ({self.selected_provider})"

    def get_context_default_model(self):
        """Returns default model for current provider."""
        config = self.get_config()
        # Fallback for old config keys
        if self.selected_provider in self.provider_map:
             return config.get(f"default_model_{self.selected_provider}")
        return config.get(f"default_model_{self.selected_provider}")

    def set_context_default_model(self, model_name):
        """Sets default model for current provider."""
        key = f"default_model_{self.selected_provider}"
        self.update_config(key, model_name)

    def check_connection(self):
        """Checks connection to current provider."""
        if self.selected_provider in self.provider_map:
            p = self.provider_map[self.selected_provider]
            url = p['url']
            p_type = p.get('type', 'ollama')
            
            try:
                if p_type == 'ollama':
                    resp = requests.get(f"{url}/api/tags", timeout=2)
                    if resp.status_code == 200:
                        return True, f"Connected to {p['name']}"
                    return False, f"Status: {resp.status_code}"
                elif p_type == 'openai':
                    # Check models endpoint for OpenAI compatible
                    resp = requests.get(f"{url}/models", timeout=2) # Often no /v1 prefix needed if base has it, or /v1/models
                    # Wait, config url has /v1 usually for OpenAI compatible.
                    # Standard is GET /v1/models
                    # If config url is .../v1, then just /models might work? 
                    # Let's try appending /models if it doesn't end with it.
                    target = f"{url}/models"
                    resp = requests.get(target, timeout=2)
                    if resp.status_code == 200:
                        return True, f"Connected to {p['name']}"
                    return False, f"Status: {resp.status_code}"
                    
                return False, f"Unknown local type: {p_type}"
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
        if self.selected_provider in self.provider_map:
            p = self.provider_map[self.selected_provider]
            url = p['url']
            p_type = p.get('type', 'ollama')
            
            try:
                if p_type == 'ollama':
                    resp = requests.get(f"{url}/api/tags", timeout=5)
                    if resp.status_code == 200:
                        return [m['name'] for m in resp.json().get('models', [])]
                elif p_type == 'openai':
                    resp = requests.get(f"{url}/models", timeout=5)
                    if resp.status_code == 200:
                        data = resp.json()
                        # OpenAI format: { data: [ {id: ...}, ... ] }
                        if 'data' in data:
                            return [m['id'] for m in data['data']]
                        # Some local endpoints might just return list
                        return [str(m) for m in data]
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
            if self.selected_provider in self.provider_map:
                p = self.provider_map[self.selected_provider]
                if p.get('type') == 'openai':
                     return self._call_openai_compatible(prompt, model, stream, tracker, p['url'])
                else:
                     # Default to ollama
                     return self._call_ollama(prompt, model, stream, tracker, p['url'])

            elif self.selected_provider == "openai":
                return self._call_openai(prompt, model, stream, tracker)
            elif self.selected_provider == "gemini":
                # For stability, use non-streaming for now unless requested otherwise
                return self._call_gemini(prompt, model, stream, tracker)
        except Exception as e:
            logger.error(f"Generate Error ({self.selected_provider}): {e}")
            return f"Error: {e}"
        
        return "Error: Unknown Provider"

    def _call_ollama(self, prompt, model, stream, tracker, base_url):
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "context": [] # Stateless
        }
        tracker.add_tx(len(json.dumps(payload)))
        
        response = requests.post(f"{base_url}/api/generate", json=payload, stream=stream, timeout=120)
        response.raise_for_status()
        
        if stream:
             tracker.add_rx(len(response.content)) # Approx
             # See notes below regarding stream
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

    def _call_openai_compatible(self, prompt, model, stream, tracker, base_url):
        """Calls an OpenAI-compatible endpoint (like LM Studio)."""
        url = f"{base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        # Some local servers might need a dummy key
        headers["Authorization"] = "Bearer local-key"
        
        messages = [{"role": "user", "content": prompt}]
        payload = {"model": model, "messages": messages, "stream": False} # Force False
        
        tracker.add_tx(len(json.dumps(payload)))
        r = requests.post(url, headers=headers, json=payload, timeout=120)
        r.raise_for_status()
        
        res = r.json()
        text = res['choices'][0]['message']['content']
        tracker.add_rx(len(r.content))
        return text

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
            return [f"Detailed GPU info only for 'remote' host"]
            
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

