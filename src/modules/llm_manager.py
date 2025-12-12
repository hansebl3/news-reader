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
            "remote": "http://2080ti.tail8b1392.ts.net:11434",
            "local": "http://172.17.0.4:11434"
        }
        self.hosts = list(self.host_map.values())
        self.ssh_key_path = os.path.expanduser('~/.ssh/id_ed25519')
        self.ssh_host = 'ross@2080ti.tail8b1392.ts.net'
        
        # 설정 로드
        self.config = self.get_config()
        self.selected_host_type = self.config.get("selected_host_type", "local") # 리모트가 다운되었을 때 지연을 방지하기 위해 로컬을 기본값으로 사용
        self.current_host = self.host_map.get(self.selected_host_type, self.host_map["local"])

    def set_host_type(self, host_type):
        """호스트 유형(remote/local)을 수동으로 설정하고 구성에 저장합니다."""
        if host_type in self.host_map:
            self.selected_host_type = host_type
            self.current_host = self.host_map[host_type]
            self.update_config("selected_host_type", host_type)
            logger.info(f"Manual host switched to: {host_type} ({self.current_host})")
            return True
        return False

    def get_context_default_model(self):
        """현재 호스트 유형에 대한 기본 모델을 반환합니다."""
        config = self.get_config()
        key = f"default_model_{self.selected_host_type}"
        return config.get(key)

    def set_context_default_model(self, model_name):
        """현재 호스트 유형에 대한 기본 모델을 설정합니다."""
        key = f"default_model_{self.selected_host_type}"
        self.update_config(key, model_name)
        # 필요한 경우 하위 호환성을 위해 전역 기본값도 업데이트하지만,
        # 이제 컨텍스트 키에 의존합니다.
        self.update_config("default_model", model_name) 

    def check_connection(self):
        """수동으로 선택된 현재 호스트에 대한 연결만 확인합니다."""
        try:
            # UI 지연을 방지하기 위한 짧은 타임아웃
            resp = requests.get(f"{self.current_host}/api/tags", timeout=1) 
            if resp.status_code == 200:
                # 로컬인 경우 모델을 가져와야 하는지 확인
                if "localhost" in self.current_host:
                     models = [m['name'] for m in resp.json().get('models', [])]
                     if not models:
                         return True, "연결됨 (로컬) - 모델 없음. 자동 풀링 예정."
                return True, f"{self.current_host}에 연결됨"
            return False, f"상태 코드: {resp.status_code}"
        except requests.exceptions.RequestException as e:
             logger.error(f"Connection error to {self.current_host}: {e}")
             return False, f"연결 실패: {str(e)}"

    def get_models(self):
        try:
            # 유효한 호스트 확인
            self.check_connection()
            
            resp = requests.get(f"{self.current_host}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = [m['name'] for m in resp.json().get('models', [])]
                
                # 로컬 폴백을 위한 자동 풀 로직
                if "localhost" in self.current_host and not models:
                    logger.info("로컬 호스트에 모델이 없습니다. llama3.1 자동 풀링 중...")
                    if self._pull_model("llama3.1"):
                        return ["llama3.1"]
                return models
            return []
        except Exception as e:
            logger.error(f"Error fetching models: {e}")
            return []

    def _pull_model(self, model_name):
        """현재 호스트에서 모델을 가져옵니다 (Pull)."""
        try:
            payload = {"name": model_name}
            resp = requests.post(f"{self.current_host}/api/pull", json=payload, stream=True, timeout=600)
            # 풀링이 완료되도록 스트림 소비
            for line in resp.iter_lines():
                if line:
                    logger.info(f"Pulling {model_name}: {line.decode('utf-8')}")
            return True
        except Exception as e:
            logger.error(f"Failed to pull {model_name}: {e}")
            return False

    def generate_response(self, prompt, model, stream=False):
        try:
            # 연결 확인
            self.check_connection()
            
            # 로컬이고 모델이 일반/누락된 경우 존재 여부를 확인해야 할 수 있음
            # 일단 get_models가 풀링을 수행한다고 가정하거나 명시적 오류 처리
            
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": stream,
                "context": [] # 속도 저하를 방지하기 위해 무상태(statelessness) 강제
            }
            logger.info(f"Ollama({self.current_host})로 요청 전송: {model}")
            
            try:
                response = requests.post(f"{self.current_host}/api/generate", json=payload, stream=stream, timeout=120)
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                # 요청 실패 시 페일오버 시도
                logger.warning(f"{self.current_host}에서 요청 실패, 페일오버 시도 중...")
                if self._check_and_set_host():
                     # 새 호스트에서 재시도
                     logger.info(f"{self.current_host}에서 재시도 중")
                     response = requests.post(f"{self.current_host}/api/generate", json=payload, stream=stream, timeout=120)
                     response.raise_for_status()
                else:
                    raise e

            # TX 추적 (대략적인 페이로드 크기)
            tracker = DataUsageTracker()
            tracker.add_tx(len(json.dumps(payload)))

            if not stream:
                resp_json = response.json()
                res_text = resp_json.get("response", "")
                
                # RX 추적
                tracker.add_rx(len(response.content))
                
                return res_text
            else:
                # 스트리밍 지원 (기본)
                result = response.json()
                res_text = result.get("response", "No response generated.")
                tracker.add_rx(len(response.content))
                return res_text
        except Exception as e:
            logger.error(f"Ollama Error for {model}: {e}")
            return f"Error generating response: {e}"

    def get_gpu_info(self):
        """SSH를 통해 2080ti에서 GPU 정보(개수/이름)를 가져오거나, 가능한 경우 로컬에서 가져옵니다.
           사용자가 2080ti 연결 로직에 대해 구체적으로 질문했습니다.
           하지만 로컬인 경우 로컬 GPU를 보여주는 것이 좋을까요?
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

        if not os.path.exists(self.ssh_key_path):
            return [f"SSH Key Not Found: {self.ssh_key_path}"]

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
            else:
                logger.warning(f"SSH failed: {result.stderr}")
                return [f"SSH Error: {result.stderr.strip()[:20]}..."]
        except Exception as e:
            logger.error(f"GPU Info Error: {e}")
            return [f"Error: {str(e)}"]

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
