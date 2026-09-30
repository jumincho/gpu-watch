# GPU Watch Dashboard v2

연구실 NVIDIA GPU 서버를 SSH로 확인하는 가벼운 대시보드입니다. Python 표준 라이브러리·SQLite와 순수 HTML/CSS/JavaScript로 동작하며 GPU 서버에는 상주 에이전트를 설치하지 않습니다.

**정식 출범: v2 · 2026-09-30 · GPT-6.1 Sol (max). 화면에는 출시 정보 footer를 표시하지 않습니다.**

[English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-HK.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

이 공개판의 서버 이름·주소·SSH 경로는 가상 예시입니다. 실제 환경에 맞게 바꾸고 키·known_hosts를 검증한 뒤 사용하세요. 비밀번호·API 키·DB·내부 운영 문서는 저장소에 포함하지 않습니다.

## 현재 기능과 계산 계약

- GPU free/busy, Util·VRAM·온도·프로세스 요약·사용자·recent. 기본 GPU 10초, Disk 30분이며 느린 디스크 수집은 별도 작업자로 처리합니다.
- busy는 process 존재 또는 VRAM ≥500 MiB 또는 Util ≥10%인 점유 상태입니다. Util 0% 상주 프로세스도 GPU를 예약한 것으로 집계합니다.
- effective UID·NSS/숫자 UID·PID 시작 tick·GPU UUID를 재검증합니다. root 소유 proc 디렉터리만으로 root를 추정하지 않습니다. 확인된 점유 process는 owner 조회 실패 때문에 버리지 않습니다.
- 같은 GPU의 여러 process는 중복 집계하지 않습니다. 모든 owner가 확인되면 서로 다른 사용자에게 균등 분할하고, 일부 미확인은 해당 구간을 미귀속으로 남깁니다. 전체 argv·환경·옵션 값은 노출하지 않습니다.
- 60초 미만 사용도 기록·통계에 남지만 장기 유휴 해제에는 제외합니다. 7일 이상 추적 시 관측률 100%를 요구하지 않고 장기 유휴를 판정합니다. 최근 7일 지수는 관측 슬롯 시간을 분모로 쓰며 100% 표시는 `7일 / 7일`입니다.
- LAB DAILY INDEX는 KST 하루의 GPU별 관측 VRAM 시간 가중 평균을 합산합니다. 겹치는 시간은 중복 계산하지 않고 미관측을 0으로 채우지 않습니다.
- Recent Activity는 날짜·서버·사용자·페이지 이동을 지원합니다. DOWN/UP 포함은 기본 OFF이고 observation_gap/user_change는 내부 데이터로만 유지합니다.
- Disk Used와 경고 배지는 파일시스템 합산 used/(used+available)로 같습니다. 중복 마운트와 예약 공간을 처리하고 사용자별 읽기 한계는 ≥/≈로 표시합니다. Docker 오류는 명령 대신 안내 문구를 제공합니다.
- 타이머 6개·고정 색상 풀·마감일 정렬·동시각 등록 순서·TBA·로컬 SVG 국기를 지원합니다. 공지의 만료일은 선택이며 기존 공지도 무기한으로 바꿀 수 있습니다. 작성창은 바깥 클릭으로 닫히지 않습니다.
- 자동 갱신은 Disk 선택·스크롤·키보드 포커스를 유지합니다. 상단 바는 고정되지 않고 Refresh 버튼 폭은 변하지 않습니다.
- AA 상위 29개 모델을 서버에서 캐시하며 API 제공 버전과 간략 이름을 표시합니다. 100회/일·4페이지면 약 1시간 간격, 최소 15분이며 최대 8회를 남깁니다. 별도 scheduler가 무접속 시에도 예약을 지킵니다. 전체 페이지 검증 후 게시하고 브라우저는 5분마다 서버 캐시를 확인합니다.

## 설치와 보안

Python 3.12+·OpenSSH 또는 Docker를 사용합니다. 실제 hosts.json, SSH key/known_hosts, IP·Host 허용 목록을 먼저 구성하세요. API key는 app 소유의 secrets/artificial_analysis_api_key, 0600 파일과 제한된 부모 폴더에 보관합니다. GPU 서버에는 SSH·python3·nvidia-smi·df·GNU du가 필요하며 홈 폴더 삭제와 무관하게 probe를 실행합니다.

공지 비밀번호와 관리자 PIN은 PBKDF2-SHA256 600,000회로 해시하고 동시 확인 2개·서브넷/전역 실패 제한을 적용합니다. IP·Host·Origin은 edge와 app에서 검증합니다. HTTP는 신뢰하는 LAN을 전제로 하며 IP 제한은 암호화를 대신하지 않습니다. 비밀번호 저장 억제 힌트는 브라우저 정책까지 보장하지는 못합니다.

권한 Disk helper가 필요한 경우 scripts/provision-disk-helper.py로 설치물을 생성·검토한 뒤 해당 서버 관리자 권한으로 설치합니다. root 소유의 고정 helper에는 인자를 허용하지 않고 5분 캐시·낮은 우선순위·동시 실행 잠금을 사용합니다. 관리자 암호를 앱에 저장하지 않습니다. Disk probe를 바꾸면 helper도 재설치합니다.

## 운영과 비상 사본

deploy.sh와 Caddyfile의 예시 주소·경로를 수정한 뒤 배포합니다. app은 내부 Docker 네트워크에 있고 Caddy만 port를 공개합니다. 비root·readonly·cap-drop·no-new-privileges와 메모리/PID/로그 제한을 유지합니다. 배포 직전 검증된 SQLite 백업을 만들며 실패 시 컨테이너·DB·SSH 파일을 되돌립니다.

Windows 비상 사본은 평소 OFF이고 VERSION·release fingerprint·전체 파일 manifest를 운영본과 맞춥니다. Start는 운영 서버가 응답하면 거부하고, Force도 버전/지문 불일치를 무시하지 않습니다. 앱은 일반 사용자로 실행하고 LAN 방화벽만 UAC 승인을 받습니다. Stop은 해당 process·방화벽·임시 SSH secret을 제거합니다. 비상 운용 중 수정한 공지/타이머는 복귀 시 별도로 화해해야 합니다.

일별 SQLite 백업 14일·배포 전 백업 30일·event/일별 지수 180일·raw 구간 8일을 기본으로 보존합니다. 자동 offsite 백업은 없으며 수동 도구만 제공합니다. 유효한 migration·회귀 테스트·복구 백업을 과거 자료라는 이유로 지우지 않습니다. 상세 설정·구조·계산·운영 명령은 [English README](README.md)를 참조하세요.

## Commands

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch
# Configure hosts.json and SSH before starting.
python3 scripts/admin-passphrase.py ensure
python3 server.py --host 127.0.0.1 --port 8787
python3 scripts/audit-data.py data/gpu_watch.sqlite3
python3 -m unittest discover -s tests -v
node tests/test_frontend.js
```

[MIT License](LICENSE) · [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) · [Twemoji](https://github.com/jdecked/twemoji) (CC-BY 4.0).
