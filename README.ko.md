# GPU Watch Dashboard v3

[English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-HK.md)

공유 NVIDIA GPU 서버를 위한 가벼운 상태창입니다. Python 표준 라이브러리와 SQLite, 별도 빌드가 없는 HTML/CSS/JavaScript를 사용합니다. 저장소의 서버 목록과 스크린샷은 예제이며 실제 운영 인벤토리와 비밀은 포함하지 않습니다.

GPU·VRAM·온도·프로세스·사용자, 최근 기록과 점유율, Disk 용량, LAB DAILY INDEX를 제공합니다. 최대 6개 학회 타이머는 TBA와 고정 색상 풀을 지원하고 공지는 만료일 없이 등록할 수 있습니다. DOWN/UP은 선택 표시하며 내부 관측 진단 이벤트는 목록에서 제외합니다.

기본 GPU 수집은 10초, Disk는 30분입니다. Util 0%여도 프로세스가 메모리를 점유하면 busy입니다. 짧은 사용도 기록하지만 60초 미만 세션은 장기 유휴 해제에서 제외합니다. 미관측 시간과 확인되지 않은 사용자를 추측으로 채우지 않습니다.

v3은 effective UID 확인, GPU 오류와 독립적인 Disk 갱신, 선택적인 고정 권한 Disk helper, 응답 호출 한도에 맞춘 AA 갱신을 보완합니다. AA 키는 서버 비밀 파일에만 두고, 현재 무료 100회/일·4페이지 기준 약 한 시간 간격을 목표로 합니다. 브라우저는 5분마다 캐시를 확인합니다.

설치·설정·보안·복구 절차의 기준 문서는 [영문 README](README.md)입니다. 예제 주소와 SSH 키를 반드시 자신의 환경에 맞게 변경하십시오. Windows는 비상 운용용이며 평상시 OFF, 운영과 같은 소스 지문을 유지합니다. HTTP는 전송 암호화를 제공하지 않으며 자동 offsite 백업은 구성되어 있지 않습니다.

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch
# Configure hosts.json and SSH before starting.
python3 scripts/admin-passphrase.py ensure
python3 server.py --host 127.0.0.1 --port 8787
```

[MIT License](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)

2026-09-25 · GPT-6 Astra Max / Claude Opus 5.5 Max
