'use strict';
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api = async (path, body) => {
  const r = await fetch('/api/' + path, body === undefined ? {} : {method:'POST', headers:{'X-VF':'1','Content-Type':'application/json'}, body: JSON.stringify(body)});
  const j = await r.json().catch(() => ({error: 'bad response'}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
};
const fmtT = t => t ? new Date(t * 1000).toLocaleString([], {dateStyle:'medium', timeStyle:'short'}) : '';
const TABS = ['Approve','Research','Ideas','Create','Library','Analytics','Settings'];
let tab = (location.hash || '#Approve').slice(1); let voices = [];

function nav() {
  $('#tabs').innerHTML = TABS.map(t => `<button class="${t===tab?'on':''}" data-t="${t}">${t}</button>`).join('');
  $('#tabs').onclick = e => { const t = e.target.dataset.t; if (t) { tab = t; location.hash = t; render(); } };
}
async function status() {
  try {
    const s = await api('status');
    const pend = s.counts.pending_approval || 0;
    $('#statusline').innerHTML = `${pend} awaiting approval · LLM: ${esc(s.llm.split(' ')[0])}<br>voice: ${esc(s.voice)} · IG: ${s.instagram.ready ? 'ready' : 'manual mode'}`;
    return s;
  } catch (e) { $('#statusline').textContent = esc(e.message); }
}
async function render() { nav(); await status(); $('#view').innerHTML = '<p class="mut">Loading…</p>'; try { await views[tab](); } catch (e) { $('#view').innerHTML = `<div class="card fail">${esc(e.message)}</div>`; } }

// ---------------------------------------------------------------- jobs
async function watchJob(id, label, done) {
  const bar = $('#jobbar'); bar.hidden = false;
  for (;;) {
    const j = await api('jobs/' + id);
    bar.innerHTML = `<b>${esc(j.label)}</b>: ${esc(j.stage)} ${j.frac ? Math.round(j.frac*100)+'%' : ''}<div class="bar"><i style="width:${Math.round((j.frac||0)*100)}%"></i></div>${j.error ? `<div class="fail">${esc(j.error)}</div>` : ''}`;
    if (j.state === 'done' || j.state === 'error') { setTimeout(() => bar.hidden = true, j.error ? 8000 : 1500); if (j.state === 'done') done && done(j.result); else alert(j.error); return; }
    await new Promise(r => setTimeout(r, 1200));
  }
}
const run = (path, body, label, done) => api(path, body).then(r => r.job ? watchJob(r.job, label, done || render) : (done || render)(r)).catch(e => alert(e.message));

// ---------------------------------------------------------------- approve
const views = {};
function qcHtml(qc) {
  if (!qc || !qc.checks) return '';
  return `<div class="qc">${qc.checks.map(c => `<div><b class="${c.status}">${c.status}</b><div>${esc(c.check)}<br><span>${esc(c.detail)}</span></div></div>`).join('')}</div>`;
}
function videoCard(v) {
  const m = v.meta || {}, d = m.description || {}, rt = m.recommended_time;
  const st = v.status, pending = st === 'pending_approval', approved = v.approved;
  const failed = v.qc && v.qc.overall === 'fail';
  return `<div class="card" data-id="${v.id}">
   <div class="vid">
    ${st === 'generating' || st === 'failed' ? `<div class="mut">${st}${m.error ? ': ' + esc(m.error) : ''}</div>` :
      `<video controls playsinline preload="metadata" poster="${v.media.thumb}" src="${v.media.video}"></video>`}
    <div class="info">
      <div class="row"><span class="badge">${esc(st)}</span><span class="badge ${v.qc?.overall}">QC ${esc(v.qc?.overall || '?')}</span><span class="mut">${esc(v.category)} · ${esc(v.style)} · ${v.duration ? v.duration.toFixed(0)+'s' : ''}</span></div>
      <h3 style="margin:8px 0 4px">${esc(v.title)}</h3>
      <div class="row"><img class="thumb" src="${v.media.thumb}" alt="thumbnail" onerror="this.style.display='none'"><div class="mut grow">Why this idea: ${esc(m.idea_why || 'manual topic')}</div></div>
      <label>Instagram description</label><pre class="cap">${esc(d.caption || '')}</pre>
      ${rt ? `<p class="mut">Recommended time: <b>${esc(rt.iso.replace('T', ' '))}</b> — ${esc(rt.why)}</p>` : ''}
      ${v.scheduled_at ? `<p>Scheduled for <b>${fmtT(v.scheduled_at)}</b></p>` : ''}
      ${m.publish_error ? `<p class="fail">Last publish error: ${esc(m.publish_error)}</p>` : ''}
      ${v.feedback ? `<p class="mut">Feedback: ${esc(v.feedback)}</p>` : ''}
    </div>
   </div>
   <details open><summary>Quality checks</summary>${qcHtml(v.qc)}</details>
   <details><summary>Topic &amp; sources (${(m.sources || []).length})</summary><div class="mut">${(m.sources || []).map(s => `• ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>` : esc(s.title)} <small>(${esc(s.license || '')})</small>`).join('<br>') || 'none'}</div></details>
   ${failed && pending ? '<p class="fail">QC has failures. Fix with EDIT/REGENERATE, or approve anyway if you accept the risk.</p>' : ''}
   <div class="row" style="margin-top:10px">
     <button class="ok" data-a="approve" ${pending ? '' : 'disabled'}>APPROVE</button>
     <button class="bad" data-a="reject" ${['pending_approval','approved','scheduled'].includes(st) ? '' : 'disabled'}>REJECT</button>
     <button data-a="regenerate" ${['generating','failed','posted','publishing'].includes(st) ? 'disabled' : ''}>REGENERATE</button>
     <button data-a="edit" ${['generating','publishing','posted'].includes(st) ? 'disabled' : ''}>EDIT</button>
     <button class="pri" data-a="schedule" ${approved ? '' : 'disabled'} title="${approved ? '' : 'approve first'}">SCHEDULE</button>
     <button class="pri" data-a="post_now" ${approved ? '' : 'disabled'} title="${approved ? '' : 'approve first'}">POST NOW</button>
     ${approved ? `<button data-a="manual">Manual package</button><button data-a="manual_posted">Mark posted</button>` : ''}
     ${st === 'approved' || st === 'scheduled' ? `<button data-a="unapprove">Withdraw approval</button>` : ''}
   </div></div>`;
}
views.Approve = async () => {
  const [{videos}] = [await api('videos')];
  const q = videos.filter(v => ['pending_approval','approved','scheduled','generating','publishing','failed'].includes(v.status));
  $('#view').innerHTML = (q.length ? q.map(videoCard).join('') : '<div class="card">Nothing waiting. Go to <b>Create</b> or <b>Ideas</b> to make a video, or enable daily automation in <b>Settings</b>.</div>');
  $('#view').onclick = async e => {
    const a = e.target.dataset.a; if (!a) return; const id = e.target.closest('.card').dataset.id;
    const v = videos.find(x => x.id === id);
    try {
      if (a === 'approve') { await api(`videos/${id}/approve`, {}); render(); }
      else if (a === 'unapprove') { await api(`videos/${id}/unapprove`, {}); render(); }
      else if (a === 'reject') { const r = prompt('Why are you rejecting it? (optional; nothing will be regenerated automatically)') ; if (r !== null) { await api(`videos/${id}/reject`, {reason: r}); render(); } }
      else if (a === 'regenerate') { const f = prompt('What should change? e.g. "shorter, funnier hook, different angle"'); if (f !== null) run(`videos/${id}/regenerate`, {feedback: f}, 'regenerate'); }
      else if (a === 'edit') openEditor(id);
      else if (a === 'schedule') {
        const rec = v.meta.recommended_time?.iso || '';
        const at = prompt('Schedule at (local time, YYYY-MM-DDTHH:MM):', rec);
        if (at) { const r = await api(`videos/${id}/schedule`, {at}); if (r.note) alert(r.note); render(); }
      }
      else if (a === 'post_now') {
        if (!confirm(`Publish "${v.title}" to Instagram now?`)) return;
        const r = await api(`videos/${id}/post_now`, {confirm: true});
        if (r.manual) { alert(`Instagram publishing isn't set up (${r.reason}).\nDownloading the manual package instead.`); location.href = r.package; }
        else watchJob(r.job, 'publish', res => { alert('Posted! ' + (res.permalink || res.media_id)); render(); });
      }
      else if (a === 'manual') { const r = await api(`videos/${id}/manual_package`, {}); location.href = r.url; }
      else if (a === 'manual_posted') { const p = prompt('Paste the Instagram post link (optional):', ''); if (p !== null) { await api(`videos/${id}/manual_posted`, {permalink: p}); render(); } }
    } catch (err) { alert(err.message); }
  };
};

// ---------------------------------------------------------------- editor
async function openEditor(id) {
  const v = await api('videos/' + id); const p = v.project;
  if (!voices.length) voices = (await api('voices')).voices;
  const sh = $('#sheet'); sh.hidden = false;
  $('#sheet-body').innerHTML = `<h3>Edit: ${esc(v.title)}</h3>
   <p class="mut">Edits void any earlier approval. Narration changes re-voice the video; caption/text changes re-render; thumbnail/description changes are quick.</p>
   <label>Title</label><input id="e-title" value="${esc(p.script.title)}">
   <label>Narrator</label><select id="e-voice">${voices.map(x => `<option value="${esc(x.id)}" ${x.id === v.meta.voice?.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select>
   <label>Scenes (narration is spoken; on-screen text is the big headline)</label>
   ${p.script.scenes.map((s, i) => `<div class="card"><small>Scene ${i+1} · ${esc(s.motif)}</small><textarea class="e-nar" rows="2">${esc(s.narration)}</textarea><input class="e-txt" value="${esc(s.text)}"></div>`).join('')}
   <details><summary>Caption editor (${p.captions.length} chunks, timing is kept)</summary>
     <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px">${p.captions.map((c, i) => `<div><small>${c.start.toFixed(1)}s</small><input class="e-cap" value="${esc(c.words.map(w => w.w).join(' '))}"></div>`).join('')}</div></details>
   <label>Thumbnail text</label><input id="e-tt" placeholder="(default: title)" value="">
   <label>Thumbnail frame time: <span id="e-ttv">${(p.thumb_time || 1.6).toFixed(1)}</span>s</label><input id="e-tf" type="range" min="0" max="${p.timeline_total.toFixed(1)}" step="0.1" value="${p.thumb_time || 1.6}" oninput="$('#e-ttv').textContent=this.value">
   <label>Instagram description</label><textarea id="e-desc" rows="9">${esc(v.meta.description.caption)}</textarea>
   <div class="row" style="margin-top:12px"><button class="pri" id="e-save">Save changes</button><button id="e-cancel">Cancel</button></div>`;
  $('#e-cancel').onclick = () => sh.hidden = true;
  $('#e-save').onclick = () => {
    const nars = [...document.querySelectorAll('.e-nar')], txts = [...document.querySelectorAll('.e-txt')];
    const body = {title: $('#e-title').value, voice: $('#e-voice').value, description: $('#e-desc').value, thumb_title: $('#e-tt').value, thumb_time: +$('#e-tf').value,
      scenes: nars.map((n, i) => ({narration: n.value, text: txts[i].value})), captions: [...document.querySelectorAll('.e-cap')].map(c => c.value)};
    sh.hidden = true; run(`videos/${id}/edit`, body, 'edit');
  };
}

// ---------------------------------------------------------------- research
views.Research = async () => {
  const r = await api('research');
  const st = r.run ? r.run.source_status : {};
  $('#view').innerHTML = `<div class="card"><div class="row"><button class="pri" id="rr">Run research now</button><button id="rs">Load SAMPLE data (offline test)</button></div>
   <p class="mut">Sources use public/official endpoints only. Instagram Reels and TikTok have no sanctioned public trend API, so they are never scraped — add what you see there below.</p>
   <table>${Object.entries(st).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="${v.ok ? 'pass' : 'warn'}">${v.ok ? 'ok · ' + v.count : 'unavailable'}</td><td class="mut">${esc(v.message)}</td></tr>`).join('')}</table></div>
   <div class="card"><b>Add an observation</b> <span class="mut">(a trend you spotted on TikTok / Instagram / anywhere)</span>
   <div class="row"><select id="m-p" style="width:auto"><option>tiktok</option><option>instagram</option><option>youtube</option><option>other</option></select><input id="m-t" class="grow" placeholder="Topic or format you saw trending"></div>
   <input id="m-u" placeholder="link (optional)"><button id="m-add" style="margin-top:8px">Add</button></div>
   <div class="card"><b>Latest signals</b> <span class="mut">${r.signals.length}</span><table>${r.signals.slice(0, 60).map(s => `<tr><td>${esc(s.source)}</td><td>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>` : esc(s.title)}</td><td class="mut">${esc(s.category)}</td></tr>`).join('')}</table></div>`;
  $('#rr').onclick = () => run('research/run', {}, 'research', () => { tab = 'Ideas'; render(); });
  $('#rs').onclick = () => run('research/run', {sample: true}, 'research', () => { tab = 'Ideas'; render(); });
  $('#m-add').onclick = async () => { if ($('#m-t').value) { await api('research/manual', {platform: $('#m-p').value, title: $('#m-t').value, url: $('#m-u').value}); render(); } };
};

// ---------------------------------------------------------------- ideas
views.Ideas = async () => {
  const r = await api('ideas');
  $('#view').innerHTML = `<div class="card mut">${esc(r.disclaimer)} Weights: ${Object.entries(r.weights).map(([k, v]) => `${k} ${v*100}%`).join(', ')}. ${r.learning.n ? `Learning from ${r.learning.n} of your videos (confidence: ${r.learning.confidence || 'low'}).` : 'No performance data applied yet.'}</div>` +
   (r.ideas.length ? r.ideas.map(i => `<div class="card"><div class="row"><b class="grow">${esc(i.topic)}</b><span class="badge">${esc(i.category)}</span><span class="badge">${esc(i.format)}</span><b style="font-size:20px">${i.score}</b></div>
    <p class="mut">${esc(i.why)}</p><p>Suggested hook: <i>${esc(i.hook)}</i></p>
    <details><summary>Score breakdown</summary>${Object.entries(i.breakdown).map(([k, v]) => `<div class="row"><small style="width:100px">${k}</small><div class="bar grow"><i style="width:${v}%"></i></div><small>${Math.round(v)}</small></div><div class="mut" style="margin:-2px 0 6px 108px"><small>${esc(r.help[k])}</small></div>`).join('')}
    <p class="mut">Seen in: ${i.signals.map(s => esc(s.source)).join(', ')}</p></details>
    <div class="row" style="margin-top:8px"><button class="pri" data-id="${i.id}">Create video from this idea</button></div></div>`).join('') : '<div class="card">No ideas yet. Run research first.</div>');
  $('#view').onclick = e => { const id = e.target.dataset.id; if (id) { const notes = prompt('Optional: paste facts you trust (one per line) for the script to use. Leave empty to try Wikipedia.', ''); if (notes === null) return; run('videos/create', {idea_id: +id, notes_text: notes}, 'create video', () => { tab = 'Approve'; render(); }); } };
};

// ---------------------------------------------------------------- create
views.Create = async () => {
  voices = (await api('voices')).voices;
  $('#view').innerHTML = `<div class="card"><b>Make a video about any topic</b>
   <label>Topic</label><input id="c-topic" placeholder="e.g. Why octopuses have three hearts">
   <div class="row"><div class="grow"><label>Style</label><select id="c-style"><option value="explainer">Explainer (facts)</option><option value="story">Story (needs local LLM)</option><option value="what_if">What-if (needs local LLM)</option><option value="comedy">Comedy (needs local LLM)</option></select></div>
   <div class="grow"><label>Narrator</label><select id="c-voice">${voices.map(v => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join('')}</select></div></div>
   <label>Facts you trust (one per line). Strongly recommended: the fact-checker only verifies against these + Wikipedia.</label><textarea id="c-notes" placeholder="One verified fact per sentence…"></textarea>
   <button class="pri" id="c-go" style="margin-top:10px">Create video for approval</button><p class="mut">Takes a few minutes. It never publishes; it lands in <b>Approve</b>.</p></div>`;
  $('#c-go').onclick = () => { const t = $('#c-topic').value.trim(); if (!t) return alert('Enter a topic'); run('videos/create', {topic: t, style: $('#c-style').value, voice: $('#c-voice').value, notes_text: $('#c-notes').value}, 'create video', () => { tab = 'Approve'; render(); }); };
};

views.Library = async () => {
  const {videos} = await api('videos');
  $('#view').innerHTML = `<div class="card"><table>${videos.map(v => `<tr><td><img class="thumb" style="width:48px" src="${v.media.thumb}" onerror="this.style.display='none'"></td><td><b>${esc(v.title)}</b><br><span class="mut">${esc(v.category)} · ${esc(v.style)}</span></td><td><span class="badge">${esc(v.status)}</span><br>${v.permalink ? `<a href="${esc(v.permalink)}" target="_blank" rel="noopener">post</a>` : ''}</td><td class="mut">${fmtT(v.created_at)}</td></tr>`).join('') || '<tr><td>No videos yet</td></tr>'}</table></div>`;
};

// ---------------------------------------------------------------- analytics
views.Analytics = async () => {
  const a = await api('analytics'); const L = a.learning;
  const f = x => x == null ? '–' : (typeof x === 'number' ? (x % 1 ? x.toFixed(1) : x) : x);
  $('#view').innerHTML = `<div class="card mut">${esc(a.note)}</div>
   <div class="card"><div class="row"><button id="an-sync" class="pri">Sync from Instagram</button><span class="mut">needs token + user id (Settings)</span></div>
   <table><tr><th>Video</th><th>Views</th><th>Reach</th><th>Avg watch</th><th>Shares</th><th>Saves</th><th>Comments</th><th>Eng./100 reach</th></tr>
   ${a.table.map(r => `<tr><td>${esc(r.title)}<br><small>${esc(r.category)} · ${esc(r.style)}</small></td><td>${f(r.metrics.views)}</td><td>${f(r.metrics.reach)}</td><td>${f(r.metrics.avg_watch_s)}s ${r.retention ? `<br><small>${Math.round(r.retention*100)}% of length</small>` : ''}</td><td>${f(r.metrics.shares)}</td><td>${f(r.metrics.saves)}</td><td>${f(r.metrics.comments)}</td><td><b>${f(r.engagement)}</b></td></tr>`).join('') || '<tr><td colspan=8>No posted videos with metrics yet.</td></tr>'}</table></div>
   <div class="card"><b>What the data says so far</b><p class="mut">${L.n ? `Based on ${L.n} videos (confidence: ${L.confidence}). ` : ''}${esc(L.note || '')}</p>
   ${Object.entries(L.category || {}).map(([k, v]) => `<div>${esc(k)}: ×${v}</div>`).join('')}${Object.entries(L.style || {}).map(([k, v]) => `<div>style ${esc(k)}: ×${v}</div>`).join('')}
   <p class="mut">Next suggested posting slot: ${esc(a.recommend.iso.replace('T', ' '))} — ${esc(a.recommend.why)}</p></div>
   <div class="card"><b>Import numbers manually</b><p class="mut">Paste CSV with a header row: <code>id,views,reach,likes,comments,shares,saves,avg_watch_s,completion_rate,followers_gained</code> (id = video id, post link, or title)</p><textarea id="an-csv"></textarea><button id="an-imp" style="margin-top:8px">Import</button></div>`;
  $('#an-sync').onclick = () => run('analytics/sync', {}, 'sync', r => { alert(JSON.stringify(r)); render(); });
  $('#an-imp').onclick = async () => { const r = await api('analytics/import', {csv: $('#an-csv').value}); alert(`Imported ${r.imported}. Unmatched: ${r.unmatched.join(', ') || 'none'}`); render(); };
};

// ---------------------------------------------------------------- settings
views.Settings = async () => {
  const s = await api('settings'); const hw = await api('hardware'); const st = await api('status');
  $('#view').innerHTML = `<div class="card"><b>Daily automation</b>
   <label><input type="checkbox" id="s-den" style="width:auto" ${s.daily.enabled ? 'checked' : ''}> Generate one video per day for my approval</label>
   <label>Time (local)</label><input id="s-dt" type="time" value="${esc(s.daily.time)}">
   <p class="mut">Needs <code>python -m viralforge tick</code> scheduled (see <code>install-schedule</code>) while your Mac is awake. Status: ${esc(st.daily.why)}. It never approves/publishes, and never regenerates a rejected video by itself.</p>
   <button id="s-run">Run the daily job now</button></div>
   <div class="card"><b>Niche interests</b><p class="mut">0 = never, 1 = love it. Used in idea ranking (relevance).</p>${s.categories.map(c => `<div class="row"><span style="width:130px">${esc(c)}</span><input class="s-int" data-c="${esc(c)}" type="range" min="0" max="1" step="0.1" value="${s.interests[c] ?? 1}" style="width:200px"></div>`).join('')}</div>
   <div class="card"><b>Instagram publishing</b><p class="mut">Token is read from the environment/.env, never saved here. Status: ${esc(st.instagram.detail)}</p>
   <label>Instagram professional account user id</label><input id="s-igu" value="${esc(s.instagram.user_id)}"><label>Mode</label><select id="s-igm"><option value="resumable" ${s.instagram.mode==='resumable'?'selected':''}>resumable upload (no public URL needed)</option><option value="video_url" ${s.instagram.mode==='video_url'?'selected':''}>video_url (needs public tunnel)</option></select>
   <label>Public base URL (video_url mode only)</label><input id="s-igp" value="${esc(s.instagram.public_base_url)}"></div>
   <div class="card"><b>Local AI</b><label>LLM backend</label><select id="s-llm"><option value="auto" ${s.llm.backend==='auto'?'selected':''}>auto (Ollama if running)</option><option value="template" ${s.llm.backend==='template'?'selected':''}>template only</option></select>
   <label>Ollama model (blank = auto)</label><input id="s-lm" value="${esc(s.llm.model)}"><label>TTS speed</label><input id="s-rate" type="number" step="0.05" min="0.7" max="1.4" value="${s.tts.rate}"></div>
   <div class="card"><b>This machine</b><pre class="cap">${esc(JSON.stringify({chip: hw.chip, ram_gb: hw.ram_gb, cores: hw.cpu_cores, tools: hw.tools, recommended: hw.recommended, ollama_models: hw.ollama_models}, null, 1))}</pre></div>
   <button class="pri" id="s-save">Save settings</button>`;
  $('#s-save').onclick = async () => {
    const interests = {}; document.querySelectorAll('.s-int').forEach(i => interests[i.dataset.c] = +i.value);
    await api('settings', {daily: {enabled: $('#s-den').checked, time: $('#s-dt').value}, interests, instagram: {user_id: $('#s-igu').value.trim(), mode: $('#s-igm').value, public_base_url: $('#s-igp').value.trim()}, llm: {backend: $('#s-llm').value, model: $('#s-lm').value.trim()}, tts: {rate: +$('#s-rate').value}});
    alert('Saved'); render();
  };
  $('#s-run').onclick = () => run('daily/run', {}, 'daily run', () => { tab = 'Approve'; render(); });
};

render(); setInterval(status, 15000);
