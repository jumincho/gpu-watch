# GPU Watch Dashboard v3

연구실이 함께 쓰는 GPU 서버를 위한 가볍고 에이전트가 필요 없는 대시보드입니다. 지금 비어 있는 GPU, 사용 중인 GPU의 사용자, 남은 디스크 용량을 보여 줍니다. 데이터는 일반 SSH로만 수집합니다.

[English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-HK.md) | [日本語](README.ja.md) | **한국어**

![연구실 요약, 학회 마감 타이머, 서버별 GPU 카드가 보이는 GPU Watch 대시보드](docs/images/dashboard.png)

<sub>가상의 데모 데이터로 찍은 화면입니다. 인터페이스는 한국어이며 기술 용어는 영어로 표기합니다.</sub>

## 왜 GPU Watch인가

공용 서버에서 작업을 시작하기 전에는 보통 세 가지를 확인해야 합니다. 어느 GPU가 비어 있는지, 사용 중인 GPU는 누가 쓰는지, 디스크 공간은 충분한지입니다. GPU Watch는 이 세 가지를 한 페이지에서 알려 줍니다.

- **에이전트가 필요 없습니다.** 각 GPU 서버에는 SSH 접속, `nvidia-smi`, `python3`만 있으면 됩니다. 서버에 상주하는 프로그램이 없습니다.
- **가볍습니다.** 백엔드는 Python 표준 라이브러리와 SQLite만 사용합니다. 프런트엔드는 빌드 단계가 없는 순수 HTML, CSS, JavaScript입니다.
- **신중합니다.** 프로세스 소유자를 추측하지 않고, 전체 명령행을 보여 주지 않습니다. 오래된 측정값을 현재 값처럼 표시하지 않습니다.

## v3에서 달라진 점

- **프로세스 소유자 확인**에 `/proc/<pid>/status`의 effective UID를 씁니다. `/proc` 디렉터리가 root 소유로 보이는 프로세스(nondumpable 프로세스)도 올바른 사용자에게 귀속됩니다.
- **GPU 조회가 실패해도 디스크는 계속 갱신합니다.** SSH는 되는데 NVIDIA 드라이버 불일치 등으로 GPU 조회만 실패하는 경우입니다.
- **선택적인 권한 디스크 helper**로, 모니터링 계정이 다른 사용자의 홈 디렉터리를 읽지 못하는 서버에서도 사용자별 사용량을 측정합니다. [권한 Disk helper](#권한-disk-helper)를 참고하세요.
- **Artificial Analysis 갱신이 API 호출 한도를 따릅니다.** 고정 6시간 대신 응답의 호출 한도 헤더로 간격을 정합니다.
- **보안 강화.** 공지 작성도 비밀번호 해시의 동시 실행 제한을 함께 쓰고, Caddy 빌드는 보안 패치된 OpenTelemetry 모듈로 고정했습니다.
- **모든 파일을 LF 줄바꿈으로 체크아웃**하므로 Linux 운영본과 Windows 사본의 릴리스 지문(fingerprint)이 일치합니다.
- **화면 다듬기.** 타이머의 남은 시간이 카드 세로 가운데에 옵니다. 긴 제목을 우선하고 남은 시간을 필요할 때 두 줄로 나누며, 그래도 넘치는 제목은 말줄임표로 줄이고 마우스를 올리면 전체 제목을 보여 줍니다. 휴대폰에서는 Intelligence Index가 더 촘촘해지고, 한국어는 어절 단위로 줄을 바꿉니다.

## 기능

### GPU와 서버

- 모든 GPU의 실시간 상태: free 또는 busy, 사용률, VRAM, 온도, 사용 중인 시간이나 마지막 사용 시각.
- GPU별 프로세스와 그 사용자, 메모리, 짧은 명령 요약. 상세 보기에는 PID, 시작 시각, 컨테이너가 더해지지만 전체 명령행은 표시하지 않습니다.
- 연구실 전환, 연구실 전체 요약(온라인 서버, free·busy GPU, VRAM), 그리고 사용 중·전체 free·연결 실패·디스크 경고 필터.
- 활동 배지: 🔥 고사용(최근 7일 바쁨 지수 50% 이상), ❄️ 장기 유휴(7일 동안 60초 이상 연속 사용 없음), ⛔ 연결 실패.

### 디스크

- 파일시스템별 여유, 사용 가능, 사용, 예약 공간. 사용률 90%부터 경고합니다.
- 읽을 수 있는 홈 디렉터리와 설정한 경로, Docker 쓰기 계층을 합친 사용자별 사용량. 일부만 집계된 경우 최솟값은 `≥`, 추정값은 `≈`로 표시합니다.

### 이력과 추세

- busy/free 전환을 기록하는 Recent Activity. 날짜, 서버, 사용자로 걸러 볼 수 있습니다. 연결 DOWN/UP 이벤트는 기본적으로 숨기며 포함해서 볼 수도 있습니다.
- 서버별 최근 7일 바쁨 지수와 사용자별 점유율.
- LAB DAILY INDEX: 최근 24시간의 시간별 VRAM 캔들과 30일간의 일일 평균 VRAM 추이.

### 연구실 도구

- 최대 6개의 학회 마감 카운트다운(KST). TBA 항목도 지원합니다. 타이머 제목의 국기 이모지는 저장소에 포함된 SVG 파일로 그립니다.
- 만료 시각을 선택적으로 지정할 수 있는 전광판 공지. 작성자는 자신이 정한 비밀번호로 공지를 수정하고, 관리자는 관리자 PIN으로 모든 공지를 관리합니다.
- 학회 마감 사이트, AI 서비스 상태 페이지, AI 소식으로 가는 바로가기. Artificial Analysis Intelligence Index(상위 29개 모델)도 보여 주며, 서버가 대신 가져오므로 API 키가 브라우저에 노출되지 않습니다.

### 일상 사용

- 기본 10초마다 자동으로 새로 고칩니다. 선택한 탭, 스크롤 위치, 키보드 포커스를 유지하고, 백그라운드 탭에서는 주기를 늦춥니다. 데이터 갱신이 멈추면 배너로 알립니다.
- 폭 320px 화면까지 지원하고, 키보드 탐색과 모션 줄이기 설정을 지원합니다. 릴리즈 점검에서 글자 대비, 포커스 표시, 화면 폭별 배치를 확인합니다.

## 동작 방식

```text
Browser ──HTTP──▶ Caddy  (IP allowlist, compression)
                    │
                    ▼
              server.py   (HTTP API · collector · maintenance)
               │      │
          SSH  │      └──▶ SQLite  (data/)
               ▼
          GPU servers  (nvidia-smi · /proc · df · du · docker)
```

1. 수집기는 `poll_interval_seconds`(10초)마다 `hosts.json`의 모든 호스트에 병렬로 접속합니다. 각 호스트에서 `nvidia-smi` 조회와 작은 인라인 Python 프로브를 실행합니다.
2. 프로세스 소유자는 `/proc/<pid>/status`의 effective UID와 NSS 이름으로 정하고, PID 시작 시각과 GPU UUID로 다시 확인합니다. 확인할 수 없으면 추측하지 않고 알 수 없음으로 표시합니다.
3. 디스크 사용량은 `disk_poll_interval_seconds`(30분)마다 `df`, 시간 제한이 있는 `du`, `docker ps --size`로 측정합니다. 선택적인 권한 helper를 쓸 수도 있습니다.
4. 관측값은 SQLite에 시간 구간으로 저장합니다. 겹치는 GPU, 프로세스, 사용자를 합쳐서 계산하므로 사용 시간이 두 번 집계되지 않습니다.
5. 웹 페이지는 `/api/snapshot`, `/api/events`, `/api/insights`, `/api/intelligence-index`를 읽습니다.

### 상태 판정 규칙

- GPU에 연산 프로세스가 있거나, VRAM을 500MiB 이상 쓰거나, 사용률이 10% 이상이면 **busy**입니다. 두 기준값은 설정으로 바꿀 수 있습니다.
- VRAM을 잡아 둔 프로세스는 사용률이 0%여도 busy로 봅니다. 그래서 예약해 둔 GPU가 free로 보이지 않습니다.
- SSH 연결이든 GPU 조회든 GPU를 완전히 관측하지 못하면 서버는 **DOWN**입니다. 그 서버의 GPU는 하나도 사용 가능으로 치지 않으며, 이전 측정값을 현재 값처럼 보여 주지 않습니다. 디스크 조회가 성공해도 DOWN 서버를 UP으로 바꾸지 않습니다.

## 보안 모델

- **접근 제어.** Caddy가 IP 허용 목록을 적용합니다. 앱도 클라이언트 IP, `Host`, `Origin`을 독립적으로 다시 확인합니다.
- **쓰기 요청.** 공지와 타이머를 바꾸려면 같은 출처에서 보낸 요청과 비밀번호 또는 PIN이 필요합니다. 해시는 600,000회 반복한 PBKDF2-SHA256을 씁니다. 해시 검사는 동시에 최대 2건만 실행하고, 실패한 시도는 서브넷별, 전체 기준으로 속도를 제한합니다.
- **제한된 프로세스 정보.** 전체 명령행, 환경변수, 자격 증명은 브라우저에 보내지 않고 정제한 짧은 요약만 제공합니다. 운영 오류는 길이를 제한하고 민감한 값을 가리지만, 호스트 주소나 포트를 포함할 수 있으므로 네트워크 구성을 숨기는 기능은 아닙니다. 서버 카드에는 검증한 IP 주소를 의도적으로 표시합니다.
- **브라우저 보호.** 엄격한 콘텐츠 보안 정책(CSP)이 인라인 스크립트를 차단합니다. 아이콘과 국기는 외부 호스트가 아니라 로컬에서 제공합니다.
- **컨테이너.** root가 아닌 사용자로 실행하며, 루트 파일시스템은 읽기 전용이고, 모든 권한(capability)을 제거하고, `no-new-privileges`와 PID·메모리·로그 제한을 적용합니다.
- **비밀 정보.** SSH 비밀번호, API 키, PIN 해시는 git이 무시하는 런타임 폴더(`secrets/`, `data/`, `operator-secrets/`)에만 두고 저장소에는 넣지 않습니다. SSH 비밀번호는 명령행이 아니라 `SSH_ASKPASS`로 OpenSSH에 전달합니다.
- **전송 구간.** 일반 HTTP는 신뢰할 수 있는 LAN에서만 쓰도록 의도한 것입니다. 더 넓게 공개하려면 먼저 TLS와 인증을 추가하세요.

## 요구 사항

| 위치 | 필요한 것 |
|---|---|
| 대시보드 호스트 | Python 3.12(표준 라이브러리만)와 OpenSSH 클라이언트, 또는 Docker |
| 각 GPU 서버 | 모니터링 계정의 SSH 접속, `nvidia-smi`가 포함된 NVIDIA 드라이버, `python3`, `df`, GNU `du`. Docker는 선택이며 있으면 컨테이너별 사용량을 추가로 보여 줍니다. 권한 디스크 helper를 쓰려면 `sudo`도 필요합니다. |
| 사용자 | 최신 웹 브라우저 |

## 빠르게 시작하기

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch

# 1. 서버 목록을 작성합니다(아래 설정 항목 참고).
$EDITOR hosts.json

# 2. 관리자 PIN을 만듭니다. 일회용 평문 PIN을 저장한 위치가 출력됩니다.
python3 scripts/admin-passphrase.py ensure

# 3. 대시보드를 실행합니다.
python3 server.py --host 127.0.0.1 --port 8787
```

<http://127.0.0.1:8787/>을 엽니다. 기본값으로는 루프백 클라이언트만 접속할 수 있습니다. LAN의 다른 컴퓨터에서 보려면 허용할 대상을 명시하세요. 아래 주소는 예시입니다.

```sh
GPU_WATCH_ALLOWED_NETWORKS="127.0.0.0/8,::1/128,192.0.2.0/24" \
GPU_WATCH_ALLOWED_HOSTS="192.0.2.10:8787,127.0.0.1:8787,localhost:8787" \
python3 server.py --host 0.0.0.0 --port 8787
```

## 설정

### `hosts.json`

저장소에 들어 있는 `hosts.json`은 가상의 연구실을 설명합니다. 자신의 서버로 바꿔서 쓰세요.

```json
{
  "poll_interval_seconds": 10,
  "disk_poll_interval_seconds": 1800,
  "busy_memory_threshold_mib": 500,
  "busy_utilization_threshold_percent": 10,
  "labs": [{ "id": "vision", "label": "VISION LAB" }],
  "hosts": [
    {
      "name": "atlas",
      "label": "atlas",
      "lab": "vision",
      "ssh_host": "192.0.2.11",
      "ssh_port": 22,
      "ssh_user": "gpuwatch",
      "ssh_identity_file": "~/.ssh/id_ed25519",
      "expected_gpu_count": 4,
      "note": "NVIDIA GeForce RTX 4090 x4",
      "owner": "Vision",
      "owner_type": "assigned",
      "location": "Room 301"
    }
  ]
}
```

| 호스트 필드 | 의미 |
|---|---|
| `name` | 고유 ID(영문자, 숫자, `.`, `_`, `-`). `ssh_host`를 생략하면 SSH 별칭으로도 쓰입니다. |
| `label`, `lab` | 표시 이름과 소속 연구실 |
| `ssh_host`, `ssh_port`, `ssh_user` | 접속 대상 |
| `ssh_identity_file` | 키 로그인에 쓸 개인 키 |
| `ssh_password_file`, `ssh_options` | 비밀번호 로그인. 비밀번호 파일은 `secrets/` 아래에 두며 실행 중에 읽습니다. |
| `display_ip` | 별칭으로 접속하는 호스트의 카드에 표시할 IP |
| `expected_gpu_count` | 서버가 DOWN일 때도 계속 보여 줄 GPU 칸 수 |
| `note`, `owner`, `owner_type`, `location` | 카드에 표시할 문구와 배지 스타일(`assigned` 또는 `shared`) |
| `disk_user_paths` | 추가로 측정할 사용자별 경로. `{ "user": …, "path": … }` 형식입니다. |
| `collect_docker_usage` | `docker ps --size`로 Docker 쓰기 계층도 측정합니다. 기본으로는 `nll` 연구실의 호스트에서만 켜집니다. |
| `privileged_disk_helper` | 설치한 root helper로 디스크 사용량을 측정합니다. `disk_user_paths`와 함께 쓸 수 없습니다. |

그 밖의 최상위 설정으로는 프로브 제한 시간, `collector_workers`, 🔥·❄️ 배지 기준을 정하는 `activity_policy`, 보존 기간이 있습니다. 기본값으로 이벤트는 180일, 일일 백업은 14일 동안 보존합니다. 공지는 삭제 또는 만료 후 90일이 지나면 정리하며, 만료일 없는 공지는 삭제할 때까지 유지합니다.

### 환경변수

| 변수 | 용도 | 기본값 |
|---|---|---|
| `GPU_WATCH_ALLOWED_NETWORKS` | 클라이언트 IP 허용 목록(쉼표로 구분한 CIDR 대역) | 루프백 |
| `GPU_WATCH_ALLOWED_HOSTS` | 허용할 `Host` 헤더 값 | 루프백 |
| `GPU_WATCH_TRUSTED_PROXY_NETWORKS` | `X-Forwarded-For` 헤더를 신뢰할 리버스 프록시 | 루프백 |
| `GPU_WATCH_SSH_CONFIG_FILE` | 사용할 OpenSSH 설정 파일의 절대 경로 | 없음 |
| `GPU_WATCH_SSH_IDENTITY_FILE` | 호스트별 키 파일 대신 쓸 키 파일 | 없음 |
| `GPU_WATCH_ADMIN_PIN_HASH` | `data/admin_pin.hash` 대신 쓸 관리자 PIN 해시 | 없음 |
| `GPU_WATCH_RUNTIME_MODE` | `standalone`, `production`, `emergency` 중 하나 | `standalone` |
| `GPU_WATCH_BUILD_VERSION` | 푸터에 표시할 릴리스 번호 | `VERSION` |

### Artificial Analysis

Intelligence Index를 보여 주려면 API 키를 `secrets/artificial_analysis_api_key`에 넣으세요. 심볼릭 링크가 아닌 일반 파일이어야 하고 권한은 `0600`이어야 합니다. 브라우저는 캐시된 순위만 받습니다.

- 서버는 응답의 `X-RateLimit-*` 헤더와 페이지 수를 보고 전체 갱신을 호출 한도 기간에 고르게 나눕니다. 여유 요청을 조금(최대 8회) 남겨 두고, 15분보다 자주 갱신하지 않습니다. 하루 100회 한도에 4페이지라면 약 1시간에 한 번입니다.
- 남은 호출이 부족하면 한도가 초기화될 때까지 기다립니다. 요청을 보내기 전에 먼저 횟수를 차감하므로, 재시작이나 네트워크 오류 때문에 호출이 남아 있다고 착각하지 않습니다.
- 호출 한도 헤더가 없으면 6시간마다 갱신합니다. 실패하면 1시간 뒤에 다시 시도하고, API가 더 긴 `Retry-After`를 보내면 그에 따릅니다. 48시간이 지난 데이터는 오래된 데이터로 표시합니다.
- 호출 한도 상태(한도, 잔여량, 초기화 시각, 페이지 수, 다음 시도 시각)는 키와 별도로 `data/`의 캐시 옆에 저장합니다.
- 웹 페이지는 5분마다 서버 캐시를 확인합니다. 페이지를 열어 둔 사람이 없으면 다음 유지보수 주기(기본 1시간)까지 갱신이 늦어질 수 있습니다.

## 권한 Disk helper

모니터링 계정이 다른 사용자의 홈 디렉터리를 읽지 못하는 서버에서는 사용자별 합계가 일부만 집계됩니다. 이런 서버에는 GPU Watch 자체의 디스크 수집기만 실행하는 작은 root helper를 설치할 수 있습니다.

```sh
# 검토할 수 있는 설치 스크립트를 만듭니다(설치는 하지 않습니다).
python3 scripts/provision-disk-helper.py --user gpuwatch --output disk-installer.py

# disk-installer.py를 검토한 뒤 GPU 서버로 복사해 관리자 권한으로 실행합니다.
sudo python3 -I disk-installer.py
```

설치 스크립트는 `/usr/local/libexec/gpu-watch-disk`와, 모니터링 계정이 이 helper만 인자 없이 실행할 수 있게 하는 sudoers 규칙, 그리고 `/var/cache/gpu-watch/` 캐시 폴더를 만듭니다. helper는 경로·명령·환경 입력을 받지 않습니다. 동시 실행을 막고 결과를 5분 동안 캐시하며, 낮은 CPU 우선순위로 동작합니다. 데몬을 설치하지 않고 관리자 비밀번호도 저장하지 않습니다.

공개 예제는 helper를 기본 비활성으로 둡니다. `privileged_disk_helper`가 없거나 false이면 sudo를 사용하지 않습니다. 설치한 뒤 해당 호스트에만 `"privileged_disk_helper": true`를 설정하세요. helper가 없으면 GPU Watch는 같은 시간 예산 안에서 일반 권한 집계로 돌아가고 사용자별 사용량을 일부 미집계로 표시합니다.

## 배포

기준이 되는 운영 구성은 보안을 강화한 컨테이너 두 개입니다. 외부로 공개하는 포트는 Caddy 하나이고, 앱은 내부 Docker 네트워크에만 있습니다.

- `Dockerfile`은 다이제스트로 고정한 `python:3.12-alpine` 이미지 위에 앱을 빌드합니다.
- `Dockerfile.caddy`는 고정한 커밋과 고정한 의존성 버전으로 Caddy를 빌드합니다.
- `deploy.sh`는 연구실 운영 서버용 배포 스크립트입니다. 경로와 권한을 확인하고 두 이미지를 빌드한 뒤 SSH·Caddy 설정을 검사합니다. 전환 직전 앱을 멈추고 SQLite 온라인 백업을 생성·검증합니다. 이전 컨테이너를 보존하고 새 배포의 health와 접근 제어를 확인하며, 실패하면 롤백합니다. 다른 곳에서 쓰려면 먼저 호스트에 맞는 경로와 주소를 바꾸세요.

릴리스 지문은 `VERSION`, `server.py`, `hosts.json`, `gpu_watch/`, `static/`을 해시한 값입니다. 운영본과 비상 사본의 지문이 같아야 합니다.

### Windows 비상 대체 운영

`emergency-local-fallback.ps1 -Action Status|Start|Stop`은 Windows 비상 사본을 관리합니다. 먼저 예제의 경로, 운영 주소, SSH 설정, LAN 범위를 환경에 맞춰 바꾸세요. `Start`는 `-Force`를 주지 않는 한 운영 서버가 응답하면 실행을 거부합니다. `VERSION`·release fingerprint와 새 수집 결과를 확인하고, 방화벽은 설정한 LAN으로만 제한합니다. `Stop`은 자신이 만든 리스너·방화벽 규칙·임시 비밀번호 파일을 정리합니다. 로컬 DB는 별개이므로 복귀 전에 장애 중 생성한 공지와 타이머를 대조하세요.

## 운영

```sh
# 실행 중인 컨테이너 상태 확인
docker exec gpu-watch-dashboard python3 /app/scripts/check_local.py --health-only --expected-build-version 3

# SQLite 무결성과 집계 불변식 검사
python3 scripts/audit-data.py data/gpu_watch.sqlite3

# data/backups 안의 백업 복구(경로·SQLite 무결성·외래 키·스키마 확인)
sh scripts/restore-backup.sh "$PWD/data/backups/backup.sqlite3"
```

유지보수 작업이 SQLite 일일 백업을 만들고, `deploy.sh`는 배포할 때마다 백업을 하나 더 만듭니다. 자동 외부 백업은 없으며, `scripts/offsite-backup.sh`는 수동 도구입니다.

## 개발과 테스트

```sh
python3 -m unittest discover -s tests -v   # Python 회귀·계약 테스트
node tests/test_frontend.js                # 프런트엔드 로직 계약
```

테스트는 상태 판정 규칙, 구간 계산, 보안 헤더, UI 문구 같은 제품 동작도 고정합니다. 계약 테스트가 실패한다면 대개 사용자에게 보이는 변화이므로 의도적인 결정이 필요합니다.

## 프로젝트 구조

| 경로 | 역할 |
|---|---|
| `server.py` | HTTP API, 수집기, 저장소, 유지보수 |
| `gpu_watch/` | 인증, 프로세스 귀속, 보안 도우미, 이력, 호출 한도를 추적하는 Artificial Analysis 클라이언트 |
| `static/` | 프런트엔드(`index.html`, `app.js`, `styles.css`)와 포함된 아이콘·국기 |
| `hosts.json` | 서버, 연구실, 수집 설정(가상의 예시) |
| `Caddyfile`, `Dockerfile`, `Dockerfile.caddy` | 에지 프록시와 컨테이너 이미지 |
| `deploy.sh` | 백업과 롤백을 포함한 운영 배포 |
| `run-dashboard.ps1`, `emergency-local-fallback.ps1` | Windows 비상 수집기 |
| `scripts/` | 상태 점검, 데이터 감사, 백업·복구, 관리자 PIN, SSH 도우미, 디스크 helper 설치 스크립트 생성기 |
| `tests/` | Python·Node 테스트 |

## 릴리스

v3, 2026-09-25 릴리스. 화면 푸터에는 `GPT-6 Astra Max (Implementation) · Claude Opus 5.5 Max (Frontend)`가 표시됩니다.

## 감사의 말

국기 아이콘은 CC-BY 4.0 라이선스의 [Twemoji](https://github.com/jdecked/twemoji)를 사용합니다. 자세한 내용은 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 참고하세요. 서비스 아이콘의 권리는 각 소유자에게 있으며, 해당 상태 페이지로 가는 링크를 표시하는 용도로만 씁니다.

## 라이선스

GPU Watch는 [MIT 라이선스](LICENSE)로 배포합니다. 함께 들어 있는 국기 아이콘과 서비스 아이콘은 이 라이선스에 포함되지 않습니다. 자세한 내용은 위의 감사의 말을 참고하세요.
