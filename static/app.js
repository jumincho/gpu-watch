const summaryGrid = document.getElementById("summaryGrid");
const hostGrid = document.getElementById("hostGrid");
const labSwitcher = document.getElementById("labSwitcher");
const eventsBody = document.getElementById("eventsBody");
const statusLine = document.getElementById("statusLine");
const updatedAt = document.getElementById("updatedAt");
const refreshButton = document.getElementById("refreshButton");
const eventsMeta = document.getElementById("eventsMeta");
const prevEventsButton = document.getElementById("prevEventsButton");
const nextEventsButton = document.getElementById("nextEventsButton");
const eventDateInput = document.getElementById("eventDateInput");
const eventServerSelect = document.getElementById("eventServerSelect");
const eventUserInput = document.getElementById("eventUserInput");
const eventAvailabilityInput = document.getElementById("eventAvailabilityInput");
const resetEventFiltersButton = document.getElementById("resetEventFiltersButton");
const noticeMeta = document.getElementById("noticeMeta");
const noticeItems = document.getElementById("noticeItems");
const noticeOpenButton = document.getElementById("noticeOpenButton");
const noticeDialog = document.getElementById("noticeDialog");
const noticeForm = document.getElementById("noticeForm");
const noticeDialogTitle = document.getElementById("noticeDialogTitle");
const noticeCancelButton = document.getElementById("noticeCancelButton");
const noticeAuthorInput = document.getElementById("noticeAuthorInput");
const noticeExpiresInput = document.getElementById("noticeExpiresInput");
const noticeMessageInput = document.getElementById("noticeMessageInput");
const noticePinInput = document.getElementById("noticePinInput");
const noticePinHelp = document.getElementById("noticePinHelp");
const noticeFormMessage = document.getElementById("noticeFormMessage");
const noticeSaveButton = document.getElementById("noticeSaveButton");
const noticeDeleteDialog = document.getElementById("noticeDeleteDialog");
const noticeDeleteForm = document.getElementById("noticeDeleteForm");
const noticeDeleteCancelButton = document.getElementById("noticeDeleteCancelButton");
const noticeDeletePinInput = document.getElementById("noticeDeletePinInput");
const noticeDeleteMessage = document.getElementById("noticeDeleteMessage");
const noticeDeleteSubmitButton = document.getElementById("noticeDeleteSubmitButton");
const scrollTopButton = document.getElementById("scrollTopButton");
const activityLegend = document.getElementById("activityLegend");
const hostFilters = document.getElementById("hostFilters");
const staleBanner = document.getElementById("staleBanner");
const labPulse = document.getElementById("labPulse");
const deadlineWidgets = document.getElementById("deadlineWidgets");
const deadlineAddButton = document.getElementById("deadlineAddButton");
const deadlineDialog = document.getElementById("deadlineDialog");
const deadlineForm = document.getElementById("deadlineForm");
const deadlineDialogTitle = document.getElementById("deadlineDialogTitle");
const deadlineCancelButton = document.getElementById("deadlineCancelButton");
const deadlineTitleInput = document.getElementById("deadlineTitleInput");
const deadlineAtInput = document.getElementById("deadlineAtInput");
const deadlineAtLabel = document.getElementById("deadlineAtLabel");
const deadlineTbaInput = document.getElementById("deadlineTbaInput");
const deadlineTbaTextInput = document.getElementById("deadlineTbaTextInput");
const deadlineTbaTextLabel = document.getElementById("deadlineTbaTextLabel");
const deadlineUrlInput = document.getElementById("deadlineUrlInput");
const deadlinePinInput = document.getElementById("deadlinePinInput");
const deadlineFormMessage = document.getElementById("deadlineFormMessage");
const deadlineDeleteButton = document.getElementById("deadlineDeleteButton");
const deadlineSaveButton = document.getElementById("deadlineSaveButton");
const intelligenceIndex = document.getElementById("intelligenceIndex");
const intelligenceIndexStatus = document.getElementById("intelligenceIndexStatus");
const intelligenceIndexMeta = document.getElementById("intelligenceIndexMeta");
const intelligenceIndexMessage = document.getElementById("intelligenceIndexMessage");
const intelligenceIndexTopModels = document.getElementById("intelligenceIndexTopModels");
const intelligenceIndexMore = document.getElementById("intelligenceIndexMore");
const intelligenceIndexMoreSummary = document.getElementById("intelligenceIndexMoreSummary");
const intelligenceIndexExtraModels = document.getElementById("intelligenceIndexExtraModels");

const activeTabs = new Map();
let activeLab = null;
let pollMs = 10000;
let timer = null;
let filterTimer = null;
let lastSnapshot = null;
let eventLimit = 50;
let eventOffset = 0;
let eventMeta = { next_offset: null, prev_offset: null };
let activeHostFilter = "all";
let lastSuccessfulLoadAt = 0;
let lastCollectorCompletedAt = 0;
let loadInFlight = false;
let loadQueued = false;
let eventsLoadedAt = 0;
let eventsInFlight = false;
let eventsGeneration = 0;
let lastEventsQuery = "";
let pendingNoticeDeleteId = null;
let pendingNoticeEditId = null;
let currentAnnouncements = [];
let insightsData = null;
let insightsLoadedAt = 0;
let insightsInFlight = false;
let insightsGeneration = 0;
let intelligenceIndexInFlight = false;
let intelligenceIndexData = null;
let intelligenceIndexBootstrapRetryScheduled = false;
let currentDeadlines = [];
let deadlineRenderSignature = "";
let pendingDeadlineEditId = null;
let deadlineReturnId = null;

const eventFilters = { date: "", host: "", user: "", includeAvailability: false };
eventAvailabilityInput.checked = false;
const recentUsageColors = ["#22d3ee", "#60a5fa", "#a78bfa", "#34d399", "#fbbf24", "#f472b6"];
const defaultActivityPolicy = {
  hot_server_busy_fraction: 0.5,
  cold_idle_days: 7,
  cold_min_session_seconds: 60,
};
const expandedProcessGroups = new Set();
const INTELLIGENCE_INDEX_REFRESH_MS = 5 * 60 * 1000;
const INTELLIGENCE_INDEX_BOOTSTRAP_RETRY_MS = 30 * 1000;
const MAX_DEADLINES = 6;
const DEADLINE_TONE_ORDER = ["silver", "gold", "emerald", "diamond", "master", "grandmaster"];
const DEADLINE_TONES = new Set(DEADLINE_TONE_ORDER);
const LOCAL_FLAG_REGION_CODES = new Set(`
ac ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bl bm bn bo bq br bs bt bv bw by bz
ca cc cd cf cg ch ci ck cl cm cn co cp cq cr cu cv cw cx cy cz de dg dj dk dm do dz ea ec ee eg eh er es et eu fi
fj fk fm fo fr ga gb gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu ic id ie il im in io iq
ir is it je jm jo jp ke kg kh ki km kn kp kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc md me mf mg mh mk ml
mm mn mo mp mq mr ms mt mu mv mw mx my mz na nc ne nf ng ni nl no np nr nu nz om pa pe pf pg ph pk pl pm pn pr
ps pt pw py qa re ro rs ru rw sa sb sc sd se sg sh si sj sk sl sm sn so sr ss st sv sx sy sz ta tc td tf tg th tj
tk tl tm tn to tr tt tv tw tz ua ug um un us uy uz va vc ve vg vi vn vu wf ws xk ye yt za zm zw
`.trim().split(/\s+/));
const intelligenceProviderRules = [
  { className: "anthropic", tokens: ["anthropic", "claude"] },
  { className: "openai", tokens: ["openai", "gpt", "chatgpt", "codex"] },
  { className: "kimi", tokens: ["moonshot", "kimi"] },
  { className: "xai", tokens: ["x.ai", "xai", "grok"] },
  { className: "meta", tokens: ["meta", "llama"] },
  { className: "google", tokens: ["google", "gemini"] },
  { className: "alibaba", tokens: ["alibaba", "qwen"] },
  { className: "minimax", tokens: ["minimax"] },
  { className: "deepseek", tokens: ["deepseek"] },
  { className: "xiaomi", tokens: ["xiaomi", "mimo"] },
  { className: "nvidia", tokens: ["nvidia", "nemotron"] },
  { className: "mistral", tokens: ["mistral", "ministral", "magistral"] },
  { className: "mbzuai", tokens: ["mbzuai", "institute of foundation models", "ifm"] },
  { className: "thinking-machines", tokens: ["thinking machines", "tinker"] },
  { className: "cohere", tokens: ["cohere", "command r"] },
  { className: "zai", tokens: ["z ai", "z.ai", "zhipu", "glm"] },
];

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function pct(value, total) {
  if (!Number.isFinite(value) || !Number.isFinite(total) || total <= 0) return 0;
  return clamp(Math.round((value / total) * 100), 0, 100);
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function regionalFlagCode(flag) {
  const symbols = [...String(flag ?? "")];
  if (symbols.length !== 2) return "";
  const codePoints = symbols.map((symbol) => symbol.codePointAt(0));
  if (codePoints.some((codePoint) => codePoint < 0x1f1e6 || codePoint > 0x1f1ff)) return "";
  return codePoints
    .map((codePoint) => String.fromCharCode(97 + codePoint - 0x1f1e6))
    .join("");
}

function deadlineTitleHtml(value) {
  const title = String(value ?? "");
  let html = "";
  let cursor = 0;
  for (const match of title.matchAll(/[\u{1F1E6}-\u{1F1FF}]{2}/gu)) {
    const code = regionalFlagCode(match[0]);
    html += esc(title.slice(cursor, match.index));
    html += LOCAL_FLAG_REGION_CODES.has(code)
      ? `<img class="deadline-flag-icon" src="/flag-icons/${code}.svg" alt="" aria-hidden="true" width="14" height="14" decoding="async" draggable="false">`
      : esc(match[0]);
    cursor = match.index + match[0].length;
  }
  return html + esc(title.slice(cursor));
}

function domIdPart(value) {
  return [...String(value ?? "")]
    .map((character) => character.codePointAt(0).toString(16))
    .join("-") || "empty";
}

async function fetchJsonWithTimeout(input, init = {}, timeoutMs = 12000) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(input, { ...init, signal: controller.signal });
    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      throw error;
    }
    return { response, payload };
  } catch (error) {
    if (error?.name === "AbortError") throw new Error("요청 시간이 초과되었습니다.");
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

function invalidateEvents() {
  eventsLoadedAt = 0;
  eventsGeneration += 1;
}

function invalidateInsights() {
  insightsLoadedAt = 0;
  insightsGeneration += 1;
}

function gpuDisplayName(name) {
  return String(name || "").replace(/^NVIDIA\s+/, "");
}

function shortTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function intelligenceProviderClass(model) {
  const haystack = `${model?.provider || ""} ${model?.name || ""}`.toLocaleLowerCase("en-US");
  return intelligenceProviderRules.find((rule) => rule.tokens.some((token) => haystack.includes(token)))?.className || "fallback";
}

function intelligenceScoreText(score) {
  return Number.isInteger(score) ? String(score) : score.toFixed(1);
}

function intelligenceIndexVersion(value) {
  const version = typeof value === "string" ? value.trim() : "";
  if (!version) return "버전 미확인";
  return `v${version.replace(/^v\s*/i, "")}`;
}

function intelligenceModelDisplayName(name) {
  const value = String(name || "").trim();
  const match = value.match(
    /^(.+?) \((?:(?:Adaptive )?Reasoning,\s*)?(Low|Medium|High|Xhigh|Max|Ultra) Effort(?:,\s*[^)]*)?\)$/i,
  );
  if (!match) return value;
  return `${match[1]} (${match[2].toLocaleLowerCase("en-US")})`;
}

function intelligenceModelRows(models) {
  return models.map((model) => {
    const width = clamp(model.score, 0, 100);
    const score = intelligenceScoreText(model.score);
    const provider = model.provider || "Provider unavailable";
    const colorClass = intelligenceProviderClass(model);
    const displayName = intelligenceModelDisplayName(model.name);
    const fullNameTitle = displayName === model.name ? "" : ` title="${esc(model.name)}"`;
    return `
      <div class="intelligence-model aa-provider-${colorClass}" role="listitem" aria-label="${esc(`${model.rank}위 ${model.name}, ${provider}, ${score}점`)}">
        <span class="intelligence-model-rank" aria-hidden="true">${model.rank}</span>
        <span class="intelligence-model-copy">
          <strong${fullNameTitle}>${esc(displayName)}</strong>
          <small>${esc(provider)}</small>
        </span>
        <span class="intelligence-model-bar" aria-hidden="true"><span style="width:${width.toFixed(2)}%"></span></span>
        <strong class="intelligence-model-score"><span class="sr-only">점수 </span>${score}</strong>
      </div>`;
  }).join("");
}

function renderIntelligenceModelList(list, models) {
  // Keep the ranked DOM order intact for assistive technology and the mobile
  // single-column layout. The desktop grid uses this row count to fill the
  // left column before continuing at the top of the right column.
  const rowCount = Math.max(1, Math.ceil(models.length / 2));
  list.style.setProperty("--intelligence-grid-rows", String(rowCount));
  list.innerHTML = intelligenceModelRows(models);
}

function normalizedIntelligenceModels(payload) {
  if (!Array.isArray(payload?.models)) return [];
  return payload.models
    .map((model, index) => ({
      rank: Number.isInteger(Number(model?.rank)) && Number(model.rank) > 0 ? Number(model.rank) : index + 1,
      name: typeof model?.name === "string" ? model.name.trim() : "",
      provider: typeof model?.provider === "string" ? model.provider.trim() : "",
      score: typeof model?.score === "number" ? model.score : Number.NaN,
    }))
    .filter((model) => model.name && Number.isFinite(model.score) && model.score >= 0 && model.score <= 100)
    .sort((left, right) => left.rank - right.rank || right.score - left.score)
    .slice(0, 29);
}

function renderIntelligenceIndex(payload) {
  const states = new Set(["fresh", "stale", "unavailable"]);
  const reportedState = states.has(payload?.status) ? payload.status : "unavailable";
  const models = normalizedIntelligenceModels(payload);
  const state = models.length ? reportedState : "unavailable";
  const topModels = models.slice(0, 10);
  const extraModels = models.slice(10, 29);
  const total = Number.isInteger(Number(payload?.total_models)) && Number(payload.total_models) >= models.length
    ? Number(payload.total_models)
    : models.length;
  const version = intelligenceIndexVersion(payload?.index_version);
  const lastSuccess = shortTime(payload?.last_success_at);
  const statusLabels = { fresh: "최신 데이터", stale: "갱신 지연", unavailable: "사용 불가" };

  intelligenceIndex.dataset.state = state;
  intelligenceIndexStatus.className = `intelligence-index-status ${state}`;
  intelligenceIndexStatus.textContent = statusLabels[state];
  intelligenceIndexMeta.textContent = `${version} · 100점 만점 · ${total}개 모델 중 상위 ${Math.min(10, models.length)}개 · 데이터 기준 ${lastSuccess}`;
  renderIntelligenceModelList(intelligenceIndexTopModels, topModels);
  renderIntelligenceModelList(intelligenceIndexExtraModels, extraModels);
  intelligenceIndexMore.hidden = extraModels.length === 0;
  intelligenceIndexMoreSummary.textContent = extraModels.length
    ? `11–${10 + extraModels.length}위 모델 보기`
    : "11–29위 모델 보기";

  if (!models.length) {
    intelligenceIndexMessage.hidden = false;
    intelligenceIndexMessage.textContent = "현재 표시할 점수 데이터가 없습니다.";
  } else if (state === "stale") {
    intelligenceIndexMessage.hidden = false;
    intelligenceIndexMessage.textContent = "새 데이터가 지연되어 이전 점수를 표시합니다.";
  } else if (state === "unavailable") {
    intelligenceIndexMessage.hidden = false;
    intelligenceIndexMessage.textContent = "새 점수를 불러오지 못해 이전 점수를 표시합니다.";
  } else {
    intelligenceIndexMessage.hidden = true;
    intelligenceIndexMessage.textContent = "";
  }
}

async function loadIntelligenceIndex() {
  if (intelligenceIndexInFlight) return;
  intelligenceIndexInFlight = true;
  intelligenceIndex.setAttribute("aria-busy", "true");
  try {
    const { response, payload } = await fetchJsonWithTimeout("/api/intelligence-index", { cache: "no-store" });
    if (!response.ok && !payload?.status) throw new Error(`intelligence index ${response.status}`);
    intelligenceIndexData = payload;
    renderIntelligenceIndex(payload);
    if (
      payload?.status === "unavailable"
      && normalizedIntelligenceModels(payload).length === 0
      && !intelligenceIndexBootstrapRetryScheduled
    ) {
      intelligenceIndexBootstrapRetryScheduled = true;
      window.setTimeout(loadIntelligenceIndex, INTELLIGENCE_INDEX_BOOTSTRAP_RETRY_MS);
    }
  } catch (error) {
    if (intelligenceIndexData) {
      renderIntelligenceIndex({ ...intelligenceIndexData, status: "stale" });
    } else {
      renderIntelligenceIndex({ status: "unavailable", models: [] });
    }
  } finally {
    intelligenceIndexInFlight = false;
    intelligenceIndex.removeAttribute("aria-busy");
  }
}

function bytes(value) {
  if (!Number.isFinite(value)) return "-";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let size = Math.max(0, value);
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const digits = size >= 100 || unit === 0 ? 0 : size >= 10 ? 1 : 2;
  return `${size.toFixed(digits)} ${units[unit]}`;
}

function durationText(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "만료";
  const total = Math.max(0, Math.round(seconds));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days) return hours ? `${days}일 ${hours}시간` : `${days}일`;
  if (hours) return minutes ? `${hours}시간 ${minutes}분` : `${hours}시간`;
  return `${Math.max(1, minutes)}분`;
}

function candlestickChart(hourly, width, height, padX = 10, padY = 10) {
  const finite = hourly.flatMap((item) => [item.open, item.high, item.low, item.close])
    .filter(Number.isFinite)
    .map(Number);
  if (!finite.length) return "";
  let minimum = Math.min(...finite);
  let maximum = Math.max(...finite);
  if (maximum === minimum) {
    const padding = Math.max(1, Math.abs(maximum) * 0.08);
    minimum -= padding;
    maximum += padding;
  } else {
    const padding = (maximum - minimum) * 0.12;
    minimum = Math.max(0, minimum - padding);
    maximum += padding;
  }
  const y = (value) => padY + ((height - padY * 2) * (1 - (Number(value) - minimum) / (maximum - minimum)));
  const slotWidth = (width - padX * 2) / Math.max(1, hourly.length);
  const bodyWidth = clamp(slotWidth * 0.78, 5, 18);
  const axis = [maximum, (maximum + minimum) / 2, minimum].map((value) => {
    const axisY = y(value);
    const label = Number(value).toLocaleString("en-US", {
      minimumFractionDigits: value < 100 ? 1 : 0,
      maximumFractionDigits: value < 100 ? 1 : 0,
    });
    return `
      <line class="pulse-grid-line" x1="${padX}" y1="${axisY}" x2="${width - padX}" y2="${axisY}"></line>
      <text class="pulse-axis-label" x="${padX - 6}" y="${axisY + 3}" text-anchor="end">${label}</text>
    `;
  }).join("");
  const candles = hourly.map((item, index) => {
    const rawValues = [item.open, item.high, item.low, item.close];
    if (!rawValues.every(Number.isFinite)) return "";
    const open = Number(item.open);
    const high = Number(item.high);
    const low = Number(item.low);
    const close = Number(item.close);
    const x = padX + slotWidth * (index + 0.5);
    const openY = y(open);
    const closeY = y(close);
    const highY = y(high);
    const lowY = y(low);
    const rising = close >= open;
    const color = rising ? "#ef5350" : "#4f8ee8";
    const rawHeight = Math.abs(closeY - openY);
    const bodyHeight = Math.max(1.8, rawHeight);
    const bodyY = rawHeight < 1.8 ? ((openY + closeY) / 2) - 0.9 : Math.min(openY, closeY);
    const title = `${item.label || "-"} · O ${indexText(open)} · H ${indexText(high)} · L ${indexText(low)} · C ${indexText(close)} GB`;
    return `
      <g class="pulse-candle ${rising ? "up" : "down"}">
        <title>${esc(title)}</title>
        <line class="pulse-candle-wick" x1="${x}" y1="${highY}" x2="${x}" y2="${lowY}" stroke="${color}"></line>
        <rect class="pulse-candle-body" x="${x - bodyWidth / 2}" y="${bodyY}" width="${bodyWidth}" height="${bodyHeight}" fill="${color}"></rect>
      </g>
    `;
  }).join("");
  return `<text class="pulse-axis-unit" x="2" y="9">GB</text>${axis}${candles}`;
}

function indexText(value) {
  if (!Number.isFinite(value)) return "-";
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function axisIndexText(value) {
  if (!Number.isFinite(value)) return "-";
  const magnitude = Math.abs(Number(value));
  const digits = magnitude >= 100 ? 0 : magnitude >= 10 ? 1 : 2;
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function renderDailyTrend(lab, tone) {
  const trend = lab.daily_trend || [];
  const values = trend.map((item) => Number.isFinite(item.index) ? Number(item.index) : null);
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return `<div class="daily-trend-empty">일일 인덱스 추이 수집 중</div>`;
  const width = 280;
  const height = 62;
  const padLeft = 34;
  const padRight = 4;
  const padY = 6;
  let minimum = Math.min(...finite);
  let maximum = Math.max(...finite);
  if (minimum === maximum) {
    const padding = Math.max(1, Math.abs(maximum) * 0.06);
    minimum = Math.max(0, minimum - padding);
    maximum += padding;
  } else {
    const padding = (maximum - minimum) * 0.12;
    minimum = Math.max(0, minimum - padding);
    maximum += padding;
  }
  const denominator = Math.max(1, values.length - 1);
  const points = values.map((value, index) => Number.isFinite(value) ? {
    x: padLeft + ((width - padLeft - padRight) * index / denominator),
    y: padY + ((height - padY * 2) * (1 - (Number(value) - minimum) / (maximum - minimum))),
    value: Number(value),
  } : null);
  const segments = [];
  let current = [];
  for (const point of points) {
    if (point) {
      current.push(point);
    } else if (current.length) {
      segments.push(current);
      current = [];
    }
  }
  if (current.length) segments.push(current);
  const trendTone = tone === "up" ? "up" : tone === "down" ? "down" : "neutral";
  const color = trendTone === "up" ? "#ef5350" : trendTone === "down" ? "#4f8ee8" : "#8c929b";
  const safeId = String(lab.id || "lab").replace(/[^a-zA-Z0-9_-]/g, "-");
  const gradientId = `daily-trend-${safeId}`;
  const glowId = `daily-trend-glow-${safeId}`;
  const axis = [maximum, (maximum + minimum) / 2, minimum].map((value) => {
    const axisY = padY + ((height - padY * 2) * (1 - (Number(value) - minimum) / (maximum - minimum)));
    return `
      <line class="daily-trend-grid-line" x1="${padLeft}" y1="${axisY}" x2="${width - padRight}" y2="${axisY}"></line>
      <text class="daily-trend-axis-label" x="${padLeft - 5}" y="${axisY + 2.5}" text-anchor="end">${axisIndexText(value)}</text>
    `;
  }).join("");
  const linePaths = segments.map((segment) => {
    const commands = segment.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ");
    return `<path class="daily-trend-line" d="${commands}" stroke="${color}"></path>`;
  }).join("");
  const areaPaths = segments.filter((segment) => segment.length > 1).map((segment) => {
    const commands = segment.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ");
    const first = segment[0];
    const last = segment[segment.length - 1];
    return `<path class="daily-trend-area" d="${commands} L${last.x.toFixed(2)} ${height - padY} L${first.x.toFixed(2)} ${height - padY} Z" fill="url(#${gradientId})"></path>`;
  }).join("");
  const completedPoints = points.map((point, index) => {
    if (!point || !trend[index]?.complete) return "";
    const item = trend[index];
    return `<circle class="daily-trend-final-point" cx="${point.x}" cy="${point.y}" r="1.7" fill="${color}"><title>${esc(`${item.label || item.day || "-"} · ${indexText(point.value)} GB · 확정`)}</title></circle>`;
  }).join("");
  let latestIndex = -1;
  for (let index = points.length - 1; index >= 0; index -= 1) {
    if (points[index]) {
      latestIndex = index;
      break;
    }
  }
  const latestPoint = latestIndex >= 0 ? points[latestIndex] : null;
  const firstLabel = trend[0]?.label || "";
  const lastLabel = trend[trend.length - 1]?.label || "";
  const latestItem = latestIndex >= 0 ? trend[latestIndex] : null;
  const latestObserved = Number(latestItem?.observed_hours || 0).toFixed(1);
  const latestState = latestItem?.complete ? "확정" : "진행 중";
  return `
    <div class="daily-trend ${trendTone}">
      <div class="daily-trend-head"><span>일일 평균 VRAM 추이</span><small>30D · 단위 GB</small></div>
      <svg class="daily-trend-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(`${lab.label} 최근 30일 일별 평균 실제 VRAM 추이, 세로축 GB, 확정일은 점으로 표시`)}">
        <defs>
          <linearGradient id="${gradientId}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity="0.24"></stop><stop offset="1" stop-color="${color}" stop-opacity="0"></stop></linearGradient>
          <filter id="${glowId}" x="-150%" y="-150%" width="400%" height="400%"><feGaussianBlur stdDeviation="2.2" result="blur"></feGaussianBlur><feFlood flood-color="${color}" flood-opacity="0.62" result="glow-color"></feFlood><feComposite in="glow-color" in2="blur" operator="in" result="soft-glow"></feComposite><feMerge><feMergeNode in="soft-glow"></feMergeNode><feMergeNode in="SourceGraphic"></feMergeNode></feMerge></filter>
        </defs>
        ${axis}${areaPaths}${linePaths}${completedPoints}
        ${latestPoint ? `<circle class="daily-trend-latest-point" cx="${latestPoint.x}" cy="${latestPoint.y}" r="2.5" fill="${color}" filter="url(#${glowId})"><title>${esc(`${latestItem?.label || lastLabel} · ${indexText(latestPoint.value)} GB · 관측 ${latestObserved}h · ${latestState}`)}</title></circle>` : ""}
      </svg>
      <div class="daily-trend-dates"><span>${esc(firstLabel)}</span><span>${esc(lastLabel)}</span></div>
    </div>
  `;
}

function formatKstInput(isoValue) {
  const date = new Date(isoValue);
  if (Number.isNaN(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const part = (type) => parts.find((item) => item.type === type)?.value || "";
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}

function limitDateTimeYear(input) {
  // Chrome allows an expanded year while editing despite max=9999.
  // Keep the native date picker and constrain the entered year immediately.
  const normalized = input.value.replace(/^(\d{4})\d+(-)/, "$1$2");
  if (normalized !== input.value) input.value = normalized;
}

function kstInputToIso(value) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return "";
  const date = new Date(`${value}:00+09:00`);
  if (!Number.isFinite(date.getTime()) || formatKstInput(date.toISOString()) !== value) return "";
  return date.toISOString();
}

function normalizedDeadlines(value) {
  if (!Array.isArray(value)) return [];
  return value.slice(0, MAX_DEADLINES).map((deadline, index) => {
    const id = /^[A-Za-z0-9_-]{1,32}$/.test(String(deadline?.id || ""))
      ? String(deadline.id)
      : `slot-${index + 1}`;
    const tone = DEADLINE_TONES.has(String(deadline?.tone || ""))
      ? String(deadline.tone)
      : DEADLINE_TONE_ORDER[index % DEADLINE_TONE_ORDER.length];
    const title = [...String(deadline?.title || "Deadline").trim()].slice(0, 64).join("") || "Deadline";
    const mode = deadline?.mode === "tba" ? "tba" : "scheduled";
    const tbaText = [...String(deadline?.tba_text || "").trim()].slice(0, 32).join("");
    const deadlineAt = mode === "tba" ? "" : String(deadline?.deadline_at || "");
    let url = "#";
    try {
      const parsed = new URL(String(deadline?.url || ""));
      if (["http:", "https:"].includes(parsed.protocol) && !parsed.username && !parsed.password) {
        url = parsed.href;
      }
    } catch (_error) {
      // Invalid links are made inert even if a malformed payload reaches the browser.
    }
    return { ...deadline, id, tone, title, mode, tba_text: tbaText, deadline_at: deadlineAt, url };
  }).sort((left, right) => {
    const leftTime = new Date(left.deadline_at).getTime();
    const rightTime = new Date(right.deadline_at).getTime();
    const safeLeft = Number.isFinite(leftTime) ? leftTime : Number.POSITIVE_INFINITY;
    const safeRight = Number.isFinite(rightTime) ? rightTime : Number.POSITIVE_INFINITY;
    // Array.prototype.sort is stable: a newly appended timer with the same
    // timestamp must remain behind timers that were already registered.
    return safeLeft - safeRight;
  });
}

function deadlineDateText(deadlineAt) {
  const target = new Date(deadlineAt);
  if (Number.isNaN(target.getTime())) return "시각 미설정";
  return `${new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(target)} KST`;
}

function deadlineCountdownText(deadlineAt) {
  const target = new Date(deadlineAt);
  if (Number.isNaN(target.getTime())) return "시각 미설정";
  const remaining = Math.max(0, target.getTime() - Date.now());
  if (remaining <= 0) return "마감됨";
  const totalSeconds = Math.floor(remaining / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (value) => String(value).padStart(2, "0");
  return `${days}일 ${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}

function deadlineButtonFor(id) {
  if (id === null || id === undefined) return null;
  return [...deadlineWidgets.querySelectorAll("[data-deadline-edit]")]
    .find((button) => button.dataset.deadlineEdit === String(id)) || null;
}

function updateDeadlineCountdowns() {
  for (const countdown of deadlineWidgets.querySelectorAll("[data-deadline-countdown]")) {
    const deadline = currentDeadlines.find(
      (item) => item.id === countdown.dataset.deadlineCountdown,
    );
    countdown.textContent = deadline?.mode === "tba"
      ? deadline.tba_text
      : deadline ? deadlineCountdownText(deadline.deadline_at) : "시각 미설정";
  }
}

function renderDeadlines(force = false) {
  currentDeadlines = normalizedDeadlines(currentDeadlines);
  deadlineWidgets.dataset.count = String(currentDeadlines.length);
  const signature = JSON.stringify(currentDeadlines.map((deadline) => [
    deadline.id,
    deadline.tone,
    deadline.title,
    deadline.deadline_at,
    deadline.mode,
    deadline.tba_text,
    deadline.url,
  ]));
  if (force || signature !== deadlineRenderSignature) {
    deadlineRenderSignature = signature;
    deadlineWidgets.innerHTML = currentDeadlines.length
      ? currentDeadlines.map((deadline) => `
        <article class="deadline-widget tone-${esc(deadline.tone)}" data-deadline-id="${esc(deadline.id)}">
          <a class="deadline-link" href="${esc(deadline.url)}" target="_blank" rel="noopener noreferrer" aria-label="${esc(`${deadline.title} 학회 페이지 열기 · 새 탭에서 열기`)}">
            <span class="deadline-title" title="${esc(deadline.title)}">${deadlineTitleHtml(deadline.title)}</span>
            <b class="deadline-countdown" data-deadline-countdown="${esc(deadline.id)}">계산 중</b>
            <small class="deadline-date">${esc(deadline.mode === "tba" ? "TBA" : deadlineDateText(deadline.deadline_at))}</small>
          </a>
          <button class="deadline-edit-button" type="button" data-deadline-edit="${esc(deadline.id)}" aria-label="${esc(`${deadline.title} 타이머 수정`)}" title="관리자 타이머 수정">✎</button>
        </article>
      `).join("")
      : `<div class="deadline-empty">등록된 타이머 없음</div>`;
  }
  deadlineAddButton.hidden = currentDeadlines.length >= MAX_DEADLINES;
  updateDeadlineCountdowns();
}

function updateDeadlineMode() {
  const tba = deadlineTbaInput.checked;
  deadlineAtLabel.hidden = tba;
  deadlineAtInput.disabled = tba;
  deadlineAtInput.required = !tba;
  deadlineTbaTextLabel.hidden = !tba;
  deadlineTbaTextInput.disabled = !tba;
  deadlineTbaTextInput.required = tba;
}

function openDeadlineDialog(id = null) {
  const deadline = currentDeadlines.find((item) => item.id === String(id)) || null;
  pendingDeadlineEditId = deadline?.id || null;
  deadlineReturnId = deadline?.id || null;
  deadlineForm.reset();
  deadlineDialogTitle.textContent = deadline ? "타이머 수정" : "타이머 추가";
  deadlineTitleInput.value = deadline?.title || "";
  deadlineTbaInput.checked = deadline?.mode === "tba";
  deadlineTbaTextInput.value = deadline?.tba_text || "";
  deadlineAtInput.value = deadline?.deadline_at ? formatKstInput(deadline.deadline_at) : "";
  updateDeadlineMode();
  deadlineUrlInput.value = deadline?.url === "#" ? "" : deadline?.url || "";
  deadlinePinInput.value = "";
  deadlineFormMessage.textContent = "";
  deadlineFormMessage.className = "form-message";
  deadlineDeleteButton.hidden = !deadline;
  deadlineDeleteButton.disabled = false;
  deadlineSaveButton.disabled = false;
  if (typeof deadlineDialog.showModal === "function") deadlineDialog.showModal();
  else deadlineDialog.setAttribute("open", "");
  deadlineTitleInput.focus();
}

function closeDeadlineDialog(restoreFocus = true) {
  const returnId = deadlineReturnId;
  pendingDeadlineEditId = null;
  deadlineReturnId = null;
  if (typeof deadlineDialog.close === "function") deadlineDialog.close();
  else deadlineDialog.removeAttribute("open");
  deadlineForm.reset();
  deadlineFormMessage.textContent = "";
  deadlineFormMessage.className = "form-message";
  if (restoreFocus) {
    window.setTimeout(() => {
      focusWithoutScroll(
        deadlineButtonFor(returnId)
        || (!deadlineAddButton.hidden ? deadlineAddButton : deadlineWidgets.querySelector("[data-deadline-edit]")),
      );
    }, 0);
  }
}

async function submitDeadline(event) {
  event.preventDefault();
  const tba = deadlineTbaInput.checked;
  const deadlineAt = tba ? null : kstInputToIso(deadlineAtInput.value);
  if (!tba && !deadlineAt) {
    deadlineFormMessage.textContent = "마감 시각을 다시 선택해 주세요.";
    return;
  }
  deadlineSaveButton.disabled = true;
  deadlineDeleteButton.disabled = true;
  deadlineFormMessage.textContent = "저장 중...";
  deadlineFormMessage.className = "form-message";
  try {
    const requestBody = {
      title: deadlineTitleInput.value,
      deadline_at: deadlineAt,
      mode: tba ? "tba" : "scheduled",
      tba_text: tba ? deadlineTbaTextInput.value : "",
      url: deadlineUrlInput.value,
      pin: deadlinePinInput.value,
    };
    if (pendingDeadlineEditId) requestBody.id = pendingDeadlineEditId;
    const { response, payload } = await fetchJsonWithTimeout("/api/deadline", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    if (!response.ok) throw new Error(payload.error || `save ${response.status}`);
    currentDeadlines = normalizedDeadlines(payload.deadlines);
    deadlineReturnId = payload.deadline?.id || pendingDeadlineEditId;
    renderDeadlines(true);
    closeDeadlineDialog();
  } catch (error) {
    deadlineFormMessage.textContent = String(error.message || error);
    deadlineFormMessage.classList.add("error");
  } finally {
    deadlineSaveButton.disabled = false;
    deadlineDeleteButton.disabled = false;
  }
}

async function deleteDeadline() {
  if (!pendingDeadlineEditId) return;
  if (!/^\d{4}$/.test(deadlinePinInput.value)) {
    deadlineFormMessage.textContent = "관리자 PIN 숫자 4자리를 입력해 주세요.";
    deadlineFormMessage.className = "form-message error";
    deadlinePinInput.focus();
    return;
  }
  const deletedId = pendingDeadlineEditId;
  deadlineSaveButton.disabled = true;
  deadlineDeleteButton.disabled = true;
  deadlineFormMessage.textContent = "삭제 중...";
  deadlineFormMessage.className = "form-message";
  try {
    const { response, payload } = await fetchJsonWithTimeout(
      `/api/deadline/${encodeURIComponent(deletedId)}`,
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin: deadlinePinInput.value }),
      },
    );
    if (!response.ok) throw new Error(payload.error || `delete ${response.status}`);
    currentDeadlines = normalizedDeadlines(payload.deadlines);
    deadlineReturnId = null;
    renderDeadlines(true);
    closeDeadlineDialog();
  } catch (error) {
    deadlineFormMessage.textContent = String(error.message || error);
    deadlineFormMessage.classList.add("error");
  } finally {
    deadlineSaveButton.disabled = false;
    deadlineDeleteButton.disabled = false;
  }
}

function setNoticeExpiryBounds() {
  noticeExpiresInput.removeAttribute("min");
  noticeExpiresInput.max = "9999-12-31T23:59";
  noticeExpiresInput.value = "";
}

function shareLabel(value, seconds = null, total = null) {
  let share = Number(value);
  if (Number.isFinite(Number(seconds)) && Number.isFinite(Number(total)) && Number(total) > 0) {
    share = (Number(seconds) / Number(total)) * 100;
  }
  if (!Number.isFinite(share)) return "-";
  if (share > 0 && share < 1) return "<1%";
  return `${Math.round(share)}%`;
}

function mib(value) {
  return Number.isFinite(value) ? value * 1024 * 1024 : 0;
}

function gbFromBytes(value) {
  if (!Number.isFinite(value)) return "0 GB";
  return `${Math.round(value / (1024 * 1024 * 1024))} GB`;
}

function flattenGpus(hosts) {
  return hosts.flatMap((host) => (host.gpus || []).map((gpu) => ({ host, gpu })));
}

function gpuAvailable(gpu) {
  return gpu?.available !== false;
}

function hostReady(host) {
  return Boolean(host.online) && (host.gpus || []).length > 0 && host.gpus.every(gpuAvailable);
}

function labForHost(host) {
  return host.lab || "default";
}

function labsFrom(data) {
  const hosts = data.hosts || [];
  const configured = (data.labs || []).map((lab) => ({
    id: lab.id || "default",
    label: lab.label || lab.id || "Servers",
    note: lab.note || "",
  }));
  const seen = new Set(configured.map((lab) => lab.id));
  for (const host of hosts) {
    const id = labForHost(host);
    if (!seen.has(id)) {
      configured.push({ id, label: id === "default" ? "Servers" : id, note: "" });
      seen.add(id);
    }
  }
  return configured.filter((lab) => hosts.some((host) => labForHost(host) === lab.id));
}

function ensureActiveLab(data) {
  const labs = labsFrom(data);
  const previousLab = activeLab;
  if (!labs.length) {
    activeLab = null;
    if (previousLab !== activeLab) invalidateEvents();
    return labs;
  }
  if (!activeLab || !labs.some((lab) => lab.id === activeLab)) {
    activeLab = labs[0].id;
  }
  if (previousLab !== activeLab) invalidateEvents();
  return labs;
}

function hostsForActiveLab(data) {
  const hosts = data.hosts || [];
  if (!activeLab) return hosts;
  return hosts.filter((host) => labForHost(host) === activeLab);
}

function scopedData(data) {
  return { ...data, hosts: hostsForActiveLab(data) };
}

function activityPolicy(data = lastSnapshot) {
  return { ...defaultActivityPolicy, ...(data?.activity_policy || {}) };
}

function renderActivityLegend(data) {
  const policy = activityPolicy(data);
  const hotPercent = Math.round(Number(policy.hot_server_busy_fraction || 0) * 100);
  const coldDays = Number(policy.cold_idle_days || 0);
  const coldMinimumSeconds = Number(policy.cold_min_session_seconds || 60);
  activityLegend.innerHTML = `
    <strong>상태</strong>
    <span>🔥 = 고사용 · 최근 7일 관측 GPU 슬롯 바쁨 지수 ${esc(hotPercent)}%+</span>
    <span>❄️ = 장기 유휴 · 최근 ${esc(coldDays)}일 ${esc(coldMinimumSeconds)}초 이상 연속 사용 없음</span>
    <span>⛔ = 연결 실패</span>
  `;
}

function hostMatchesFilter(host, filterName) {
  const gpus = host.gpus || [];
  if (filterName === "busy") return hostReady(host) && gpus.some((gpu) => gpuAvailable(gpu) && gpu.busy);
  if (filterName === "free") return hostReady(host) && gpus.filter(gpuAvailable).every((gpu) => !gpu.busy);
  if (filterName === "down") return !hostReady(host);
  if (filterName === "disk") return (host.reachable ?? host.online) && Boolean(storageWarningFor(host));
  return true;
}

function renderHostFilters(data) {
  const focusedFilter = hostFilters.contains(document.activeElement)
    ? document.activeElement?.dataset?.hostFilter
    : null;
  const definitions = [
    ["all", "전체"],
    ["busy", "사용 중"],
    ["free", "전체 free"],
    ["down", "연결 실패"],
    ["disk", "디스크 경고"],
  ];
  hostFilters.innerHTML = definitions
    .map(([name, label]) => {
      const count = (data.hosts || []).filter((host) => hostMatchesFilter(host, name)).length;
      return `<button type="button" data-host-filter="${name}" aria-pressed="${activeHostFilter === name}">${esc(label)} <strong>${count}</strong></button>`;
    })
    .join("");
  if (focusedFilter) {
    [...hostFilters.querySelectorAll("[data-host-filter]")]
      .find((button) => button.dataset.hostFilter === focusedFilter)
      ?.focus({ preventScroll: true });
  }
}

function renderLabSwitcher(data, labs) {
  const focusedLab = labSwitcher.contains(document.activeElement)
    ? document.activeElement?.dataset?.lab
    : null;
  labSwitcher.innerHTML = labs
    .map((lab) => {
      const hosts = (data.hosts || []).filter((host) => labForHost(host) === lab.id);
      const online = hosts.filter((host) => hostReady(host)).length;
      const gpus = flattenGpus(hosts);
      const busy = gpus.filter(({ host, gpu }) => hostReady(host) && gpuAvailable(gpu) && gpu.busy).length;
      const free = gpus.filter(({ host, gpu }) => hostReady(host) && gpuAvailable(gpu) && !gpu.busy).length;
      return `
        <button class="lab-tab ${activeLab === lab.id ? "active" : ""}" type="button" data-lab="${esc(lab.id)}" aria-pressed="${activeLab === lab.id}">
          <span>${esc(lab.label)}</span>
          <strong>${esc(online)}/${esc(hosts.length)} servers</strong>
          <em>${esc(gpus.length ? `${free}/${gpus.length} GPU free` : lab.note || "waiting")}</em>
        </button>
      `;
    })
    .join("");
  if (focusedLab) {
    [...labSwitcher.querySelectorAll("[data-lab]")]
      .find((button) => button.dataset.lab === focusedLab)
      ?.focus({ preventScroll: true });
  }
}

function tabFor(hostName) {
  return activeTabs.get(hostName) === "disk" ? "disk" : "gpu";
}

function renderSummary(data) {
  const all = flattenGpus(data.hosts);
  const online = data.hosts.filter((h) => hostReady(h)).length;
  const available = all.filter(({ host, gpu }) => hostReady(host) && gpuAvailable(gpu));
  const busy = available.filter(({ gpu }) => gpu.busy).length;
  const free = available.filter(({ gpu }) => !gpu.busy).length;
  const users = [...new Set(available.flatMap(({ gpu }) => (gpu.processes || []).map((p) => p.user).filter(Boolean)))];
  const vramTotal = all.reduce((sum, { gpu }) => sum + mib(gpu.memory_total), 0);
  const vramUsed = available.reduce((sum, { gpu }) => sum + mib(gpu.memory_used), 0);
  const vramFree = available.reduce(
    (sum, { gpu }) => sum + Math.max(0, mib(gpu.memory_total) - mib(gpu.memory_used)),
    0,
  );

  const items = [
    ["Servers", `${online}/${data.hosts.length}`, "online"],
    ["GPUs", `${all.length} / ${gbFromBytes(vramTotal)}`, "total"],
    ["Free", `${free} / ${gbFromBytes(vramFree)}`, "ready"],
    ["Busy", `${busy} / ${gbFromBytes(vramUsed)}`, users.length ? users.join(", ") : "idle"],
  ];

  summaryGrid.innerHTML = items
    .map(([label, value, note]) => `
      <div class="summary-item">
        <div class="summary-label">${esc(label)}</div>
        <div class="summary-value">${esc(value)}</div>
        <div class="summary-note">${esc(note)}</div>
      </div>
    `)
    .join("");
}

function renderLabPulse() {
  const lab = (insightsData?.labs || []).find((item) => item.id === activeLab);
  if (!lab) {
    labPulse.hidden = true;
    return;
  }
  labPulse.hidden = false;
  const hourly = lab.hourly || [];
  const width = 720;
  const height = 104;
  const padX = 52;
  const padY = 12;
  const candles = candlestickChart(hourly, width, height, padX, padY);
  const latestIndex = Number.isFinite(lab.daily_index) ? Number(lab.daily_index) : null;
  const previousIndex = lab.comparison_ready && Number.isFinite(lab.previous_daily_index)
    ? Number(lab.previous_daily_index)
    : null;
  const delta = latestIndex === null || previousIndex === null ? null : latestIndex - previousIndex;
  const percent = delta === null || previousIndex === 0 ? null : (delta / previousIndex) * 100;
  const tone = delta === null || Math.abs(delta) < 0.005 ? "neutral" : delta > 0 ? "up" : "down";
  const deltaText = delta === null
    ? "수집 중"
    : `${delta >= 0 ? "+" : ""}${indexText(delta)}${percent === null ? "" : ` (${percent >= 0 ? "+" : ""}${percent.toFixed(2)}%)`}`;
  const firstDate = hourly.find((item) => Number.isFinite(item.index))?.label || "";
  const lastDate = [...hourly].reverse().find((item) => Number.isFinite(item.index))?.label || "";
  labPulse.innerHTML = `
    <div class="pulse-price">
      <span class="eyebrow">1시간 단위 · 실제 VRAM · 최근 24시간 · ${esc(lab.label)}</span>
      <div class="pulse-symbol">${esc(lab.label)} DAILY INDEX</div>
      <div class="pulse-market-line">
        <div class="pulse-quote ${tone}"><strong>${indexText(latestIndex)}</strong><span>GB</span></div>
        <div class="pulse-change-block">
          <span>전일 평균 대비</span>
          <div class="pulse-change ${tone}">${esc(deltaText)}</div>
        </div>
      </div>
      <div class="pulse-capacity">총 VRAM ${indexText(Number(lab.max_index))} GB · 오늘 관측 ${esc(lab.observed_hours ?? 0)}/24h</div>
    </div>
    ${renderDailyTrend(lab, tone)}
    <div class="pulse-chart-wrap">
      <svg class="pulse-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(lab.label)} 최근 24개 1시간 실제 VRAM 사용량 캔들 차트">
        <line class="pulse-baseline" x1="${padX}" y1="${height / 2}" x2="${width - padX}" y2="${height / 2}"></line>
        ${candles}
      </svg>
      <div class="pulse-dates"><span>${esc(firstDate)}</span><span>${esc(lastDate)}</span></div>
    </div>
  `;
}

function focusWithoutScroll(element) {
  if (!element) return;
  try {
    element.focus({ preventScroll: true });
  } catch (_error) {
    element.focus();
  }
}

function noticeActionButtonFor(id, action) {
  if (id === null || id === undefined) return null;
  const dataKey = action === "edit" ? "noticeEdit" : "noticeDelete";
  return [...noticeItems.querySelectorAll(`[data-notice-${action}]`)]
    .find((button) => button.dataset[dataKey] === String(id)) || null;
}

function renderAnnouncements(announcements = []) {
  currentAnnouncements = Array.isArray(announcements) ? announcements.slice() : [];
  const focusedAction = noticeItems.contains(document.activeElement)
    ? {
      id: document.activeElement?.dataset?.noticeEdit || document.activeElement?.dataset?.noticeDelete,
      action: document.activeElement?.dataset?.noticeEdit ? "edit" : "delete",
    }
    : null;
  noticeMeta.textContent = announcements.length ? `${announcements.length}개 공지` : "공지 없음";
  if (!announcements.length) {
    noticeItems.innerHTML = `<div class="notice-empty">등록된 공지가 없습니다.</div>`;
    if (focusedAction?.id) focusWithoutScroll(noticeOpenButton);
    return;
  }
  noticeItems.innerHTML = announcements
    .map((notice) => {
      const permanent = notice.permanent === true || !notice.expires_at;
      const expiryText = permanent
        ? "기한 없음"
        : `${durationText(Number(notice.remaining_seconds || 0))} 남음`;
      const expiryTitle = permanent ? "만료 없음" : shortTime(notice.expires_at);
      const editLabel = `${notice.author || "작성자 미상"}의 공지 수정: ${notice.message || "내용 없음"}`;
      const deleteLabel = `${notice.author || "작성자 미상"}의 공지 삭제: ${notice.message || "내용 없음"}`;
      return `
        <article class="notice-item">
          <div class="notice-item-head">
            <strong>${esc(notice.author)}</strong>
            <span title="${esc(expiryTitle)}">${esc(expiryText)}</span>
          </div>
          <p>${esc(notice.message)}</p>
          <div class="notice-actions">
            <button class="notice-action notice-edit" type="button" data-notice-edit="${esc(notice.id)}" aria-label="${esc(editLabel)}">수정</button>
            <button class="notice-action notice-delete" type="button" data-notice-delete="${esc(notice.id)}" aria-label="${esc(deleteLabel)}">삭제</button>
          </div>
        </article>
      `;
    })
    .join("");
  if (focusedAction?.id) {
    focusWithoutScroll(
      noticeActionButtonFor(focusedAction.id, focusedAction.action) || noticeOpenButton,
    );
  }
}

function openNoticeDialog(id = null) {
  const notice = currentAnnouncements.find((item) => String(item.id) === String(id)) || null;
  pendingNoticeEditId = notice?.id || null;
  noticeForm.reset();
  setNoticeExpiryBounds();
  noticeDialogTitle.textContent = notice ? "공지 수정" : "공지 작성";
  noticeSaveButton.textContent = notice ? "수정" : "등록";
  noticePinHelp.textContent = notice
    ? "등록할 때 정한 공지 비밀번호 또는 관리자 PIN을 입력해 주세요."
    : "이 비밀번호로 본인이 등록한 공지를 수정·삭제할 수 있습니다. 관리자는 관리자 PIN으로 모든 공지를 관리할 수 있습니다.";
  if (notice) {
    noticeAuthorInput.value = notice.author || "";
    noticeExpiresInput.value = notice.expires_at ? formatKstInput(notice.expires_at) : "";
    noticeMessageInput.value = notice.message || "";
  }
  noticeFormMessage.textContent = "";
  noticeFormMessage.className = "form-message";
  noticeSaveButton.disabled = false;
  if (typeof noticeDialog.showModal === "function") {
    noticeDialog.showModal();
  } else {
    noticeDialog.setAttribute("open", "");
  }
  noticeAuthorInput.focus();
}

function closeNoticeDialog(restoreFocus = true) {
  const editId = pendingNoticeEditId;
  pendingNoticeEditId = null;
  noticePinInput.value = "";
  if (typeof noticeDialog.close === "function") {
    noticeDialog.close();
  } else {
    noticeDialog.removeAttribute("open");
  }
  if (restoreFocus) {
    focusWithoutScroll(
      editId ? noticeActionButtonFor(editId, "edit") || noticeOpenButton : noticeOpenButton,
    );
  }
}

async function submitAnnouncement(event) {
  event.preventDefault();
  noticeFormMessage.textContent = "";
  noticeFormMessage.className = "form-message";
  noticeSaveButton.disabled = true;
  const editId = pendingNoticeEditId;
  try {
    const endpoint = editId
      ? `/api/announcements/${encodeURIComponent(editId)}`
      : "/api/announcements";
    const { response: res, payload: data } = await fetchJsonWithTimeout(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        author: noticeAuthorInput.value,
        message: noticeMessageInput.value,
        expires_at: noticeExpiresInput.value,
        pin: noticePinInput.value,
      }),
    });
    if (!res.ok || !data.ok) {
      throw new Error(data.error || "공지 저장에 실패했습니다.");
    }
    closeNoticeDialog(false);
    await load();
    focusWithoutScroll(editId ? noticeActionButtonFor(editId, "edit") || noticeOpenButton : noticeOpenButton);
  } catch (err) {
    noticeFormMessage.textContent = String(err.message || err);
    noticeFormMessage.classList.add("error");
  } finally {
    noticeSaveButton.disabled = false;
  }
}

function openNoticeDeleteDialog(id) {
  pendingNoticeDeleteId = id;
  noticeDeleteForm.reset();
  noticeDeleteMessage.textContent = "";
  noticeDeleteMessage.className = "form-message";
  noticeDeleteSubmitButton.disabled = false;
  if (typeof noticeDeleteDialog.showModal === "function") noticeDeleteDialog.showModal();
  else noticeDeleteDialog.setAttribute("open", "");
  noticeDeletePinInput.focus();
}

function closeNoticeDeleteDialog(restoreFocus = true) {
  const deleteId = pendingNoticeDeleteId;
  pendingNoticeDeleteId = null;
  noticeDeletePinInput.value = "";
  if (typeof noticeDeleteDialog.close === "function") noticeDeleteDialog.close();
  else noticeDeleteDialog.removeAttribute("open");
  if (restoreFocus) {
    focusWithoutScroll(noticeActionButtonFor(deleteId, "delete") || noticeOpenButton);
  }
}

async function deleteAnnouncement(event) {
  event.preventDefault();
  if (!pendingNoticeDeleteId) return;
  noticeDeleteSubmitButton.disabled = true;
  noticeDeleteMessage.textContent = "";
  try {
    const { response: res, payload: data } = await fetchJsonWithTimeout(`/api/announcements/${encodeURIComponent(pendingNoticeDeleteId)}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin: noticeDeletePinInput.value }),
    });
    if (!res.ok || !data.ok) {
      throw new Error(data.error || "공지 삭제에 실패했습니다.");
    }
    closeNoticeDeleteDialog(false);
    invalidateEvents();
    await load();
    focusWithoutScroll(noticeOpenButton);
  } catch (err) {
    noticeDeleteMessage.textContent = String(err.message || err);
    noticeDeleteMessage.classList.add("error");
  } finally {
    noticeDeleteSubmitButton.disabled = false;
  }
}

function renderMeter(label, value, total, tone = "") {
  const width = pct(value, total);
  return `
    <div class="metric">
      <div class="metric-head">
        <span>${esc(label)}</span>
        <strong>${esc(Number.isFinite(value) ? value : 0)}${label === "Util" ? "%" : ""}</strong>
      </div>
      <div class="bar ${tone}"><span style="width:${width}%"></span></div>
    </div>
  `;
}

function renderGpu(gpu, hostName, ready = true) {
  const name = gpuDisplayName(gpu.name);
  if (!ready || !gpuAvailable(gpu)) {
    return `
      <div class="gpu-line unavailable" title="${esc(gpu.last_error || "연결 실패")}">
        <div class="gpu-id">
          <span class="gpu-num">GPU ${esc(gpu.index)}</span>
          <span class="state-pill bad">offline</span>
        </div>
        <div class="gpu-main">
          <div class="gpu-title"><span title="${esc(name)}">${esc(name)}</span><em>-°C</em></div>
          <div class="gpu-sub">연결 실패</div>
        </div>
        <div class="metric">
          <div class="metric-head"><span>Util</span><strong>-</strong></div>
          <div class="bar"><span></span></div>
        </div>
        <div class="metric">
          <div class="metric-head"><span>VRAM</span><strong>-</strong></div>
          <div class="bar mem"><span></span></div>
          <div class="metric-foot">- / - MiB</div>
        </div>
      </div>
    `;
  }
  const util = Number.isFinite(gpu.utilization) ? gpu.utilization : 0;
  const memUsed = Number.isFinite(gpu.memory_used) ? gpu.memory_used : 0;
  const memTotal = Number.isFinite(gpu.memory_total) ? gpu.memory_total : 0;
  const memPct = pct(memUsed, memTotal);
  const processes = (gpu.processes || []).slice().sort((left, right) => {
    const memoryDifference = Number(right.used_memory || 0) - Number(left.used_memory || 0);
    return memoryDifference || Number(left.pid || 0) - Number(right.pid || 0);
  });
  const users = [...new Set(processes.map((p) => p.user).filter(Boolean))];
  const statusText = gpu.busy ? "busy" : "free";
  const rowClass = gpu.busy ? "busy" : "free";
  const subtitle = gpu.busy
    ? `${users.length ? users.join(", ") : "사용자 미상"} · busy ${gpu.busy_for || "-"}`
    : gpu.last_used_at
      ? `available · recent ${gpu.last_used_ago || "-"} · ${gpu.last_user || "사용자 미상"}`
      : "available · no usage record";
  const processGroupKey = `${hostName}:${gpu.index}`;
  const hiddenProcessCount = Math.max(0, processes.length - 3);
  const processLines = processes.slice(0, 3).map((p) => {
    const command = p.command_summary || p.process_name || "process";
    const details = [
      command,
      `PID ${p.pid ?? "?"}`,
      `user ${p.user || "사용자 미상"}`,
      `${p.used_memory ?? "?"} MiB`,
      p.started ? `started ${p.started}` : "",
      p.container_name ? `container ${p.container_name}` : "",
    ].filter(Boolean).join(" · ");
    const processKey = `${hostName}:${gpu.index}:${p.pid ?? "unknown"}`;
    return `
      <div class="process" role="note" tabindex="0" data-process-key="${esc(processKey)}" aria-label="${esc(details)}" data-details="${esc(details)}" title="${esc(details)}">
        ${esc(p.user || "사용자 미상")} · ${esc(p.used_memory ?? "?")} MiB · ${esc(command)}
      </div>
    `;
  }).join("");
  const hiddenProcessLines = processes.slice(3).map((p) => {
    const command = p.command_summary || p.process_name || "process";
    const details = [
      command,
      `PID ${p.pid ?? "?"}`,
      `user ${p.user || "사용자 미상"}`,
      `${p.used_memory ?? "?"} MiB`,
      p.started ? `started ${p.started}` : "",
      p.container_name ? `container ${p.container_name}` : "",
    ].filter(Boolean).join(" · ");
    const processKey = `${hostName}:${gpu.index}:${p.pid ?? "unknown"}`;
    return `<div class="process" role="note" tabindex="0" data-process-key="${esc(processKey)}" aria-label="${esc(details)}" data-details="${esc(details)}" title="${esc(details)}">${esc(p.user || "사용자 미상")} · ${esc(p.used_memory ?? "?")} MiB · ${esc(command)}</div>`;
  }).join("");
  const moreProcesses = hiddenProcessCount
    ? `<details class="process-more" data-process-group="${esc(processGroupKey)}" ${expandedProcessGroups.has(processGroupKey) ? "open" : ""}>
        <summary data-process-summary="${esc(processGroupKey)}">+${hiddenProcessCount} more processes</summary>
        <div class="process-more-list">${hiddenProcessLines}</div>
      </details>`
    : "";

  return `
    <div class="gpu-line ${rowClass}">
      <div class="gpu-id">
        <span class="gpu-num">GPU ${esc(gpu.index)}</span>
        <span class="state-pill ${rowClass}">${statusText}</span>
      </div>
      <div class="gpu-main">
        <div class="gpu-title">
          <span title="${esc(name)}">${esc(name)}</span>
          <em>${esc(gpu.temperature ?? "-")}°C</em>
        </div>
        <div class="gpu-sub">${esc(subtitle)}</div>
        ${processLines ? `<div class="process-list">${processLines}${moreProcesses}</div>` : ""}
      </div>
      ${renderMeter("Util", util, 100)}
      <div class="metric">
        <div class="metric-head">
          <span>VRAM</span>
          <strong>${memPct}%</strong>
        </div>
        <div class="bar mem"><span style="width:${memPct}%"></span></div>
        <div class="metric-foot">${esc(memUsed)} / ${esc(memTotal)} MiB</div>
      </div>
    </div>
  `;
}

function renderGpuTab(host) {
  const gpus = host.gpus || [];
  if (!hostReady(host)) {
    return `<div class="empty">${esc(host.last_error || "연결 실패")}</div>`;
  }
  if (!gpus.length) {
    return `<div class="empty">GPU 정보를 기다리는 중입니다.</div>`;
  }
  return `<div class="gpu-list">${gpus.map((gpu) => renderGpu(gpu, host.name, hostReady(host))).join("")}</div>`;
}

function isCapacityFilesystem(fs) {
  const type = String(fs.type || "");
  const mount = String(fs.mount || "");
  const total = fs.total_bytes;
  if (!Number.isFinite(total) || total <= 0) return false;
  if (["squashfs", "tmpfs", "devtmpfs", "efivarfs", "vfat", "overlay", "aufs", "fuse.overlayfs"].includes(type)) return false;
  const systemRoots = ["/boot", "/sys", "/proc", "/run", "/dev", "/snap", "/var/lib/docker/overlay2"];
  if (systemRoots.some((root) => mount === root || mount.startsWith(`${root}/`))) return false;
  return true;
}

function storageTotals(filesystems) {
  const selected = filesystems.filter(isCapacityFilesystem);
  const source = selected.length ? selected : filesystems.filter((fs) => Number.isFinite(fs.total_bytes) && fs.total_bytes > 0);
  const seen = new Set();
  return source.reduce((acc, fs) => {
    const key = fs.filesystem || fs.mount;
    if (key && seen.has(key)) return acc;
    if (key) seen.add(key);
    const total = Number.isFinite(fs.total_bytes) ? fs.total_bytes : 0;
    const available = Number.isFinite(fs.available_bytes) ? fs.available_bytes : 0;
    const used = Number.isFinite(fs.used_bytes) ? fs.used_bytes : Math.max(0, total - available);
    acc.total += total;
    acc.available += available;
    acc.used += used;
    acc.usable += used + available;
    return acc;
  }, { total: 0, available: 0, used: 0, usable: 0 });
}

function storageUsedPercent(totals) {
  return pct(totals.used, totals.usable || totals.total);
}

function storageWarningFor(host) {
  const filesystems = (host.disk?.filesystems || []).filter(isCapacityFilesystem);
  if (!filesystems.length) return null;
  // A nearly full root must remain visible even when a separate data
  // filesystem has ample space. The summary still uses aggregate capacity.
  const usedPct = Math.max(...filesystems.map((fs) => storageUsedPercent(storageTotals([fs]))));
  return usedPct >= 90 ? { usedPct } : null;
}

function filesystemUsableBytes(fs) {
  const used = Number.isFinite(fs.used_bytes) ? fs.used_bytes : 0;
  const available = Number.isFinite(fs.available_bytes) ? fs.available_bytes : 0;
  return used + available;
}

function reservedBytes(totals) {
  return Math.max(0, (totals.total || 0) - (totals.usable || 0));
}

function renderDriveLine(host) {
  if (!(host.reachable ?? host.online)) return "";
  const filesystems = (host.disk?.filesystems || []).filter(isCapacityFilesystem);
  if (!filesystems.length) return "";
  const rows = filesystems
    .slice()
    .sort((a, b) => {
      if (a.mount === "/") return -1;
      if (b.mount === "/") return 1;
      return (b.total_bytes || 0) - (a.total_bytes || 0);
    })
    .slice(0, 4);
  const title = filesystems.map((fs) => {
    const usable = filesystemUsableBytes(fs);
    return `${fs.mount} ${bytes(fs.available_bytes)} free / ${bytes(usable)} usable`;
  }).join(" · ");
  return `
    <div class="drive-line" title="${esc(title)}">
      ${rows.map((fs) => `
        <span>
          <strong>${esc(fs.mount)}</strong>
          ${bytes(fs.available_bytes)} free / ${bytes(filesystemUsableBytes(fs))} usable
        </span>
      `).join("")}
      ${filesystems.length > rows.length ? `<span>+${filesystems.length - rows.length}</span>` : ""}
    </div>
  `;
}

function renderBusyIndex(host) {
  const capacity = host.recent_capacity || {};
  const index = Number.isFinite(capacity.busy_index) ? clamp(capacity.busy_index, 0, 100) : null;
  const windowDays = Number.isFinite(capacity.window_days) ? capacity.window_days : 7;
  const windowText = Number.isInteger(windowDays) ? String(windowDays) : windowDays.toFixed(1);
  const tone = index === null ? "empty" : index >= 50 ? "hot" : "cool";
  const fillStyle = tone === "hot"
    ? "background: var(--warn); box-shadow: 0 0 10px rgba(251, 191, 36, 0.22);"
    : "background: linear-gradient(90deg, var(--blue), var(--cyan));";
  const coverage = capacity.observed_wall_for || "0s";
  const coveragePercent = clamp(Math.round((Number(capacity.observed_wall_seconds || 0) / Math.max(1, windowDays * 86400)) * 1000) / 10, 0, 100);
  const coverageLabel = coveragePercent >= 100 ? `${windowText}일` : coverage;
  return `
    <div class="busy-index">
      <div class="busy-index-head">
        <span>최근 ${windowText}일 바쁨 지수</span>
        <strong>${index === null ? "-" : index}</strong>
      </div>
      <div class="bar busy-index-bar ${tone}"><span style="width:${index === null ? 0 : index}%; ${fillStyle}"></span></div>
      <div class="busy-index-foot">${index === null ? "관측 대기 중" : `관측 ${esc(coverageLabel)} / ${esc(windowText)}일 · ${coveragePercent}%`}</div>
    </div>
  `;
}

function renderOwnerBadge(host) {
  const owner = String(host.owner || "").trim();
  const location = String(host.location || "").trim();
  if (!owner && !location) return "";
  let type = "assigned";
  let title = "담당 서버";
  if (host.owner_type === "physical_ai_2") {
    type = "physical-ai-2";
    title = "피지컬 AI 2 서버";
  } else if (host.owner_type === "shared" || owner === "공용") {
    type = "shared";
    title = "공용 서버";
  }
  const text = [owner, location].filter(Boolean).join(" / ");
  return `<span class="state-pill owner-badge ${type}" title="${esc(title)}">${esc(text)}</span>`;
}

function renderRecentUsage(host) {
  const recent = host.recent_usage || {};
  const users = (recent.top_users || []).filter((user) => Number(user.seconds || 0) > 0);
  // top_users includes the aggregated "기타" row, so this sum is the exact
  // reportable denominator. recent.busy_seconds deliberately also includes
  // sub-minute unattributed probes that must not dilute displayed user shares.
  const reportableTotal = users.reduce((sum, user) => sum + Number(user.seconds || 0), 0);
  const windowDays = Number.isFinite(recent.window_days) ? recent.window_days : 7;
  const windowText = Number.isInteger(windowDays) ? String(windowDays) : windowDays.toFixed(1);
  const label = `최근 ${windowText}일 점유율`;
  const segments = reportableTotal > 0 && users.length
    ? users.map((user, index) => {
        const seconds = Number(user.seconds || 0);
        const width = clamp((seconds / reportableTotal) * 100, 0, 100);
        const color = recentUsageColors[index % recentUsageColors.length];
        return `<span style="width:${width.toFixed(2)}%; background:${color}"></span>`;
      }).join("")
    : "";
  const people = users.length
    ? users.map((user, index) => {
        const color = recentUsageColors[index % recentUsageColors.length];
        return `<span><i style="background:${color}"></i>${esc(user.user)} ${shareLabel(user.share_percent, user.seconds, reportableTotal)}</span>`;
      }).join("")
    : "사용자 기록 없음";
  return `
    <div class="recent-usage">
      <div class="recent-usage-head">
        <span title="동시 사용자는 GPU 점유시간을 사용자 수로 균등 분할합니다.">${label}</span>
      </div>
      <div class="recent-usage-bar">${segments}</div>
      <div class="recent-usage-foot">${people}</div>
    </div>
  `;
}

function renderUsageMetrics(host) {
  return `
    <div class="usage-metrics">
      ${renderBusyIndex(host)}
      ${renderRecentUsage(host)}
    </div>
  `;
}

function splitDiskErrors(errors) {
  const partials = [];
  const dockerIssues = [];
  const other = [];
  for (const error of errors) {
    const text = String(error || "");
    const match = text.match(/^user usage partial at\s+(.+?):\s+(\d+)\s+permission denied paths/i);
    if (match) {
      partials.push({ kind: "permission", path: match[1], count: Number(match[2]) });
    } else if (/^user usage partial(?::| at )/i.test(text)) {
      partials.push({ kind: "timeout" });
    } else if (/^docker (?:usage unavailable|system df unavailable|inspect partial|inspect parse failed):/i.test(text)) {
      dockerIssues.push({ timedOut: /\btimeout\b/i.test(text) });
    } else if (text) {
      other.push(text);
    }
  }
  return { partials, dockerIssues, other };
}

function renderDiskWarnings(errors) {
  const { partials, dockerIssues, other } = splitDiskErrors(errors);
  const warnings = [];
  if (partials.length || dockerIssues.length) {
    const permissionPartials = partials.filter((item) => item.kind === "permission");
    const unreadable = permissionPartials
      .map((item) => `${item.path}의 ${item.count}개 경로`)
      .join(", ");
    const details = [];
    if (unreadable) {
      details.push(`<span>권한이 없어 ${esc(unreadable)}를 읽지 못했습니다.</span>`);
    }
    if (partials.some((item) => item.kind === "timeout")) {
      details.push("<span>사용자 경로 용량 집계가 제한 시간 내 완료되지 않았습니다.</span>");
    }
    if (dockerIssues.length) {
      const dockerMessage = dockerIssues.some((item) => item.timedOut)
        ? "Docker 용량 집계 시간이 초과되어 컨테이너 사용량이 일부 누락될 수 있습니다."
        : "Docker 용량을 가져오지 못해 컨테이너 사용량이 일부 누락될 수 있습니다.";
      details.push(`<span>${dockerMessage}</span>`);
    }
    warnings.push(`
      <div class="tab-warning usage-warning">
        <strong>사용자별 용량 일부 미집계</strong>
        ${details.join("")}
        <span>전체 Storage 사용량은 정상이며, 아래 사용자별 Usage는 읽을 수 있는 경로와 Docker 쓰기 영역의 합계이며, ≥ 값에는 읽지 못한 경로가 빠져 있습니다.</span>
      </div>
    `);
  }
  if (other.length) {
    warnings.push("<div class=\"tab-warning\">일부 보조 용량 집계 항목을 가져오지 못했습니다.</div>");
  }
  return warnings.join("");
}

function renderDiskTab(host) {
  if (!(host.reachable ?? host.online)) {
    return `
      <div class="empty">
        ${esc(host.last_error || "연결 실패")}
        ${host.last_seen ? `<br>last seen ${esc(shortTime(host.last_seen))}` : ""}
      </div>
    `;
  }
  const disk = host.disk;
  if (!disk) {
    return `<div class="empty">디스크 정보 첫 수집을 기다리는 중입니다.</div>`;
  }
  const filesystems = disk.filesystems || [];
  const users = disk.users || [];
  const errors = disk.errors || [];
  if (!filesystems.length && errors.length) {
    return `<div class="empty">디스크 정보를 가져오지 못했습니다. 다음 수집 주기에 다시 시도합니다.</div>`;
  }
  const totals = storageTotals(filesystems);
  const usedPct = storageUsedPercent(totals);
  const reserved = reservedBytes(totals);
  const userMax = Math.max(...users.map((user) => user.bytes || 0), 1);
  const userRows = users.length ? users.map((user) => {
    const width = pct(user.bytes || 0, userMax);
    const locations = user.locations || [];
    const pathText = locations.length > 1
      ? `${locations.length} locations · ${locations.slice(0, 3).map((loc) => loc.path).join(", ")}${locations.length > 3 ? ", ..." : ""}`
      : user.path;
    const pathTitle = locations.length
      ? locations.map((loc) => `${loc.path} · ${loc.complete === false ? "최소 " : ""}${bytes(loc.bytes)}${loc.complete === false ? " (일부 경로 미집계)" : ""}`).join("\n")
      : user.path;
    const estimated = user.user === "system/other" || user.user?.startsWith("docker:");
    const sizePrefix = estimated ? "≈ " : user.complete === false ? "≥ " : "";
    const sizeTitle = estimated ? "추정 잔여 용량 또는 Docker 공유 항목 · 사용자별 확정 사용량이 아닙니다."
      : user.complete === false ? "읽을 수 있는 경로와 Docker 쓰기 영역만 합산한 최소값입니다."
      : "읽은 경로와 Docker 쓰기 영역 합계 · /home 단독 용량과 다를 수 있습니다.";
    return `
      <div class="user-disk-row">
        <div class="user-name">
          <strong>${esc(user.user)}</strong>
          <span title="${esc(pathTitle)}">${esc(pathText)}</span>
        </div>
        <div class="user-size">
          <strong title="${esc(sizeTitle)}">${sizePrefix}${bytes(user.bytes)}</strong>
          <div class="bar user"><span style="width:${width}%"></span></div>
        </div>
      </div>
    `;
  }).join("") : `<div class="empty compact">표시할 사용자별 용량이 없습니다.</div>`;

  return `
    <div class="storage-summary">
      <div class="storage-main">
        <span>Storage</span>
        <strong>${bytes(totals.available)} free</strong>
        <div>${bytes(totals.usable || totals.total)} usable total · ${bytes(totals.used)} used${reserved ? ` · ${bytes(reserved)} reserved` : ""}</div>
      </div>
      <div class="storage-meter">
        <div class="metric-head">
          <span>Used</span>
          <strong>${usedPct}%</strong>
        </div>
        <div class="bar disk"><span style="width:${usedPct}%"></span></div>
        <div class="metric-foot">sampled ${esc(shortTime(host.disk_updated_at))}</div>
      </div>
    </div>
    ${host.disk_stale ? '<div class="tab-warning">디스크 정보 갱신이 지연되어 마지막 수집 값을 표시합니다.</div>' : ""}
    ${renderDiskWarnings(errors)}
    <div class="user-section-title">
      <span title="경로별 용량 + Docker 쓰기 영역 · ≥는 일부 미집계된 최소값 · ≈는 추정/공유 항목">Usage</span>
      <strong>${users.length}</strong>
    </div>
    <div class="user-disk-list">${userRows}</div>
  `;
}

function renderHostBody(host) {
  const tab = tabFor(host.name);
  if (tab === "disk") return renderDiskTab(host);
  return renderGpuTab(host);
}

function hostActivityEffect(host, policyInput) {
  const policy = { ...defaultActivityPolicy, ...(policyInput || {}) };
  const gpus = host.gpus || [];
  if (!hostReady(host)) {
    return { kind: "down", className: "activity-down", title: "서버 연결 실패" };
  }
  if (!gpus.length) return { kind: "normal", className: "", title: "" };

  const recent = host.recent_usage || {};
  const windowDays = Number(recent.window_days || policy.cold_idle_days || 7);
  const activeSeconds = Number(recent.active_seconds || 0);
  const meaningfulActiveSeconds = Number.isFinite(Number(recent.meaningful_active_seconds))
    ? Number(recent.meaningful_active_seconds)
    : activeSeconds;
  const allFree = gpus.every((gpu) => !gpu.busy);
  const coldDays = Number(policy.cold_idle_days || defaultActivityPolicy.cold_idle_days);
  const minimumSessionSeconds = Number(policy.cold_min_session_seconds || defaultActivityPolicy.cold_min_session_seconds);
  const trackingSpanSeconds = Number(recent.tracking_span_seconds);
  const trackedLongEnough = Number.isFinite(trackingSpanSeconds)
    && trackingSpanSeconds >= coldDays * 86400;
  const noRecentUse = meaningfulActiveSeconds <= 0 && trackedLongEnough;

  if (allFree && noRecentUse) {
    return {
      kind: "cold",
      className: "activity-cold",
      title: `최근 ${coldDays}일 ${minimumSessionSeconds}초 이상 연속 사용 없음`,
    };
  }

  const capacityIndex = Number(host.recent_capacity?.busy_index);
  const hotFraction = Number(policy.hot_server_busy_fraction || defaultActivityPolicy.hot_server_busy_fraction);
  if (Number.isFinite(capacityIndex) && capacityIndex >= hotFraction * 100) {
    return {
      kind: "hot",
      className: "activity-hot",
      title: `최근 ${windowDays}일 관측 GPU 슬롯 바쁨 지수 ${capacityIndex.toFixed(1)}%`,
    };
  }

  return { kind: "normal", className: "", title: "" };
}

function activityStateMeta(activity) {
  if (activity.kind === "hot") return { icon: "🔥", label: "고사용" };
  if (activity.kind === "cold") return { icon: "❄️", label: "장기 유휴" };
  if (activity.kind === "unknown") return null;
  if (activity.kind === "down") return { icon: "⛔", label: "연결 실패" };
  return null;
}

function renderHosts(data) {
  const focusedHost = hostGrid.contains(document.activeElement) ? document.activeElement?.dataset?.host : null;
  const focusedTab = hostGrid.contains(document.activeElement) ? document.activeElement?.dataset?.tab : null;
  const focusedProcessKey = hostGrid.contains(document.activeElement)
    ? document.activeElement?.dataset?.processKey
    : null;
  const focusedProcessSummary = hostGrid.contains(document.activeElement)
    ? document.activeElement?.dataset?.processSummary
    : null;
  const visibleHosts = (data.hosts || []).filter((host) => hostMatchesFilter(host, activeHostFilter));
  if (!visibleHosts.length) {
    hostGrid.innerHTML = `<div class="filter-empty">선택한 조건에 해당하는 서버가 없습니다.</div>`;
    return;
  }
  hostGrid.innerHTML = visibleHosts
    .map((host) => {
      const gpus = host.gpus || [];
      const busy = gpus.filter((gpu) => gpuAvailable(gpu) && gpu.busy).length;
      const free = gpus.filter((gpu) => gpuAvailable(gpu) && !gpu.busy).length;
      const tab = tabFor(host.name);
      const statusClass = hostReady(host) ? (busy ? "warn" : "good") : "bad";
      const statusText = hostReady(host) ? `${free}/${gpus.length} free` : "offline";
      const storageWarning = (host.reachable ?? host.online) ? storageWarningFor(host) : null;
      const failures = Number.isFinite(host.consecutive_failures) ? host.consecutive_failures : 0;
      const stampText = (host.reachable ?? host.online)
        ? `seen ${shortTime(host.last_seen)}${failures ? ` · missed ${failures}` : ""}`
        : `error ${shortTime(host.last_error_at)} · last seen ${shortTime(host.last_seen)}`;
      const hostNote = [host.note, host.driver_version ? `driver ${host.driver_version}` : null]
        .filter(Boolean)
        .join(" · ") || host.hostname || "";
      const activity = hostActivityEffect(host, data.activity_policy);
      const activityMeta = activityStateMeta(activity);
      const hostDomId = `host-${domIdPart(host.name || "host")}`;
      const realName = host.hostname || host.name;
      const hostIdentity = host.ip_address ? `${realName} · ${host.ip_address}` : realName;
      return `
        <article class="host-card ${activity.className}" data-activity="${activity.kind}" data-host-card="${esc(host.name)}" aria-labelledby="${hostDomId}-heading">
          <div class="host-head">
            <div class="host-name-block">
              <div class="host-title">
                <strong id="${hostDomId}-heading">${esc(host.label || host.name)}</strong>
                <span class="state-pill ${statusClass}">${esc(statusText)}</span>
                ${storageWarning ? `<span class="state-pill disk-warn" title="${esc(`Storage used ${storageWarning.usedPct}%`)}">Disk ${esc(storageWarning.usedPct)}%</span>` : ""}
                ${renderOwnerBadge(host)}
                ${activityMeta ? `<span class="state-pill activity-state ${activity.kind}" role="img" aria-label="${esc(activityMeta.label)}">${activityMeta.icon}</span>` : ""}
              </div>
              <div class="host-note">${esc(hostNote)}</div>
              ${renderDriveLine(host)}
              ${renderUsageMetrics(host)}
            </div>
            <div class="host-stamp">
              <strong title="${esc(hostIdentity)}">${esc(hostIdentity)}</strong>
              <span title="${esc(host.last_error || stampText)}">${esc(stampText)}</span>
            </div>
          </div>
          <div class="tab-strip" role="tablist" aria-label="${esc(host.label || host.name)} 상세 정보">
            ${["gpu", "disk"].map((name) => `
              <button class="tab-button ${tab === name ? "active" : ""}" type="button" role="tab" id="${hostDomId}-${name}-tab" aria-selected="${tab === name}" aria-controls="${hostDomId}-panel" tabindex="${tab === name ? "0" : "-1"}" data-host="${esc(host.name)}" data-tab="${name}">
                ${name === "gpu" ? "GPU" : "Disk"}
              </button>
            `).join("")}
          </div>
          <div class="host-body" id="${hostDomId}-panel" role="tabpanel" aria-labelledby="${hostDomId}-${tab}-tab">${renderHostBody(host)}</div>
        </article>
      `;
    })
    .join("");
  if (focusedProcessKey) {
    focusWithoutScroll(
      hostGrid.querySelector(`[data-process-key="${CSS.escape(focusedProcessKey)}"]`),
    );
  } else if (focusedProcessSummary) {
    focusWithoutScroll(
      hostGrid.querySelector(`[data-process-summary="${CSS.escape(focusedProcessSummary)}"]`),
    );
  } else if (focusedHost && focusedTab) {
    focusWithoutScroll(
      hostGrid.querySelector(`[data-host="${CSS.escape(focusedHost)}"][data-tab="${CSS.escape(focusedTab)}"]`),
    );
  }
}

function eventLabel(event) {
  if (event === "busy_start") return "busy";
  if (event === "free_start") return "free";
  if (event === "host_down" || event === "gpu_down") return "DOWN";
  if (event === "host_recovered" || event === "gpu_recovered") return "UP";
  return event;
}

function eventPillClass(event) {
  if (event === "free_start") return "good";
  if (event === "host_down" || event === "gpu_down") return "bad";
  if (event === "host_recovered" || event === "gpu_recovered") return "recovered";
  return "warn";
}

function eventExplanation(event) {
  if (event.event === "host_down") return "서버 연결 실패";
  if (event.event === "host_recovered") return "서버 연결 복구";
  // Historical per-GPU events keep their original labels and indices.
  if (event.event === "gpu_down") return "GPU 연결 실패";
  if (event.event === "gpu_recovered") return "GPU 연결 복구";
  return "";
}

function eventGpuLabel(event) {
  return event.gpu_indices?.length ? event.gpu_indices.join(", ") : (event.gpu_index ?? "-");
}

function publicActivityEvents(events) {
  // The API filters these before pagination. Keep this boundary as protection
  // against cached or older payloads exposing internal observation diagnostics.
  const visible = new Set([
    "busy_start", "free_start", "host_down", "host_recovered", "gpu_down", "gpu_recovered",
  ]);
  return Array.isArray(events) ? events.filter((event) => visible.has(event?.event)
    && (eventFilters.includeAvailability || event.event === "busy_start" || event.event === "free_start")) : [];
}

function renderEvents(eventData) {
  const events = publicActivityEvents(eventData.events);
  eventMeta = eventData;
  const start = events.length ? eventData.offset + 1 : 0;
  const end = eventData.offset + events.length;
  const filters = activeEventFilterText();
  eventsMeta.textContent = events.length
    ? `보존된 free/busy · DOWN/UP 기록 · ${start}-${end}${filters}`
    : `보존된 free/busy · DOWN/UP 기록${filters}`;
  prevEventsButton.disabled = eventData.prev_offset === null;
  nextEventsButton.disabled = !eventData.has_more;

  if (!events.length) {
    const emptyMessage = filters
      ? "현재 조건에 해당하는 활동 기록이 없습니다."
      : "아직 기록된 활동이 없습니다.";
    eventsBody.innerHTML = `<tr><td colspan="5" class="muted">${emptyMessage}</td></tr>`;
    return;
  }
  eventsBody.innerHTML = events
    .map((event) => `
      <tr>
        <td>${esc(shortTime(event.time))}</td>
        <td>${esc(event.host)}</td>
        <td>${esc(eventGpuLabel(event))}</td>
        <td><span class="state-pill ${eventPillClass(event.event)}" title="${esc(eventExplanation(event))}">${esc(eventLabel(event.event))}</span></td>
        <td>${esc(event.users || "-")}</td>
      </tr>
    `)
    .join("");
}

function normalizeEventFilters(data) {
  const hosts = hostsForActiveLab(data);
  if (eventFilters.host && !hosts.some((host) => host.name === eventFilters.host)) {
    eventFilters.host = "";
    invalidateEvents();
  }
}

function renderEventFilters(data) {
  const hosts = hostsForActiveLab(data);
  const optionsHtml = [
    `<option value="">All servers</option>`,
    ...hosts.map((host) => `<option value="${esc(host.name)}">${esc(host.label || host.name)}</option>`),
  ].join("");
  if (eventServerSelect.innerHTML !== optionsHtml) eventServerSelect.innerHTML = optionsHtml;
  if (document.activeElement !== eventDateInput) eventDateInput.value = eventFilters.date;
  if (document.activeElement !== eventServerSelect) eventServerSelect.value = eventFilters.host;
  if (document.activeElement !== eventUserInput) eventUserInput.value = eventFilters.user;
  eventAvailabilityInput.checked = eventFilters.includeAvailability;
}

function updateEventFiltersFromControls() {
  eventFilters.date = eventDateInput.value;
  eventFilters.host = eventServerSelect.value;
  eventFilters.user = eventUserInput.value.trim();
  eventFilters.includeAvailability = eventAvailabilityInput.checked;
}

function activeEventFilterText() {
  const parts = [];
  if (eventFilters.date) parts.push(eventFilters.date);
  if (eventFilters.host) parts.push(eventFilters.host);
  if (eventFilters.user) parts.push(`user ${eventFilters.user}`);
  return parts.length ? ` · ${parts.join(" · ")}` : "";
}

function eventsQueryString() {
  const params = new URLSearchParams({
    limit: String(eventLimit),
    offset: String(eventOffset),
    include_availability: eventFilters.includeAvailability ? "1" : "0",
  });
  if (activeLab) params.set("lab", activeLab);
  if (eventFilters.date) params.set("date", eventFilters.date);
  if (eventFilters.host) params.set("host", eventFilters.host);
  if (eventFilters.user) params.set("user", eventFilters.user);
  return params.toString();
}

function scheduleFilteredLoad(delay = 0) {
  updateEventFiltersFromControls();
  eventOffset = 0;
  invalidateEvents();
  window.clearTimeout(filterTimer);
  filterTimer = window.setTimeout(() => loadEvents(true), delay);
}

function renderSnapshot(data) {
  const viewportScrollX = window.scrollX;
  const viewportScrollY = window.scrollY;
  const labs = ensureActiveLab(data);
  const visibleData = scopedData(data);
  normalizeEventFilters(data);
  lastSnapshot = data;
  pollMs = Math.max(3000, (data.poll_interval_seconds || 10) * 1000);
  const diskSeconds = data.disk_poll_interval_seconds || 300;
  const diskInterval = diskSeconds >= 3600 && diskSeconds % 3600 === 0
    ? `${diskSeconds / 3600}시간`
    : `${Math.max(1, Math.round(diskSeconds / 60))}분`;
  const labLabel = labs.find((lab) => lab.id === activeLab)?.label || "Servers";
  statusLine.textContent = `연구실 LAN · ${labLabel} · GPU ${Math.round(pollMs / 1000)}초 · 디스크 ${diskInterval}`;
  updatedAt.textContent = `Updated ${shortTime(data.now)}`;
  if (Array.isArray(data.deadlines)) {
    currentDeadlines = normalizedDeadlines(data.deadlines);
  } else if (data.deadline) {
    currentDeadlines = normalizedDeadlines([data.deadline]);
  }
  renderDeadlines();
  lastCollectorCompletedAt = Number(data.collector?.last_cycle_completed_at || 0) * 1000;
  renderLabSwitcher(data, labs);
  renderAnnouncements(data.announcements || []);
  renderSummary(visibleData);
  renderLabPulse();
  renderActivityLegend(data);
  renderHostFilters(visibleData);
  renderHosts(visibleData);
  renderEventFilters(data);
  if (window.scrollX !== viewportScrollX || window.scrollY !== viewportScrollY) {
    window.scrollTo({ left: viewportScrollX, top: viewportScrollY, behavior: "auto" });
  }
  if (!insightsInFlight && Date.now() - insightsLoadedAt > 60000) {
    window.setTimeout(() => loadInsights(), 0);
  }
}

async function loadInsights(force = false) {
  if (insightsInFlight) return;
  if (!force && insightsData && Date.now() - insightsLoadedAt < 60000) return;
  const generation = insightsGeneration;
  insightsInFlight = true;
  try {
    const { response, payload } = await fetchJsonWithTimeout("/api/insights", { cache: "no-store" });
    if (!response.ok) throw new Error(`insights ${response.status}`);
    if (generation === insightsGeneration) {
      insightsData = payload;
      insightsLoadedAt = Date.now();
      renderLabPulse();
    }
  } catch (error) {
    if (generation === insightsGeneration && !insightsData) {
      labPulse.hidden = false;
      labPulse.innerHTML = `<div class="pulse-empty">Lab Pulse를 불러오지 못했습니다 · ${esc(error)}</div>`;
    }
  } finally {
    insightsInFlight = false;
    if (generation !== insightsGeneration) window.setTimeout(() => loadInsights(true), 0);
  }
}

async function loadEvents(force = false) {
  if (eventsInFlight) return;
  const query = eventsQueryString();
  if (!force && (document.hidden || (query === lastEventsQuery && Date.now() - eventsLoadedAt < 30000))) return;
  const generation = eventsGeneration;
  eventsInFlight = true;
  try {
    const { response: eventsRes, payload: eventData } = await fetchJsonWithTimeout(`/api/events?${query}`, { cache: "no-store" });
    if (!eventsRes.ok) throw new Error(`events ${eventsRes.status}`);
    if (generation === eventsGeneration && query === eventsQueryString()) {
      renderEvents(eventData);
      eventsLoadedAt = Date.now();
      lastEventsQuery = query;
    }
  } catch (eventError) {
    if (generation === eventsGeneration && query === eventsQueryString()) {
      eventsMeta.textContent = `활동 기록을 불러오지 못했습니다 · ${eventError}`;
      eventsBody.innerHTML = `<tr><td colspan="5" class="muted">현재 조건의 활동 기록을 불러오지 못했습니다.</td></tr>`;
      prevEventsButton.disabled = true;
      nextEventsButton.disabled = true;
    }
  } finally {
    eventsInFlight = false;
    if (generation !== eventsGeneration) window.setTimeout(() => loadEvents(true), 0);
  }
}

function updateStaleBanner(force = false) {
  const threshold = Math.max(90000, pollMs * 6);
  const fetchStale = lastSuccessfulLoadAt > 0 && Date.now() - lastSuccessfulLoadAt > threshold;
  const collectorStale = lastCollectorCompletedAt > 0 && Date.now() - lastCollectorCompletedAt > threshold;
  const stale = force || fetchStale || collectorStale;
  staleBanner.hidden = !stale;
}

async function load() {
  if (loadInFlight) {
    loadQueued = true;
    return;
  }
  loadInFlight = true;
  refreshButton.disabled = true;
  refreshButton.textContent = "Refreshing…";
  document.body.setAttribute("aria-busy", "true");
  try {
    const { response: snapshotRes, payload: data } = await fetchJsonWithTimeout("/api/snapshot", { cache: "no-store" });
    if (!snapshotRes.ok) throw new Error(`snapshot ${snapshotRes.status}`);
    renderSnapshot(data);
    lastSuccessfulLoadAt = Date.now();
    updateStaleBanner(false);

    await loadEvents(false);
  } catch (err) {
    statusLine.textContent = `Load failed: ${err}`;
    updateStaleBanner(true);
  } finally {
    loadInFlight = false;
    refreshButton.disabled = false;
    refreshButton.textContent = "Refresh";
    document.body.removeAttribute("aria-busy");
    window.clearTimeout(timer);
    if (loadQueued) {
      loadQueued = false;
      timer = window.setTimeout(load, 0);
    } else {
      const nextPollMs = document.hidden ? Math.max(60000, pollMs * 6) : pollMs;
      timer = window.setTimeout(load, nextPollMs);
    }
  }
}

hostGrid.addEventListener("click", (event) => {
  const button = event.target.closest("[data-tab]");
  if (button) {
    activeTabs.set(button.dataset.host, button.dataset.tab);
    if (lastSnapshot) renderHosts(scopedData(lastSnapshot));
  }
});

hostGrid.addEventListener("toggle", (event) => {
  const details = event.target.closest?.("[data-process-group]");
  if (!details) return;
  if (details.open) expandedProcessGroups.add(details.dataset.processGroup);
  else expandedProcessGroups.delete(details.dataset.processGroup);
}, true);

hostGrid.addEventListener("keydown", (event) => {
  if (!event.target.matches('[role="tab"]') || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  const tabs = [...event.target.closest('[role="tablist"]').querySelectorAll('[role="tab"]')];
  const nextIndex = (tabs.indexOf(event.target) + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  const hostName = tabs[nextIndex].dataset.host;
  const tabName = tabs[nextIndex].dataset.tab;
  tabs[nextIndex].click();
  window.setTimeout(() => {
    hostGrid.querySelector(`[data-host="${hostName}"][data-tab="${tabName}"]`)?.focus();
  }, 0);
});

labSwitcher.addEventListener("click", (event) => {
  const button = event.target.closest("[data-lab]");
  if (!button || button.dataset.lab === activeLab) return;
  activeLab = button.dataset.lab;
  eventOffset = 0;
  invalidateEvents();
  if (lastSnapshot) {
    renderSnapshot(lastSnapshot);
  }
  loadEvents(true);
});

hostFilters.addEventListener("click", (event) => {
  const button = event.target.closest("[data-host-filter]");
  if (!button || button.dataset.hostFilter === activeHostFilter) return;
  activeHostFilter = button.dataset.hostFilter;
  if (lastSnapshot) {
    const visibleData = scopedData(lastSnapshot);
    renderHostFilters(visibleData);
    renderHosts(visibleData);
  }
});

refreshButton.addEventListener("click", () => {
  invalidateEvents();
  invalidateInsights();
  load();
});

eventAvailabilityInput.addEventListener("change", () => scheduleFilteredLoad());
eventDateInput.addEventListener("change", () => scheduleFilteredLoad());
eventServerSelect.addEventListener("change", () => scheduleFilteredLoad());
eventUserInput.addEventListener("input", () => scheduleFilteredLoad(250));
resetEventFiltersButton.addEventListener("click", () => {
  eventDateInput.value = "";
  eventServerSelect.value = "";
  eventUserInput.value = "";
  eventAvailabilityInput.checked = false;
  scheduleFilteredLoad();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" && event.key !== "Esc") return;
  if (noticeDeleteDialog.open) {
    event.preventDefault();
    closeNoticeDeleteDialog();
  } else if (deadlineDialog.open) {
    event.preventDefault();
    closeDeadlineDialog();
  } else if (noticeDialog.open) {
    event.preventDefault();
    closeNoticeDialog();
  }
});

noticeOpenButton.addEventListener("click", () => openNoticeDialog());
noticeCancelButton.addEventListener("click", closeNoticeDialog);
noticeForm.addEventListener("submit", submitAnnouncement);
noticeDialog.addEventListener("close", () => {
  noticePinInput.value = "";
  pendingNoticeEditId = null;
});

deadlineAddButton.addEventListener("click", () => openDeadlineDialog());
deadlineWidgets.addEventListener("click", (event) => {
  const button = event.target.closest("[data-deadline-edit]");
  if (!button) return;
  openDeadlineDialog(button.dataset.deadlineEdit);
});
deadlineCancelButton.addEventListener("click", closeDeadlineDialog);
deadlineForm.addEventListener("submit", submitDeadline);
deadlineTbaInput.addEventListener("change", updateDeadlineMode);
for (const input of [deadlineAtInput, noticeExpiresInput]) {
  input.addEventListener("input", () => limitDateTimeYear(input));
}

deadlineDeleteButton.addEventListener("click", deleteDeadline);
deadlineDialog.addEventListener("close", () => {
  deadlinePinInput.value = "";
  pendingDeadlineEditId = null;
});

noticeDeleteCancelButton.addEventListener("click", closeNoticeDeleteDialog);
noticeDeleteForm.addEventListener("submit", deleteAnnouncement);
noticeDeleteDialog.addEventListener("close", () => { noticeDeletePinInput.value = ""; });

function updateScrollTopButton() {
  scrollTopButton.classList.toggle("visible", window.scrollY > 480);
}

window.addEventListener("scroll", updateScrollTopButton, { passive: true });
scrollTopButton.addEventListener("click", () => {
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top: 0, behavior: reducedMotion ? "auto" : "smooth" });
});
updateScrollTopButton();
window.setInterval(() => updateStaleBanner(false), 5000);
window.setInterval(updateDeadlineCountdowns, 1000);
window.setInterval(loadIntelligenceIndex, INTELLIGENCE_INDEX_REFRESH_MS);

noticeItems.addEventListener("click", (event) => {
  const editButton = event.target.closest("[data-notice-edit]");
  if (editButton) {
    openNoticeDialog(editButton.dataset.noticeEdit);
    return;
  }
  const button = event.target.closest("[data-notice-delete]");
  if (!button) return;
  openNoticeDeleteDialog(button.dataset.noticeDelete);
});

prevEventsButton.addEventListener("click", () => {
  if (eventMeta.prev_offset === null) return;
  eventOffset = eventMeta.prev_offset;
  invalidateEvents();
  loadEvents(true);
});

nextEventsButton.addEventListener("click", () => {
  if (eventMeta.next_offset === null) return;
  eventOffset = eventMeta.next_offset;
  invalidateEvents();
  loadEvents(true);
});

document.addEventListener("visibilitychange", () => {
  window.clearTimeout(timer);
  if (document.hidden) {
    timer = window.setTimeout(load, Math.max(60000, pollMs * 6));
  } else {
    invalidateEvents();
    load();
  }
});

loadIntelligenceIndex();
load();
