"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = typeof injectedSource === "string" ? injectedSource : fs.readFileSync(path.join(__dirname,"../static/app.js"),"utf8");
const context = vm.createContext({
  defaultActivityPolicy: {hot_server_busy_fraction:0.5,cold_idle_days:7,cold_min_session_seconds:60},
  MAX_DEADLINES:6,DEADLINE_TONE_ORDER:["silver","gold","emerald","diamond","master","grandmaster"],
  DEADLINE_TONES:new Set(["silver","gold","emerald","diamond","master","grandmaster"]),
  summaryGrid:{innerHTML:""},labSwitcher:{innerHTML:"",contains:()=>false},
  document:{activeElement:null},activeLab:"nlp",activeHostFilter:"all",
  eventsMeta:{textContent:""},eventsBody:{innerHTML:""},
  prevEventsButton:{disabled:false},nextEventsButton:{disabled:false},
  activeEventFilterText:()=>"",CSS:{escape:value=>value},
  storageWarningFor:()=>null,tabFor:()=>"gpu",renderOwnerBadge:()=>"",
  renderDriveLine:()=>"",renderUsageMetrics:()=>"",renderHostBody:()=>"",
  noticePinInput:{value:""},noticeDeletePinInput:{value:""},
  eventFilters:{date:"",host:"",user:"",includeAvailability:true},
  eventLimit:50,eventOffset:0,URLSearchParams,
  URL,Intl,Date,expandedProcessGroups:new Set(),
});
const names = ["clamp","pct","esc","limitDateTimeYear","formatKstInput","kstInputToIso","normalizedDeadlines",
  "deadlineDateText","deadlineCountdownText","gpuAvailable","hostReady","hostActivityEffect",
  "flattenGpus","mib","gbFromBytes","renderSummary","labForHost","renderLabSwitcher","hostMatchesFilter",
  "durationText","renderBusyIndex","renderOwnerBadge","eventLabel","eventPillClass","eventExplanation","eventGpuLabel","gpuDisplayName","renderMeter","renderGpu","renderGpuTab",
  "shortTime","intelligenceModelDisplayName","regionalFlagCode","deadlineTitleHtml",
  "eventsQueryString","publicActivityEvents","renderEvents","splitDiskErrors","renderDiskWarnings",
  "domIdPart","activityStateMeta","focusWithoutScroll","renderHosts",
  "closeNoticeDialog","closeNoticeDeleteDialog","isCapacityFilesystem","storageTotals","storageUsedPercent","storageWarningFor"];
for (const name of names) {
  const match = source.match(new RegExp("^(?:async )?function "+name+"\\([^]*?^}","m"));
  assert.ok(match, name);
  vm.runInContext(match[0], context);
}
const flagCodes = source.match(/const LOCAL_FLAG_REGION_CODES = new Set\(`[^]*?`\.trim\(\)\.split\(\/\\s\+\/\)\);/);
assert.ok(flagCodes,"local flag region list");
vm.runInContext(flagCodes[0],context);
let checks=0;
const check=(name,fn)=>{fn();checks++;console.log("PASS "+name);};
const gpu={available:true,busy:false,memory_total:8192,memory_used:0,processes:[]};
const healthy={name:"good",lab:"nlp",online:true,reachable:true,gpus:[gpu],recent_usage:{tracking_span_seconds:8*86400,window_days:7,meaningful_active_seconds:0},recent_capacity:{busy_index:0}};
check("partial failure excludes all free GPUs and produces down effect",()=>{
 const partial={...healthy,name:"partial",online:false,reachable:true,degraded:true,gpus:[gpu,{...gpu,available:false}]};
 assert.equal(context.hostReady(partial),false);
 assert.equal(context.hostActivityEffect(partial).kind,"down");
 assert.equal(context.hostMatchesFilter(partial,"down"),true);
 assert.equal(context.hostMatchesFilter(partial,"free"),false);
 context.renderSummary({hosts:[healthy,partial]});
 assert.match(context.summaryGrid.innerHTML,/1 \/ 8 GB/);
 context.renderLabSwitcher({hosts:[healthy,partial]},[{id:"nlp",label:"NLP"}]);
 assert.match(context.labSwitcher.innerHTML,/1\/2 servers/);
 assert.match(context.labSwitcher.innerHTML,/1\/3 GPU free/);
 const oldPayload={...partial,online:true};
 assert.equal(context.hostReady({...oldPayload,gpus:[{...gpu,usable:false},{...gpu,available:false}]}),false);
 const usablePartial={...oldPayload,gpus:[{...gpu,usable:true},{...gpu,available:false}]};
 assert.equal(context.hostReady(usablePartial),false);
 assert.equal(context.hostActivityEffect(usablePartial).kind,"down");
 context.renderSummary({hosts:[usablePartial]});
 assert.match(context.summaryGrid.innerHTML,/0 \/ 0 GB/);
 assert.equal(context.eventLabel("host_down"),"DOWN");
 assert.equal(context.eventLabel("host_recovered"),"UP");
 assert.equal(context.eventPillClass("host_down"),"bad");
 assert.equal(context.eventPillClass("host_recovered"),"recovered");
});
check("cold ignores observation coverage and short pulses; hot uses observed ratio",()=>{
 assert.equal(context.hostActivityEffect(healthy).kind,"cold");
 assert.equal(context.hostActivityEffect({...healthy,recent_usage:{...healthy.recent_usage,active_seconds:40}}).kind,"cold");
 assert.equal(context.hostActivityEffect({...healthy,recent_usage:{...healthy.recent_usage,meaningful_active_seconds:60},recent_capacity:{busy_index:65}}).kind,"hot");
 assert.equal(context.hostActivityEffect({...healthy,gpus:[{...gpu,busy:true,utilization:0,processes:[{user:"resident"}]}],recent_capacity:{busy_index:98}}).kind,"hot");
});
check("rounded 100 percent shows seven days consistently",()=>{
 const rendered=context.renderBusyIndex({recent_capacity:{window_days:7,observed_wall_seconds:604790,observed_wall_for:"6d 23h",busy_index:0}});
 assert.match(rendered,/관측 7일 \/ 7일/);
 assert.doesNotMatch(rendered,/관측 6d 23h/);
});
check("norma physical AI badge is distinct from the shared badge",()=>{
 const physical=context.renderOwnerBadge({owner:"피지컬 AI 2",owner_type:"physical_ai_2",location:"Room 305"});
 const shared=context.renderOwnerBadge({owner:"공용",owner_type:"shared",location:"Room 305"});
 assert.match(physical,/owner-badge physical-ai-2/);
 assert.match(physical,/title="피지컬 AI 2 서버"/);
 assert.match(physical,/>피지컬 AI 2 \/ Room 305</);
 assert.match(shared,/owner-badge shared/);
 assert.doesNotMatch(physical,/owner-badge shared/);
});
check("TBA sorts last, preserves tone and legacy scheduled defaults",()=>{
 const deadlines=context.normalizedDeadlines([
   {id:"tba",title:"🇺🇸 Meeting",mode:"tba",tba_text:"2027년 1월 예정",tone:"gold",url:"https://example.org"},
   {id:"late",title:"Late",deadline_at:"2027-02-01T00:00:00Z",tone:"silver",url:"https://example.org"},
   {id:"early",title:"Early",deadline_at:"2027-01-01T00:00:00Z",tone:"master",url:"https://example.org"}]);
 assert.equal(Array.from(deadlines,x=>x.id).join(","),"early,late,tba");
 assert.equal(deadlines[2].tone,"gold");
 assert.equal(deadlines[1].mode,"scheduled");
 assert.equal(deadlines[2].tba_text,"2027년 1월 예정");
});
check("equal deadline timers keep registration order",()=>{
 const deadlines=context.normalizedDeadlines([
   {id:"existing-z",title:"Existing",deadline_at:"2027-01-01T00:00:00Z",tone:"silver",url:"https://example.org"},
   {id:"new-a",title:"New",deadline_at:"2027-01-01T00:00:00Z",tone:"gold",url:"https://example.org"}]);
 assert.equal(Array.from(deadlines,x=>x.id).join(","),"existing-z,new-a");
});
check("invalid dates cannot throw or silently roll into another day",()=>{
 for(const value of ["123456-01-01T00:00","2027-13-01T00:00","2027-02-30T10:00","2027-01-01T24:99",""])
   assert.equal(context.kstInputToIso(value),"");
 assert.equal(context.kstInputToIso("2027-01-15T20:59"),"2027-01-15T11:59:00.000Z");
});


check("DOWN host never shows sibling free GPUs or diagnostic state",()=>{
 const down={...healthy,online:false,gpus:[{...gpu,index:0},{...gpu,index:1,available:false}]};
 const rendered=context.renderGpuTab(down);
 assert.match(rendered,/연결 실패/);
 assert.doesNotMatch(rendered,/gpu-line free|진단 정보|상태 미확인/);
 assert.equal(context.hostMatchesFilter(down,"free"),false);
 assert.equal(context.hostActivityEffect(down).kind,"down");
 const row=context.renderGpu(gpu,"test",false);
 assert.match(row,/state-pill bad/);
 assert.match(row,/offline/);
 assert.doesNotMatch(row,/available ·|process-list/);
});

check("legacy unknown payload is DOWN and recovery is UP",()=>{
 const lost={...healthy,online:false,availability_state:"unknown",gpus:[{...gpu,available:false,health_state:"unknown"}]};
 assert.equal(context.hostActivityEffect(lost).kind,"down");
 assert.equal(context.hostReady(lost),false);
 assert.equal(context.eventLabel("host_down"),"DOWN");
 assert.equal(context.eventLabel("host_recovered"),"UP");
 assert.equal(context.eventGpuLabel({gpu_indices:[],gpu_index:null}),"-");
 assert.equal(context.eventExplanation({event:"host_down",details:{cause:"timeout"}}),"서버 연결 실패");
 assert.equal(context.eventExplanation({event:"host_recovered"}),"서버 연결 복구");
 assert.match(context.renderGpu(lost.gpus[0],"test",false),/state-pill bad/);
});

check("public activity hides diagnostic events even from an older payload",()=>{
 const visible=["busy_start","free_start","host_down","host_recovered","gpu_down","gpu_recovered"];
 const internal=["observation_gap","user_change","observation_lost","observation_resumed","future_diagnostic"];
 const events=[...visible,...internal].map(event=>({event,time:"2026-09-09T12:00:00+09:00",host:"host",users:"alice",gpu_index:0}));
 const filtered=context.publicActivityEvents(events);
 assert.equal(Array.from(filtered,event=>event.event).join(","),visible.join(","));
 assert.equal(context.publicActivityEvents(null).length,0);
 context.renderEvents({events,offset:0,prev_offset:null,has_more:false});
 assert.equal((context.eventsBody.innerHTML.match(/<tr>/g)||[]).length,visible.length);
 assert.doesNotMatch(context.eventsBody.innerHTML,/UNKNOWN|SEEN|observation_|user_change|future_diagnostic/);
 assert.equal(context.eventsMeta.textContent,"보존된 free/busy · DOWN/UP 기록 · 1-6");
});
check("availability checkbox excludes old and new DOWN/UP and queries the server",()=>{
 const events=["busy_start","host_down","free_start","host_recovered","gpu_down","gpu_recovered","observation_lost"].map(event=>({event}));
 context.eventFilters.includeAvailability=false;
 assert.deepEqual(Array.from(context.publicActivityEvents(events),e=>e.event),["busy_start","free_start"]);
 assert.equal(new URLSearchParams(context.eventsQueryString()).get("include_availability"),"0");
 context.eventFilters.includeAvailability=true;
 assert.equal(context.publicActivityEvents(events).length,6);
 assert.equal(new URLSearchParams(context.eventsQueryString()).get("include_availability"),"1");
 context.eventFilters.includeAvailability=false;
});
check("local flag icons escape surrounding text and model effort names stay concise",()=>{
 const title=context.deadlineTitleHtml('🇺🇸 ACL <img src=x onerror="alert(1)"> 🇰🇷');
 assert.match(title,/src="\/flag-icons\/us.svg"/);
 assert.match(title,/src="\/flag-icons\/kr.svg"/);
 assert.match(title,/&lt;img/);
 assert.doesNotMatch(title,/<img src=x/);
 assert.equal(context.regionalFlagCode("🇺🇸"),"us");
 assert.equal(context.regionalFlagCode("US"),"");
 for(const [full,short] of [
  ["Claude Opus 5 (Adaptive Reasoning, Max Effort)","Claude Opus 5 (max)"],
  ["GPT-6 (Reasoning, Ultra Effort)","GPT-6 (ultra)"],
  ["Model (High Effort)","Model (high)"],
  ["Model (Thinking)","Model (Thinking)"]]) assert.equal(context.intelligenceModelDisplayName(full),short);
 const invalid=context.normalizedDeadlines([{id:"bad",title:"Bad",url:"javascript:alert(1)"}]);
 assert.equal(invalid[0].url,"#");
});
check("all processes and users stay visible with stable memory ordering and expansion",()=>{
 const processes=[
  {pid:9,user:"alice",used_memory:90,command_summary:"python · train.py"},
  {pid:3,user:"bob",used_memory:300,command_summary:"python · evaluate.py"},
  {pid:4,user:"alice",used_memory:200,command_summary:"python"},
  {pid:5,user:"carol",used_memory:100,command_summary:"python"},
 ];
 context.expandedProcessGroups.add("host:0");
 const html=context.renderGpu({...gpu,index:0,busy:true,processes},"host");
 assert.equal((html.match(/class="process"/g)||[]).length,4);
 assert.match(html,/bob, alice, carol/);
 assert.match(html,/data-process-group="host:0" open/);
 assert.match(html,/data-process-summary="host:0"/);
 assert.ok(html.indexOf('data-process-key="host:0:3"')<html.indexOf('data-process-key="host:0:4"'));
 assert.equal(processes[0].pid,9,"render must not reorder snapshot data");
 context.document.activeElement={dataset:{processSummary:"host:0"}};
 let restored=false;
 context.hostGrid={innerHTML:"",contains:()=>true,querySelector:selector=>{
   assert.equal(selector,'[data-process-summary="host:0"]');
   return {focus:options=>{assert.equal(options.preventScroll,true);restored=true;}};
 }};
 context.renderHosts({hosts:[{...healthy,name:"host"}]});
 assert.equal(restored,true,"refresh restores keyboard focus on expanded-process summary");
 context.document.activeElement=null;
});
check("disk timeout diagnostics never leak collector commands",()=>{
 const warning=context.renderDiskWarnings([
  "docker usage unavailable: timeout after 16s: docker ps -a --size --format {{json .}}",
  "user usage partial at /home: 7 permission denied paths",
  "unexpected internal command: secret-token",
 ]);
 assert.match(warning,/Docker 용량 집계 시간이 초과되어/);
 assert.match(warning,/\/home의 7개 경로/);
 assert.doesNotMatch(warning,/docker ps|--format|secret-token|16s/);
});
check("closing a dialog clears its secret synchronously before native close dispatch",()=>{
 context.noticePinInput.value="test-owner-passphrase";
 context.noticeDialog={close:()=>assert.equal(context.noticePinInput.value,"")};
 context.pendingNoticeEditId=1;
 context.closeNoticeDialog(false);
 assert.equal(context.pendingNoticeEditId,null);
 context.noticeDeletePinInput.value="test-owner-passphrase";
 context.noticeDeleteDialog={close:()=>assert.equal(context.noticeDeletePinInput.value,"")};
 context.pendingNoticeDeleteId=1;
 context.closeNoticeDeleteDialog(false);
 assert.equal(context.pendingNoticeDeleteId,null);
});

check("disk capacity deduplicates bind mounts and a full root is never masked",()=>{
 const root={filesystem:"/dev/root",mount:"/",type:"ext4",total_bytes:1000,used_bytes:910,available_bytes:90};
 const data={filesystem:"/dev/data",mount:"/data",type:"ext4",total_bytes:9000,used_bytes:100,available_bytes:8900};
 const duplicate={...data,mount:"/data-alias"};
 const totals=context.storageTotals([root,data,duplicate]);
 assert.equal(totals.total,10000);
 assert.equal(totals.used,1010);
 assert.equal(totals.available,8990);
 assert.equal(context.storageWarningFor({disk:{filesystems:[root,data]}}).usedPct,91);
 assert.equal(context.storageWarningFor({disk:{filesystems:[data,duplicate]}}),null);
});

check("native datetime editing stays within four year digits",()=>{
 const input={value:"123456-09-12T12:30"};
 context.limitDateTimeYear(input);
 assert.equal(input.value,"1234-09-12T12:30");
 input.value="2028-02-29T23:59";
 context.limitDateTimeYear(input);
 assert.equal(context.kstInputToIso(input.value),"2028-02-29T14:59:00.000Z");
});
check("collection failures expose no UNKNOWN or SEEN badge",()=>{
 const uncertain={...healthy,online:false,availability_state:"unknown",gpus:[{...gpu,available:false,health_state:"unknown"}]};
 context.hostGrid={innerHTML:"",contains:()=>false};
 context.renderHosts({hosts:[uncertain]});
 assert.doesNotMatch(context.hostGrid.innerHTML,/>UNKNOWN<|>SEEN</);
 assert.doesNotMatch(context.renderGpu(uncertain.gpus[0],"test"),/>UNKNOWN<|>SEEN</);
 assert.equal(context.activityStateMeta({kind:"unknown"}),null);
 assert.match(context.hostGrid.innerHTML,/activity-down/);
 assert.match(context.hostGrid.innerHTML,/offline/);
});
check("real server name and validated IP share the host stamp line",()=>{
 context.hostGrid={innerHTML:"",contains:()=>false};
 context.renderHosts({hosts:[{...healthy,name:"atlas",hostname:"atlas",ip_address:"192.0.2.11"}]});
 assert.match(context.hostGrid.innerHTML,/<strong title="atlas · 192\.0\.2\.11">atlas · 192\.0\.2\.11<\/strong>/);
 context.renderHosts({hosts:[{...healthy,name:"fallback",hostname:"real-name"}]});
 assert.match(context.hostGrid.innerHTML,/<strong title="real-name">real-name<\/strong>/);
});
check("system mount boundaries agree with the collector",()=>{
 for(const mount of ["/bootstrap","/system-data","/projects","/runtime-data","/devices","/snapshots","/var/lib/docker/overlay2-backup"])
  assert.equal(context.isCapacityFilesystem({type:"ext4",mount,total_bytes:100}),true,mount);
 for(const mount of ["/boot","/boot/efi","/sys","/proc","/run","/dev","/snap/core","/var/lib/docker/overlay2/x"])
  assert.equal(context.isCapacityFilesystem({type:"ext4",mount,total_bytes:100}),false,mount);
});
console.log(JSON.stringify({ok:true,checks}));
