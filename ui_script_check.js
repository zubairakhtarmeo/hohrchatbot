let hist=[], busy=false;

function pnl(n,btn){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('on'));
  document.querySelectorAll('.nb').forEach(b=>b.classList.remove('on'));
  document.getElementById('panel-'+n).classList.add('on');
  btn.classList.add('on');
  if(n!=='chat') loadDocs();
}

function rsz(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,130)+'px'}
function kd(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();snd()}}
function ch(b){document.getElementById('inp').value=b.textContent;snd()}
function clearChat(){
  hist=[];
  const w=document.getElementById('msgs');
  w.innerHTML='<div class="welcome"><div class="wi">🏢</div><h2>Chat cleared.</h2><p>Ask me anything about MG Apparel HR policies.</p></div>';
}

async function snd(){
  const inp=document.getElementById('inp'), msg=inp.value.trim();
  if(!msg||busy) return;
  document.querySelector('.welcome')?.remove();
  addMsg('u',msg); inp.value=''; inp.style.height='auto';
  hist.push({role:'user',content:msg});
  busy=true; document.getElementById('sb').disabled=true;
  const t=addTyping();
  try{
    const ctrl=new AbortController();
    const tm=setTimeout(()=>ctrl.abort(),45000);
    const r=await fetch('/api/chat',{method:'POST',
      headers:{'Content-Type':'application/json'},
      signal:ctrl.signal,
      body:JSON.stringify({message:msg,history:hist.slice(-10)})});
    clearTimeout(tm);
    const d=await r.json(); t.remove();
    if(!r.ok){
      addMsg('b',d.error||'⚠️ Chat request failed. Please try again.');
      return;
    }
    const reply=d.response||d.error||'No response received.';
    addMsg('b',reply);
    hist.push({role:'assistant',content:reply});
  }catch(e){
    t.remove();
    if(e && e.name==='AbortError'){
      addMsg('b','⚠️ Response took too long. Please try a shorter question.');
    }else{
      addMsg('b','⚠️ Could not reach the server. Please check if the chatbot is running.');
    }
  }
  busy=false; document.getElementById('sb').disabled=false;
}

function addMsg(role,text){
  const w=document.getElementById('msgs');
  const d=document.createElement('div');
  d.className='msg '+role;
  d.innerHTML=`<div class="av">${role==='b'?'🤖':'👤'}</div><div class="bbl">${fmt(text)}</div>`;
  w.appendChild(d); w.scrollTop=w.scrollHeight; return d;
}

function addTyping(){
  const w=document.getElementById('msgs');
  const d=document.createElement('div');
  d.className='msg b';
  d.innerHTML='<div class="av">🤖</div><div class="typing"><span></span><span></span><span></span></div>';
  w.appendChild(d); w.scrollTop=w.scrollHeight; return d;
}

function fmt(t){
  // Escape HTML
  t=t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  // Markdown-like formatting
  t=t.replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>');
  t=t.replace(/__(.*?)__/g,'<strong>$1</strong>');
  t=t.replace(/`(.*?)`/g,'<code>$1</code>');
  // Headings
  t=t.replace(/^### (.+)$/gm,'<h4>$1</h4>');
  t=t.replace(/^## (.+)$/gm,'<h3>$1</h3>');
  // Numbered lists
  t=t.replace(/^\d+\.\s+(.+)$/gm,'<li>$1</li>');
  // Bullet lists
  t=t.replace(/^[-•*]\s+(.+)$/gm,'<li>$1</li>');
  // Wrap consecutive <li> in <ul>
  t=t.replace(/(<li>.*?<\/li>)(\s*<li>)/g,'$1$2');
  t=t.replace(/(<li>[^]*?<\/li>)/g,'<ul>$1</ul>');
  // Newlines
  t=t.replace(/\n\n/g,'<br><br>');
  t=t.replace(/\n/g,'<br>');
  return t;
}

function ico(n){
  const e=(n.split('.').pop()||'').toLowerCase();
  return {pdf:'📄',docx:'📝',doc:'📝',xlsx:'📊',xls:'📊',txt:'📋',md:'📋'}[e]||'📁';
}

async function loadDocs(){
  try{
    const ctrl=new AbortController();
    const tm=setTimeout(()=>ctrl.abort(),12000);
    const r=await fetch('/api/documents',{signal:ctrl.signal});
    clearTimeout(tm);
    const d=await r.json(), docs=d.documents||[];
    document.getElementById('dcnt').textContent=docs.length;
    document.getElementById('slist').innerHTML=docs.length
      ? docs.map(doc=>`<div class="sdi">${ico(doc.name)}<span style="overflow:hidden;text-overflow:ellipsis">${doc.name}</span><span class="cpill">${doc.chunks}</span></div>`).join('')
      : '<div style="color:var(--dim);font-size:12px;padding:6px">No files indexed yet.</div>';
    document.getElementById('dgrid').innerHTML=docs.length
      ? docs.map(doc=>`
          <div class="dc">
            <div class="dc-ico">${ico(doc.name)}</div>
            <div class="dc-nm">${doc.name}</div>
            <div class="dc-mt">${doc.chunks} chunks · ${doc.indexed_at.slice(0,10)}</div>
            <div class="dc-act">
              <button class="dab" onclick="askDoc('${doc.name}')">Ask about</button>
              <button class="dab" onclick="revDoc('${doc.name}')">Review</button>
            </div>
          </div>`).join('')
      : '<p style="color:var(--dim);font-size:13px">No documents indexed yet.<br>Add files to your Data folder.</p>';
    const sel=document.getElementById('dsel');
    sel.innerHTML=docs.length
      ? docs.map(doc=>`<option value="${doc.name}">${doc.name}</option>`).join('')
      : '<option>No documents found</option>';
  }catch(e){
    console.error('loadDocs error:',e);
    document.getElementById('slist').innerHTML='<div style="color:var(--warn);font-size:12px;padding:6px">Unable to load files. Check server.</div>';
    document.getElementById('dgrid').innerHTML='<p style="color:var(--warn);font-size:13px">Unable to load documents right now.</p>';
    const sel=document.getElementById('dsel');
    sel.innerHTML='<option>Unable to load documents</option>';
  }
}

function askDoc(name){
  document.querySelectorAll('.nb')[0].click();
  document.getElementById('inp').value=`Explain the complete contents of: ${name}`;
  snd();
}

function revDoc(name){
  document.querySelectorAll('.nb')[2].click();
  setTimeout(()=>{
    document.getElementById('dsel').value=name;
    analyse();
  },150);
}

async function analyse(){
  const doc=document.getElementById('dsel').value;
  const out=document.getElementById('sout');
  const btn=document.getElementById('abtn');
  if(!doc||doc==='Loading…'||doc==='No documents found') return;
  out.className='';
  out.textContent='⏳ Analysing document… this may take 30–90 seconds.';
  btn.disabled=true;
  try{
    const r=await fetch('/api/suggest',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({document:doc})});
    const d=await r.json();
    out.textContent=d.suggestions||'No suggestions returned.';
  }catch{
    out.textContent='⚠️ Error connecting to server.';
  }
  btn.disabled=false;
}

async function reindex(){
  toast('⏳ Re-indexing all documents…');
  try{
    const r=await fetch('/api/reindex',{
      method:'POST',
      headers:{'X-Master-User': prompt('Master User ID:'), 'X-Master-Password': prompt('Master Password:')}
    });
    const d=await r.json();
    if(d.error){toast('⚠️ '+d.error);return;}
    toast(`✅ Done — ${d.count} documents indexed.`);
    loadDocs();
  }catch{toast('⚠️ Reindex failed.')}
}

async function checkStatus(){
  try{
    const ctrl=new AbortController();
    const tm=setTimeout(()=>ctrl.abort(),8000);
    const r=await fetch('/api/status',{signal:ctrl.signal});
    clearTimeout(tm);
    const d=await r.json();
    document.getElementById('mname').textContent=d.model||'—';
    document.getElementById('dcnt').textContent=d.documents;
    document.getElementById('lanip').textContent=d.lan_url||'localhost';
    if(d.warn) document.getElementById('owarn').style.display='block';
  }catch{
    document.getElementById('mname').textContent='offline';
    document.getElementById('dcnt').textContent='—';
    document.getElementById('lanip').textContent='unreachable';
    document.getElementById('owarn').style.display='block';
  }
}

function toast(msg){
  const t=document.getElementById('toast');
  t.textContent=msg; t.style.display='block';
  clearTimeout(t._t); t._t=setTimeout(()=>t.style.display='none',4000);
}

checkStatus(); loadDocs(); setInterval(loadDocs,30000);