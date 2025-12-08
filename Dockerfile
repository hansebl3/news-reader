# 베이스 이미지 (가벼운 파이썬)
FROM python:3.9-slim

# 작업 폴더 설정
WORKDIR /app

# 라이브러리 설치 (requirements.txt 이용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 시스템 패키지 설치 (wakeonlan, ssh, ping)
RUN apt-get update && apt-get install -y \
    wakeonlan \
    openssh-client \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# 소스 코드 복사
# 소스 코드 복사 (모든 파일)
COPY . .

# 실행 명령어 (포트 8501)
CMD ["streamlit", "run", "News_Reader.py", "--server.port=8503", "--server.address=0.0.0.0"]
