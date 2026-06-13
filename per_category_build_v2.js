export const meta = {
  name: 'per-category-learning-v2',
  description: 'Build per-category brain specialists with a per-category OUT-OF-SAMPLE gate (a category tilts sizing only if its specialist beats global OOS; else no-op). Addresses v1 design-critique must-fixes. Tested + adversarially verified + rollback.',
  phases: [ { title: 'Lock' }, { title: 'Build' }, { title: 'Verify' }, { title: 'Ship' } ],
}

const RULES = [
  'You are building PER-CATEGORY learning for the PAPER-TRADING bot at ~/polymarket-bot. Live daemon: http://127.0.0.1:8765. Git repo; HEAD is last known-good.',
  'PRINCIPLE: hierarchical/partial-pooling — each category gets a brain specialist that BLENDS with the global model and influences sizing ONLY once it has proven out-of-sample skill on its OWN data. Otherwise it is a pure no-op (identical to today). Never an independent per-category model; the global model is always the prior/fallback.',
  '',
  'HARD RULES — never violate:',
  '- PAPER ONLY. Never touch real money / wallet / .env / .envtab (gitignored; never read or print them).',
  '- NO FUTURE-DATA LEAKAGE: per-category training is past-only; category is read from the position/opportunity (a static label set at entry), never recomputed at decision time.',
  '- ERA HYGIENE: dead_cohort() threaded through every per-category learner (training rows AND credibility n_eff).',
  '- The GLOBAL model must NOT regress: its OOS cv_skill after the change must equal before on the category=None path.',
  '- PER-CATEGORY OOS GATE IS MANDATORY: a category tilts sizing only if its specialist oos_skill > 0 (beats global/base on walk-forward folds). No OOS skill -> cw=0 -> no-op.',
  '- One coherent, fully-tested increment. When in doubt, present instead of ship.',
].join('\n')
const R = (b) => RULES + '\n\n=== YOUR TASK ===\n' + b

const GATE_SCHEMA = { type:'object', additionalProperties:false, required:['clear','deferred','reason'],
  properties:{ clear:{type:'boolean'}, deferred:{type:'boolean'}, reason:{type:'string'} } }
const BUILD_SCHEMA = { type:'object', additionalProperties:false, required:['built','reason'],
  properties:{ built:{type:'boolean'}, reason:{type:'string'}, commit:{type:'string'}, files:{type:'array',items:{type:'string'}},
    test_summary:{type:'string'}, global_skill_before:{type:'string'}, global_skill_after:{type:'string'},
    oos_gate_proof:{type:'string', description:'how a test proves an OOS-negative category does NOT tilt sizing'}, diffstat:{type:'string'} } }
const REVIEW_SCHEMA = { type:'object', additionalProperties:false, required:['refuted','violation','reasoning'],
  properties:{ refuted:{type:'boolean'}, violation:{type:'boolean'}, reasoning:{type:'string'} } }
const SHIP_SCHEMA = { type:'object', additionalProperties:false, required:['shipped','reason'],
  properties:{ shipped:{type:'boolean'}, rolled_back:{type:'boolean'}, reason:{type:'string'}, commit:{type:'string'}, health_after:{type:'string'} } }
const REL_SCHEMA = { type:'object', additionalProperties:false, required:['clean','reason'], properties:{ clean:{type:'boolean'}, reason:{type:'string'} } }

const DESIGN = [
  'IMPLEMENT this exact design (extend the EXISTING per-strategy specialist machinery in brain_train/brain_adjust to a category key, with a per-category OOS gate). Locate the real current line numbers yourself with grep/Read — do not trust any hardcoded numbers.',
  '',
  'TRAINING (brain_train):',
  '1. Carry category on each training row: where rows are appended for the brain, add `t.get("category") or "Other"` as a 4th tuple field. dead_cohort() already filters these rows (keep that) so category rows inherit era hygiene. Update EVERY place that unpacks the rows tuple (the `data` comprehensions and the strategy-specialist loop) to consume the extra field without using it where irrelevant.',
  '2. After the global stack is built, for each category C present with >= 20 rows: fit `w_C = _fit_logistic(rows_of_C, l2=best_l2)`. Then compute that category C\'s OUT-OF-SAMPLE skill with the SAME expanding-window walk-forward CV the global model uses (cv_generic/cv_skill machinery) restricted to C\'s chronological rows -> oos_skill_C. Compute n_eff_C = effective_n() over C\'s settles filtered `and not dead_cohort(s)`. Store `out["cat_specialists"][C] = {"w": w_C, "oos_skill": oos_skill_C, "n_eff": n_eff_C, "n": len(rows_of_C)}`.',
  '3. INIT `out["cat_specialists"] = {}` in EVERY return path of brain_train (base/empty, cache-hit early return, drift path, normal). Add "cat_specialists" to the cache-persist key list.',
  '4. CACHE FIX: the cache-hit early-return must NOT return a stale/missing per-category map. Bump a cache version OR: if the loaded BRAIN lacks "cat_specialists", force a full retrain (skip the cache-hit). Ensure a dead_cohort shift cannot silently reuse stale cat credibility.',
  '',
  'DECISION (brain_adjust):',
  '5. Add a `category=None` parameter. After the existing strategy-specialist blend produces p_model, apply the per-category blend ONLY IF `cs = (BRAIN.get("cat_specialists") or {}).get(category)` exists AND `cs.get("oos_skill", 0) > 0`:  `cw = cs["n_eff"]/(cs["n_eff"]+60.0); p_model = (1-cw)*p_model + cw*_predict(cs["w"], x)`. The OOS-skill check is the gate — a category with oos_skill <= 0 (or None/unknown category, or no specialist) is a pure NO-OP. The existing outer credibility*skill_factor and [0.4,1.6] clamp are unchanged.',
  '6. Thread `category` from the OPPORTUNITY/POSITION dict at the call sites (it is already computed via market_category at scan time and recorded). Do NOT recompute market_category() inside brain_adjust or hoist a fresh API call onto the hot path. Test-only callers default category=None.',
  '',
  'BACKFILL + TESTS (extend tests.py and/or bot self_test):',
  '7. Old settled trades without "category" bin to "Other"; because n_eff uses (family, day) clusters, replication cannot inflate it. ',
  '8. Add tests asserting: (a) GLOBAL cv_skill is UNCHANGED on the category=None path after the change (regression guard); (b) a category whose specialist has oos_skill <= 0 produces brain_adjust IDENTICAL to today (gate works — the key proof); (c) a thin category (no specialist / n_eff small) is a no-op; (d) a dead-cohort-only category (e.g. r90 sports) has n_eff=0 and never tilts; (e) a category with oos_skill>0 AND real n_eff DOES diverge from global. ',
  '',
  'DASHBOARD: surface per-category specialists in the Models tab (category, n, n_eff, oos_skill, cw) — read-only display.',
].join('\n')

const releasePrompt = (note) => R([
  'Close out cleanly. Reason: ' + note,
  '1. If `cd ~/polymarket-bot && git log -1 --format=%s` starts with "FEATURE:" and NOT shipped this run, discard: `git reset --hard HEAD~1`. Else leave HEAD.',
  '2. `rm -f .autopilot_pause && git checkout -- . 2>/dev/null`; `git status --short` must be empty.',
  '3. Ensure watchdog alive; bot healthy on :8765.',
  '4. Release lock if ours: `grep -q "^feature:" .autopilot_lock 2>/dev/null && rm -f .autopilot_lock || true`.',
  'Return {clean, reason}.',
].join('\n'))

phase('Lock')
const gate = await agent(R([
  '0. LOCK: `cd ~/polymarket-bot && [ -f .autopilot_lock ] && echo age=$(( $(date +%s) - $(stat -f %m .autopilot_lock) )) || echo none`. If exists and age<900 -> deferred=true, clear=false, touch nothing, return. Else `echo "feature:$(date +%s)" > .autopilot_lock`.',
  '1. `rm -f .autopilot_pause && git checkout -- . 2>/dev/null`; ensure watchdog: `pgrep -f watchdog.sh >/dev/null || (cd ~/polymarket-bot && nohup bash watchdog.sh >> watchdog.log 2>&1 &)`.',
  '2. Health ok+balanced AND `python3 bot.py test|tail -1` + `python3 tests.py|tail -1` all-passed, else clear=false (and `rm -f .autopilot_lock`).',
  'Return {clear, deferred, reason}.',
].join('\n')), { schema: GATE_SCHEMA, label:'gate+lock', phase:'Lock', agentType:'Explore' })
if (!gate || gate.deferred) { log('LOCK -> deferred'); return { outcome:'deferred' } }
if (!gate.clear) { await agent(releasePrompt('gate: '+gate.reason), {schema:REL_SCHEMA,label:'release',phase:'Lock'}); return { outcome:'gated', reason:gate.reason } }

phase('Build')
const build = await agent(R([
  'Refresh lock + fence first: `cd ~/polymarket-bot && echo "feature:$(date +%s)" > .autopilot_lock && touch .autopilot_pause`. Record GLOBAL brain OOS cv_skill BEFORE (brain.json / models_state.json).',
  '',
  DESIGN,
  '',
  'Then: run `python3 bot.py test` && `python3 tests.py` && `python3 chartml.py` && `python3 ml.py`. Re-measure GLOBAL cv_skill AFTER. If any suite red, OR global skill regressed, OR the OOS-gate test fails: `git checkout -- . && rm -f .autopilot_pause`, built=false (report why + skills). Else `git add -A && git commit -m "FEATURE: per-category brain specialists (OOS-gated partial pooling)"`, `rm -f .autopilot_pause`. Return built, commit, files, test_summary, global_skill_before/after, oos_gate_proof, diffstat.',
  'NEVER touch .env. Never weaken a test to pass.',
].join('\n')), { schema: BUILD_SCHEMA, label:'build', phase:'Build' })
if (!build || !build.built) {
  log('BUILD -> not built: ' + (build?build.reason:'failed'))
  await agent(releasePrompt('build failed: '+(build?build.reason:'failed')), {schema:REL_SCHEMA,label:'release',phase:'Build'})
  return { outcome:'build-failed', reason: build?build.reason:'failed' }
}
log('BUILD -> ' + build.commit + ' (global skill ' + build.global_skill_before + ' -> ' + build.global_skill_after + ')')

phase('Verify')
const reviews = (await parallel([1,2,3].map(i => () => agent(R([
  'Hostile reviewer #'+i+'. Inspect the committed change: `cd ~/polymarket-bot && git show HEAD`.',
  'REFUTE it. violation=true (instant veto) if ANY of: the per-category OOS gate is missing or wrong (a category can tilt sizing without oos_skill>0); the global model regressed or is no longer the fallback; future-data leakage; dead-cohort counted as evidence; category re-fetched at decision time (latency/tag-drift); real-money/.env/safety touch. Verify the tests actually PROVE an OOS-negative category is a no-op and global skill is unchanged. Default refuted=true unless it clearly holds. Return {refuted, violation, reasoning}.',
].join('\n')), { schema: REVIEW_SCHEMA, label:'verify:'+i, phase:'Verify', agentType:'Explore' })))).filter(Boolean)
const violation = reviews.some(v => v.violation)
const pass = reviews.filter(v => v.refuted === false).length
const survives = !violation && pass >= 2 && reviews.length >= 2
log('VERIFY -> pass ' + pass + '/' + reviews.length + ' violation=' + violation + ' -> ' + (survives?'SURVIVES':'KILLED'))

phase('Ship')
if (!survives) {
  await agent(releasePrompt('review killed it (violation='+violation+', pass='+pass+'); discard '+build.commit), {schema:REL_SCHEMA,label:'release',phase:'Ship'})
  return { outcome:'review-killed', build, reviews }
}
const ship = await agent(R([
  'Passed review. SHIP it; bot must never be left dead.',
  '1. Refresh lock, ensure watchdog, `touch .autopilot_pause`.',
  '2. `pkill -f "bot.py paper"; sleep 2; cd ~/polymarket-bot && nohup caffeinate -i python3 bot.py paper >> bot.log 2>&1 &`; `sleep 12`.',
  '3. Verify :8765/api/health ok+balanced; exactly one bot (`pgrep -f "bot.py paper"|wc -l`==2).',
  '4. If BAD: `git reset --hard HEAD~1`, restart same way, sleep 12, re-verify reverted. shipped=false, rolled_back=true.',
  '5. Once healthy: `rm -f .autopilot_pause`. Append dated QUANT_LOG.md entry (what shipped, global skill before/after, OOS-gate, test tally, rollback HEAD~1), commit ONLY that file, `git push origin master 2>&1|tail -1||true`.',
  '6. Release lock: `grep -q "^feature:" .autopilot_lock 2>/dev/null && rm -f .autopilot_lock || true`.',
  'Return {shipped, rolled_back, reason, commit, health_after}.',
].join('\n')), { schema: SHIP_SCHEMA, label:'ship', phase:'Ship' })
log('SHIP -> ' + (ship && ship.shipped ? 'SHIPPED ' + ship.commit : 'rolled back / ' + (ship?ship.reason:'failed')))
return { outcome: ship && ship.shipped ? 'shipped' : 'rolled-back', ship, build }
