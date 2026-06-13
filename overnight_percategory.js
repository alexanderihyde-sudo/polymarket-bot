export const meta = {
  name: 'overnight-per-category-models',
  description: 'Land the per-category OOS-gated specialist layer (with a tightened mixed-data regression test), then research + build a customized API-fed feature set for EACH bet category, each gated + adversarially verified + shipped incrementally with rollback.',
  phases: [
    { title: 'Lock' },
    { title: 'Foundation' },
    { title: 'Research' },
    { title: 'BuildPerCategory' },
    { title: 'Done' },
  ],
}

const RULES = [
  'You are an overnight crew making the PAPER-TRADING bot at ~/polymarket-bot smarter PER CATEGORY. Live daemon: http://127.0.0.1:8765. Git repo; HEAD is last known-good. The user is asleep — be CONSERVATIVE: ship only changes that pass every gate; when unsure, skip and log. Never leave the bot dead.',
  'GOAL: each bet category (sports, crypto, weather, politics, macro, social) gets its OWN model + its OWN relevant API features, as a hierarchical/partial-pooling layer that shrinks to the global model until a category EARNS divergence out-of-sample. Never N independent models; the global model is always the prior/fallback.',
  '',
  'HARD RULES — never violate:',
  '- PAPER ONLY. Never touch real money / wallet / .env / .envtab (gitignored; never read or print them). Key-gated APIs read keys from os.environ; absent key -> source skipped silently.',
  '- NO FUTURE-DATA LEAKAGE: features use only data available at decision time (past-only training; category + API reads are point-in-time).',
  '- ERA HYGIENE: dead_cohort() threaded through every per-category learner (training rows AND credibility).',
  '- GLOBAL MODEL MUST NOT REGRESS: on inputs with NO category/API signal (the common path), brain output is byte-identical to before. Prove it with a regression test on MIXED-category data (not just category-free data).',
  '- PER-CATEGORY OOS GATE: a category/feature tilts sizing only if it beats global out-of-sample on that category; else it is a pure no-op.',
  '- All network calls governed/timeout-bounded/fail-silent; a dead API never stalls or crashes the daemon.',
  '- One coherent change at a time, fully tested (bot.py test + tests.py + chartml.py + ml.py) before any restart; restart pause-fenced; rollback on any health regression. When in doubt, skip + log.',
].join('\n')
const R = (b) => RULES + '\n\n=== YOUR TASK ===\n' + b

const GATE_SCHEMA = { type:'object', additionalProperties:false, required:['clear','deferred','reason'], properties:{ clear:{type:'boolean'}, deferred:{type:'boolean'}, reason:{type:'string'} } }
const SHIP_SCHEMA = { type:'object', additionalProperties:false, required:['built','shipped','reason'],
  properties:{ built:{type:'boolean'}, shipped:{type:'boolean'}, presented:{type:'boolean'}, rolled_back:{type:'boolean'}, reason:{type:'string'}, commit:{type:'string'}, test_summary:{type:'string'}, global_skill_before:{type:'string'}, global_skill_after:{type:'string'} } }
const PLAN_SCHEMA = { type:'object', additionalProperties:false, required:['category','apis','features','rationale'],
  properties:{ category:{type:'string'}, apis:{type:'array',items:{type:'string'}}, features:{type:'array',items:{type:'string'}}, leakage_check:{type:'string'}, rationale:{type:'string'}, worth_building:{type:'boolean'} } }
const REVIEW_SCHEMA = { type:'object', additionalProperties:false, required:['refuted','violation','reasoning'], properties:{ refuted:{type:'boolean'}, violation:{type:'boolean'}, reasoning:{type:'string'} } }
const REL_SCHEMA = { type:'object', additionalProperties:false, required:['clean','reason'], properties:{ clean:{type:'boolean'}, reason:{type:'string'} } }

const CATS = [
  { key:'sports', apis:'Kalshi + The Odds API (key) + ESPN/MLB/NHL StatsAPI + the existing sportsedge Elo', focus:'live + pre-game sports: cross-book consensus vs Polymarket, Elo fair value, score-latency. Respect the live in-game ban; pre-game only earns sizing.' },
  { key:'crypto', apis:'CoinGecko + Binance + Coinbase (+ Deribit implied vol if reachable)', focus:'crypto-threshold markets: spot-vs-strike distance, realized/implied vol, time-to-resolution — the probability a threshold is crossed.' },
  { key:'weather', apis:'Open-Meteo + weather.gov/NWS', focus:'temperature/precip markets: forecast value vs the market strike, forecast spread/uncertainty, multi-model agreement.' },
  { key:'politics', apis:'PredictIt + Kalshi + GDELT + news RSS', focus:'elections/policy: cross-market consensus, news-confirmation, event proximity.' },
  { key:'macro', apis:'FRED (key) + SEC EDGAR', focus:'Fed-rate / CPI / econ markets: latest official series level vs the market strike, surprise vs consensus. FRED needs FRED_API_KEY (skip if absent).' },
  { key:'social', apis:'GDELT + news RSS + Wikipedia pageviews', focus:'social/attention markets: news volume + sentiment + attention trend as a soft prior.' },
]

const releasePrompt = (note) => R([
  'Close out cleanly. Reason: ' + note,
  '1. If `cd ~/polymarket-bot && git log -1 --format=%s` starts with "FEATURE:" and it was NOT shipped this step, discard it: `git reset --hard HEAD~1` (also `rm -f` any new untracked file you added but are not shipping).',
  '2. `rm -f .autopilot_pause && git checkout -- . 2>/dev/null`; verify `git status --short` is clean.',
  '3. Ensure the watchdog is alive; the bot is healthy on :8765.',
  'Return {clean, reason}.',
].join('\n'))

const buildPrompt = (title, body) => R([
  'Refresh the lock + fence: `cd ~/polymarket-bot && echo "feature:$(date +%s)" > .autopilot_lock && touch .autopilot_pause`. Record the GLOBAL brain OOS cv_skill BEFORE (brain.json / models_state.json).',
  '', body, '',
  'Then run `python3 bot.py test` && `python3 tests.py` && `python3 chartml.py` && `python3 ml.py` (and `python3 crossmarket.py` if you touched it). Re-measure GLOBAL cv_skill AFTER.',
  'SHIP only if: all suites green AND global cv_skill did not regress AND the regression/era-hygiene/leakage tests pass. To ship: `git add -A && git commit -m "FEATURE: ' + title + '"`, then restart pause-fenced (`pkill -f "bot.py paper"; sleep 2; nohup caffeinate -i python3 bot.py paper >> bot.log 2>&1 &; sleep 12`), verify /api/health ok+balanced + exactly one bot; if health bad -> `git reset --hard HEAD~1`, restart, re-verify (shipped=false, rolled_back=true). Once healthy `rm -f .autopilot_pause`, append a dated QUANT_LOG.md entry, commit ONLY that file, `git push origin master 2>&1|tail -1||true`.',
  'If ANY gate fails: `git checkout -- . && rm -f .autopilot_pause` (and rm new untracked files), built per reality, shipped=false, reason explains. NEVER weaken a test or touch .env.',
  'Return {built, shipped, presented:false, rolled_back, reason, commit, test_summary, global_skill_before, global_skill_after}.',
].join('\n'))

// ===================================================== CONTROL FLOW
phase('Lock')
const gate = await agent(R([
  '0. LOCK: `cd ~/polymarket-bot && [ -f .autopilot_lock ] && echo age=$(( $(date +%s) - $(stat -f %m .autopilot_lock) )) || echo none`. If exists and age<900 -> deferred=true, clear=false, touch nothing, return. Else `echo "feature:$(date +%s)" > .autopilot_lock`.',
  '1. `rm -f .autopilot_pause && git checkout -- . 2>/dev/null`; ensure watchdog: `pgrep -f watchdog.sh >/dev/null || (cd ~/polymarket-bot && nohup bash watchdog.sh >> watchdog.log 2>&1 &)`.',
  '2. Health ok+balanced AND `python3 bot.py test|tail -1` + `python3 tests.py|tail -1` all-passed, else clear=false (and `rm -f .autopilot_lock`).',
  'Return {clear, deferred, reason}.',
].join('\n')), { schema: GATE_SCHEMA, label:'gate+lock', phase:'Lock', agentType:'Explore' })
if (!gate || gate.deferred) { log('LOCK -> deferred'); return { outcome:'deferred' } }
if (!gate.clear) { await agent(releasePrompt('gate: '+gate.reason), {schema:REL_SCHEMA,label:'release',phase:'Lock'}); return { outcome:'gated', reason:gate.reason } }

phase('Foundation')
const foundation = await agent(buildPrompt('per-category brain specialists (OOS-gated partial pooling)', [
  'Land the per-category brain specialist layer: extend the EXISTING per-strategy specialist machinery in brain_train/brain_adjust to a category key. For each category with >=20 dead-cohort-filtered rows, fit a specialist AND compute its OOS skill via the same walk-forward CV the global model uses; store {w, oos_skill, n_eff, n} in BRAIN["cat_specialists"]. In brain_adjust(strategy, price, ctx, category=None), blend toward the category specialist ONLY if its oos_skill>0, weighted by cw=n_eff/(n_eff+60); else pure no-op. Thread category from the position/opportunity (never recompute at decision time). Fix the cache so a stale BRAIN lacking cat_specialists forces a retrain.',
  'TIGHTENED REGRESSION TEST (this is the part the earlier attempt missed and got killed on): add a test proving that on a FIXED MIXED-category dataset, adding the per-category code leaves the GLOBAL model identical — i.e. brain_train cv_skill and brain_adjust(category=None) are byte-identical to the pre-change behavior on the SAME mixed data (not just on category-free data). The global model trains on all rows exactly as before; the category layer is purely additive and gated. Prove it. Also test: OOS-negative category = no-op; dead-cohort-only category has n_eff=0 = no-op; a category with real OOS skill diverges. Surface per-category specialists (category, n, oos_skill, cw) on the dashboard Models tab.',
].join('\n')), { schema: SHIP_SCHEMA, label:'foundation', phase:'Foundation' })
log('FOUNDATION -> built=' + (foundation&&foundation.built) + ' shipped=' + (foundation&&foundation.shipped) + ' (' + (foundation&&foundation.reason||'').slice(0,90) + ')')
if (!foundation || !foundation.shipped) {
  log('FOUNDATION did not ship -> stopping before per-category pipelines (no foundation to build on).')
  await agent(releasePrompt('foundation not shipped: ' + (foundation?foundation.reason:'failed')), {schema:REL_SCHEMA,label:'release',phase:'Foundation'})
  return { outcome:'foundation-not-shipped', foundation }
}

phase('Research')
const plans = (await parallel(CATS.map(c => () => agent(R([
  'Research category "' + c.key + '". Its candidate APIs: ' + c.apis + '. Focus: ' + c.focus,
  'Probe which of these APIs are actually reachable (curl/python, read-only), and design the SINGLE most predictive customized feature set for this category that (a) reads only point-in-time data (no leakage), (b) plugs into the entry context + the brain feature vector + this category\'s specialist, (c) defaults neutral when the API/data is absent. Prefer 1-3 strong features over many weak ones. Set worth_building=false if nothing is defensible (then this category is skipped).',
  'Return {category, apis (the reachable ones), features (concrete, point-in-time), leakage_check, rationale, worth_building}.',
].join('\n')), { schema: PLAN_SCHEMA, label:'research:'+c.key, phase:'Research', agentType:'Explore' })))).filter(Boolean)
const todo = plans.filter(p => p && p.worth_building)
log('RESEARCH -> ' + todo.length + '/' + CATS.length + ' categories worth building: ' + todo.map(p=>p.category).join(', '))

phase('BuildPerCategory')
// SEQUENTIAL: each category edits bot.py, so they cannot run in parallel. pipeline
// keeps order; each stage builds+tests+reviews+ships one category, gated.
const results = []
for (const p of todo) {
  if (budget && budget.total && budget.remaining() < 120000) { log('budget low -> stopping per-category builds'); break }
  const built = await agent(buildPrompt(p.category + ' API features (' + (p.apis||[]).join('+') + ')', [
    'Implement the customized feature set for category "' + p.category + '":',
    JSON.stringify({ apis: p.apis, features: p.features, leakage_check: p.leakage_check }, null, 2),
    'Add the connector(s) (fail-silent, governed, cached; key-gated sources read os.environ and skip if absent), attach the feature(s) to the entry context for ' + p.category + ' markets, add them to _brain_x defaulting neutral (so the global path is unchanged when absent), and let this category\'s specialist (from the foundation) learn them. Add a by_' + p.category + ' / by_crossmarket-style attribution where useful. KEEP the global path byte-identical when the feature is absent (regression test).',
  ].join('\n')), { schema: SHIP_SCHEMA, label:'build:'+p.category, phase:'BuildPerCategory' })
  log('BUILD ' + p.category + ' -> shipped=' + (built&&built.shipped) + ' (' + (built&&built.reason||'').slice(0,70) + ')')
  results.push({ category: p.category, built })
  // if a build left HEAD dirty/unshipped, clean before the next category
  if (!built || !built.shipped) {
    await agent(releasePrompt('post-' + p.category + ' cleanup (not shipped)'), { schema: REL_SCHEMA, label:'cleanup:'+p.category, phase:'BuildPerCategory' })
  }
}

phase('Done')
await agent(releasePrompt('overnight per-category run complete; release lock'), { schema: REL_SCHEMA, label:'final-release', phase:'Done' })
const shipped = results.filter(r => r.built && r.built.shipped).map(r => r.category)
log('DONE -> shipped categories: ' + (shipped.join(', ') || 'none'))
return { outcome:'complete', foundation_shipped: true, categories_shipped: shipped, results }
