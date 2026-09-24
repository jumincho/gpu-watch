import ast
import hashlib
import ipaddress
import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

import server


ROOT = Path(__file__).resolve().parents[1]


def branch_score(source: str, qualified_name: str) -> int:
    tree = ast.parse(source)
    parts = qualified_name.split(".")
    body = tree.body
    target = None
    for part in parts:
        target = next(node for node in body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == part)
        body = target.body
    return 1 + sum(
        1
        for node in ast.walk(target)
        if isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.IfExp, ast.Match, ast.BoolOp))
    )


class IdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])


class ReleaseContractTests(unittest.TestCase):
    def test_http_edge_and_ip_allowlist_contract(self):
        caddy = (ROOT / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("auto_https off", caddy)
        self.assertIn("http://192.0.2.10:8787", caddy)
        self.assertIn('\n:8787 {\n', caddy)
        self.assertEqual(caddy.count('respond "Forbidden" 403'), 2)
        self.assertIn("192.0.2.0/24", caddy)
        self.assertNotIn("tls internal", caddy)
        self.assertNotIn("Strict-Transport-Security", caddy)
        self.assertNotIn("/gpu-watch-ca.crt", caddy)
        # GHSA-6365-7ppr-5r92 requires forward_auth and reverse_proxy in the
        # same handler. This edge intentionally has no authentication upstream.
        self.assertNotIn("forward_auth", caddy)

    def test_deployment_has_lock_rollback_and_no_direct_app_port(self):
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("flock -n", script)
        self.assertIn("trap finish", script)
        self.assertIn("rollback()", script)
        self.assertIn("PREVIOUS_CADDY_CONTAINER", script)
        self.assertIn("docker exec \"$CADDY_CONTAINER\" wget", script)
        self.assertRegex(script, r"-p\s+8787:8787")
        self.assertEqual(len(re.findall(r"-p\s+8787:8787", script)), 1)
        app_start = script.index('docker run -d \\\n    --name "$CONTAINER"')
        app_end = script.index('\n\nif ! wait_container_healthy "$CONTAINER"', app_start)
        app_run = script[app_start:app_end]
        caddy_start = script.index('docker run -d \\\n    --name "$CADDY_CONTAINER"')
        caddy_run = script[caddy_start:]
        self.assertNotRegex(app_run, r"(?:^|\s)(?:-p|--publish)(?:\s|=)")
        self.assertIn("--ulimit core=0:0", app_run)
        self.assertRegex(caddy_run, r"-p\s+8787:8787")
        self.assertNotRegex(script, r"-p\s+(?:80|443):")
        self.assertIn("--cap-drop ALL", script)
        self.assertIn("--read-only", script)
        self.assertIn('--tmpfs "/data:rw,noexec,nosuid,size=16m', script)
        self.assertIn('--tmpfs "/config:rw,noexec,nosuid,size=16m', script)
        self.assertNotIn("CADDY_DATA_DIR", script)
        self.assertNotIn("CADDY_CONFIG_DIR", script)
        self.assertNotIn("public-ca", script)
        trap_end = script.index("trap 'exit 143' TERM")
        switch_flag = script.index("APP_SWITCHED=1", trap_end)
        destructive_stop = script.index('docker stop -t 15 "$CONTAINER"', switch_flag)
        self.assertLess(switch_flag, destructive_stop)
        self.assertIn('elif docker container inspect "$CONTAINER"', script)
        self.assertIn('elif docker container inspect "$CADDY_CONTAINER"', script)
        self.assertIn("NETWORK_SUBNETS=$(docker network inspect", script)
        self.assertIn('GPU_WATCH_TRUSTED_PROXY_NETWORKS="$NETWORK_SUBNETS"', script)
        self.assertNotIn("GPU_WATCH_TRUSTED_PROXY_NETWORKS=172.16.0.0/12", script)
        self.assertIn("backup_stamp=$(date -u +%Y%m%dT%H%M%SZ)", script)
        self.assertIn('APP_DATA_DIR="$ROOT/data"', script)
        self.assertIn('predeploy_temporary="$APP_DATA_DIR/backups/.pre-deploy-', script)
        self.assertIn('-v "$APP_DATA_DIR:/app/data"', app_run)
        self.assertNotIn('runtime-data:/app/data', app_run)
        self.assertGreaterEqual(script.count("validate_application_data_directory"), 3)
        self.assertNotIn('if [ ! -f "$predeploy_backup" ]', script)
        self.assertIn('mv -f "$predeploy_temporary" "$predeploy_backup"', script)
        self.assertIn("restore_predeploy_database()", script)
        self.assertIn('restore_predeploy_database; then', script)
        self.assertIn('previous app was not restarted without its verified database', script)
        rejected_remove = script.index('docker rm -f "$CONTAINER"', script.index("rollback()"))
        database_restore = script.index("restore_predeploy_database; then", rejected_remove)
        previous_restart = script.index('docker rename "$PREVIOUS_CONTAINER" "$CONTAINER"', database_restore)
        self.assertLess(rejected_remove, database_restore)
        self.assertLess(database_restore, previous_restart)
        switch_phase = script.index("APP_SWITCHED=1", script.index("trap 'exit 143' TERM"))
        old_app_stop = script.index('docker stop -t 15 "$CONTAINER"', switch_phase)
        backup_call = script.index("create_predeploy_database_backup", old_app_stop)
        database_mutation = script.index('python3 "$ROOT/scripts/sanitize-databases.py"', backup_call)
        self.assertLess(old_app_stop, backup_call)
        self.assertLess(backup_call, database_mutation)
        self.assertIn("DATABASE_RESTORE_REQUIRED=1", script[backup_call:database_mutation])
        self.assertEqual(script.count("docker build --pull --no-cache"), 2)
        self.assertIn("cleanup_dangling_release_images()", script)
        self.assertIn('"GPU Watch Edge Builder"', script)
        self.assertNotIn("docker image prune --all", script)

    def test_http_listener_binds_before_background_workers_start(self):
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        main = source[source.index("def main()") :]
        listener = main.index("server = DashboardHTTPServer")
        collector = main.index("collector.start()")
        maintenance = main.index("maintenance.start()")
        self.assertLess(listener, collector)
        self.assertLess(listener, maintenance)
        self.assertIn("collector_started = False", main)
        self.assertIn("maintenance_started = False", main)
        self.assertIn("if collector_started:", main)
        self.assertIn("if maintenance_started:", main)

    def test_runtime_images_are_pinned_and_minimal(self):
        app_image = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        edge_image = (ROOT / "Dockerfile.caddy").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".runtime-ssh.*", dockerignore)
        self.assertIn(".runtime-ssh.*", gitignore)
        self.assertIn("data/", dockerignore)
        self.assertIn("data/", gitignore)
        self.assertNotIn("runtime-data/", dockerignore)
        self.assertNotIn("runtime-data/", gitignore)
        self.assertIn(".partial-gpu-stage.*", dockerignore)
        self.assertIn("python:3.12.14-alpine3.24@sha256:", app_image)
        self.assertIn("apk upgrade --no-cache", app_image)
        self.assertIn("pip uninstall --yes pip setuptools wheel", app_image)
        self.assertIn("golang:1.26.7-alpine3.24@sha256:", edge_image)
        self.assertIn(
            "caddy/v2/cmd/caddy@b2693fb63a30e6d7be0972c3645e9a2c0a500e93",
            edge_image,
        )
        self.assertIn("caddy/v2.CustomVersion=v2.11.4+gpuwatch.b2693fb", edge_image)
        self.assertIn("alpine:3.24.1@sha256:", edge_image)
        self.assertIn('org.opencontainers.image.title="GPU Watch Edge Builder"', edge_image)
        self.assertIn("XDG_CONFIG_HOME=/config", edge_image)
        self.assertIn("XDG_DATA_HOME=/data", edge_image)
        self.assertIn("HEALTHCHECK", app_image)
        self.assertIn("/app/scripts/check_local.py", app_image)
        self.assertIn("COPY scripts/docker-entrypoint.sh scripts/check_local.py scripts/ssh-askpass.sh ./scripts/", app_image)
        self.assertNotIn("COPY scripts ./scripts", app_image)
        self.assertNotIn("offsite-backup.sh", app_image)
        self.assertNotIn("restore-backup.sh", app_image)
        self.assertIn("HEALTHCHECK", edge_image)
        self.assertIn("http://127.0.0.1:8787/api/health", edge_image)
        for dockerfile in (app_image, edge_image):
            for source in re.findall(r"^FROM\s+(\S+)", dockerfile, re.MULTILINE):
                self.assertRegex(source, r"@sha256:[0-9a-f]{64}$")

    def test_static_release_placeholder_and_accessibility_contract(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        parser = IdParser()
        parser.feed(html)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertIn("__GPU_WATCH_BUILD_VERSION__", html)
        self.assertEqual(html.count("__GPU_WATCH_ASSET_VERSION__"), 7)
        self.assertIn(
            "GPU Watch · Release v__GPU_WATCH_BUILD_VERSION__ · __GPU_WATCH_RELEASE_DATE__",
            html,
        )
        self.assertIn(
            "Implemented by __GPU_WATCH_RELEASE_MODEL__ / Frontend design assisted by Claude Opus 5.5 Max",
            html,
        )
        self.assertIn(
            "http://aideadlines.org/?sub=ML,CV,NLP,RO,SP,DM,AP,KR,HCI,IRSM,MISC",
            html,
        )
        self.assertNotIn("https://aideadlin.es/", html)
        service_urls = (
            "https://status.openai.com/",
            "https://status.claude.com/",
            "https://www.codexrunway.com/",
            "https://codex-resets.com/",
            "https://www.reddit.com/r/MachineLearning/new/",
            "https://www.threads.com/@choi.openai",
        )
        self.assertLess(html.index('id="labPulse"'), html.index('id="aiServiceWatch"'))
        self.assertLess(html.index('id="aiServiceWatch"'), html.index('<footer class="site-footer">'))
        self.assertIn('aria-labelledby="serviceWatchTitle"', html)
        self.assertIn(">사이트 모음</span>", html)
        self.assertIn("서비스 상태 · Codex 사용량 · 이슈 체크 · AI 소식", html)
        self.assertNotIn("운영·연구 사이트 모음", html)
        self.assertNotIn("상태 · CODEX 사용량 · 연구·AI", html)
        self.assertNotIn("서비스 상태 · Codex 사용량 · 연구·AI 소식", html)
        self.assertNotIn("SERVICE STATUS · RESET · RESEARCH SIGNALS", html)
        self.assertNotIn("AI 상태 · 연구 소식", html)
        self.assertIn('class="events-section" aria-labelledby="recentActivityTitle"', html)
        self.assertIn('class="events-table" aria-labelledby="recentActivityTitle" aria-describedby="eventsMeta"', html)
        self.assertIn('aria-labelledby="officialServiceWatchTitle"', html)
        self.assertIn('aria-labelledby="resetServiceWatchTitle"', html)
        self.assertIn('aria-labelledby="researchServiceWatchTitle"', html)
        self.assertIn('aria-labelledby="newsServiceWatchTitle"', html)
        self.assertEqual(html.count('class="service-watch-badge official"'), 2)
        self.assertNotIn('class="service-watch-badge external"', html)
        self.assertNotIn(">외부<", html)
        self.assertIn("CodexRunway", html)
        self.assertNotIn("Codex Reset Monitor", html)
        self.assertNotIn("https://codexreset.org/", html)
        self.assertIn("Codex Resets", html)
        self.assertIn("r/MachineLearning", html)
        self.assertIn("@choi.openai", html)
        self.assertIn(">전체 순위 보기 <span", html)
        self.assertIn(">평가 방법 보기 <span", html)
        self.assertNotIn(">원본 보기 <span", html)
        self.assertNotIn(">원문 보기 <span", html)
        self.assertNotIn(">방법론 <span", html)
        self.assertNotIn("관리되는 연구실 LAN에서만 입력하세요.", html)
        self.assertNotIn("service-watch-disclaimer", html)
        self.assertIn("Codex를 포함한 OpenAI 서비스 상태", html)
        self.assertIn("Claude Code를 포함한 Claude 서비스 상태", html)
        self.assertIn("Codex 리셋 현황·이력과 사용량 확인용 macOS 메뉴 막대 앱", html)
        self.assertIn("사용량 한도 초기화 후 경과 시간 및 초기화 이력", html)
        self.assertIn("학회 제출 사이트 이슈 체크", html)
        self.assertIn("학회 제출 사이트 장애 등 이슈 발생 시 참고", html)
        self.assertIn("최신 AI 뉴스 및 주요 업데이트 소식통", html)
        self.assertLess(html.index('href="https://www.codexrunway.com/"'), html.index('href="https://codex-resets.com/"'))
        for stale_label in (
            "Will Codex Reset?",
            "Did Codex Reset Today?",
            "48H reset 전망 점수",
            "오늘의 reset 판정",
            "공유 Codex quota reset 상태와 근거·타임라인을 확인합니다",
            "최근 Codex reset 시점과 발표 이력을 확인합니다",
            "학회 제출 사이트 장애·마감 관련 제보를 확인합니다",
            "Codex reset·커뮤니티·소식 링크는 참고용이며 OpenAI 또는 학회의 공식 상태 페이지가 아닙니다.",
            "Codex reset 현황은 물론 Juice Check와 Capability Watch 정보도 함께 살펴봅니다",
            "최근 Codex reset 시점과 관련 발표를 한곳에서 살펴봅니다",
            "학회 제출 사이트 장애 등 문제가 생겼을 때 최신 제보를 참고합니다",
            "최신 AI 소식과 주요 업데이트를 확인합니다",
            "이 영역의 외부 링크는 참고용입니다. 공식 안내와 서비스 상태는 OpenAI 및 각 학회 페이지에서 확인해 주세요.",
        ):
            self.assertNotIn(stale_label, html)
        self.assertNotIn('href="https://www.reddit.com/r/MachineLearning/"', html)
        for stale_url in ("https://www.willcodexquotareset.com/", "https://hascodexratelimitreset.today/"):
            self.assertNotIn(stale_url, html)
        icon_sources = {
            "/brand-icons/openai-status.png?v=__GPU_WATCH_ASSET_VERSION__": 1,
            "/brand-icons/claude-status.png?v=__GPU_WATCH_ASSET_VERSION__": 1,
            "/brand-icons/codex.png?v=__GPU_WATCH_ASSET_VERSION__": 2,
        }
        for icon_source, expected_count in icon_sources.items():
            self.assertEqual(html.count(f'src="{icon_source}"'), expected_count)
        icon_hashes = {
            "openai-status.png": "b74126d9daf80d754d752e8eaf765ce14c587c4ce56d97ef13c3fa46786e12c9",
            "claude-status.png": "a93ab579dc5d89f0dae532f30fea16391de405b6404ab0dc0b98d7bc7dfce83e",
            "codex.png": "fc3f9b64d0d58c284a2cd6180cc4f72708bf6540de6aa4297194c55308c55f05",
        }
        for icon_name, expected_hash in icon_hashes.items():
            icon = (ROOT / "static" / "brand-icons" / icon_name).read_bytes()
            self.assertTrue(icon.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertLess(len(icon), 64 * 1024)
            self.assertEqual(hashlib.sha256(icon).hexdigest(), expected_hash)
        self.assertEqual(html.count('class="service-watch-link brand-openai"'), 1)
        self.assertEqual(html.count('class="service-watch-link brand-claude"'), 1)
        self.assertEqual(html.count('class="service-watch-link brand-codex"'), 2)
        self.assertEqual(html.count('class="service-watch-link brand-reddit"'), 1)
        self.assertEqual(html.count('class="service-watch-link brand-threads"'), 1)
        self.assertNotIn("service-watch-icon-shell", html)
        self.assertEqual(html.count('class="service-watch-icon-tile'), 3)
        self.assertIn('class="service-watch-icon-tile claude"', html)
        self.assertIn('class="service-watch-icon-tile reddit"', html)
        self.assertIn('class="service-watch-icon-tile threads"', html)
        self.assertEqual(html.count('class="service-watch-reddit-icon"'), 1)
        self.assertIn('class="service-watch-reddit-icon" viewBox="0 0 24 24" focusable="false"', html)
        self.assertEqual(html.count('class="service-watch-threads-icon"'), 1)
        self.assertIn('class="service-watch-threads-icon" viewBox="0 0 512 512"', html)
        self.assertNotIn('<rect width="512" height="512"', html)
        self.assertNotIn("service-watch-mark", html)
        for url in service_urls:
            with self.subTest(service_url=url):
                self.assertEqual(html.count(f'href="{url}"'), 1)
                link = re.search(rf'<a\b[^>]*href="{re.escape(url)}"[^>]*>', html)
                self.assertIsNotNone(link)
                tag = link.group(0)
                self.assertIn('target="_blank"', tag)
                self.assertIn('rel="noopener noreferrer"', tag)
                self.assertIn('새 탭에서 열기', tag)
                self.assertNotIn(url, app)
        self.assertNotIn("<iframe", html.lower())
        self.assertNotRegex(html, r'<(?:script|img)\b[^>]+src="https?://')
        self.assertRegex(
            styles,
            r"\.service-watch-link\s*\{[^}]*min-height:\s*52px;",
        )
        brand_colors = {
            "openai": ("215, 217, 220", "#d7d9dc"),
            "claude": ("217, 119, 87", "#d97757"),
            "codex": ("112, 144, 240", "#718af5"),
            "reddit": ("255, 69, 0", "#ff4500"),
            "threads": ("0, 0, 0", "#656a70"),
        }
        for brand, (rgb, accent) in brand_colors.items():
            base_block = re.search(rf"\.service-watch-link\.brand-{brand}\s*\{{([^}}]*)\}}", styles)
            self.assertIsNotNone(base_block)
            self.assertIn(f"--service-brand-rgb: {rgb};", base_block.group(1))
            self.assertIn(f"--service-brand-accent: {accent};", base_block.group(1))
        self.assertRegex(
            styles,
            r"\.service-watch-link\s*\{[^}]*background:\s*rgba\(var\(--service-brand-rgb\),\s*0\.12\);",
        )
        self.assertIn("background: rgba(var(--service-brand-rgb), 0.12);", styles)
        self.assertIn("background: rgba(var(--service-brand-rgb), 0.20);", styles)
        threads_block = re.search(r"\.service-watch-link\.brand-threads\s*\{([^}]*)\}", styles)
        threads_hover = re.search(r"\.service-watch-link\.brand-threads:hover\s*\{([^}]*)\}", styles)
        self.assertIn("background: rgba(0, 0, 0, 0.28);", threads_block.group(1))
        self.assertIn("background: rgba(0, 0, 0, 0.40);", threads_hover.group(1))
        self.assertRegex(styles, r"\.service-watch-brand-icon\s*\{[^}]*width:\s*18px;[^}]*height:\s*18px;")
        self.assertNotIn(".service-watch-icon-shell", styles)
        self.assertRegex(styles, r"\.service-watch-icon-tile\s*\{[^}]*width:\s*18px;[^}]*height:\s*18px;[^}]*background:\s*#f4f4f2;")
        self.assertRegex(styles, r"\.service-watch-icon-tile\.reddit\s*\{[^}]*color:\s*#ff4500;")
        self.assertRegex(styles, r"\.service-watch-reddit-icon\s*\{[^}]*width:\s*14px;[^}]*height:\s*14px;")
        self.assertRegex(styles, r"\.service-watch-threads-icon\s*\{[^}]*width:\s*14px;[^}]*height:\s*14px;")
        self.assertRegex(styles, r"\.service-watch-link:focus-visible\s*\{[^}]*outline-offset:\s*-3px;")
        self.assertIn('class="process" role="note" tabindex="0" data-process-key=', app)
        self.assertIn('data-details="${esc(details)}"', app)
        self.assertRegex(styles, r"\.process:focus-visible\s*\{[^}]*outline:\s*2px solid")
        self.assertRegex(styles, r"\.process:focus::after\s*\{[^}]*content:\s*attr\(data-details\)")
        self.assertRegex(
            styles,
            r"\.deadline-widget > a:focus-visible,\s*\.deadline-edit-button:focus-visible\s*\{[^}]*outline-offset:\s*-3px;",
        )
        render_hosts = app[app.index("function renderHosts"):app.index("function eventLabel")]
        self.assertIn("focusedProcessKey", render_hosts)
        self.assertIn("data-process-key", render_hosts)
        self.assertIn("focusWithoutScroll(", render_hosts)
        responsive_css = styles[styles.index("@media (max-width: 760px)"):]
        self.assertRegex(
            responsive_css,
            r"\.service-watch-grid\s*\{[^}]*grid-template-columns:\s*1fr;",
        )
        self.assertNotIn("STATIC LINKS · NO BACKGROUND FETCH", html)
        self.assertNotIn("service-watch-static", styles)
        self.assertNotIn("window.prompt", app)
        self.assertNotRegex(app, r"\bp\.cmd\b")
        self.assertIn("function invalidateEvents()", app)
        self.assertIn("generation === eventsGeneration", app)
        self.assertIn("function invalidateInsights()", app)
        self.assertIn("daily-trend-grid-line", app)
        self.assertIn("daily-trend-final-point", app)
        self.assertIn("daily-trend-latest-point", app)
        self.assertIn("trend[index]?.complete", app)
        self.assertIn("p.command_summary || p.process_name", app)
        self.assertIn('const statusText = gpu.busy ? "busy" : "free";', app)
        self.assertIn('["free", "전체 free"]', app)
        self.assertIn("recent ${gpu.last_used_ago", app)
        self.assertNotIn("last use ${gpu.last_used_ago", app)
        self.assertIn('coveragePercent >= 100 ? `${windowText}일` : coverage', app)
        self.assertIn("관측 ${esc(coverageLabel)} / ${esc(windowText)}일", app)
        self.assertIn("sampled ${esc(shortTime(host.disk_updated_at))}", app)
        self.assertNotIn("Sampled ${esc(shortTime(host.disk_updated_at))}", app)
        self.assertNotIn("Updated ${esc(shortTime(host.disk_updated_at))}", app)
        self.assertIn(">Usage</span>", app)
        self.assertNotIn('Usage${isPartial ? " · 일부 미집계" : ""}', app)
        self.assertIn("보존된 free/busy · DOWN/UP 기록", html)
        self.assertIn("보존된 free/busy · DOWN/UP 기록", app)
        self.assertIn("Docker 용량 집계 시간이 초과되어", app)
        self.assertNotIn('esc(other.join(" · "))', app)
        self.assertLess(app.index("${renderDiskWarnings(errors)}"), app.index('<div class="user-section-title">'))
        self.assertIn("focusWithoutScroll(", app[app.index("function renderHosts"):app.index("function eventLabel")])
        self.assertRegex(styles, r"\.host-stamp strong,\s*\.host-stamp span\s*\{[^}]*white-space:\s*nowrap;")
        host_stamp_block = re.search(
            r"\.host-stamp\s*\{\s*flex:\s*0 0 auto;([^}]*)\}",
            styles,
        )
        self.assertIsNotNone(host_stamp_block)
        self.assertNotIn("max-width:", host_stamp_block.group(1))
        self.assertNotIn("text-overflow: ellipsis", styles[styles.index(".host-stamp"):styles.index(".host-stamp strong {")])
        self.assertNotIn("전체 여유", app)
        self.assertNotIn("free ${gpu.free_for", app)
        self.assertIn("processes = (gpu.processes || []).slice().sort", app)
        self.assertIn("function gpuAvailable(gpu)", app)
        self.assertIn('class="gpu-line unavailable"', app)
        self.assertIn(".gpu-line.unavailable", styles)
        self.assertIn('filterName === "down"', app)
        self.assertIn("if (!hostReady(host))", app)
        self.assertIn('<details class="process-more"', app)
        self.assertIn('data-process-group=', app)
        self.assertIn('hostGrid.addEventListener("toggle"', app)
        self.assertIn("동시 사용자는 GPU 점유시간을 사용자 수로 균등 분할합니다.", app)
        self.assertIn("cold_min_session_seconds: 60", app)
        self.assertNotIn("cold_min_observation_fraction", app)
        self.assertIn("meaningful_active_seconds", app)
        self.assertNotIn("observed_gpu_percent", app)
        self.assertIn("tracking_span_seconds", app)
        self.assertIn("trackingSpanSeconds >= coldDays * 86400", app)
        self.assertNotIn("observedLongEnough", app)
        activity_function = app[app.index("function hostActivityEffect"):app.index("function activityStateMeta")]
        self.assertLess(
            activity_function.index("if (allFree && noRecentUse)"),
            activity_function.index("if (Number.isFinite(capacityIndex)"),
        )
        self.assertIn('const coverageLabel = coveragePercent >= 100 ? `${windowText}일` : coverage;', app)
        self.assertNotIn("GB·h", app)
        self.assertIn("전일 평균 대비", app)
        self.assertIn("총 VRAM", app)
        self.assertIn("async function fetchJsonWithTimeout", app)
        self.assertIn("focus({ preventScroll: true })", app)
        self.assertIn('document.addEventListener("visibilitychange"', app)
        self.assertIn('document.addEventListener("keydown"', app)
        self.assertIn('event.key !== "Escape"', app)
        self.assertIn('aria-labelledby="noticeDialogTitle"', html)
        self.assertNotIn('autocomplete="current-password"', html)
        self.assertNotIn('autocomplete="new-password"', html)
        for form_id in ("noticeForm", "deadlineForm", "noticeDeleteForm"):
            form = re.search(rf'<form\b[^>]*id="{form_id}"[^>]*>', html)
            self.assertIsNotNone(form)
            self.assertIn('autocomplete="off"', form.group(0))
        notice_input = re.search(r'<input id="noticePinInput"[^>]+>', html).group(0)
        delete_input = re.search(r'<input id="noticeDeletePinInput"[^>]+>', html).group(0)
        deadline_input = re.search(r'<input id="deadlinePinInput"[^>]+>', html).group(0)
        for password_input in (notice_input, delete_input, deadline_input):
            self.assertIn('autocomplete="off"', password_input)
        for password_input in (notice_input, delete_input):
            self.assertIn('minlength="4"', password_input)
            self.assertIn('maxlength="64"', password_input)
            self.assertNotIn('pattern="[0-9]{4}"', password_input)
            self.assertNotIn('inputmode="numeric"', password_input)
        deadline_url_input = re.search(r'<input id="deadlineUrlInput"[^>]+>', html).group(0)
        self.assertIn('autocomplete="url"', deadline_url_input)
        self.assertIn('pattern="[0-9]{4}"', deadline_input)
        self.assertIn('maxlength="4"', deadline_input)
        self.assertEqual(html.count('pattern="[0-9]{4}"'), 1)
        self.assertIn('id="deadlineWidgets"', html)
        self.assertIn('<div class="deadline-empty">등록된 타이머 없음</div>', html)
        self.assertNotIn('data-deadline-id="primary"', html)
        self.assertIn('id="deadlineAddButton"', html)
        self.assertIn('id="deadlineDeleteButton"', html)
        self.assertIn("const MAX_DEADLINES = 6;", app)
        self.assertIn("let currentDeadlines = [];", app)
        self.assertIn(
            'const DEADLINE_TONE_ORDER = ["silver", "gold", "emerald", "diamond", "master", "grandmaster"];',
            app,
        )
        self.assertNotIn("DEADLINE_CRITICAL_WINDOW_MS", app)
        self.assertNotIn("DEADLINE_WARNING_WINDOW_MS", app)
        self.assertNotIn("🚨", app)
        self.assertNotIn("⚠️", app)
        self.assertNotIn("deadlineIsUrgent", app)
        self.assertNotIn("deadline-urgent", app)
        self.assertIn('return `${days}일 ${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;', app)
        self.assertIn("Number.POSITIVE_INFINITY", app)
        self.assertIn("function renderDeadlines(force = false)", app)
        self.assertIn("function deleteDeadline()", app)
        self.assertIn('method: "DELETE"', app[app.index("async function deleteDeadline"):])
        self.assertIn(".deadline-widget.tone-silver", styles)
        self.assertIn(".deadline-widget.tone-gold", styles)
        self.assertIn(".deadline-widget.tone-emerald", styles)
        self.assertIn(".deadline-widget.tone-diamond", styles)
        self.assertIn(".deadline-widget.tone-master", styles)
        self.assertIn(".deadline-widget.tone-grandmaster", styles)
        self.assertNotIn("@keyframes deadline-urgent-ember", styles)
        self.assertNotIn(".deadline-widget.deadline-urgent", styles)
        self.assertIn('grid-template-columns: repeat(var(--deadline-columns), minmax(220px, 280px));', styles)
        self.assertIn("animation: deadline-glow 4.6s ease-in-out infinite;", styles)
        self.assertIn("@keyframes deadline-glow", styles)
        mobile_styles = styles[styles.index("@media (max-width: 760px)"):]
        mobile_deadline = re.search(r"\.deadline-widget\s*\{([^}]*)\}", mobile_styles)
        self.assertIsNotNone(mobile_deadline)
        self.assertIn("flex: 0 0 auto;", mobile_deadline.group(1))
        self.assertIn('data-notice-edit=', app)
        self.assertIn("async function submitAnnouncement", app)
        self.assertIn("pendingNoticeEditId", app)
        self.assertIn("공지 수정", app)
        self.assertLess(app.index("data-notice-edit="), app.index("data-notice-delete="))
        notice_expiry = re.search(r'<input id="noticeExpiresInput"[^>]+>', html).group(0)
        self.assertNotIn("required", notice_expiry)
        self.assertIn('max="9999-12-31T23:59"', notice_expiry)
        deadline_at = re.search(r'<input id="deadlineAtInput"[^>]+>', html).group(0)
        self.assertIn('max="9999-12-31T23:59"', deadline_at)
        self.assertIn("비워 두면 만료 없이 게시됩니다.", html)
        self.assertIn('noticeExpiresInput.value = "";', app)
        self.assertIn('noticeExpiresInput.max = "9999-12-31T23:59";', app)
        self.assertIn('[...String(deadline?.title || "Deadline").trim()].slice(0, 64).join("")', app)
        self.assertIn('"Segoe UI Emoji"', styles)
        self.assertIn("function regionalFlagCode(flag)", app)
        self.assertIn("function deadlineTitleHtml(value)", app)
        self.assertIn('/[\\u{1F1E6}-\\u{1F1FF}]{2}/gu', app)
        self.assertIn('${deadlineTitleHtml(deadline.title)}</span>', app)
        self.assertIn('src="/flag-icons/${code}.svg"', app)
        self.assertIn('alt="" aria-hidden="true"', app)
        self.assertIn(".deadline-flag-icon", styles)
        flag_directory = ROOT / "static" / "flag-icons"
        flag_assets = sorted(flag_directory.glob("*.svg"))
        self.assertEqual(len(flag_assets), 259)
        self.assertTrue((flag_directory / "us.svg").is_file())
        self.assertTrue((flag_directory / "kr.svg").is_file())
        self.assertTrue((flag_directory / "LICENSE-GRAPHICS").is_file())
        code_block = re.search(
            r"const LOCAL_FLAG_REGION_CODES = new Set\(`(.*?)`\.trim\(\)\.split\(/\\s\+/\)\);",
            app,
            re.DOTALL,
        )
        self.assertIsNotNone(code_block)
        self.assertEqual(set(code_block.group(1).split()), {asset.stem for asset in flag_assets})
        unsafe_svg = re.compile(
            r"<script|javascript:|<foreignobject|(?:href|xlink:href)\s*=|\bon\w+\s*=|url\(",
            re.IGNORECASE,
        )
        for flag_asset in flag_assets:
            svg = flag_asset.read_text(encoding="utf-8")
            self.assertTrue(svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"'))
            self.assertIsNone(unsafe_svg.search(svg), flag_asset.name)
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("Twemoji", notices)
        self.assertIn("CC-BY-4.0", notices)
        self.assertIn("b6b55fef1e8636b540a6d016a4729ca8cdf2e60b", notices)
        self.assertNotIn('noticeDialog.addEventListener("click"', app)
        self.assertNotIn('noticeDeleteDialog.addEventListener("click"', app)
        self.assertNotIn('deadlineDialog.addEventListener("click"', app)
        self.assertIn('const expiryText = permanent', app)
        self.assertNotIn("공지 작성과 삭제는 관리자 passphrase로만", html)
        self.assertNotRegex(styles, r"(?:input|select|textarea):focus[^}]*outline:\s*none")
        self.assertNotIn('id="motionToggleButton"', html)
        self.assertNotIn("motionToggleButton", app)
        self.assertNotIn("gpu-watch-motion", app)
        self.assertNotIn('data-motion="reduced"', styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)
        self.assertIn("animation: none !important", styles)
        self.assertIn("transition: none !important", styles)
        self.assertIn('animation: eye-blink 6.8s ease-in-out infinite;', styles)
        self.assertIn('window.matchMedia?.("(prefers-reduced-motion: reduce)")', app)
        self.assertIn('behavior: reducedMotion ? "auto" : "smooth"', app)
        scroll_top_base = re.search(r"\.scroll-top-button\s*\{([^}]*)\}", styles)
        scroll_top_visible = re.search(r"\.scroll-top-button\.visible\s*\{([^}]*)\}", styles)
        self.assertIsNotNone(scroll_top_base)
        self.assertIsNotNone(scroll_top_visible)
        self.assertIn("visibility: hidden;", scroll_top_base.group(1))
        self.assertIn("visibility: visible;", scroll_top_visible.group(1))
        self.assertIn("function noticeActionButtonFor", app)
        self.assertIn("const focusedAction", app)
        self.assertIn("closeNoticeDeleteDialog(restoreFocus = true)", app)
        self.assertIn("function focusWithoutScroll", app)
        self.assertIn('p.user || "사용자 미상"', app)
        self.assertNotIn('p.user || "?"', app)
        self.assertIn("--font-nano: 11px", styles)
        self.assertIn("--font-micro: 12px", styles)
        self.assertNotRegex(styles, r"font-size:\s*(?:9|10)px")
        self.assertIn(
            "const reportableTotal = users.reduce((sum, user) => sum + Number(user.seconds || 0), 0);",
            app,
        )
        self.assertIn("seconds / reportableTotal", app)
        self.assertNotIn("seconds / total", app)
        filtered_load = app[
            app.index("function scheduleFilteredLoad"):app.index("function renderSnapshot")
        ]
        self.assertIn("window.setTimeout(() => loadEvents(true), delay)", filtered_load)
        self.assertNotIn("window.setTimeout(load, delay)", filtered_load)
        pagination = app[
            app.index('prevEventsButton.addEventListener("click"'):app.index(
                'document.addEventListener("visibilitychange"'
            )
        ]
        self.assertEqual(pagination.count("loadEvents(true);"), 2)
        self.assertNotIn("load();", pagination)
        self.assertNotIn("min-width: 320px", styles)
        topbar = re.search(r"\.topbar\s*\{([^}]*)\}", styles)
        self.assertIsNotNone(topbar)
        self.assertIn("position: relative;", topbar.group(1))
        self.assertNotIn("position: sticky;", topbar.group(1))
        self.assertRegex(styles, r"#refreshButton\s*\{[^}]*width:\s*98px;[^}]*min-width:\s*98px;")
        self.assertIn("const viewportScrollY = window.scrollY;", app)
        self.assertIn('window.scrollTo({ left: viewportScrollX, top: viewportScrollY, behavior: "auto" });', app)
        for dead_selector in (".legend-dot", ".state-pill.info", ".tab-note"):
            self.assertNotIn(dead_selector, styles)
        self.assertNotIn("--violet", styles)
        mobile_css = styles[styles.index("@media (max-width: 460px)"):]
        self.assertRegex(mobile_css, r"#updatedAt\s*\{[^}]*min-width:\s*0;[^}]*text-overflow:\s*ellipsis;")

    def test_intelligence_index_frontend_contract(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertLess(html.index('id="aiServiceWatch"'), html.index('id="intelligenceIndex"'))
        self.assertLess(html.index('id="intelligenceIndex"'), html.index('<footer class="site-footer">'))
        self.assertIn('aria-labelledby="intelligenceIndexTitle"', html)
        self.assertIn('id="intelligenceIndexMessage" role="status" aria-live="polite"', html)
        self.assertIn('id="intelligenceIndexTopModels" role="list"', html)
        self.assertIn('id="intelligenceIndexMore" hidden', html)
        self.assertIn("AI 모델 벤치마크 · 일일 갱신", html)
        self.assertIn("Artificial Analysis Intelligence Index</h2>", html)
        self.assertNotIn("Artificial Analysis Intelligence Index: Score", html)
        self.assertIn("11–29위 모델 보기", html)
        source_url = "https://artificialanalysis.ai/evaluations/artificial-analysis-intelligence-index"
        methodology_url = "https://artificialanalysis.ai/methodology/intelligence-benchmarking"
        for url in (source_url, methodology_url):
            self.assertEqual(html.count(f'href="{url}"'), 1)
            link = re.search(rf'<a\b[^>]*href="{re.escape(url)}"[^>]*>', html)
            self.assertIsNotNone(link)
            self.assertIn('target="_blank"', link.group(0))
            self.assertIn('rel="noopener noreferrer"', link.group(0))
            self.assertIn("새 탭에서 열기", link.group(0))
        self.assertIn('fetchJsonWithTimeout("/api/intelligence-index", { cache: "no-store" })', app)
        self.assertIn("const INTELLIGENCE_INDEX_REFRESH_MS = 60 * 60 * 1000;", app)
        self.assertIn("window.setInterval(loadIntelligenceIndex, INTELLIGENCE_INDEX_REFRESH_MS);", app)
        self.assertIn("const INTELLIGENCE_INDEX_BOOTSTRAP_RETRY_MS = 30 * 1000;", app)
        self.assertIn("let intelligenceIndexBootstrapRetryScheduled = false;", app)
        self.assertIn("payload?.status === \"unavailable\"", app)
        self.assertIn("&& !intelligenceIndexBootstrapRetryScheduled", app)
        self.assertIn("intelligenceIndexBootstrapRetryScheduled = true;", app)
        self.assertEqual(
            app.count("window.setTimeout(loadIntelligenceIndex, INTELLIGENCE_INDEX_BOOTSTRAP_RETRY_MS);"),
            1,
        )
        self.assertEqual(app.count("loadIntelligenceIndex();"), 1)
        refresh_handler = app[app.index('refreshButton.addEventListener("click"'):app.index('eventDateInput.addEventListener')]
        self.assertNotIn("loadIntelligenceIndex", refresh_handler)
        self.assertIn('renderIntelligenceIndex({ status: "unavailable", models: [] })', app)
        self.assertIn("현재 표시할 점수 데이터가 없습니다.", app)
        self.assertIn("새 데이터가 지연되어 이전 점수를 표시합니다.", app)
        self.assertIn("새 점수를 불러오지 못해 이전 점수를 표시합니다.", app)
        intelligence_renderer = app[
            app.index("function renderIntelligenceIndex"):
            app.index("async function loadIntelligenceIndex")
        ]
        self.assertNotIn("마지막 정상", intelligence_renderer)
        self.assertNotIn("최신 캐시", intelligence_renderer)
        self.assertIn("function intelligenceIndexVersion(value)", app)
        self.assertIn('return `v${version.replace(/^v\\s*/i, "")}`;', app)
        self.assertIn("`${version} · 100점 만점", app)
        self.assertIn("function intelligenceModelDisplayName(name)", app)
        self.assertIn("(?:Adaptive )?Reasoning", app)
        self.assertIn("|Max|Ultra", app)
        self.assertIn("esc(displayName)", app)
        self.assertIn("title=\"${esc(model.name)}\"", app)
        self.assertIn("esc(provider)", app)
        self.assertIn("intelligenceProviderRules", app)
        self.assertIn("function renderIntelligenceModelList(list, models)", app)
        self.assertIn("const rowCount = Math.max(1, Math.ceil(models.length / 2));", app)
        self.assertIn('list.style.setProperty("--intelligence-grid-rows", String(rowCount));', app)
        self.assertIn("renderIntelligenceModelList(intelligenceIndexTopModels, topModels);", app)
        self.assertIn("renderIntelligenceModelList(intelligenceIndexExtraModels, extraModels);", app)
        self.assertNotRegex(app, r"style=\\?\"[^\"]*(?:model\.provider|model\.color)")
        palette = {
            "anthropic": "#cc785c",
            "openai": "#1f1f1f",
            "kimi": "#047afe",
            "xai": "#736cd3",
            "meta": "#0089f4",
            "google": "#34a853",
            "alibaba": "#ff7018",
            "minimax": "#eb3568",
            "deepseek": "#2243e6",
            "xiaomi": "#ff6900",
            "nvidia": "#86b737",
            "mistral": "#fd6f00",
            "mbzuai": "#1521a9",
            "thinking-machines": "#676767",
            "cohere": "#d18ee2",
            "zai": "#1c7ff8",
            "fallback": "#748091",
        }
        for provider, color in palette.items():
            selector = re.search(rf"\.aa-provider-{provider}\s*\{{([^}}]*)\}}", styles)
            self.assertIsNotNone(selector)
            self.assertIn(f"--aa-color: {color};", selector.group(1))
        self.assertRegex(styles, r"\.intelligence-model\s*\{[^}]*grid-template-columns:")
        model_list_css = re.search(r"\.intelligence-model-list\s*\{([^}]*)\}", styles)
        self.assertIsNotNone(model_list_css)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", model_list_css.group(1))
        self.assertIn("grid-template-rows: repeat(var(--intelligence-grid-rows, 1), auto);", model_list_css.group(1))
        self.assertIn("grid-auto-flow: column;", model_list_css.group(1))
        self.assertRegex(styles, r"\.intelligence-model-bar\s*\{[^}]*height:\s*10px;")
        self.assertRegex(styles, r"\.intelligence-index-more summary\s*\{[^}]*min-height:\s*44px;")
        self.assertRegex(styles, r"\.intelligence-index-more summary:focus-visible\s*\{[^}]*outline:\s*2px solid")
        self.assertRegex(
            styles,
            r"\.intelligence-index-source,\s*\.intelligence-index-attribution a\s*\{[^}]*min-height:\s*24px;",
        )
        mobile_css = styles[styles.index("@media (max-width: 460px)"):]
        self.assertRegex(mobile_css, r"\.intelligence-model\s*\{[^}]*grid-template-columns:\s*22px minmax\(0, 1fr\) 36px;")
        tablet_css = styles[styles.index("@media (max-width: 760px)"):styles.index("@media (max-width: 460px)")]
        mobile_model_list_css = re.search(r"\.intelligence-model-list\s*\{([^}]*)\}", tablet_css)
        self.assertIsNotNone(mobile_model_list_css)
        self.assertIn("grid-template-columns: 1fr;", mobile_model_list_css.group(1))
        self.assertIn("grid-template-rows: none;", mobile_model_list_css.group(1))
        self.assertIn("grid-auto-flow: row;", mobile_model_list_css.group(1))

    def test_emergency_fallback_is_guarded_and_local(self):
        wrapper = (ROOT / "emergency-local-fallback.ps1").read_text(encoding="utf-8")
        fallback = (ROOT / "scripts" / "emergency-local-fallback.ps1").read_text(encoding="utf-8")
        runner = (ROOT / "run-dashboard.ps1").read_text(encoding="utf-8")
        askpass = (ROOT / "scripts" / "ssh-askpass.cmd").read_text(encoding="utf-8")
        hosts = (ROOT / "hosts.json").read_text(encoding="utf-8")
        self.assertIn('scripts\\emergency-local-fallback.ps1', wrapper)
        self.assertIn('$global:LASTEXITCODE = 0', wrapper)
        self.assertIn('exit $exitCode', wrapper)
        self.assertIn('$ProductionSnapshotUrl = "http://192.0.2.10:8787/api/snapshot"', fallback)
        self.assertIn('$productionSnapshot = Get-ProductionSnapshot', fallback)
        self.assertIn('$productionBuildVersion -ne $ExpectedBuildVersion', fallback)
        self.assertIn('Synchronize the emergency copy before takeover.', fallback)
        self.assertIn('if (-not $Force)', fallback)
        self.assertNotIn('function Test-HttpOk', fallback)
        self.assertIn('function Get-ProductionSnapshot', fallback)
        self.assertIn('ProductionBuildVersion = $productionBuildVersion', fallback)
        self.assertIn('LocalBuildVersion = $ExpectedBuildVersion', fallback)
        self.assertIn('VersionMatches = if ($productionSnapshot)', fallback)
        self.assertIn('Test-GpuWatchListener', fallback)
        self.assertIn('TCP/$Port is already used by a non-GPU-Watch process.', fallback)
        self.assertIn('$LabRemoteAddress = "192.0.2.0/24"', fallback)
        self.assertIn('Remove-NlpPasswordFile', fallback)
        self.assertIn('Invoke-FirewallOperation', fallback)
        self.assertIn('Only the firewall helper may run elevated.', fallback)
        self.assertIn('Clear-SensitiveProcessEnvironment', fallback)
        self.assertIn('icacls.exe', fallback)
        for source in (fallback, runner):
            self.assertIn('$VersionPath = Join-Path $Root "VERSION"', source)
        self.assertIn('$ExpectedBuildVersion = $null', fallback)
        self.assertIn('function Initialize-ReleaseMetadata', fallback)
        self.assertIn('$buildVersion = (Get-Content -Raw -LiteralPath $VersionPath', fallback)
        self.assertIn('"Stop" { Stop-Fallback }', fallback)
        self.assertNotIn('$ExpectedBuildVersion = (Get-Content -Raw -LiteralPath $VersionPath', fallback)
        self.assertIn('$ExpectedBuildVersion = (Get-Content -Raw -LiteralPath $VersionPath', runner)
        self.assertIn('$snapshot.build_version -ne $ExpectedBuildVersion', fallback)
        self.assertIn('$snapshot.build_version -eq $ExpectedBuildVersion', runner)
        self.assertIn('127.0.0.0/8,::1/128,192.0.2.0/24', runner)
        self.assertIn('$env:GPU_WATCH_BUILD_VERSION = $ExpectedBuildVersion', runner)
        self.assertIn('Direct local launch is disabled. Use emergency-local-fallback.ps1.', runner)
        self.assertIn('$env:GPU_WATCH_RUNTIME_MODE = "emergency"', runner)
        self.assertIn('GPU_WATCH_EMERGENCY_LAUNCH_TOKEN', fallback)
        self.assertIn('$env:PYTHONDONTWRITEBYTECODE = "1"', runner)
        self.assertIn('Test-GpuWatchListener', runner)
        self.assertIn('TCP/$Port is already used by a non-GPU-Watch process.', runner)
        self.assertIn('GPU Watch must run as a standard user.', runner)
        self.assertIn('Clear-SensitiveProcessEnvironment', runner)
        self.assertNotIn('SetEnvironmentVariable("GPU_WATCH_NLP_PASSWORD"', fallback)
        self.assertNotIn('%GPU_WATCH_SSH_PASSWORD%', askpass)
        self.assertIn("GetEnvironmentVariable('GPU_WATCH_SSH_PASSWORD', 'Process')", askpass)
        self.assertNotIn('ssh_password_env', hosts)

    def test_maintenance_complexity_guardrails(self):
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        limits = {
            "validate_config": 8,
            "Collector.probe_host": 18,
            "Collector._probe_host_once": 20,
            "Store.insights": 38,
            "Store.snapshot": 38,
            "Store.update_gpu": 50,
        }
        for qualified_name, maximum in limits.items():
            with self.subTest(function=qualified_name):
                self.assertLessEqual(branch_score(source, qualified_name), maximum)

    def test_release_identity_contract(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        package = (ROOT / "gpu_watch" / "__init__.py").read_text(encoding="utf-8")
        deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(version, "2.6")
        self.assertIn('__version__ = "2.6"', package)
        self.assertIn('__release_model__ = "GPT-6 Sol Max (Daybreak Blue)"', package)
        self.assertIn('__release_date__ = "2026-09-23"', package)
        for filename in ("Dockerfile", "Dockerfile.caddy"):
            dockerfile = (ROOT / filename).read_text(encoding="utf-8")
            self.assertIn("ARG BUILD_VERSION=2.6", dockerfile)
            self.assertIn('org.opencontainers.image.version="$BUILD_VERSION"', dockerfile.replace('${BUILD_VERSION}', '$BUILD_VERSION'))
        self.assertIn("# GPU Watch Dashboard v2.6", readme)
        self.assertIn("export PYTHONDONTWRITEBYTECODE=1", deploy)
        self.assertIn('VERSION_FILE="$ROOT/VERSION"', deploy)
        self.assertIn('BUILD_VERSION=$RELEASE_VERSION', deploy)
        self.assertIn('GPU_WATCH_BUILD_VERSION must match VERSION', deploy)
        self.assertIn('destination.execute("pragma foreign_key_check")', deploy)
        self.assertIn('2>/dev/null && [ -e "$path" ]', deploy)
        self.assertNotIn('BUILD_VERSION=${GPU_WATCH_BUILD_VERSION:-$RELEASE_VERSION}', deploy)

    def test_octans_and_pictor_use_the_standard_direct_nvidia_path(self):
        config = json.loads((ROOT / "hosts.json").read_text(encoding="utf-8"))
        hosts = {host["name"]: host for host in config["hosts"]}
        for name, expected_host in {
            "octans": "192.0.2.22",
            "pictor": "192.0.2.23",
        }.items():
            with self.subTest(host=name):
                self.assertEqual(hosts[name]["ssh_host"], expected_host)
                self.assertEqual(hosts[name]["ssh_port"], 22)
                self.assertNotIn("nvidia_smi_docker_container", hosts[name])
        self.assertNotIn("docker_gpu_process_scan_limit", config)
        self.assertNotIn("docker_gpu_process_scan_budget_seconds", config)
        self.assertTrue(all(
            "nvidia_smi_docker_container" not in host
            for host in config["hosts"]
        ))
        self.assertNotIn("temporary_driver_fallback", hosts["grus"])
        server_source = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("nvidia_smi_docker_container", server_source)

    def test_every_host_has_a_display_ip_without_exposing_ssh_aliases(self):
        config = server.load_config()
        addresses = {host["name"]: server.host_ip_for_display(host) for host in config["hosts"]}
        self.assertEqual(len(addresses), 22)
        for name, address in addresses.items():
            with self.subTest(host=name):
                self.assertIsNotNone(address)
                self.assertEqual(str(ipaddress.ip_address(address)), address)
        self.assertEqual(addresses["atlas"], "192.0.2.11")
        self.assertEqual(addresses["lab21"], "198.51.100.21")
        self.assertEqual(addresses["lab28"], "198.51.100.28")
        local_ssh_config = (ROOT / "scripts" / "local-ssh-config").read_text(encoding="utf-8")
        for host in config["hosts"]:
            if host["lab"] == "nlp" and "display_ip" in host:
                with self.subTest(ssh_alias=host["name"]):
                    self.assertIn(
                        f"Host gpuwatch-local-{host['name']}\n  HostName {host['display_ip']}\n",
                        local_ssh_config,
                    )
        self.assertIsNone(server.host_ip_for_display({"ssh_host": "gpuwatch-lab21"}))
        with self.assertRaisesRegex(ValueError, "invalid display IP"):
            server._validate_host(
                {"name": "bad", "lab": "nlp", "expected_gpu_count": 1, "display_ip": "not-an-ip"},
                {"nlp"},
            )

    def test_nll_host_order_and_norma_badge_contract(self):
        config = json.loads((ROOT / "hosts.json").read_text(encoding="utf-8"))
        nll_hosts = [host for host in config["hosts"] if host["lab"] == "nll"]
        nll_names = [host["name"] for host in nll_hosts]
        self.assertEqual(nll_names.index("fornax"), nll_names.index("eridanus") + 1)
        norma = next(host for host in nll_hosts if host["name"] == "norma")
        self.assertEqual(norma["owner"], "피지컬 AI 2")
        self.assertEqual(norma["owner_type"], "physical_ai_2")
        self.assertEqual(norma["location"], "Room 305")
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('host.owner_type === "physical_ai_2"', app)
        self.assertIn('type = "physical-ai-2"', app)
        self.assertIn(".state-pill.owner-badge.physical-ai-2", styles)

    def test_restore_stops_before_snapshot_and_has_failure_rollback(self):
        restore = (ROOT / "scripts" / "restore-backup.sh").read_text(encoding="utf-8")
        self.assertIn("rollback_restore()", restore)
        self.assertIn("trap finish EXIT", restore)
        self.assertIn("trap 'exit 143' TERM", restore)
        self.assertIn("source.backup(destination)", restore)
        self.assertGreaterEqual(restore.count("pragma foreign_key_check"), 2)
        main_restore = restore.index('docker container inspect "$CONTAINER"')
        self.assertLess(
            restore.index('docker stop -t 15 "$CONTAINER"', main_restore),
            restore.index("source.backup(destination)", main_restore),
        )
        self.assertIn('if [ "$status" -ne 0 ]', restore)

    def test_source_tree_has_no_legacy_gpustat_wrapper(self):
        self.assertFalse((ROOT / "scripts" / "gpustat-watch-wrapper-body.py").exists())

    def test_caddy_security_dependency_overrides_are_pinned(self):
        edge_image = (ROOT / "Dockerfile.caddy").read_text(encoding="utf-8")
        self.assertIn(
            "github.com/caddyserver/caddy/v2/cmd/caddy@b2693fb63a30e6d7be0972c3645e9a2c0a500e93",
            edge_image,
        )
        self.assertIn("github.com/go-chi/chi/v5@v5.3.1", edge_image)
        self.assertIn("github.com/google/cel-go@v0.30.0", edge_image)
        self.assertIn("github.com/klauspost/compress@v1.19.0", edge_image)
        self.assertIn("go.opentelemetry.io/otel@v1.44.0", edge_image)
        self.assertIn("golang.org/x/net@v0.58.0", edge_image)
        self.assertIn("golang.org/x/text@v0.41.0", edge_image)
        self.assertIn("google.golang.org/grpc@v1.83.2", edge_image)
        self.assertNotIn("@latest", edge_image)


if __name__ == "__main__":
    unittest.main()
