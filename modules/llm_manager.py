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
        self.host_map = {
            "remote": "http://2080ti:11434",
            "local": "http://172.17.0.4:11434"
        }
        self.hosts = list(self.host_map.values())
        self.ssh_key_path = '/home/ross/.ssh/id_ed25519'
        self.ssh_host = 'ross@2080ti'
        
        # Load preference
        self.config = self.get_config()
        self.selected_host_type = self.config.get("selected_host_type", "local") # Default to local to avoid lag if remote is down
        self.current_host = self.host_map.get(self.selected_host_type, self.host_map["local"])

    def set_host_type(self, host_type):
        """Manually set the host type (remote/local) and save to config."""
        if host_type in self.host_map:
            self.selected_host_type = host_type
            self.current_host = self.host_map[host_type]
            self.update_config("selected_host_type", host_type)
            logger.info(f"Manual host switched to: {host_type} ({self.current_host})")
            return True
        return False

    def get_context_default_model(self):
        """Returns the default model for the CURRENT host type."""
        config = self.get_config()
        key = f"default_model_{self.selected_host_type}"
        return config.get(key)

    def set_context_default_model(self, model_name):
        """Sets the default model for the CURRENT host type."""
        key = f"default_model_{self.selected_host_type}"
        self.update_config(key, model_name)
        # Also update global default for backward compatibility if needed, 
        # but let's rely on context keys now.
        self.update_config("default_model", model_name) 

    def check_connection(self):
        """Checks connection to CURRENT manually selected host only."""
        try:
            # Short timeout to prevent UI lag
            resp = requests.get(f"{self.current_host}/api/tags", timeout=1) 
            if resp.status_code == 200:
                # If we are on local, check if we need to pull a model
                if "localhost" in self.current_host:
                     models = [m['name'] for m in resp.json().get('models', [])]
                     if not models:
                         return True, "Connected (Local) - No Models Found. Will auto-pull."
                return True, f"Connected to {self.current_host}"
            return False, f"Status: {resp.status_code}"
        except requests.exceptions.RequestException:
             return False, f"Connection Failed ({self.current_host})"

    def get_models(self):
        try:
            # Ensure valid host
            self.check_connection()
            
            resp = requests.get(f"{self.current_host}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = [m['name'] for m in resp.json().get('models', [])]
                
                # Auto-pull logic for local fallback
                if "localhost" in self.current_host and not models:
                    logger.info("Local host has no models. Auto-pulling llama3.1...")
                    if self._pull_model("llama3.1"):
                        return ["llama3.1"]
                return models
            return []
        except Exception as e:
            logger.error(f"Error fetching models: {e}")
            return []

    def _pull_model(self, model_name):
        """Pulls a model on the current host."""
        try:
            payload = {"name": model_name}
            resp = requests.post(f"{self.current_host}/api/pull", json=payload, stream=True, timeout=600)
            # Consume stream to ensure pull completes
            for line in resp.iter_lines():
                if line:
                    logger.info(f"Pulling {model_name}: {line.decode('utf-8')}")
            return True
        except Exception as e:
            logger.error(f"Failed to pull {model_name}: {e}")
            return False

    def generate_response(self, prompt, model, stream=False):
        try:
            # Ensure connection
            self.check_connection()
            
            # If on local and model is generic/missing, might need to ensure it exists
            # For now, rely on get_models doing the pull, or explicit error handling
            
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": stream,
                "context": [] # Force statelessness to prevent slowdown
            }
            logger.info(f"Sending request to Ollama ({self.current_host}): {model}")
            
            try:
                response = requests.post(f"{self.current_host}/api/generate", json=payload, stream=stream, timeout=120)
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                # If request fails, maybe try failover?
                logger.warning(f"Request failed on {self.current_host}, trying failover...")
                if self._check_and_set_host():
                     # Retry on new host
                     logger.info(f"Retrying on {self.current_host}")
                     response = requests.post(f"{self.current_host}/api/generate", json=payload, stream=stream, timeout=120)
                     response.raise_for_status()
                else:
                    raise e

            # Track TX (Approximate payload size)
            tracker = DataUsageTracker()
            tracker.add_tx(len(json.dumps(payload)))

            if not stream:
                resp_json = response.json()
                res_text = resp_json.get("response", "")
                
                # Track RX
                tracker.add_rx(len(response.content))
                
                return res_text
            else:
                # Streaming support (basic)
                result = response.json()
                res_text = result.get("response", "No response generated.")
                tracker.add_rx(len(response.content))
                return res_text
        except Exception as e:
            logger.error(f"Ollama Error for {model}: {e}")
            return f"Error generating response: {e}"

    def get_gpu_info(self):
        """Fetch GPU info (count/names) from 2080ti via SSH, or Local if applicable? 
           User specifically asked about 2080ti connection logic.
           But if we are local, we might want to show local GPU?
        """
        if "localhost" in self.current_host:
            # Try nvidia-smi locally
             try:
                cmd = ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader']
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                if result.returncode == 0:
                     return [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
             except:
                 pass
             return ["Local CPU/GPU (Ollama)"]

        try:
            cmd = [
                'ssh', 
                '-o', 'StrictHostKeyChecking=no', 
                '-o', 'UserKnownHostsFile=/dev/null',
                '-o', 'ConnectTimeout=2',
                '-i', self.ssh_key_path,
                self.ssh_host, 
                'nvidia-smi --query-gpu=name --format=csv,noheader'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                # Output: "GeForce RTX 2080 Ti\nGeForce RTX 2080 Ti"
                gpus = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
                return gpus
            return []
        except Exception as e:
            logger.error(f"GPU Info Error: {e}")
            return []

    def get_config(self):
        config_path = "llm_config.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading config: {e}")
        return {}

    def update_config(self, key, value):
        config_path = "llm_config.json"
        try:
            data = self.get_config()
            data[key] = value
            with open(config_path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"Error saving config: {e}")
