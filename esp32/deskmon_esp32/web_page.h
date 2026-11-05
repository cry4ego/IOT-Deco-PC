// Trang cấu hình DeskMon (phục vụ tại http://deskmon.local/). HTML/CSS/JS thuần, không cần Internet.
// Thêm tiện ích: thêm 1 thẻ <section class="card"> + đọc/ghi trong render()/collect().
#pragma once
#include <pgmspace.h>

static const char WEB_PAGE[] PROGMEM = R"DMHTML(<!doctype html>
<html lang="vi"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DeskMon</title>
<style>
:root{--bg:#0b0e14;--card:#141925;--line:#232a3a;--fg:#eef1f6;--dim:#8b93a7;--acc:#38bdf8;--ok:#34d399;--warn:#fbbf24;--bad:#f87171;--in:#0f131c}
@media (prefers-color-scheme:light){:root{--bg:#f3f5f9;--card:#fff;--line:#dde2ec;--fg:#151a24;--dim:#5d6678;--acc:#0284c7;--in:#f7f9fc}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px 16px 96px}
h1{font-size:22px;margin:4px 0 2px}h2{font-size:16px;margin:0 0 12px}
.sub{color:var(--dim);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 14px}
.chip{background:var(--card);border:1px solid var(--line);border-radius:99px;padding:3px 10px;font-size:12.5px;color:var(--dim)}
.chip b{color:var(--fg);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}
.row{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}.row>label{flex:1 1 150px}
label{display:block;font-size:13px;color:var(--dim)}
input,select{width:100%;margin-top:4px;padding:9px 10px;border-radius:9px;border:1px solid var(--line);background:var(--in);color:var(--fg);font:inherit}
input[type=checkbox]{width:auto;margin:0 6px 0 0;accent-color:var(--acc)}input[type=range]{padding:0;accent-color:var(--acc)}
.check{display:flex;align-items:center;color:var(--fg);font-size:14px;padding:8px 0}
button{font:inherit;border:1px solid var(--line);background:var(--in);color:var(--fg);border-radius:9px;padding:8px 14px;cursor:pointer}
button.primary{background:var(--acc);border-color:var(--acc);color:#04121c;font-weight:600}
button.danger{color:var(--bad)}button:disabled{opacity:.5;cursor:default}
.seg{display:flex;border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-top:4px}
.seg button{flex:1;border:0;border-radius:0;background:transparent;color:var(--dim)}
.seg button.on{background:var(--acc);color:#04121c;font-weight:600}
.media{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.media .np{flex:1 1 220px;min-width:0}.np .t{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.media button{font-size:18px;width:52px;height:44px;padding:0}
.list .item{display:grid;gap:8px;grid-template-columns:1fr auto;border-top:1px solid var(--line);padding:10px 0}
.list .item:first-child{border-top:0}
.days{display:flex;gap:4px;flex-wrap:wrap;margin-top:6px}
.days button{padding:4px 8px;font-size:12.5px;color:var(--dim)}.days button.on{background:var(--acc);border-color:var(--acc);color:#04121c}
.banner{display:none;align-items:center;gap:12px;background:var(--warn);color:#1b1300;border-radius:12px;padding:12px 14px;font-weight:600}
.banner span{flex:1}
.bar{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:10px 16px;display:flex;gap:10px;justify-content:center;align-items:center}
.bar .msg{color:var(--dim);font-size:13px;min-width:0;flex:0 1 auto}
progress{width:100%;height:10px;margin-top:8px;accent-color:var(--acc)}
.muted{color:var(--dim);font-size:13px;margin:6px 0 0}
</style></head><body><main>
<h1>DeskMon</h1><div class="sub">Màn lời nhạc + theo dõi PC + đồng hồ để bàn</div>
<div class="chips" id="chips"><span class="chip">Đang tải…</span></div>
<div class="banner" id="remBanner"><span id="remText">Đang nhắc việc</span><button id="remOff">Tắt nhắc</button></div>
<div class="banner" id="apBanner"><span id="apText"></span></div>

<section class="card"><h2>Điều khiển nhạc</h2>
<div class="media"><div class="np"><div class="t" id="npTitle">—</div><div class="sub" id="npSub">PC chưa kết nối</div></div>
<button data-cmd="prev" title="Bài trước">⏮</button><button data-cmd="play_pause" title="Phát / Dừng">⏯</button><button data-cmd="next" title="Bài sau">⏭</button></div></section>

<section class="card"><h2>Đồng hồ &amp; màn hình (khi PC tắt)</h2>
<label>Cỡ đồng hồ</label><div class="seg" id="clockSize"><button data-v="0">Nhỏ</button><button data-v="1">Vừa</button><button data-v="2">Lớn</button></div>
<div class="row" style="margin-top:12px">
<label>Độ sáng ban ngày <b id="bdV"></b><input type="range" id="brightDay" min="5" max="255"></label>
<label>Độ sáng ban đêm <b id="bnV"></b><input type="range" id="brightNight" min="0" max="255"></label></div>
<div class="row" style="margin-top:12px">
<label>Đêm bắt đầu<select id="nightFrom"></select></label><label>Đêm kết thúc<select id="nightTo"></select></label>
<label>Đổi trang màn 1.54" (giây)<input type="number" id="pageSec" min="5" max="120"></label></div>
<p class="muted">Độ sáng ngày/đêm áp dụng cho màn 1.54" cả khi PC bật.</p></section>

<section class="card"><h2>Thời tiết &amp; không khí</h2>
<div class="row"><label>Khu vực<select id="city"></select></label>
<label>Cập nhật mỗi (phút)<input type="number" id="refreshMin" min="5" max="120"></label></div>
<div class="row" id="customPlace" style="margin-top:12px;display:none">
<label>Tên hiển thị<input id="place" maxlength="38"></label><label>Vĩ độ<input type="number" id="lat" step="0.0001"></label><label>Kinh độ<input type="number" id="lon" step="0.0001"></label></div>
<label class="check"><input type="checkbox" id="aqi">Hiện chất lượng không khí (AQI)</label></section>

<section class="card"><h2>Tin tức</h2>
<label class="check"><input type="checkbox" id="newsOn">Bật tin tức (VnExpress)</label>
<div class="row"><label>Khi PC tắt, hiện trên<select id="newsOffline"></select></label><label>Khi PC bật, hiện trên<select id="newsOnline"></select></label></div>
<div class="row"><label class="check"><input type="checkbox" id="newsDomestic">Trong nước</label><label class="check"><input type="checkbox" id="newsWorld">Thế giới</label></div>
<div class="row"><label>Mỗi tin hiện (giây)<input type="number" id="newsItemSec" min="3" max="120"></label>
<label>Nghỉ giữa các lượt (giây)<input type="number" id="newsGapSec" min="0" max="250"></label>
<label>Tải tin mới mỗi (phút)<input type="number" id="newsRefreshMin" min="1" max="120"></label></div>
<p class="muted">Mỗi lượt 3 tin, xong thì màn trở về nội dung thường trong thời gian nghỉ. Đặt nghỉ = 0 để chỉ hiện tin. Khi đang phát nhạc, màn 2.0" ưu tiên lời nhạc; khi PC quá nóng, màn 1.54" ưu tiên thông số.</p></section>

<section class="card"><h2>Sự kiện đếm ngược</h2>
<p class="muted" style="margin-top:0">Ngày lễ Việt Nam (Tết, Trung thu, Quốc khánh…) đã có sẵn. Thêm sinh nhật, kỷ niệm… ở đây.</p>
<div class="list" id="events"></div><button id="addEvent">+ Thêm sự kiện</button></section>

<section class="card"><h2>Nhắc việc</h2>
<p class="muted" style="margin-top:0">Đến giờ, màn 2.0" hiện lời nhắc, màn 1.54" nhấp nháy trong 5 phút. Tắt bằng nút BOOT trên ESP32 hoặc trên trang này.</p>
<div class="list" id="reminders"></div><button id="addReminder">+ Thêm nhắc việc</button></section>

<section class="card"><h2>Máy tính (khi PC bật)</h2>
<div class="row"><label>Lời hiện sớm hơn (giây)<input type="number" id="lyricsLead" step="0.05" min="-2" max="3"></label>
<label>Cảnh báo CPU nóng (°C)<input type="number" id="hotCpu" min="50" max="110"></label>
<label>Cảnh báo GPU nóng (°C)<input type="number" id="hotGpu" min="50" max="110"></label></div>
<div class="row" style="margin-top:6px"><label class="check" style="flex:1 1 260px"><input type="checkbox" id="extraPage">Thêm trang ổ đĩa &amp; mạng trên màn 1.54"</label>
<label>Đổi trang mỗi (giây)<input type="number" id="pcPageSec" min="5" max="120"></label></div></section>

<section class="card" id="wifiCard"><h2>WiFi</h2>
<div class="row"><label>Mạng tìm thấy<select id="wList"><option value="">Bấm "Quét mạng"</option></select></label><button id="scanWifi">Quét mạng</button></div>
<div class="row" style="margin-top:12px"><label>Tên WiFi (2.4 GHz)<input id="wSsid" maxlength="32" autocomplete="off"></label><label>Mật khẩu<input id="wPass" type="password" maxlength="64" autocomplete="new-password"></label></div>
<p class="muted">Lưu xong ESP32 tự khởi động lại. Không vào được mạng đó thì sau 20 giây nó tự phát WiFi <b>DeskMon-Setup</b>, mật khẩu hiện trên màn hình.</p>
<div class="row" style="margin-top:8px"><button id="saveWifi">Lưu WiFi &amp; khởi động lại</button><button id="testAp">Phát thử WiFi cài đặt (3 phút)</button></div></section>

<section class="card"><h2>Cập nhật firmware</h2>
<input type="file" id="fw" accept=".bin"><progress id="fwProg" max="100" value="0" hidden></progress>
<p class="muted">Chọn file <code>.pio/build/esp32dev/firmware.bin</code>. Hoặc nạp thẳng từ VS Code: môi trường <code>ota</code> trong platformio.ini.</p>
<div class="row" style="margin-top:8px"><button id="upload">Nạp firmware</button><button id="reboot" class="danger">Khởi động lại ESP32</button></div></section>
</main>
<div class="bar"><span class="msg" id="msg">Chưa có thay đổi</span><button class="primary" id="save">Lưu cài đặt</button></div>
<script>
const $=s=>document.querySelector(s);
const CITIES=[["Hà Nội",21.0285,105.8542],["TP. Hồ Chí Minh",10.7769,106.7009],["Đà Nẵng",16.0544,108.2022],["Hải Phòng",20.8449,106.6881],["Cần Thơ",10.0452,105.7469],["Huế",16.4637,107.5909],["Nha Trang",12.2388,109.1967],["Đà Lạt",11.9404,108.4583],["Vinh",18.6796,105.6813],["Hạ Long",20.9599,107.0425],["Thái Nguyên",21.5942,105.8482],["Nam Định",20.4388,106.1621],["Thanh Hóa",19.8067,105.7852],["Quy Nhơn",13.782,109.2196],["Buôn Ma Thuột",12.6667,108.05],["Vũng Tàu",10.4114,107.1362],["Biên Hòa",10.9574,106.8429],["Bắc Ninh",21.1861,106.0763],["Lào Cai",22.4856,103.9707],["Sa Pa",22.3364,103.8438]];
const DAYS=[["T2",1],["T3",2],["T4",3],["T5",4],["T6",5],["T7",6],["CN",0]];
const NEWS_SCREENS=[["Không hiện",0],['Màn 2.0"',1],['Màn 1.54"',2],["Cả hai màn",3]];
let cfg=null,dirty=false,wifiFirst=false;
async function api(p,o={}){const r=await fetch(p,o);const t=await r.text();let j={};try{j=t?JSON.parse(t):{}}catch(e){}if(!r.ok)throw new Error(j.error||("Lỗi "+r.status));return j}
const post=(p,b)=>api(p,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})});
function msg(t,bad){const m=$("#msg");m.textContent=t;m.style.color=bad?"var(--bad)":""}
function touch(){dirty=true;msg("Có thay đổi chưa lưu")}
function el(tag,props,kids){const e=document.createElement(tag);Object.assign(e,props||{});(kids||[]).forEach(k=>e.append(k));return e}
function field(label,input){return el("label",{},[label,input])}

function fillSelects(){for(const id of["nightFrom","nightTo"]){const s=$("#"+id);for(let h=0;h<24;h++)s.append(el("option",{value:h,textContent:String(h).padStart(2,"0")+":00"}))}
 for(const id of["newsOffline","newsOnline"])NEWS_SCREENS.forEach(([t,v])=>$("#"+id).append(el("option",{value:v,textContent:t})));
 const c=$("#city");CITIES.forEach((x,i)=>c.append(el("option",{value:i,textContent:x[0]})));c.append(el("option",{value:"custom",textContent:"Tuỳ chỉnh…"}))}
function setSeg(v){document.querySelectorAll("#clockSize button").forEach(b=>b.classList.toggle("on",+b.dataset.v===v))}

function render(){const d=cfg.display,w=cfg.weather,p=cfg.pc,n=cfg.news;setSeg(cfg.clock.size);
 $("#newsOn").checked=n.on;$("#newsOffline").value=n.offline;$("#newsOnline").value=n.online;$("#newsDomestic").checked=n.domestic;$("#newsWorld").checked=n.world;
 $("#newsItemSec").value=n.itemSec;$("#newsGapSec").value=n.gapSec;$("#newsRefreshMin").value=n.refreshMin;
 $("#brightDay").value=d.brightDay;$("#brightNight").value=d.brightNight;$("#bdV").textContent=d.brightDay;$("#bnV").textContent=d.brightNight;
 $("#nightFrom").value=d.nightFrom;$("#nightTo").value=d.nightTo;$("#pageSec").value=d.pageSec;
 const i=CITIES.findIndex(x=>Math.abs(x[1]-w.lat)<.01&&Math.abs(x[2]-w.lon)<.01);$("#city").value=i>=0?i:"custom";$("#customPlace").style.display=i>=0?"none":"flex";
 $("#place").value=w.place;$("#lat").value=w.lat;$("#lon").value=w.lon;$("#refreshMin").value=w.refreshMin;$("#aqi").checked=w.aqi;
 $("#lyricsLead").value=p.lyricsLead;$("#hotCpu").value=p.hotCpu;$("#hotGpu").value=p.hotGpu;$("#extraPage").checked=p.extraPage;$("#pcPageSec").value=p.pageSec;
 renderEvents();renderReminders()}

function renderEvents(){const box=$("#events");box.replaceChildren();
 if(!cfg.events.length)box.append(el("p",{className:"muted",textContent:"Chưa có sự kiện riêng."}));
 cfg.events.forEach((e,i)=>{const name=el("input",{value:e.name,maxLength:38,placeholder:"Tên sự kiện",oninput:()=>{e.name=name.value;touch()}});
  const day=el("input",{type:"number",min:1,max:31,value:e.day,oninput:()=>{e.day=+day.value;touch()}});
  const mon=el("input",{type:"number",min:1,max:12,value:e.month,oninput:()=>{e.month=+mon.value;touch()}});
  const lun=el("input",{type:"checkbox",checked:e.lunar,onchange:()=>{e.lunar=lun.checked;touch()}});
  const del=el("button",{className:"danger",textContent:"Xoá",onclick:()=>{cfg.events.splice(i,1);touch();renderEvents()}});
  box.append(el("div",{className:"item"},[el("div",{className:"row"},[field("Tên",name),field("Ngày",day),field("Tháng",mon),el("label",{className:"check"},[lun,"Âm lịch"])]),del]))})}

function renderReminders(){const box=$("#reminders");box.replaceChildren();
 if(!cfg.reminders.length)box.append(el("p",{className:"muted",textContent:"Chưa có nhắc việc."}));
 cfg.reminders.forEach((r,i)=>{const time=el("input",{type:"time",value:r.time,oninput:()=>{r.time=time.value;touch()}});
  const text=el("input",{value:r.text,maxLength:60,placeholder:"Nội dung nhắc",oninput:()=>{r.text=text.value;touch()}});
  const on=el("input",{type:"checkbox",checked:r.on,onchange:()=>{r.on=on.checked;touch()}});
  const days=el("div",{className:"days"});
  DAYS.forEach(([lb,bit])=>{const b=el("button",{textContent:lb,type:"button"});b.classList.toggle("on",!!(r.days&(1<<bit)));
   b.onclick=()=>{r.days^=(1<<bit);if(!r.days)r.days=1<<bit;b.classList.toggle("on",!!(r.days&(1<<bit)));touch()};days.append(b)});
  const test=el("button",{textContent:"Thử",onclick:()=>post("/api/reminder/test",{text:r.text}).catch(e=>msg(e.message,true))});
  const del=el("button",{className:"danger",textContent:"Xoá",onclick:()=>{cfg.reminders.splice(i,1);touch();renderReminders()}});
  box.append(el("div",{className:"item"},[el("div",{},[el("div",{className:"row"},[field("Giờ",time),field("Nội dung",text),el("label",{className:"check"},[on,"Bật"])]),days]),el("div",{},[test," ",del])]))})}

function num(id,def){const v=parseFloat($("#"+id).value);return isNaN(v)?def:v}
function collect(){const c=$("#city").value,custom=c==="custom",city=custom?null:CITIES[+c];
 return{clock:{size:+document.querySelector("#clockSize button.on").dataset.v},
  display:{brightDay:num("brightDay",200),brightNight:num("brightNight",30),nightFrom:+$("#nightFrom").value,nightTo:+$("#nightTo").value,pageSec:num("pageSec",15)},
  weather:{place:custom?$("#place").value.trim()||"Tuỳ chỉnh":city[0],lat:custom?num("lat",21.0285):city[1],lon:custom?num("lon",105.8542):city[2],refreshMin:num("refreshMin",15),aqi:$("#aqi").checked},
  news:{on:$("#newsOn").checked,offline:+$("#newsOffline").value,online:+$("#newsOnline").value,domestic:$("#newsDomestic").checked,world:$("#newsWorld").checked,itemSec:num("newsItemSec",10),gapSec:num("newsGapSec",60),refreshMin:num("newsRefreshMin",5)},
  events:cfg.events.filter(e=>e.name.trim()),reminders:cfg.reminders.filter(r=>r.time),
  pc:{lyricsLead:num("lyricsLead",.35),hotCpu:num("hotCpu",85),hotGpu:num("hotGpu",85),extraPage:$("#extraPage").checked,pageSec:num("pcPageSec",10)}}}

async function save(){try{$("#save").disabled=true;cfg=await post("/api/config",collect());render();dirty=false;msg("Đã lưu ✓")}catch(e){msg(e.message,true)}finally{$("#save").disabled=false}}

function fmtUp(s){const h=Math.floor(s/3600),m=Math.floor(s%3600/60);return h?`${h} giờ ${m} phút`:`${m} phút`}
async function scanWifi(){const b=$("#scanWifi"),l=$("#wList");b.disabled=true;b.textContent="Đang quét…";
 try{const r=await post("/api/wifi/scan"),seen=new Set();l.replaceChildren(el("option",{value:"",textContent:"Chọn mạng…"}));
  r.networks.sort((x,y)=>y.rssi-x.rssi).forEach(n=>{if(!n.ssid||seen.has(n.ssid))return;seen.add(n.ssid);l.append(el("option",{value:n.ssid,textContent:n.ssid+" ("+n.rssi+" dBm)"}))})}
 catch(e){msg(e.message,true)}finally{b.disabled=false;b.textContent="Quét mạng"}}
function showAp(s){const b=$("#apBanner");b.style.display=s.ap?"flex":"none";if(!s.ap)return;
 $("#apText").textContent="Đang phát WiFi cài đặt "+s.apSsid+(s.staOk?" (đang phát thử)":". Chọn WiFi nhà ở mục WiFi rồi bấm Lưu.");
 if(!s.staOk&&!wifiFirst){wifiFirst=true;b.after($("#wifiCard"));scanWifi()}}
async function poll(){try{const s=await api("/api/status");const chips=[["Chế độ",s.mode],["IP",s.ip],["WiFi",s.staOk?s.rssi+" dBm":"Chưa kết nối"],["Đã chạy",fmtUp(s.uptime)],["Firmware",s.fw]];
 showAp(s);if(s.weather)chips.push(["Thời tiết",s.weather]);if(s.aqi)chips.push(["AQI",s.aqi]);if(s.news)chips.push(["Tin tức",s.news]);
 if(s.reset)chips.push(["Khởi động",s.reset]);if(s.brownouts)chips.push(["Sụt nguồn",s.brownouts+" lần"]);if(s.crashes)chips.push(["Lỗi",s.crashes+" lần"]);
 $("#chips").replaceChildren(...chips.map(([k,v])=>el("span",{className:"chip"},[k+": ",el("b",{textContent:v})])));
 const pc=s.pc||{};$("#npTitle").textContent=pc.connected?(pc.title||"Không có nhạc đang phát"):"—";
 $("#npSub").textContent=pc.connected?((pc.artist?pc.artist+" · ":"")+(pc.source||"")+(pc.title?(pc.playing?" · đang phát":" · tạm dừng"):"")):"PC chưa kết nối";
 document.querySelectorAll("[data-cmd]").forEach(b=>b.disabled=!pc.connected);
 const rb=$("#remBanner");rb.style.display=s.reminder?"flex":"none";if(s.reminder)$("#remText").textContent="⏰ "+s.reminder}catch(e){$("#chips").replaceChildren(el("span",{className:"chip",textContent:"Mất kết nối với DeskMon"}))}}

fillSelects();
document.querySelectorAll("#clockSize button").forEach(b=>b.onclick=()=>{setSeg(+b.dataset.v);touch()});
for(const id of["brightDay","brightNight"])$("#"+id).oninput=e=>{$(id==="brightDay"?"#bdV":"#bnV").textContent=e.target.value;touch()};
document.querySelectorAll("main input:not([type=file]):not(#wSsid):not(#wPass),main select:not(#wList)").forEach(x=>x.addEventListener("change",touch));
$("#city").onchange=()=>{$("#customPlace").style.display=$("#city").value==="custom"?"flex":"none";touch()};
$("#addEvent").onclick=()=>{if(cfg.events.length>=8)return msg("Tối đa 8 sự kiện",true);cfg.events.push({name:"",day:1,month:1,lunar:false});renderEvents();touch()};
$("#addReminder").onclick=()=>{if(cfg.reminders.length>=8)return msg("Tối đa 8 nhắc việc",true);cfg.reminders.push({time:"08:00",days:127,on:true,text:""});renderReminders();touch()};
$("#save").onclick=save;
$("#remOff").onclick=()=>post("/api/reminder/dismiss").then(poll);
document.querySelectorAll("[data-cmd]").forEach(b=>b.onclick=()=>post("/api/media",{cmd:b.dataset.cmd}).then(()=>setTimeout(poll,1500)).catch(e=>msg(e.message,true)));
$("#scanWifi").onclick=scanWifi;
$("#wList").onchange=()=>{if($("#wList").value)$("#wSsid").value=$("#wList").value};
$("#testAp").onclick=()=>post("/api/ap/test").then(()=>msg("Đang phát WiFi cài đặt, xem mã QR trên màn 1.54\"")).catch(e=>msg(e.message,true));
$("#saveWifi").onclick=()=>{const ssid=$("#wSsid").value.trim();if(!ssid)return msg("Nhập tên WiFi",true);
 post("/api/wifi",{ssid,pass:$("#wPass").value}).then(()=>msg("Đã lưu WiFi, ESP32 đang khởi động lại…")).catch(e=>msg(e.message,true))};
$("#reboot").onclick=()=>{if(confirm("Khởi động lại ESP32?"))post("/api/reboot").then(()=>msg("Đang khởi động lại…")).catch(e=>msg(e.message,true))};
$("#upload").onclick=()=>{const f=$("#fw").files[0];if(!f)return msg("Chọn file firmware.bin",true);
 const x=new XMLHttpRequest(),fd=new FormData(),pr=$("#fwProg");fd.append("firmware",f);pr.hidden=false;pr.value=0;
 x.upload.onprogress=e=>{if(e.lengthComputable)pr.value=e.loaded*100/e.total};
 x.onload=()=>msg(x.status===200?"Nạp xong, ESP32 đang khởi động lại…":"Nạp lỗi ("+x.status+")",x.status!==200);
 x.onerror=()=>msg("Mất kết nối khi nạp",true);x.open("POST","/update");x.send(fd)};
window.onbeforeunload=()=>dirty?"Chưa lưu":undefined;
api("/api/config").then(c=>{cfg=c;render()}).catch(e=>msg(e.message,true));
poll();setInterval(poll,3000);
</script></body></html>)DMHTML";
