export const meta = {
  name: 'per-category-learning',
  description: 'Build the foundation for per-category learning as a hierarchical/partial-pooling layer (specialists that shrink to global until they earn divergence), tested + adversarially verified + champion/challenger gated',
  phases: [
    { title: 'Lock' },
    { title: 'Investigate' },
    { title: 'Design' },
    { title: 'Build' },
    { title: 'Verify' },
    { title: 'Ship' },
  ],
}

const RULES = [
  'You are part of a focused build crew adding PER-CATEGORY learning to the PAPER-TRADING bot at ~/polymarket-bot. The live daemon runs on http://127.0.0.1:8765. It is a git repo; HEAD is last known-good.',
  'GOAL: each market category gets its OWN learning + training, but as a HIERARCHICAL / PARTIAL-POOLING layer — NOT N independent models. A category specialist BLENDS with the global model, weighted by that category\'s own evidence (credibility n_eff/(n_eff+k)): thin categories shrink to global, only data-rich categories diverge. This is the central design principle; violating it (independent per-category models on tiny samples) would overfit and make the bot DUMBER.',
  '',
  'HARD RULES — never violate:',
  '- PAPER TRADING ONLY. Never touch real money / wallet / .env / .envtab (gitignored — keep it so; never read or print them).',
  '- NO FUTURE-DATA LEAKAGE: per-category training is past-only, same as the global model ("trained only on the PAST").',
  '- ERA HYGIENE: dead_cohort() (bot.py) must be threaded through EVERY new per-category learner — never train on or count dead-cohort trades.',
  '- The GLOBAL model stays the prior/fallback and must NOT regress: global OOS skill after the change must be >= before.',
  '- NO per-category sizing influence without OUT-OF-SAMPLE evidence on that category (champion/challenger per category); otherwise the category defers to global. Live settled money outranks every backtest.',
  '- Ship ONE coherent, fully-tested increment. When in doubt, present the plan instead of shipping.',
].join('\n')
const R = (b) => RULES + '\n\n=== YOUR TASK ===\n' + b

const GATE_SCHEMA = { type:'object', additionalProperties:false, required:['clear','deferred','reason'],
  properties:{ clear:{type:'boolean'}, deferred:{type:'boolean'}, reason:{type:'string'}, tests_green:{type:'boolean'} } }
const FIND_SCHEMA = { type:'object', additionalProperties:false, required:['area','summary','insertion_points'],
  properties:{ area:{type:'string'}, summary:{type:'string'}, insertion_points:{type:'array',items:{type:'string'}},
    functions:{type:'array',items:{type:'string'}}, dead_cohort_notes:{type:'string'} } }
const DESIGN_SCHEMA = { type:'object', additionalProperties:false, required:['design','files','shrinkage_mechanism','overfit_guard','era_hygiene_plan','regression_check','risk','ship_or_present'],
  properties:{ design:{type:'string'}, files:{type:'array',items:{type:'string'}}, shrinkage_mechanism:{type:'string'},
    overfit_guard:{type:'string'}, era_hygiene_plan:{type:'string'}, leakage_check:{type:'string'},
    regression_check:{type:'string'}, dashboard_plan:{type:'string'}, risk:{type:'string'},
    ship_or_present:{type:'string', enum:['ship','present'], description:'present if the safe increment is still too large/risky to auto-ship'} } }
const CRIT_SCHEMA = { type:'object', additionalProperties:false, required:['sound','problems'],
  properties:{ sound:{type:'boolean', description:'true = design is safe & sound to build'}, problems:{type:'array',items:{type:'string'}}, must_fix:{type:'string'} } }
const BUILD_SCHEMA = { type:'object', additionalProperties:false, required:['built','reason'],
  properties:{ built:{type:'boolean'}, reason:{type:'string'}, commit:{type:'string'}, files:{type:'array',items:{type:'string'}},
    test_summary:{type:'string'}, global_skill_before:{type:'string'}, global_skill_after:{type:'string'}, diffstat:{type:'string'} } }
const REVIEW_SCHEMA = { type:'object', additionalProperties:false, required:['refuted','violation','reasoning'],
  properties:{ refuted:{type:'boolean'}, violation:{type:'boolean', description:'leakage / era-hygiene break / global regression / safety-rail touch (instant veto)'}, reasoning:{type:'string'} } }
const SHIP_SCHEMA = { type:'object', additionalProperties:false, required:['shipped','reason'],
  properties:{ shipped:{type:'boolean'}, rolled_back:{type:'boolean'}, presented:{type:'boolean'}, reason:{type:'string'}, commit:{type:'string'}, health_after:{type:'string'} } }
const REL_SCHEMA = { type:'object', additionalProperties:false, required:['clean','reason'], properties:{ clean:{type:'boolean'}, reason:{type:'string'} } }

const AREAS = [
  { key:'categories', focus:'How categories are DEFINED and tracked: market_category(), the cluster taxonomy (weather/crypto-price/sports-game/social-posts/other), category_budgets, and how a trade/position carries its category. List the exact functions + where category is available at decision time.' },
  { key:'learning', focus:'compute_learning() and the existing per-category BLOCK logic (band/category blocks, multipliers, pause). How per-strategy learning is structured today, and exactly where a per-category dimension with credibility shrinkage would plug in. Note every place account["settled"] is read and whether dead_cohort() is applied.' },
  { key:'brain', focus:'brain_train() + the existing PER-STRATEGY specialist pattern (n>=20, blended with global, credibility-weighted), the feature builder, CV/holdout, and how brain_adjust applies sizing. This per-strategy specialist machinery is the template to extend to per-category. Note dead_cohort threading.' },
  { key:'sizing', focus:'Where sizing/gating consumes the learning/brain signals (kelly_dollars, model_multiplier, brain_adjust) — the exact insertion point where a per-category specialist would blend in, and how to keep the global model as the fallback.' },
]

const releasePrompt = (note) => R([
  'Close out the per-category build cleanly. Reason: ' + note,
  '1. If `cd ~/polymarket-bot && git log -1 --format=%s` starts with "FEATURE:" and it was NOT shipped this run, discard it: `git reset --hard HEAD~1`. Else leave HEAD.',
  '2. `rm -f .autopilot_pause && git checkout -- . 2>/dev/null`; verify `git status --short` is empty.',
  '3. Ensure the watchdog is alive and the bot is healthy on :8765.',
  '4. RELEASE THE LOCK if it is ours: `grep -q "^feature:" .autopilot_lock 2>/dev/null && rm -f .autopilot_lock || true`.',
  'Return {clean, reason}.',
].join('\n'))

// ===================================================== CONTROL FLOW
phase('Lock')
const gate = await agent(R([
  '0. LOCK: `cd ~/polymarket-bot && [ -f .autopilot_lock ] && echo age=$(( $(date +%s) - $(stat -f %m .autopilot_lock) )) || echo none`. If a lock exists and age<900 -> another loop is mid-cycle: deferred=true, clear=false, touch nothing, return. Else acquire: `echo "feature:$(date +%s)" > .autopilot_lock`.',
  '1. Housekeeping: `rm -f .autopilot_pause && git checkout -- . 2>/dev/null`; ensure watchdog: `pgrep -f watchdog.sh >/dev/null || (cd ~/polymarket-bot && nohup bash watchdog.sh >> watchdog.log 2>&1 &)`.',
  '2. Health: curl :8765/api/health must be ok+balanced. Tests: `python3 bot.py test 2>&1|tail -1` and `python3 tests.py 2>&1|tail -1` must be all-passed. If not -> clear=false (and `rm -f .autopilot_lock` since you took it).',
  'Return {clear, deferred, reason, tests_green}.',
].join('\n')), { schema: GATE_SCHEMA, label:'gate+lock', phase:'Lock', agentType:'Explore' })
if (!gate || gate.deferred) { log('LOCK -> deferred: ' + (gate?gate.reason:'gate failed')); return { outcome:'deferred' } }
if (!gate.clear) { log('GATE -> not clear: ' + gate.reason); await agent(releasePrompt('gate not clear: '+gate.reason), {schema:REL_SCHEMA, label:'release', phase:'Lock'}); return { outcome:'gated', reason:gate.reason } }

phase('Investigate')
const findings = (await parallel(AREAS.map(a => () => agent(R([
  'Investigate ONLY this area and report precisely (read-only; cite file:line). Focus: ' + a.focus,
  'Return {area, summary, insertion_points (exact file:line places a per-category layer plugs in), functions, dead_cohort_notes}.',
].join('\n')), { schema: FIND_SCHEMA, label:'investigate:'+a.key, phase:'Investigate', agentType:'Explore' })))).filter(Boolean)
log('INVESTIGATE -> ' + findings.length + ' area maps')

phase('Design')
let design = await agent(R([
  'Using these investigation findings, design the MINIMAL coherent first increment of per-category learning that honors the partial-pooling principle. Findings:',
  JSON.stringify(findings, null, 2),
  '',
  'Your design MUST specify: the shrinkage mechanism (how a category specialist blends with global by credibility), the overfit guard (why thin categories stay safe), the era-hygiene plan (dead_cohort through every new learner), the leakage check, the regression check (how you prove global OOS skill does not drop), the dashboard surfacing, and the risk. Choose ship_or_present: "present" if the safe increment is still too large to auto-ship.',
  'Prefer extending the EXISTING per-strategy specialist + credibility machinery to a per-category key over inventing new ML.',
].join('\n')), { schema: DESIGN_SCHEMA, label:'design', phase:'Design' })
// adversarial critique of the DESIGN (the overfit risk is the crux)
const crits = (await parallel([1,2,3].map(i => () => agent(R([
  'Hostile design reviewer #'+i+'. Here is a proposed per-category-learning design:',
  JSON.stringify(design, null, 2),
  'Try to show it is UNSAFE or unsound. Check above all: does it overfit thin categories (independent models / no real shrinkage)? Does the global model regress or get replaced rather than used as fallback? Any future-data leakage? Does dead_cohort reach every new learner? Is it actually one coherent testable increment? Default sound=false unless it clearly holds. Return {sound, problems, must_fix}.',
].join('\n')), { schema: CRIT_SCHEMA, label:'design-critic:'+i, phase:'Design', agentType:'Explore' })))).filter(Boolean)
const soundVotes = crits.filter(c => c.sound).length
log('DESIGN -> ' + (design?design.ship_or_present:'(none)') + '; critics sound ' + soundVotes + '/' + crits.length)
if (!design || soundVotes < 2 || design.ship_or_present === 'present') {
  const probs = crits.flatMap(c => c.problems||[]).slice(0,8)
  log('DESIGN -> not auto-shipping (present plan). issues: ' + probs.join(' | '))
  await agent(releasePrompt('design presented, not shipped'), {schema:REL_SCHEMA, label:'release', phase:'Design'})
  return { outcome:'design-presented', design, critiques: crits }
}

phase('Build')
const build = await agent(R([
  'Implement EXACTLY this critic-approved design as ONE coherent increment:',
  JSON.stringify(design, null, 2),
  '',
  '0. Refresh the lock + fence: `cd ~/polymarket-bot && echo "feature:$(date +%s)" > .autopilot_lock && touch .autopilot_pause`. Record the GLOBAL brain OOS skill BEFORE your change (from models_state.json / brain.json oos/cv_skill).',
  '1. Make the change. Thread dead_cohort() through every new per-category learner. Keep the global model as fallback. No leakage.',
  '2. Run ALL suites: `python3 bot.py test` && `python3 tests.py` && `python3 chartml.py` && `python3 ml.py`. Add/extend tests for the per-category shrinkage (thin category -> ~global; rich category -> diverges) and era hygiene.',
  '3. Re-measure GLOBAL brain OOS skill AFTER. If any suite is red OR global skill regressed OR shrinkage tests fail: `git checkout -- . && rm -f .autopilot_pause`, built=false (report skills + failure).',
  '4. If all green and global skill not regressed: `git add -A && git commit -m "FEATURE: per-category learning (partial-pooling specialists)"`, then `rm -f .autopilot_pause`. Return built, commit, files, test_summary, global_skill_before/after, diffstat=`git show --stat HEAD`.',
  'NEVER touch .env. Never weaken a test to pass.',
].join('\n')), { schema: BUILD_SCHEMA, label:'build', phase:'Build' })
if (!build || !build.built) {
  log('BUILD -> not built: ' + (build?build.reason:'failed'))
  await agent(releasePrompt('build failed: ' + (build?build.reason:'failed')), {schema:REL_SCHEMA, label:'release', phase:'Build'})
  return { outcome:'build-failed', reason: build?build.reason:'failed', design }
}
log('BUILD -> committed ' + build.commit + ' (global skill ' + build.global_skill_before + ' -> ' + build.global_skill_after + ')')

phase('Verify')
const reviews = (await parallel([1,2,3].map(i => () => agent(R([
  'Hostile reviewer #'+i+'. A per-category-learning change is committed at HEAD. Inspect: `cd ~/polymarket-bot && git show HEAD`.',
  'REFUTE it. violation=true (instant veto) for: future-data leakage, dead-cohort counted as evidence, the global model regressed or was replaced (not kept as fallback), real-money/.env/safety-rail touch, or independent per-category models with no genuine shrinkage (overfit). Also judge: do the shrinkage + era-hygiene tests actually prove the claim? Default refuted=true unless it clearly holds. Return {refuted, violation, reasoning}.',
].join('\n')), { schema: REVIEW_SCHEMA, label:'verify:'+i, phase:'Verify', agentType:'Explore' })))).filter(Boolean)
const violation = reviews.some(v => v.violation)
const pass = reviews.filter(v => v.refuted === false).length
const survives = !violation && pass >= 2 && reviews.length >= 2
log('VERIFY -> pass ' + pass + '/' + reviews.length + ' violation=' + violation + ' -> ' + (survives?'SURVIVES':'KILLED'))

phase('Ship')
if (!survives) {
  await agent(releasePrompt('review killed it (violation='+violation+', pass='+pass+'); discard commit '+build.commit), {schema:REL_SCHEMA, label:'release', phase:'Ship'})
  return { outcome:'review-killed', build, reviews }
}
const ship = await agent(R([
  'The per-category change passed review. SHIP it. The bot must never be left dead.',
  '1. Refresh lock, ensure watchdog alive, `touch .autopilot_pause`.',
  '2. Restart: `pkill -f "bot.py paper"; sleep 2; cd ~/polymarket-bot && nohup caffeinate -i python3 bot.py paper >> bot.log 2>&1 &` then `sleep 12`.',
  '3. Verify :8765/api/health ok+balanced; exactly one bot (pgrep -f "bot.py paper" | wc -l == 2).',
  '4. If BAD: `git reset --hard HEAD~1`, restart same way, sleep 12, re-verify on reverted code. shipped=false, rolled_back=true.',
  '5. Once healthy: `rm -f .autopilot_pause`. Append a dated QUANT_LOG.md entry (what shipped, global skill before/after, test tally, rollback commit HEAD~1), commit ONLY that file, `git push origin master 2>&1|tail -1||true`.',
  '6. RELEASE LOCK: `grep -q "^feature:" .autopilot_lock 2>/dev/null && rm -f .autopilot_lock || true`.',
  'Return {shipped, rolled_back, presented:false, reason, commit, health_after}.',
].join('\n')), { schema: SHIP_SCHEMA, label:'ship', phase:'Ship' })
log('SHIP -> ' + (ship && ship.shipped ? 'SHIPPED ' + ship.commit : 'rolled back / ' + (ship?ship.reason:'failed')))
return { outcome: ship && ship.shipped ? 'shipped' : 'rolled-back', ship, build, design }
