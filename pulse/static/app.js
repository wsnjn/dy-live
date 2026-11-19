/* ============================================================
   Pulse Console · 前端逻辑（多直播间 / 真实数据）
   ============================================================ */
"use strict";

const $ = (id) => document.getElementById(id);
const C = {
  primary: "#6e7bff", primary2: "#9d7bff", cyan: "#38e1d6",
  coral: "#ff5a5f", amber: "#ffb547", muted: "#8a90a6", dim: "#565d75", rose: "#f472b6",
};
const EVENT_COLOR = { chat: C.primary, gift: C.amber, member: C.muted, like: C.cyan, follow: C.amber, room: C.primary2 };

// 礼物价值（钻石数 diamond_count）由后端从 WebSocket 原始 protobuf 的
// GiftStruct.diamond_count（字段号 6）提取，不再本地硬编码价格表。

function hexToRgba(hex, a) {
  const h = hex.replace("#", "");
  return `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${a})`;
}
function hashStr(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return Math.abs(h >>> 0);
}
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function timeStr(ts) { const d = new Date(ts); const p = n => String(n).padStart(2, "0"); return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds()); }
function fmtDur(sec) { const p = n => String(n).padStart(2, "0"); return p(Math.floor(sec / 3600)) + ":" + p(Math.floor(sec / 60) % 60) + ":" + p(sec % 60); }

// ---------------- 状态（多直播间） ----------------
const State = {
  connected: false, activeId: null, rooms: {}, order: [], startTs: Date.now(),
};
function newRoom(id) {
  return {
    liveId: id, state: "idle", message: "", online: 0, lastOnlineTs: 0,
    counters: { chat: 0, gift: 0, giftValue: 0, like: 0, follow: 0, member: 0 },
    buckets: [], words: {}, viewers: {}, timeline: [], danmaku: [], lastDm: null,
  };
}
function getRoom(id) {
  if (!State.rooms[id]) { State.rooms[id] = newRoom(id); State.order.push(id); }
  return State.rooms[id];
}
function activeRoom() { return State.activeId ? State.rooms[State.activeId] : null; }

function ensureBucket(room) {
  const now = Math.floor(Date.now() / 1000);
  const last = room.buckets[room.buckets.length - 1];
  if (!last || last.t < now) {
    room.buckets.push({ t: now, chat: 0, gift: 0, like: 0, member: 0, online: 0 });
    if (room.buckets.length > 120) room.buckets.shift();
  }
}
function curBucket(room) { ensureBucket(room); return room.buckets[room.buckets.length - 1]; }
function lastN(room, n) { return room.buckets.slice(-n); }
function bucketHeat(b) { return (b.chat || 0) + (b.gift || 0) * 3 + (b.like || 0) + (b.member || 0); }
function ratePerMin(room) { return lastN(room, 60).reduce((s, b) => s + (b.chat || 0), 0); }

function extractWords(text) {
  const words = [];
  const parts = (text || "").split(/[^\u4e00-\u9fa5a-zA-Z0-9]+/);
  for (const p of parts) {
    if (!p) continue;
    if (/^[\u4e00-\u9fa5]+$/.test(p)) {
      if (p.length <= 4) words.push(p);
      else for (let i = 0; i < p.length - 1; i++) words.push(p.slice(i, i + 2));
    } else if (p.length >= 2) words.push(p.toLowerCase());
  }
  return words;
}

// ---------------- 几何头像 / 神秘人 ----------------
const SHAPES = ["circle", "square", "triangle", "diamond", "hex", "ring"];
function avatarSVG(key, isAnonymous) {
  if (isAnonymous) {
    return '<svg width="30" height="30" viewBox="0 0 30 30" xmlns="http://www.w3.org/2000/svg">' +
      '<rect x="3" y="3" width="24" height="24" rx="7" fill="#232840"/>' +
      '<circle cx="15" cy="14" r="7" fill="#454c6e"/>' +
      '<circle cx="12" cy="13" r="1.4" fill="#0f141e"/><circle cx="18" cy="13" r="1.4" fill="#0f141e"/>' +
      '<path d="M11.5 17.5 Q15 20 18.5 17.5" stroke="#0f141e" stroke-width="1.4" fill="none"/>' +
      '<text x="15" y="27.5" font-size="7" text-anchor="middle" fill="#8a90a6">神秘</text></svg>';
  }
  const h = hashStr(key || "u");
  const shape = SHAPES[h % SHAPES.length];
  const hue = (h * 37) % 360;
  const c1 = `hsl(${hue},72%,62%)`, c2 = `hsl(${(hue + 45) % 360},72%,48%)`;
  const gid = "av" + h;
  const initial = (key || "?").slice(0, 1).toUpperCase();
  let path = "";
  if (shape === "circle") path = '<circle cx="15" cy="15" r="12"/>';
  else if (shape === "square") path = '<rect x="4" y="4" width="22" height="22" rx="4"/>';
  else if (shape === "triangle") path = '<polygon points="15,3 27,27 3,27"/>';
  else if (shape === "diamond") path = '<polygon points="15,2 28,15 15,28 2,15"/>';
  else if (shape === "hex") path = '<polygon points="15,3 26,9.5 26,20.5 15,27 4,20.5 4,9.5"/>';
  else path = '<circle cx="15" cy="15" r="11" fill="none" stroke="url(#' + gid + ')" stroke-width="5"/>';
  return '<svg width="30" height="30" viewBox="0 0 30 30" xmlns="http://www.w3.org/2000/svg">' +
    '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="1" y2="1">' +
    '<stop offset="0" stop-color="' + c1 + '"/><stop offset="1" stop-color="' + c2 + '"/>' +
    '</linearGradient></defs>' + path +
    '<text x="15" y="19" font-size="11" text-anchor="middle" fill="#fff" font-family="Inter,sans-serif" font-weight="600">' + initial + '</text></svg>';
}

// ---------------- 粉丝画像 ----------------
function isMystery(u) { return !!(u.is_anonymous || u.mystery_man); }
function displayName(u) { return isMystery(u) ? (u.desensitized_nickname || "神秘人") : (u.nickname || "神秘人"); }
function fanClubLevel(u) {
  for (const b of (u.badges || [])) {
    const t = ((b.name || "") + (b.alt || "")).toLowerCase();
    if (/粉丝团|粉丝牌|灯牌|fans|club|粉丝/.test(t) && b.level) return b.level;
  }
  return null;
}
function roleBadges(u) {
  const out = [];
  const raw = ((u.badges || []).map(b => (b.name || "") + (b.alt || "")).join(" ").toLowerCase());
  if (/舰长|船长/.test(raw)) out.push({ cls: "captain", text: "舰长" });
  else if (/总督/.test(raw)) out.push({ cls: "captain", text: "总督" });
  else if (/提督/.test(raw)) out.push({ cls: "captain", text: "提督" });
  if (/房管|管理|moderator/.test(raw)) out.push({ cls: "moderator", text: "房管" });
  if (/铁粉|iron/.test(raw)) out.push({ cls: "moderator", text: "铁粉" });
  return out;
}
function viewerKey(u) { return u.sec_uid || ("nick:" + (u.nickname || "匿名")); }

function upsertViewer(room, u, score) {
  const key = viewerKey(u);
  let v = room.viewers[key];
  if (!v) {
    v = room.viewers[key] = {
      key, nickname: u.nickname || "", sec_uid: u.sec_uid || "",
      mystery: isMystery(u), desensitized: u.desensitized_nickname || "",
      level: 0, isFollower: false, fanClub: 0, roles: [],
      totalRecharge: 0, payScore: 0, consumeLevel: 0, watchMonth: 0,
      chatCount: 0, giftCount: 0, giftValue: 0, gifts: {}, likeCount: 0, lastEvent: "", lastTs: 0, score: 0,
    };
  }
  v.nickname = u.nickname || v.nickname;
  v.mystery = v.mystery || isMystery(u);
  v.desensitized = u.desensitized_nickname || v.desensitized;
  v.level = u.level || v.level;
  v.isFollower = v.isFollower || !!u.is_follower;
  v.fanClub = fanClubLevel(u) || v.fanClub;
  v.roles = roleBadges(u);
  v.totalRecharge = u.total_recharge_diamond_count || v.totalRecharge;
  v.payScore = u.pay_score || u.pay_scores || v.payScore;
  v.consumeLevel = u.consume_diamond_level || v.consumeLevel;
  v.watchMonth = u.watch_duration_month || v.watchMonth;
  v.score += score;
  v.lastTs = Date.now();
  return v;
}
function importance(v) {
  let s = v.giftValue * 10 + v.score;
  if (v.fanClub) s += 500;
  if (v.isFollower) s += 200;
  return s;
}

// ---------------- 事件摄入 ----------------
function ingest(ev, liveId) {
  ev = ev || {};
  const room = getRoom(liveId);
  const t = ev.type, u = ev.user || {};
  ensureBucket(room);
  if (t === "chat") {
    room.counters.chat++; curBucket(room).chat++;
    for (const w of extractWords(ev.content)) room.words[w] = (room.words[w] || 0) + 1;
    const v = upsertViewer(room, u, 1); v.chatCount++; v.lastEvent = "💬 " + (ev.content || "");
  } else if (t === "gift") {
    const gname = ev.gift_name || "礼物";
    const gcount = ev.gift_count || 1;
    const gval = ev.diamond_count || 0;   // 真实钻石数（价值），来自后端
    room.counters.gift++; room.counters.giftValue += gval * gcount;
    curBucket(room).gift++;
    const v = upsertViewer(room, u, 2 + gcount);
    v.giftCount += gcount;
    v.giftValue += gval * gcount;
    const g = v.gifts[gname] || (v.gifts[gname] = { count: 0, value: gval });
    g.count += gcount; g.value = gval;
    v.lastEvent = "🎁 " + gname + " ×" + gcount;
  } else if (t === "like") {
    room.counters.like++; curBucket(room).like++;
    const v = upsertViewer(room, u, 1); v.likeCount += (ev.count || 1);
    v.lastEvent = "❤️ 点赞 ×" + (ev.count || 0);
  } else if (t === "member") {
    room.counters.member++; curBucket(room).member++;
    upsertViewer(room, u, 1).lastEvent = "👤 进场";
  } else if (t === "follow") {
    room.counters.follow++;
    const v = upsertViewer(room, u, 2); v.isFollower = true; v.lastEvent = "⭐ 关注了主播";
  } else if (t === "room") {
    const m = /(\d+)/.exec(ev.display || "");
    if (m) { room.online = parseInt(m[1], 10); room.lastOnlineTs = Date.now(); }
  }
  pushTimeline(room, ev);
  appendDanmaku(room, ev);
  pulseIsland();
}

// ---------------- 时间线 ----------------
function pushTimeline(room, ev) {
  const u = ev.user || {};
  const nick = displayName(u);
  let text = "", color = EVENT_COLOR[ev.type] || C.muted;
  if (ev.type === "chat") text = `<b>${esc(nick)}</b> : ${esc(ev.content || "")}`;
  else if (ev.type === "gift") text = `<b>${esc(nick)}</b> 送出 💎${esc(ev.gift_name || "礼物")} ×${ev.gift_count || 1}`;
  else if (ev.type === "member") text = `<b>${esc(nick)}</b> 进入直播间`;
  else if (ev.type === "like") text = `<b>${esc(nick)}</b> 点赞 ×${ev.count || 0}`;
  else if (ev.type === "follow") text = `<b>${esc(nick)}</b> 关注了主播 🎉`;
  else if (ev.type === "room") text = `房间：${esc(ev.display || "")}`;
  else return;
  room.timeline.push({ ts: Date.now(), text, color });
  if (room.timeline.length > 120) room.timeline.shift();
}
function renderTimeline(room) {
  const box = $("timelineList");
  if (!room) { box.innerHTML = ""; return; }
  box.innerHTML = room.timeline.map(it =>
    `<div class="tl-item"><span class="tl-dot" style="background:${it.color};box-shadow:0 0 8px ${it.color}"></span>` +
    `<span class="tl-time">${timeStr(it.ts)}</span><span class="tl-text">${it.text}</span></div>`).join("");
  box.scrollTop = box.scrollHeight;
}

// ---------------- 弹幕流 ----------------
function appendDanmaku(room, ev) {
  const u = ev.user || {}, key = viewerKey(u);
  const last = room.danmaku[room.danmaku.length - 1];
  if (ev.type === "chat" && last && last.key === key && last.content === ev.content && Date.now() - last.ts < 6000) {
    last.count++; return;
  }
  room.danmaku.push({ key, content: ev.content, ev, ts: Date.now(), count: 1 });
  if (room.danmaku.length > 300) room.danmaku.shift();
  appendMini(room, ev);
}
function dmHTML(item) {
  const ev = item.ev, u = ev.user || {};
  const key = viewerKey(u), nick = displayName(u);
  let content = "";
  if (ev.type === "chat") content = `<span class="dm-content">${esc(ev.content || "")}</span>` + (item.count > 1 ? `<span class="dm-count"> ×${item.count}</span>` : "");
  else if (ev.type === "gift") content = `<span class="dm-content gift-txt">💎 ${esc(ev.gift_name || "礼物")} ×${ev.gift_count || 1} → ${esc((ev.to_user || {}).nickname || "主播")}</span>`;
  else if (ev.type === "member") content = `<span class="dm-content">进入直播间</span>`;
  else if (ev.type === "like") content = `<span class="dm-content">点赞 ×${ev.count || 0}</span>`;
  else if (ev.type === "follow") content = `<span class="dm-content">关注了主播 🎉</span>`;
  else if (ev.type === "room") content = `<span class="dm-content">房间热度：${esc(ev.display || "")}</span>`;
  const meta = u.sec_uid ? `<div class="dm-meta">${esc(u.sec_uid)}</div>` : "";
  return `<div class="dm${ev.type === "gift" ? " gift" : ""}"><div class="avatar">${avatarSVG(key, isMystery(u))}</div><div class="dm-body"><div class="dm-line"><span class="dm-nick">${esc(nick)}</span></div>${content}${meta}</div><span class="dm-time">${timeStr(item.ts)}</span></div>`;
}
function renderDanmaku(room) {
  const list = $("danmakuList");
  const follow = $("danmakuFollow").dataset.on === "1";
  const prev = list.scrollTop;
  list.innerHTML = room ? room.danmaku.map(dmHTML).join("") : "";
  if (follow) list.scrollTop = list.scrollHeight; else list.scrollTop = Math.min(prev, list.scrollHeight);
}

// ---------------- 迷你弹幕窗 ----------------
function appendMini(room, ev) {
  const box = $("miniList");
  if ($("miniWindow").hidden) return;
  const u = ev.user || {}, nick = displayName(u);
  let html;
  if (ev.type === "chat") html = `<b>${esc(nick)}</b> : ${esc(ev.content || "")}`;
  else if (ev.type === "gift") html = `<b>${esc(nick)}</b> 送出 <span class="gift">💎${esc(ev.gift_name || "礼物")} ×${ev.gift_count || 1}</span>`;
  else if (ev.type === "member") html = `<b>${esc(nick)}</b> 进入直播间`;
  else if (ev.type === "like") html = `<b>${esc(nick)}</b> 点赞 ×${ev.count || 0}`;
  else if (ev.type === "follow") html = `<b>${esc(nick)}</b> 关注了主播 🎉`;
  else return;
  const n = document.createElement("div");
  n.className = "mini-msg"; n.innerHTML = html;
  box.appendChild(n);
  while (box.children.length > 60) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

// ---------------- 直播间标签页 ----------------
function renderTabs() {
  const box = $("roomTabs");
  if (!State.order.length) { box.innerHTML = ""; return; }
  box.innerHTML = State.order.map(id => {
    const r = State.rooms[id];
    const st = r.state;
    return `<div class="room-tab ${id === State.activeId ? "active" : ""} ${st}" data-id="${id}">` +
      `<span class="rt-dot"></span><span>直播间 ${esc(id)}</span>` +
      `<button class="rt-close" data-close="${id}">✕</button></div>`;
  }).join("");
}

// ---------------- KPI ----------------
function easeOutExpo(p) { return p === 1 ? 1 : 1 - Math.pow(2, -10 * p); }
function animateNumber(el, to, dur) {
  const from = parseFloat(el.dataset.v || "0");
  if (from === to) { el.dataset.v = to; return; }
  el.dataset.v = to;
  const t0 = performance.now();
  function step(t) {
    const p = Math.min(1, (t - t0) / (dur || 600));
    const val = from + (to - from) * easeOutExpo(p);
    el.textContent = Math.round(val).toLocaleString("zh-CN");
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
  el.parentElement.classList.remove("flash");
  void el.parentElement.offsetWidth;
  el.parentElement.classList.add("flash");
}
function renderKPI(room) {
  if (!room) return;
  const viewers = Object.values(room.viewers);
  const fans = viewers.filter(v => v.isFollower).length;
  const paid = viewers.filter(v => v.totalRecharge || v.payScore).length;
  animateNumber($("kpiOnlineVal"), room.online, 400);
  animateNumber($("kpiFanVal"), fans, 400);
  animateNumber($("kpiGiftVal"), room.counters.giftValue || 0, 400);
  animateNumber($("kpiChatVal"), room.counters.chat, 400);
  animateNumber($("kpiPaidVal"), paid, 400);
  $("kpiOnlineSub").textContent = room.online ? "● 直播中" : "未取到在线";
  $("kpiFanSub").textContent = viewers.length ? "占互动 " + Math.round(fans / viewers.length * 100) + "%" : "--";
  $("kpiGiftSub").textContent = "本场礼物 " + room.counters.gift + " 件 · " + (room.counters.giftValue || 0) + " 钻";
  $("kpiChatSub").textContent = "本场弹幕 " + room.counters.chat;
  $("kpiPaidSub").textContent = paid ? "付费 " + paid + " 人" : "暂无付费";
  $("imsRate").textContent = ratePerMin(room);
  $("imsOnline").textContent = room.online || "--";
  $("imsGift").textContent = room.counters.gift;
}

// ---------------- 观众/粉丝明细 ----------------
function giftSummary(v) {
  const entries = Object.entries(v.gifts).sort((a, b) => b[1].value - a[1].value);
  if (!entries.length) return '<span class="cell-dim">—</span>';
  const parts = entries.slice(0, 3).map(([name, g]) => `${esc(name)}×${g.count}`);
  const more = entries.length > 3 ? ` +${entries.length - 3}` : "";
  const title = entries.map(([name, g]) => `${name}×${g.count}(${g.value * g.count}钻)`).join(" · ");
  return `<span class="gift-summary" title="${esc(title)}">${parts.join(" ") + more}</span>`;
}
function viewerRow(v) {
  const name = v.mystery ? (v.desensitized || "神秘人") : (v.nickname || "神秘人");
  const nameCls = v.mystery ? "v-name mystery" : "v-name";
  const badges = [];
  if (v.isFollower) badges.push('<span class="badge fan">粉丝</span>');
  if (v.totalRecharge || v.payScore) badges.push('<span class="badge paid">付费</span>');
  if (v.watchMonth >= 60) badges.push('<span class="badge often">常看</span>');
  for (const r of v.roles) badges.push(`<span class="badge ${r.cls}">${esc(r.text)}</span>`);
  const level = v.level ? `<span class="lv">Lv${v.level}</span>` : '<span class="cell-dim">—</span>';
  const lampCell = v.fanClub ? `<span class="badge fanclub">灯牌 Lv${v.fanClub}</span>` : '<span class="cell-dim">—</span>';
  const contrib = v.giftValue ? `<span class="contrib">${v.giftValue.toLocaleString()}</span>` : '<span class="cell-dim">0</span>';
  const giftCell = giftSummary(v);
  const chatCell = v.chatCount ? `<span class="act">💬${v.chatCount}</span>` : '<span class="cell-dim">0</span>';
  const last = `<span class="v-last" title="${esc(v.lastEvent)}">${esc(v.lastEvent)}</span>`;
  const paidRow = v.giftValue ? " paid-row" : "";
  return `<div class="viewer-row${paidRow}"><div class="v-user"><span class="avatar">${avatarSVG(v.sec_uid || v.nickname, v.mystery)}</span><span class="${nameCls}">${esc(name)}</span><span class="v-badges">${badges.join("")}</span></div>` +
    `<span>${level}</span><span>${lampCell}</span><span>${contrib}</span><span>${giftCell}</span>` +
    `<span>${chatCell}</span>${last}</div>`;
}
const ONLINE_WINDOW_MS = 10 * 60 * 1000; // 在线判定：10 分钟内有互动
function renderViewers(room) {
  const box = $("viewerList");
  if (!room) { box.innerHTML = ""; return; }
  const now = Date.now();
  const list = Object.values(room.viewers)
    .filter(v => now - v.lastTs < ONLINE_WINDOW_MS)
    .sort((a, b) => (b.giftValue - a.giftValue) || (b.score - a.score));
  if (!list.length) { box.innerHTML = '<div class="queue-placeholder">暂无在线观众 · 等待弹幕 / 礼物 / 进场事件</div>'; return; }
  box.innerHTML = list.map(viewerRow).join("");
}

// ---------------- 词云 ----------------
function renderWordCloud(room) {
  const box = $("wordcloud");
  const w = box.clientWidth || 260, h = box.clientHeight || 180;
  const entries = room ? Object.entries(room.words).sort((a, b) => b[1] - a[1]).slice(0, 60) : [];
  box.innerHTML = "";
  if (!entries.length) { box.innerHTML = '<div class="queue-placeholder">等待弹幕...</div>'; return; }
  const max = entries[0][1];
  const placed = [], cx = w / 2, cy = h / 2;
  for (const [word, count] of entries) {
    const size = 13 + (count / max) * 26;
    const hue = (hashStr(word) * 31) % 360;
    const alpha = 0.45 + (count / max) * 0.55;
    const span = document.createElement("span");
    span.className = "word"; span.textContent = word;
    span.style.fontSize = size + "px";
    span.style.color = `hsla(${hue},78%,72%,${alpha})`;
    const cw = /^[\u4e00-\u9fa5]/.test(word) ? size : size * 0.6;
    const bw = word.length * cw, bh = size * 1.15;
    let x = cx - bw / 2, y = cy - bh / 2;
    for (let i = 0; i < 520; i++) {
      const ang = i * 0.4, r = 4 + i * 1.7;
      const px = cx + Math.cos(ang) * r - bw / 2, py = cy + Math.sin(ang) * r - bh / 2;
      if (px < 3 || py < 3 || px + bw > w - 3 || py + bh > h - 3) continue;
      if (!placed.some(p => px < p.x + p.w && px + bw > p.x && py < p.y + p.h && py + bh > p.y)) { x = px; y = py; break; }
    }
    span.style.left = x + "px"; span.style.top = y + "px";
    placed.push({ x, y, w: bw, h: bh });
    box.appendChild(span);
  }
}

// ---------------- Canvas 绘制 ----------------
function setupCanvas(cv) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || cv.parentElement.clientWidth || 300;
  const h = cv.clientHeight || 100;
  cv.width = Math.max(1, Math.round(w * dpr));
  cv.height = Math.max(1, Math.round(h * dpr));
  cv.style.width = w + "px"; cv.style.height = h + "px";
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}
function drawTrend(room) {
  const cv = $("trendCanvas");
  const { ctx, w, h } = setupCanvas(cv);
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(140,150,200,0.08)"; ctx.lineWidth = 1;
  for (let i = 1; i < 4; i++) { const y = i / 4 * h; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
  const data = room ? lastN(room, 60) : [];
  if (!data.length) return;
  const mx = Math.max(1, ...data.map(b => b.chat || 0));
  const mg = Math.max(1, ...data.map(b => b.gift || 0));
  const mo = Math.max(1, ...data.map(b => b.online || 0));
  const line = (key, max, color, fill) => {
    ctx.beginPath();
    data.forEach((b, i) => {
      const x = i / 59 * w, y = h - 1 - ((b[key] || 0) / max) * (h - 6);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    if (fill) {
      ctx.save();
      ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
      const g = ctx.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, hexToRgba(color, 0.3)); g.addColorStop(1, hexToRgba(color, 0));
      ctx.fillStyle = g; ctx.fill(); ctx.restore();
      ctx.beginPath();
      data.forEach((b, i) => { const x = i / 59 * w, y = h - 1 - ((b[key] || 0) / max) * (h - 6); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    }
    ctx.strokeStyle = color; ctx.lineWidth = 1.8; ctx.lineJoin = "round"; ctx.stroke();
  };
  line("chat", mx, C.cyan, true);
  line("gift", mg, C.amber, false);
  line("online", mo, C.primary, false);
}
function drawWave(room) {
  const cv = $("waveCanvas");
  const { ctx, w, h } = setupCanvas(cv);
  ctx.clearRect(0, 0, w, h);
  const data = room ? lastN(room, 60).map(b => b.chat || 0) : [];
  const max = Math.max(1, ...data);
  const bw = w / 60;
  data.forEach((v, i) => {
    const bh = Math.max(2, (v / max) * (h - 2));
    ctx.fillStyle = hexToRgba(C.cyan, 0.2 + 0.75 * (v / max));
    ctx.fillRect(i * bw + 0.5, h - bh, Math.max(1, bw - 1.5), bh);
  });
}

// ---------------- Toast ----------------
function toast(msg) {
  let t = $("toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2400);
}

// ---------------- WebSocket 桥接 ----------------
const WS_URL = (location.protocol === "https:" ? "wss" : "ws") + "://" + (location.hostname || "localhost") + ":8766/ws";
let ws = null;
function connectWS() {
  try {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => { State.connected = true; updateConnUI(); try { ws.send(JSON.stringify({ type: "rooms" })); } catch (_) {} };
    ws.onmessage = (e) => { try { handleServerMsg(JSON.parse(e.data)); } catch (_) {} };
    ws.onclose = () => { State.connected = false; updateConnUI(); setTimeout(connectWS, 2000); };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  } catch (e) { setTimeout(connectWS, 2000); }
}
function send(obj) { if (ws && ws.readyState === 1) try { ws.send(JSON.stringify(obj)); } catch (_) {} }

function statusText(s) {
  return { listening: "监听中", connecting: "连接中", error: "异常", on: "已连接", off: "未连接" }[s] || s;
}
function activeStatus() {
  const r = activeRoom();
  if (!r) return State.connected ? "on" : "off";
  return r.state === "listening" ? "listening" : r.state === "connecting" ? "connecting" : r.state === "error" ? "error" : (State.connected ? "on" : "off");
}
function handleServerMsg(m) {
  if (m.type === "hello" || m.type === "rooms") {
    (m.rooms || []).forEach(r => { const room = getRoom(r.live_id); room.state = r.state; room.message = r.message; });
    if (!State.activeId && State.order.length) State.activeId = State.order[0];
    renderTabs(); updateConnUI();
  } else if (m.type === "status") {
    const room = getRoom(m.live_id);
    room.state = m.state; room.message = m.message;
    if (!State.activeId) State.activeId = m.live_id;
    renderTabs(); updateConnUI();
    if (m.state === "error") showAlert(m.message);
  } else if (m.type === "error") {
    showAlert(m.message); toast(m.message);
  } else if (m.type === "event") {
    if (!State.activeId) State.activeId = m.live_id;
    ingest(m.data, m.live_id);
  }
}

function togglePanels() {
  const has = !!activeRoom();
  $("emptyState").hidden = has;
  $("kpiGrid").hidden = !has;
  $("viewerCard").hidden = !has;
  $("danmakuCard").hidden = !has;
  $("trendCard").hidden = !has;
  $("cloudCard").hidden = !has;
}
function updateConnUI() {
  const r = activeRoom();
  const cls = activeStatus();
  $("islandDot").className = "status-dot " + cls;
  $("liveStatusLight").className = "status-dot sm " + cls;
  $("sbProtoDot").style.color = cls === "error" ? C.coral : (State.connected ? C.cyan : C.dim);
  const shell = $("islandShell");
  if (cls === "connecting") shell.classList.add("connecting"); else shell.classList.remove("connecting");
  if (r) {
    $("islandTitle").textContent = "直播间 " + r.liveId;
    $("islandSub").textContent = statusText(cls);
  } else {
    $("islandTitle").textContent = "Pulse Console";
    $("islandSub").textContent = State.connected ? "已连接" : "未连接";
  }
  $("sbProtocol").textContent = State.connected ? WS_URL.replace(/^wss?:\/\//, "").replace("/ws", "") : "未连接";
  $("liveStatusText").textContent = r ? (r.message || statusText(cls)) : (State.connected ? "已连接，等待监听" : "未连接");
  $("infoWs").textContent = WS_URL;
  $("infoProto").textContent = location.protocol === "https:" ? "wss" : "ws";
  $("infoRooms").textContent = State.order.length;
  $("sbRooms").textContent = State.order.length;
  togglePanels();
}
function refreshActive() {
  renderTabs();
  updateConnUI();
  const r = activeRoom();
  if (r) { renderKPI(r); renderViewers(r); renderDanmaku(r); renderTimeline(r); renderWordCloud(r); drawTrend(r); drawWave(r); }
}

// ---------------- 灵动岛 ----------------
let islandMode = "idle";
function setIslandMode(mode) {
  islandMode = mode;
  const shell = $("islandShell");
  shell.classList.toggle("expanded", mode === "wave");
  shell.classList.toggle("alert", mode === "alert");
  $("islandIdle").hidden = mode !== "idle";
  $("islandWave").hidden = mode !== "wave";
  $("islandAlert").hidden = mode !== "alert";
}
function pulseIsland() {
  if (islandMode === "idle") {
    setIslandMode("wave");
    clearTimeout(pulseIsland._t);
    pulseIsland._t = setTimeout(() => { if (islandMode === "wave") setIslandMode("idle"); }, 1700);
  }
}
function showAlert(msg) { $("islandAlertText").textContent = msg || "连接异常"; setIslandMode("alert"); }
function initIsland() {
  const shell = $("islandShell");
  shell.addEventListener("mouseenter", () => { if (islandMode === "idle") setIslandMode("wave"); });
  shell.addEventListener("mouseleave", () => { if (islandMode === "wave") setIslandMode("idle"); });
  shell.addEventListener("click", (e) => {
    if (e.target.id === "islandAlertBtn") { setIslandMode("idle"); if (State.activeId) send({ type: "start", live_id: State.activeId }); return; }
    $("overlay").hidden = false;
  });
  $("overlayClose").addEventListener("click", () => $("overlay").hidden = true);
  $("overlay").addEventListener("click", (e) => { if (e.target.id === "overlay") $("overlay").hidden = true; });
}

// ---------------- 导航 ----------------
function switchPanel(name) {
  document.querySelectorAll(".panel").forEach(p => p.hidden = (p.id !== "panel-" + name));
  document.querySelectorAll(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.panel === name));
}
function initNav() {
  document.querySelectorAll(".nav-item").forEach(btn => {
    btn.addEventListener("click", () => {
      if (btn.classList.contains("locked")) { toast("该模块将在后续阶段解锁"); return; }
      switchPanel(btn.dataset.panel);
    });
  });
}

// ---------------- 房间标签页事件 ----------------
function initTabs() {
  $("roomTabs").addEventListener("click", (e) => {
    const close = e.target.closest(".rt-close");
    const tab = e.target.closest(".room-tab");
    if (close) {
      const id = close.dataset.close;
      send({ type: "stop", live_id: id });
      delete State.rooms[id];
      State.order = State.order.filter(x => x !== id);
      if (State.activeId === id) State.activeId = State.order[0] || null;
      renderTabs(); updateConnUI(); return;
    }
    if (tab) { State.activeId = tab.dataset.id; refreshActive(); }
  });
}

// ---------------- 右栏 / 弹幕工具 ----------------
function parseLiveId(raw) {
  raw = (raw || "").trim();
  if (!raw) return "";
  const m = /\d{5,}/.exec(raw);
  return m ? m[0] : raw;
}
function initRightPanel() {
  $("liveStartBtn").addEventListener("click", () => {
    const id = parseLiveId($("liveInput").value);
    if (!id) { toast("请输入直播间链接 / ID"); return; }
    $("liveInput").value = "";
    State.activeId = id; getRoom(id);
    send({ type: "start", live_id: id });
    refreshActive();
  });
  $("liveStopBtn").addEventListener("click", () => { if (State.activeId) send({ type: "stop", live_id: State.activeId }); });
  $("btnClear").addEventListener("click", clearData);
  $("btnExport").addEventListener("click", exportLog);
  $("btnShot").addEventListener("click", screenshotBoard);
  $("btnMini").addEventListener("click", () => { $("miniWindow").hidden = !$("miniWindow").hidden; });
  $("aiAvatarShortcut").addEventListener("click", () => switchPanel("ai-avatar"));
  $("companionShortcut").addEventListener("click", () => switchPanel("companion"));

  $("danmakuFollow").addEventListener("click", function () {
    const on = this.dataset.on !== "1"; this.dataset.on = on ? "1" : "0";
    this.textContent = on ? "⏸ 暂停跟随" : "▶ 跟随最新";
  });
  $("danmakuBottom").addEventListener("click", () => { const l = $("danmakuList"); l.scrollTop = l.scrollHeight; });
  $("danmakuClear").addEventListener("click", () => { const r = activeRoom(); if (r) { r.danmaku = []; renderDanmaku(r); } });
}

// ---------------- 主题开关 ----------------
function flipSwitch(id) { $(id).click(); }
function bindSwitch(id, onChange, initial) {
  const el = $(id);
  el.addEventListener("click", () => { const v = el.dataset.on !== "1"; el.dataset.on = v ? "1" : "0"; onChange(v); });
  if (initial != null) el.dataset.on = initial ? "1" : "0";
}
let particlesOn = true, soundOn = false;
function playSound() {
  if (!soundOn) return;
  try {
    const ctx = window._ac || (window._ac = new (window.AudioContext || window.webkitAudioContext)());
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = "sine"; o.frequency.value = 720; g.gain.value = 0.03;
    o.connect(g); g.connect(ctx.destination);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.12);
    o.start(); o.stop(ctx.currentTime + 0.12);
  } catch (_) {}
}
function initToggles() {
  bindSwitch("setGlass", v => document.body.classList.toggle("glass-off", !v), true);
  bindSwitch("setParticles", v => { particlesOn = v; $("particles").style.display = v ? "" : "none"; }, true);
  bindSwitch("setWarm", v => { document.body.classList.toggle("warm", v); $("islandShell").classList.toggle("warm", v); }, false);
  bindSwitch("setSound", v => { soundOn = v; if (v) playSound(); }, false);
  $("companionWarmBtn").addEventListener("click", () => flipSwitch("setWarm"));
  $("ctxTheme").addEventListener("click", () => flipSwitch("setWarm"));
  $("ctxGlass").addEventListener("click", () => flipSwitch("setGlass"));
  $("ctxParticles").addEventListener("click", () => flipSwitch("setParticles"));
  $("ctxLog").addEventListener("click", exportLog);
  $("sbLog").addEventListener("click", exportLog);
}

// ---------------- 背景粒子流 ----------------
const P = [];
function initParticles() {
  const cv = $("particles");
  const dpr = window.devicePixelRatio || 1;
  const resize = () => { cv.width = innerWidth * dpr; cv.height = innerHeight * dpr; cv.style.width = innerWidth + "px"; cv.style.height = innerHeight + "px"; };
  resize(); window.addEventListener("resize", resize);
  for (let i = 0; i < 70; i++) P.push({ x: Math.random() * innerWidth, y: Math.random() * innerHeight, r: Math.random() * 1.6 + 0.4, vx: (Math.random() - 0.5) * 0.14, vy: (Math.random() - 0.5) * 0.14, a: Math.random() * 0.45 + 0.15 });
  const ctx = cv.getContext("2d");
  (function loop() {
    if (particlesOn) {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cv.width, cv.height);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      for (const p of P) {
        p.x += p.vx; p.y += p.vy;
        if (p.x < -10) p.x = innerWidth + 10; if (p.x > innerWidth + 10) p.x = -10;
        if (p.y < -10) p.y = innerHeight + 10; if (p.y > innerHeight + 10) p.y = -10;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(157,123,255," + p.a + ")"; ctx.fill();
      }
    }
    requestAnimationFrame(loop);
  })();
}

// ---------------- 迷你弹幕窗拖拽 ----------------
function initMiniDrag() {
  const win = $("miniWindow"), head = $("miniHead");
  let sx, sy, ox, oy, drag = false;
  head.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".mini-chip")) return;
    drag = true; sx = e.clientX; sy = e.clientY;
    const r = win.getBoundingClientRect(); ox = r.left; oy = r.top;
    head.setPointerCapture(e.pointerId);
  });
  head.addEventListener("pointermove", (e) => {
    if (!drag) return;
    win.style.left = (ox + e.clientX - sx) + "px";
    win.style.top = (oy + e.clientY - sy) + "px";
    win.style.right = "auto"; win.style.bottom = "auto";
  });
  head.addEventListener("pointerup", () => { drag = false; });
  head.addEventListener("pointercancel", () => { drag = false; });
  $("miniClose").addEventListener("click", () => $("miniWindow").hidden = true);
  $("miniPin").addEventListener("click", () => $("miniWindow").classList.toggle("pinned"));
  $("miniThrough").addEventListener("click", () => $("miniWindow").classList.toggle("through"));
}

// ---------------- 右键菜单 ----------------
function initContextMenu() {
  document.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    const m = $("contextMenu");
    m.hidden = false;
    m.style.left = Math.min(e.clientX, innerWidth - 200) + "px";
    m.style.top = Math.min(e.clientY, innerHeight - 180) + "px";
  });
  document.addEventListener("click", (e) => { if (!e.target.closest("#contextMenu")) $("contextMenu").hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("contextMenu").hidden = true; $("overlay").hidden = true; } });
}

// ---------------- 清空 / 导出 / 截图 ----------------
function clearData() {
  const r = activeRoom();
  if (!r) { toast("没有可清空的房间"); return; }
  r.counters = { chat: 0, gift: 0, giftValue: 0, like: 0, follow: 0, member: 0 };
  r.online = 0; r.buckets = []; r.words = {}; r.viewers = {}; r.timeline = []; r.danmaku = [];
  ["kpiOnlineVal", "kpiFanVal", "kpiGiftVal", "kpiChatVal", "kpiPaidVal"].forEach(id => { $(id).textContent = "0"; $(id).dataset.v = "0"; });
  renderViewers(r); renderDanmaku(r); renderTimeline(r); renderWordCloud(r);
  toast("已清空当前房间数据");
}
function exportLog() {
  const r = activeRoom();
  const lines = ["# Pulse Console 会话日志", "时间: " + new Date().toLocaleString(), ""];
  lines.push("直播间: " + (r ? r.liveId : "无"));
  if (r) lines.push("在线: " + r.online + " | 弹幕: " + r.counters.chat + " | 礼物: " + r.counters.gift + " | 点赞: " + r.counters.like + " | 关注: " + r.counters.follow, "");
  lines.push("--- 事件时间线 ---");
  if (r) r.timeline.forEach(it => lines.push("[" + timeStr(it.ts) + "] " + it.text.replace(/<[^>]+>/g, "")));
  const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "pulse-log-" + Date.now() + ".txt"; a.click();
  toast("日志已导出");
}
function screenshotBoard() {
  const r = activeRoom();
  const cv = document.createElement("canvas"); cv.width = 900; cv.height = 560;
  const ctx = cv.getContext("2d");
  const g = ctx.createLinearGradient(0, 0, 0, 560); g.addColorStop(0, "#0b0e17"); g.addColorStop(1, "#111320");
  ctx.fillStyle = g; ctx.fillRect(0, 0, 900, 560);
  ctx.fillStyle = "#e7eaf6"; ctx.font = "700 30px 'JetBrains Mono', monospace"; ctx.fillText("Pulse Console · 直播看板", 40, 60);
  ctx.fillStyle = "#8a90a6"; ctx.font = "400 14px sans-serif";
  ctx.fillText("直播间 " + (r ? r.liveId : "无") + "  ·  " + new Date().toLocaleString(), 40, 92);
  if (!r) { ctx.fillStyle = "#8a90a6"; ctx.fillText("当前无监听房间", 40, 140); }
  else {
    const viewers = Object.values(r.viewers);
    const kpis = [["在线", r.online, C.cyan], ["粉丝", viewers.filter(v => v.isFollower).length, C.primary], ["礼物", r.counters.giftValue || 0, C.amber], ["弹幕", r.counters.chat, C.primary2], ["付费", viewers.filter(v => v.totalRecharge || v.payScore).length, C.rose]];
    kpis.forEach((k, i) => {
      const x = 40 + i * 170, y = 130, w = 150, h = 84;
      ctx.fillStyle = "rgba(255,255,255,0.04)"; ctx.strokeStyle = "rgba(140,150,200,0.2)";
      ctx.fillRect(x, y, w, h); ctx.strokeRect(x, y, w, h);
      ctx.fillStyle = "#8a90a6"; ctx.font = "400 12px sans-serif"; ctx.fillText(k[0], x + 14, y + 26);
      ctx.fillStyle = k[2]; ctx.font = "700 26px 'JetBrains Mono', monospace"; ctx.fillText(String(k[1]), x + 14, y + 62);
    });
    ctx.fillStyle = "#8a90a6"; ctx.font = "400 12px sans-serif"; ctx.fillText("TOP 观众", 40, 250);
    Object.values(r.viewers).sort((a, b) => importance(b) - importance(a)).slice(0, 6).forEach((v, i) => {
      const name = v.mystery ? (v.desensitized || "神秘人") : (v.nickname || "神秘人");
      ctx.fillStyle = "#e7eaf6"; ctx.font = "400 14px sans-serif"; ctx.fillText((i + 1) + ". " + name, 40, 280 + i * 26);
      ctx.fillStyle = "#8a90a6"; ctx.font = "400 12px 'JetBrains Mono', monospace";
      ctx.fillText((v.totalRecharge ? "付费 " : "") + "互动 " + v.score, 340, 280 + i * 26);
    });
  }
  const a = document.createElement("a"); a.download = "pulse-snapshot-" + Date.now() + ".png"; a.href = cv.toDataURL("image/png"); a.click();
  toast("看板快照已导出");
}

// ---------------- 主循环 / 启动 ----------------
function mainLoop() {
  const r = activeRoom();
  if (r) {
    ensureBucket(r);
    const b = curBucket(r);
    if (r.online) b.online = r.online;
    renderKPI(r);
    drawTrend(r);
    drawWave(r);
    renderDanmaku(r);
    renderTimeline(r);
    const lb = lastN(r, 1)[0] || {};
    const rate = (lb.chat || 0) + (lb.gift || 0) + (lb.like || 0) + (lb.member || 0);
    $("sbRate").textContent = rate.toFixed(1);
    State._tn = (State._tn || 0) + 1;
    renderViewers(r);
    if (State._tn % 4 === 0) renderWordCloud(r);
  }
  $("sbUptime").textContent = fmtDur(Math.floor((Date.now() - State.startTs) / 1000));
}

function init() {
  initParticles();
  initNav();
  document.querySelectorAll(".nav-item").forEach(btn => btn.addEventListener("mouseenter", playSound));
  initTabs();
  initIsland();
  initRightPanel();
  initMiniDrag();
  initContextMenu();
  initToggles();
  $("trendLegend").innerHTML =
    '<span><i style="background:' + C.cyan + '"></i>弹幕</span>' +
    '<span><i style="background:' + C.amber + '"></i>礼物</span>' +
    '<span><i style="background:' + C.primary + '"></i>在线</span>';
  updateConnUI();
  connectWS();
  setInterval(mainLoop, 500);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();








