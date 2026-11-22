// Act target resolver for browser_operation (browser.py): re-resolves the
// observed node's geometry and occlusion immediately before input, and
// commits select/native fills in-page. __NATIVE_FILL_TYPES__ is replaced
// from browser.NATIVE_FILL_TYPES at load. Called with the action payload.
(action => {
              const cache=window.__jevFast, e=cache?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
              if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
              cache.hits ??= ((el,px,py)=>el.contains(document.elementFromPoint(px,py)));
              if (!cache.hits(e,x,y)) {
                // DEVIATION: remember the covered node so the snapshot
                // omits it until hittable again (upstream PR #245).
                // Proven before any input -- including the SELECT
                // mutation below -- so re-observing cannot double-act.
                (cache.covered ??= new Set()).add(action.node);
                return {covered:true};
              }
              if (action.kind==='select') {
                if (e.tagName!=='SELECT') return null;
                // DEVIATION: upstream checks that SOME enabled option has the
                // value, then assigns e.value — whose setter selects the
                // first option with that value, disabled ones included, so
                // duplicate-valued options pick the wrong row (upstream
                // issue #216). Select the exact observed option_index
                // instead (falling back to value+label for old snapshots).
                let idx = Number.isInteger(action.option_index) ? action.option_index
                  : [...e.options].findIndex(o=>o.value===action.value &&
                      !o.disabled && !o.closest('optgroup[disabled]'));
                const chosen = e.options[idx];
                if (!chosen || chosen.disabled || chosen.closest('optgroup[disabled]')) return null;
                e.selectedIndex = idx;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              if (action.kind==='fill' && __NATIVE_FILL_TYPES__
                  .includes((e.type||'').toLowerCase())) {
                // DEVIATION: Input.insertText does not commit into a
                // native date/time/color/range control, so the generic
                // click-and-type path silently submits these fields
                // empty (or, for range, at its centre-click value) --
                // upstream issue #227. Commit through the native
                // HTMLInputElement value setter instead (so React's
                // value tracker sees a real change), then fire the
                // events an edit would. The browser stays the format
                // authority: an invalid date/time string sanitises to
                // empty. Color/range sanitise to a default instead of
                // empty, so those two are shape-checked up front. A
                // rejected value restores the previous one and reports
                // distinctly -- never as a stale target the tick loop
                // would retry with the same text forever. Still after
                // the covered check above: no mutation before that.
                if (typeof action.text!=='string' || !action.text.trim())
                  return {rejected:'no value supplied for '+e.type+' input'};
                if (e.type==='color' && !/^#[0-9a-f]{6}$/i.test(action.text))
                  return {rejected:'not a valid color value: '+action.text};
                if (e.type==='range' && !Number.isFinite(Number(action.text)))
                  return {rejected:'not a valid range value: '+action.text};
                e.focus();
                const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')?.set;
                const assign=v=>{ if (setter) setter.call(e,v); else e.value=v; };
                const before=e.value;
                assign(action.text);
                if (!e.value) { assign(before); return {rejected:'not a valid '+e.type+' value: '+action.text}; }
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
                return {x,y,filled:true,value:e.value};
              }
              return {x,y};
            })
