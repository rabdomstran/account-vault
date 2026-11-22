// Perceive step for live pages: atomically read visible controls into an
// indexed action table. Ported from jev_ultrafast/snapshot.js (MIT,
// browser-use/jev-ultrafast). Lines marked DEVIATION fix known upstream
// coverage bugs; everything else is unchanged so behaviour stays comparable.
(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1, covered:new Set()};
  // A cache minted by an older snapshot in this same document has no
  // covered set yet; the act resolver in browser.py records into it.
  if (!cache.covered) cache.covered=new Set();
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) {
    cache.nodes.delete(id); cache.covered.delete(id);
  }
  // The act guard's own hit test, shared so offer and execution agree.
  cache.hits=(e,x,y)=>e.contains(document.elementFromPoint(x,y));
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  // A control the main pass drops for having no hittable box of its own
  // (0x0 / opacity:0). Component libraries hide native radios/checkboxes
  // this way inside a visible label (upstream issue #23, AntD comment).
  const concealed = e => {
    const r=e.getBoundingClientRect();
    return r.width<=0 || r.height<=0 || !visible(e);
  };
  // The element's rect and viewport centre, or null when the centre cannot
  // be hit (no box of its own, or outside the viewport). One spelling for
  // the three collection passes below.
  const centered = e => {
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
    return !r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight ? null : {r,x,y};
  };
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(document.getElementById(id),seen)).filter(Boolean).join(' ');
    // DEVIATION (upstream PR #246, unmerged at adoption): TEXTAREA is
    // excluded from child-text naming like INPUT -- its child text is
    // its default value, so an unlabeled prefilled textarea was named
    // by stale (possibly template-sized) content and the title /
    // placeholder fallbacks below were never reached.
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (['INPUT','TEXTAREA'].includes(e.tagName) ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  // DEVIATION: 'slider' closes the list (upstream's ends at 'spinbutton')
  // so role="slider" widgets and range inputs (#215/#227, in role below)
  // reach the table; slider is fillable in the editable test below.
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton','slider'];
  // Two scope texts, close but deliberately not the same: the guard's
  // (upstream's selectors, part of freshness semantics) vs the scope copy
  // carried on each action for field_context, which adds fieldset so
  // grouped same-labeled fields disambiguate (#198/#202).
  const GUARD_SCOPE='form,dialog,[role="dialog"],article,li,tr,[role="row"]';
  const ACTION_SCOPE='form,fieldset,dialog,[role="dialog"],article,li,tr,[role="row"]';
  // DEVIATION: upstream matches only [contenteditable="true"], so bare
  // `contenteditable` and `contenteditable=""` (both editable per HTML spec,
  // as is "plaintext-only") are never enumerated (upstream issue #218).
  // Match the attribute's presence and let role() confirm isContentEditable.
  // DEVIATION: also enumerate [onclick] rows. Upstream tables only native
  // controls + known ARIA roles, so suggestion rows built from bare
  // li/div/span with click handlers are unreachable and the loop retypes
  // forever (upstream issue #23). role() maps them to button below; the
  // 250-action cap still bounds noise.
  const selector='a[href],button,input,textarea,select,summary,[contenteditable],[onclick],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type)) return 'textbox';
      // DEVIATION: upstream returns null here, so date/time/datetime-local/
      // month/week/range/color inputs never reach the element table and
      // cannot be filled at all (upstream issues #215, #227). Map the
      // value-bearing types to fillable roles and keep the native type on
      // the action (input_type) so the decider can tell them apart.
      if (['date','time','datetime-local','month','week'].includes(e.type)) return 'textbox';
      if (e.type==='range') return 'slider';
      if (e.type==='color') return 'textbox';
    }
    // DEVIATION (issue #23): a clickable non-native element with a handler.
    if (e.hasAttribute && e.hasAttribute('onclick')) return 'button';
    // DEVIATION (issue #23): keep guard() honest for second-pass targets
    // below -- a JS-assigned handler or explicit tab stop, and a label
    // standing in for its concealed radio/checkbox (role + checked come
    // from the input; clicking the label toggles it natively).
    if (typeof e.onclick === 'function' || e.tabIndex >= 0) return 'button';
    if (e.tagName === 'LABEL') {
      const inner=e.querySelector('input[type="radio"],input[type="checkbox"]') ||
        (e.htmlFor ? document.getElementById(e.htmlFor) : null);
      if (inner && ['radio','checkbox'].includes(inner.type) && concealed(inner)) return inner.type;
    }
    return null;
  };
  cache.pageKey=()=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    [...document.querySelectorAll('input,textarea,select')].filter(safe)
      .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly])];
  cache.guard=e=>{
    if (!e?.isConnected || !visible(e)) return null;
    const scope=e.closest(GUARD_SCOPE) || e.parentElement;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope?.innerText?.slice(0,6000)||''];
  };
  const actions=[];
  const collected=new Set(), collectedEls=new Set();
  for (const e of document.querySelectorAll(selector)) {
    if (!safe(e) || !visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    const hit=centered(e), rname=role(e);
    if (!rname || !hit) continue;
    const {r}=hit;
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    // DEVIATION: carry DOM identity hints (id/name/placeholder/input_type and
    // nearest scope text) so field_context can distinguish same-labeled
    // fields (upstream issues #198, #202). Like the guard's scope text but
    // a shorter copy (600 chars, ACTION_SCOPE) on the action itself.
    const scopeEl=e.closest(ACTION_SCOPE);
    const base={node:identity(e),role:rname,label:name(e)||rname,
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    collected.add(base.node); collectedEls.add(e);
    if (e.tagName==='INPUT' && e.type) base.input_type=e.type;
    if (e.id) base.dom_id=e.id;
    if (e.getAttribute && e.getAttribute('name')) base.name=e.getAttribute('name');
    if (e.getAttribute && e.getAttribute('placeholder')) base.placeholder=e.getAttribute('placeholder');
    if (scopeEl) base.scope=(scopeEl.innerText||'').slice(0,600);
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      // DEVIATION: record the option's tree index (option_index). Upstream
      // carries only o.value and executes with e.value=..., whose setter
      // picks the FIRST option with that value (disabled included), so
      // duplicate-valued options select the wrong row (upstream issue
      // #216). Execution now sets selectedIndex to this exact index.
      [...e.options].forEach((o,option_index)=>{ if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        actions.push({...base,kind:'select',value:o.value,option_index,
          current_value:[...e.selectedOptions].map(s=>s.label).join(', '),label:base.label+' → '+o.label}); });
    } else {
      const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
        (['textbox','searchbox','spinbutton','slider'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(e.value) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      actions.push({...base,kind:editable?'fill':'click',value});
      if (editable) actions.push({...base,kind:'click',value,label:'Open '+base.label});
    }
  }
  // DEVIATION (upstream issue #23, second half): the [onclick] rule above
  // only reaches rows with an inline handler attribute. Rows wired with
  // addEventListener, icon-only in-flow controls, and labels wrapping a
  // concealed radio/checkbox stayed invisible and the loop retyped or
  // blocked. This second pass stays narrow (the drift review's terms):
  // click evidence + a usable label + list/overlay context unless the
  // wiring or icon speaks for itself, innermost only, capped. Shapes
  // follow upstream PRs #22/#24 plus the issue's two verified comments.
  const NATIVEISH='a[href],button,input,textarea,select,summary,[contenteditable],[onclick]';
  const iconName=e=>{
    const svg=e.querySelector('svg');
    if (!svg) return '';
    const title=svg.querySelector('title')?.textContent?.trim();
    if (title) return title.slice(0,120);
    const aria=svg.getAttribute('aria-label')?.trim();
    if (aria) return aria.slice(0,120);
    if (svg.id) return svg.id.slice(0,120);
    for (const token of (svg.getAttribute('class')||'').split(/\s+/)) {
      const match=token.match(/icon-([a-z0-9-]+)$/i);
      if (match) return match[1].slice(0,120);
    }
    return '';
  };
  const inOverlay=e=>{
    let a=e.parentElement, depth=0;
    while (a && depth++<8) {
      const position=getComputedStyle(a).position;
      if (position==='absolute' || position==='fixed') return true;
      a=a.parentElement;
    }
    return false;
  };
  const pointerList=e=>{
    const parent=e.parentElement;
    if (!parent || !parent.matches('ul,ol,[role="list"],[role="listbox"],[role="menu"],[role="tablist"]'))
      return false;
    return [...parent.children].filter(k=>k!==e && k.tagName===e.tagName && visible(k) &&
      getComputedStyle(k).cursor==='pointer').length>=2;
  };
  let extras=0;
  // Labels standing in for a concealed radio/checkbox keep the input's
  // role and checked state; the click lands on the label's hittable rect.
  for (const e of document.querySelectorAll('input[type="radio"],input[type="checkbox"]')) {
    if (extras>=40) break;
    if (!safe(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]') || !concealed(e)) continue;
    const label=[...(e.labels||[])].find(l=>visible(l) && !l.closest('[aria-disabled="true"]'));
    if (!label) continue;
    const hit=centered(label);
    if (!hit) continue;
    const {r}=hit;
    const node=identity(label);
    if (collected.has(node)) continue;
    collected.add(node); collectedEls.add(label);
    actions.push({node,role:e.type,label:name(label)||name(e)||e.type,
      rect:{x:r.x,y:r.y,w:r.width,h:r.height},kind:'click',value:'',
      checked:String(e.checked),input_type:e.type});
    extras++;
  }
  const candidates=[];
  let scanned=0;
  for (const e of document.querySelectorAll('li,div,span,td,dd,p')) {
    if (++scanned>5000 || candidates.length>=80) break;
    if (!visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    if (e.matches(selector) || (e.parentElement && e.parentElement.closest(selector))) continue;
    if (e.querySelector(NATIVEISH)) continue;
    const pointer=getComputedStyle(e).cursor==='pointer';
    const strong=typeof e.onclick==='function' || e.tabIndex>=0 ||
      e.hasAttribute('data-clickable') || e.hasAttribute('data-action');
    if (!pointer && !strong) continue;
    if (!centered(e)) continue;
    const icon=iconName(e), label=name(e).trim().slice(0,120) || icon;
    if (!label) continue;
    const list=pointerList(e);
    if (!strong && !icon && !list && !inOverlay(e)) continue;
    candidates.push({e,label,list,strong,icon});
  }
  // A text span inside a clickable row inherits cursor:pointer and would
  // otherwise split one row ("Beijing North" + "beijingbei") into two
  // actions. Unless the inner element carries its own wiring/icon, the
  // row speaks for its spans (its label already joins their text, and a
  // click at its centre reaches the same handler).
  const candidateEls=new Set(candidates.map(c=>c.e));
  const rows=candidates.filter(c=>!(!c.strong && !c.icon && c.e.parentElement && candidateEls.has(c.e.parentElement)));
  for (const {e,label,list} of rows) {
    if (extras>=40) break;
    if (rows.some(c=>c.e!==e && e.contains(c.e))) continue;
    let inside=false;
    for (const other of collectedEls) if (other.contains(e)) { inside=true; break; }
    if (inside) continue;
    const node=identity(e);
    if (collected.has(node)) continue;
    collected.add(node); collectedEls.add(e);
    const r=e.getBoundingClientRect();
    const extra={node,role:list?'option':'button',label,
      rect:{x:r.x,y:r.y,w:r.width,h:r.height},kind:'click',value:''};
    if (e.id) extra.dom_id=e.id;
    actions.push(extra);
    extras++;
  }
  const words=[], walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  const range=document.createRange(); let node,length=0;
  while ((node=walker.nextNode()) && length<6000) {
    const value=node.textContent.trim(), parent=node.parentElement;
    if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
    range.selectNodeContents(node); const r=range.getBoundingClientRect();
    if (r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth) {
      words.push(value); length+=value.length;
    }
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6]];
  // DEVIATION: stop reoffering a control the act guard already refused as
  // covered (upstream PR #245). Only previously rejected nodes are
  // rechecked -- one hit test each, at this snapshot's fresh rect -- and a
  // node is reoffered as soon as its centre is hittable again. Filtering
  // happens after the marker above is built from the full semantics:
  // occlusion is geometry, and the marker deliberately compares meaning
  // and identity only, so covering a control never reads as a page change.
  let offered=actions;
  if (cache.covered.size) {
    const checked=new Set();
    for (const {node,rect:{x,y,w,h}} of actions) if (cache.covered.has(node) && !checked.has(node)) {
      checked.add(node);
      if (cache.hits(cache.nodes.get(node),x+w/2,y+h/2)) cache.covered.delete(node);
    }
    offered=actions.filter(a=>!cache.covered.has(a.node));
  }
  const omitted_actions=Math.max(0,offered.length-250);
  offered.splice(250);
  offered.forEach((a,i)=>a.id='e'+(i+1));
  if (scrollY+innerHeight<height-2) offered.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scrollY>0) offered.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  offered.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions:offered,marker,page_key,guards,omitted_actions};
})()
