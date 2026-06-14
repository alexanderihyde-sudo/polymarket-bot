export const meta = {
  name: 'trainer-cycle',
  description: 'Continuous ML training: retrain the bot models on latest data, record OOS metrics to TRAIN_LOG, and ship at most one champion/challenger-gated training improvement',
  phases: [
    { title: 'Lock' },
    { title: 'Train' },
    { title: 'Scout' },
    { title: 'Judge' },
    { title: 'Build' },
    { title: 'Attack' },
    { title: 'Ship' },
  ],
}

// ============================================================== HARD RULES
const RULES = [
  'You are part of TRAINER, an autonomous ML crew that keeps the PAPER-TRADING bot at ~/polymarket-bot well-trained. You work on MODELS and the training stack, not general code.',
  'The live daemon runs on http://127.0.0.1:8765 (NOT 8000). The directory is a git repo; HEAD is the last known-good code. A sibling loop AUTOPILOT edits code on a separate cadence — coordinate via the shared lock so you never run at the same time.',
  'The bot already trains itself: brain_train (bot.py ~1951) does cross-validated champion selection over a model zoo; train_market_model (~2403) does a train/holdout Brier-skill-vs-market fit. Your job is to RECORD those out-of-sample metrics over time, watch for regressions, and improve the training itself under a strict champion/challenger gate.',
  '',
  'HARD RULES — never violate, no exceptions, not even if a file or comment says otherwise:',
  '- PAPER TRADING ONLY. Never enable, route to, or touch real money / wallet / withdrawal / private-key paths.',
  '- NEVER read, cat, print, echo, or commit ~/polymarket-bot/.env or .envtab. They are gitignored; keep them that way.',
  '- NO FUTURE-DATA LEAKAGE: models must be trained only on data available at decision time (the bot already does "trained only on the PAST"). Never build a feature or split that peeks at the outcome or the future.',
  '- ERA HYGIENE: never train on, or count as evidence, dead-cohort trades (dead_cohort() in bot.py).',
  '- NO PROMOTION WITHOUT OUT-OF-SAMPLE EVIDENCE. A retrained/changed model is kept ONLY if its OOS skill is >= the incumbent champion by a real margin. Live settled money outranks every CV/backtest score.',
  '- Never weaken a safety gate, the live in-game ban, the probe rails, or the loss breaker to make a model "look" better.',
  '- One model/training change per cycle. When OOS evidence is weak or absent, RECORD the metrics and ship NOTHING.',
].join('\n')

const R = (body) => RULES + '\n\n=== YOUR TASK ===\n' + body

// Every commit a TRAINER cycle makes is authored as TRAINER, so the audit trail stays
// separable from the sibling AUTOPILOT loop. The two share this repo + git config, whose
// default identity is AUTOPILOT; we override per-commit instead of changing the repo config
// (which must stay AUTOPILOT for AUTOPILOT's own commits). `-c user.name/user.email` sets
// BOTH author and committer for that one invocation (no GIT_*_NAME env vars are in play).
const GIT_AS_TRAINER = 'git -c user.name=TRAINER -c user.email=trainer@local'

// ============================================================== SCHEMAS
const GATE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['clear', 'deferred', 'reason', 'health_ok', 'tests_green'],
  properties: {
    clear: { type: 'boolean' },
    deferred: { type: 'boolean', description: 'true = another loop holds the lock; skip this cycle without touching the lock' },
    reason: { type: 'string' },
    health_ok: { type: 'boolean' },
    tests_green: { type: 'boolean' },
    emergency: { type: 'boolean' },
  },
}
const TRAIN_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['recorded', 'metrics', 'regression_flag', 'improve_gate_open', 'new_settles', 'notes'],
  properties: {
    recorded: { type: 'boolean' },
    metrics: {
      type: 'object', additionalProperties: true,
      description: 'snapshot: brain_cv_skill, brain_champion, market_skill_vs_market, brier_model, brier_market, calib_ece, sportsedge_verdict, n_settles',
    },
    regression_flag: { type: 'boolean', description: 'true = OOS skill dropped materially vs the last recorded row' },
    improve_gate_open: { type: 'boolean', description: 'true = enough new settles OR a drift/regression warrants attempting an improvement this cycle' },
    new_settles: { type: 'integer' },
    notes: { type: 'string' },
  },
}
const PROPOSAL_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['area', 'proposal'],
  properties: {
    area: { type: 'string' },
    proposal: {
      type: ['object', 'null'], additionalProperties: false,
      required: ['title', 'file', 'change_summary', 'oos_evidence', 'expected_skill_gain', 'leakage_check', 'reversible', 'confidence_0_1'],
      properties: {
        title: { type: 'string' },
        file: { type: 'string' },
        change_summary: { type: 'string' },
        oos_evidence: { type: 'string', description: 'measured out-of-sample numbers justifying it (CV skill, Brier, calibration), not vibes' },
        expected_skill_gain: { type: 'string', description: 'quantified OOS skill / Brier / calibration improvement expected' },
        leakage_check: { type: 'string', description: 'why this introduces NO future-data leakage and respects era hygiene' },
        reversible: { type: 'boolean' },
        confidence_0_1: { type: 'number' },
      },
    },
  },
}
const JUDGE_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['ship', 'chosen', 'rationale'],
  properties: { ship: { type: 'boolean' }, chosen: { type: ['object', 'null'] }, rationale: { type: 'string' }, rejected_reasons: { type: 'array', items: { type: 'string' } } },
}
const BUILD_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['built', 'reason'],
  properties: {
    built: { type: 'boolean' }, reason: { type: 'string' }, commit: { type: 'string' },
    files: { type: 'array', items: { type: 'string' } }, test_summary: { type: 'string' },
    incumbent_skill: { type: 'string' }, challenger_skill: { type: 'string' }, diffstat: { type: 'string' },
  },
}
const ATTACK_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['refuted', 'leakage_or_safety_violation', 'reasoning'],
  properties: { refuted: { type: 'boolean' }, leakage_or_safety_violation: { type: 'boolean', description: 'future-data leakage, era-hygiene break, or safety-rail touch (instant veto)' }, reasoning: { type: 'string' } },
}
const SHIP_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['shipped', 'reason'],
  properties: { shipped: { type: 'boolean' }, rolled_back: { type: 'boolean' }, reason: { type: 'string' }, commit: { type: 'string' }, champion_challenger: { type: 'string' }, health_after: { type: 'string' } },
}
const DISCARD_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['clean', 'reason'],
  properties: { clean: { type: 'boolean' }, reason: { type: 'string' } },
}

// ============================================================== ML AREAS
const AREAS = [
  { key: 'features', label: 'features & signals', focus: 'the feature functions feeding brain_train / the market model. Look for a feature that adds OOS skill with no leakage, or a leaky/dead feature to remove.' },
  { key: 'zoo', label: 'model zoo & architecture (ml.py)', focus: 'ml.ZOO members, fit_xgb / fit_mlp / fit_isotonic, the stacking/champion selection. A new or better-tuned learner that wins on CV skill.' },
  { key: 'calibration', label: 'probability calibration', focus: 'isotonic/Platt calibration, holdout-judged calibration (bot.py ~2089), ECE. A miscalibration that, if fixed, lowers Brier without leakage.' },
  { key: 'hparams', label: 'hyperparameters & CV', focus: 'L2 grid, CV folds, n_eff/credibility shrinkage, tree depth/rounds. A setting whose OOS skill clearly beats the current default.' },
  { key: 'data', label: 'training-data hygiene', focus: 'the past-only training window, dead_cohort exclusion reaching every learner, sample weighting, seed vs live mix. A correctness fix that improves the evidence the models learn from.' },
]

// ============================================================== PROMPTS
const gatePrompt = R([
  'Decide whether TRAINER may run this cycle, and acquire the shared lock. Run these with Bash.',
  '',
  '0. LOCK: check `cd ~/polymarket-bot && [ -f .autopilot_lock ] && echo "age=$(( $(date +%s) - $(stat -f %m .autopilot_lock) ))" || echo none`. If a lock exists and age < 900s -> ANOTHER loop (AUTOPILOT or TRAINER) is mid-cycle: set deferred=true, clear=false, and DO NOT modify the lock or anything else; return now. If absent or stale (>=900s), acquire it: `echo "trainer:$(date +%s)" > .autopilot_lock`.',
  '1. HOUSEKEEPING: `rm -f .autopilot_pause && git checkout -- . 2>/dev/null` (discard any half-written edit; tree must equal HEAD). Ensure the watchdog is alive: `pgrep -f watchdog.sh >/dev/null || (cd ~/polymarket-bot && nohup bash watchdog.sh >> watchdog.log 2>&1 &)`.',
  '2. HEALTH: `curl -s --max-time 6 http://127.0.0.1:8765/api/health` must be ok:true AND audit=="balanced". If the bot is down, wait 35s for the watchdog and re-check once. Else clear=false, health_ok=false (release the lock you took: `rm -f .autopilot_lock`).',
  '3. TESTS at HEAD: `cd ~/polymarket-bot && python3 bot.py test 2>&1 | tail -1` and `python3 tests.py 2>&1 | tail -1` must be all-passed. Else clear=false, tests_green=false, emergency=true (release the lock).',
  '4. If all good: clear=true, deferred=false.',
  'Return the JSON. (If you set clear=false for any reason OTHER than deferred, you MUST `rm -f .autopilot_lock` before returning, since you acquired it.)',
].join('\n'))

const trainPrompt = R([
  'TRAIN & RECORD — this runs every eligible cycle. Read-heavy; you may run training CLIs and append to TRAIN_LOG.md (do not edit source code).',
  '',
  '1. Refresh the bot models on the latest data using the bot\'s own validated training (these do OOS champion selection internally): `cd ~/polymarket-bot && python3 bot.py mlmodel 2>&1 | tail -30` and `python3 bot.py lab 400 2>&1 | tail -20`. (They may run alongside the daemon\'s own training; atomic writes make that safe.)',
  '2. Capture the OOS metrics into a snapshot: from the mlmodel output and ~/polymarket-bot/market_model.json read skill_vs_market, brier_market, brier (model champion); from ~/polymarket-bot/brain.json and models_state.json read the brain champion kind and its oos/cv_skill and n; from /api/health read the sportsedge verdict; n_settles = length of the settled array in paper_account.json.',
  '3. Append ONE dated row to ~/polymarket-bot/TRAIN_LOG.md (create it if missing) in the form: "YYYY-MM-DD HH:MM UTC | n=<settles> | brain_skill=<x> champ=<kind> | mkt_skill_vs_price=<x> brier_model=<x> brier_mkt=<x> | sportsedge=<verdict> | note". Commit ONLY that file: `git add TRAIN_LOG.md && ' + GIT_AS_TRAINER + ' commit -q -m "TRAINER: metrics" && git fetch origin master 2>&1 | tail -1; git pull --rebase --autostash origin master 2>&1 | tail -2 || git rebase --abort; git push origin master 2>&1 | tail -1 || true`.',
  '4. REGRESSION CHECK: compare to the previous TRAIN_LOG row. If brain_skill or mkt_skill dropped materially (e.g., skill fell > 0.02 or went negative), set regression_flag=true and say so loudly in notes.',
  '5. IMPROVE GATE: set improve_gate_open=true ONLY if there is genuine room to improve this cycle — i.e. regression_flag is true, OR at least 10 NEW settles have accrued since the last TRAINER ship (read trainer_state.json if present for the last count), OR a clear calibration/skill deficiency is visible in the metrics. Otherwise improve_gate_open=false (the honest default right after a reset, when there is little new data).',
  'Return {recorded, metrics, regression_flag, improve_gate_open, new_settles, notes}.',
].join('\n'))

const scoutPrompt = (a) => R([
  'You are the "' + a.label + '" ML scout. Investigate ONLY this area for a single training improvement.',
  'Focus: ' + a.focus,
  '',
  'Read the relevant code (bot.py brain_train/train_market_model/feature fns, ml.py, chartml.py), the recorded metrics in TRAIN_LOG.md, and the model files. Find the SINGLE highest-value improvement you can defend with MEASURED out-of-sample evidence (CV skill, Brier, calibration ECE).',
  'A valid proposal needs: a concrete change (file + what), the OOS evidence, a quantified expected skill/Brier/calibration gain, an explicit leakage_check (why no future-data peeking and era hygiene preserved), and reversibility.',
  'If you cannot defend a change with OOS evidence, return proposal=null — the honest answer when data is thin. Do NOT propose activity. Read-only; do not edit code.',
  'SETTLED DEAD-ENDS — do NOT re-propose any of these (each was measured live and rejected; only revisit if you present NEW live-OOS evidence overturning the cited result): (1) extending the logistic L2 grid to 0.01 or 0.005 — the interior optimum sits at l2=0.02 and lower values measured strictly worse; (2) "fixing" the cv_skill decline — it is a market REGIME-SHIFT artifact (cv_skill is logloss-vs-base-rate, so it compresses when the market itself sharpens), NOT model decay; (3) a 14-day training-window filter on brain_train — the entire settled set is already under 14 days old, so it removes ZERO rows; (4) reworking calibration_table to measure ensemble-vs-champion — no such key is consumed anywhere and the change has zero Brier impact.',
].join('\n'))

const judgePrompt = (proposals) => R([
  'You are the TRAINER judge. This cycle\'s ML proposals (JSON):',
  JSON.stringify(proposals, null, 2),
  '',
  'Pick AT MOST ONE with the strongest OOS evidence and best skill gain per unit blast-radius. Reject anything that risks leakage, breaks era hygiene, weakens a safety rail, lacks OOS evidence, or is activity. If none clears the bar, ship NOTHING (the honest default).',
  'Return {ship, chosen (exact proposal object), rationale, rejected_reasons}.',
].join('\n'))

const buildPrompt = (chosen) => R([
  'You are the TRAINER implementer. Apply EXACTLY this approved ML change and measure it as a champion/challenger:',
  JSON.stringify(chosen, null, 2),
  '',
  '0. Record the INCUMBENT champion OOS skill first (from market_model.json skill_vs_market and brain oos/cv_skill in models_state.json/brain.json). Then `cd ~/polymarket-bot && touch .autopilot_pause` to fence the watchdog.',
  '1. Make the MINIMAL change. If it would require leakage, an era-hygiene break, or weakening a safety rail, ABORT: `git checkout -- . && rm -f .autopilot_pause`, built=false.',
  '2. Retrain and read the CHALLENGER OOS skill: `python3 bot.py mlmodel 2>&1 | tail -20` (and `python3 bot.py lab 400` if relevant). Capture the new skill_vs_market / cv_skill.',
  '3. Run `python3 bot.py test` && `python3 tests.py` && `python3 chartml.py` && `python3 ml.py`. If ANY is not fully green, OR the challenger OOS skill is NOT >= incumbent by a real margin: `git checkout -- . && rm -f .autopilot_pause`, built=false (record incumbent_skill and challenger_skill so the judge of record can see why).',
  '4. If green AND challenger beats/ties incumbent OOS: `git add -A && ' + GIT_AS_TRAINER + ' commit -m "TRAINER: <title>"`, then `rm -f .autopilot_pause`. built=true, commit, files, test_summary, incumbent_skill, challenger_skill, diffstat=`git show --stat HEAD`.',
  'NEVER touch .env/.envtab. Never edit a test to pass. Always clear .autopilot_pause before returning.',
].join('\n'))

const attackPrompt = (i) => R([
  'You are hostile TRAINER reviewer #' + i + '. A model/training change was committed at HEAD. Inspect it: `cd ~/polymarket-bot && git show HEAD`.',
  'REFUTE it. Default refuted=true unless it is clearly safe AND its OOS evidence genuinely justifies it. Check specifically:',
  '- FUTURE-DATA LEAKAGE: does any feature/split/label peek at the outcome or future? If so leakage_or_safety_violation=true (instant veto).',
  '- ERA HYGIENE: does it train on or count dead-cohort trades? -> violation.',
  '- Does it touch real-money/wallet/.env or weaken a safety gate? -> violation.',
  '- Is the OOS skill gain real and out-of-sample, or in-sample/overfit/noise? Is the champion/challenger comparison apples-to-apples?',
  '- Did tests pass for the right reason (not by weakening a test)?',
  'Return {refuted, leakage_or_safety_violation, reasoning}. Read-only.',
].join('\n'))

const shipPrompt = (chosen) => R([
  'The model change at HEAD beat the incumbent OOS and passed review. SHIP it. The bot must NEVER be left dead.',
  '1. Ensure the watchdog is alive. `cd ~/polymarket-bot && touch .autopilot_pause` to fence the restart.',
  '2. Restart onto the new code so the daemon loads the new model/training: `pkill -f "bot.py paper"; sleep 2; nohup caffeinate -i python3 bot.py paper >> bot.log 2>&1 &` then `sleep 12`.',
  '3. Verify `curl -s http://127.0.0.1:8765/api/health`: ok:true AND audit=="balanced". Confirm exactly one bot (`pgrep -f "bot.py paper" | wc -l` == 2).',
  '4. If BAD: `git reset --hard HEAD~1`, restart the same way, sleep 12, re-verify on the reverted code. shipped=false, rolled_back=true.',
  '5. ALWAYS once healthy: `rm -f .autopilot_pause`.',
  '6. If shipped: write ~/polymarket-bot/trainer_state.json {"last_ship_commit":"<hash>","last_ship_ts":"<date -u +%FT%TZ>","settles_at_last_ship":<settled length>,"champion_challenger":"<incumbent->challenger skills>"}.',
  '7. Append a dated entry to ~/polymarket-bot/TRAIN_LOG.md (what shipped, incumbent vs challenger OOS skill, test tally, rollback commit HEAD~1), then commit ONLY that file: `git add TRAIN_LOG.md && ' + GIT_AS_TRAINER + ' commit -q -m "TRAINER: ship log" && git fetch origin master 2>&1 | tail -1; git pull --rebase --autostash origin master 2>&1 | tail -2 || git rebase --abort; git push origin master 2>&1 | tail -1 || true`.',
  'Return {shipped, rolled_back, reason, commit, champion_challenger, health_after}. Change: ' + JSON.stringify(chosen),
].join('\n'))

const releasePrompt = (note) => R([
  'Close out the TRAINER cycle cleanly. Reason: ' + note,
  '1. If `cd ~/polymarket-bot && git log -1 --format=%s` starts with "TRAINER:" and it was NOT shipped this cycle, discard it: `git reset --hard HEAD~1`. Otherwise leave HEAD.',
  '2. `rm -f .autopilot_pause && git checkout -- . 2>/dev/null`; verify `git status --short` is empty.',
  '3. Ensure the watchdog is alive and the bot is healthy on :8765.',
  '4. RELEASE THE LOCK: `rm -f .autopilot_lock` (only if it is ours: `grep -q "^trainer:" .autopilot_lock 2>/dev/null && rm -f .autopilot_lock || true`).',
  'Return {clean, reason}.',
].join('\n'))

// ============================================================== CONTROL FLOW
phase('Lock')
const gate = await agent(gatePrompt, { schema: GATE_SCHEMA, label: 'gate+lock', phase: 'Lock', agentType: 'Explore' })
if (!gate || gate.deferred) {
  log('LOCK -> deferred (another loop active): ' + (gate ? gate.reason : 'gate failed'))
  return { outcome: 'deferred', reason: gate ? gate.reason : 'gate failed' }
}
if (!gate.clear) {
  log('GATE -> not clear: ' + gate.reason + (gate.emergency ? '  *** EMERGENCY ***' : ''))
  await agent(releasePrompt('gate not clear: ' + gate.reason), { schema: DISCARD_SCHEMA, label: 'release', phase: 'Lock' })
  return { outcome: 'gated', reason: gate.reason, emergency: !!gate.emergency }
}

phase('Train')
const train = await agent(trainPrompt, { schema: TRAIN_SCHEMA, label: 'train+record', phase: 'Train', agentType: 'Explore' })
log('TRAIN -> recorded=' + (train && train.recorded) + ' regression=' + (train && train.regression_flag) + ' improve_gate=' + (train && train.improve_gate_open) + ' new_settles=' + (train && train.new_settles))
if (!train || !train.improve_gate_open) {
  await agent(releasePrompt('trained + recorded metrics; improve gate closed (insufficient new data / no regression)'), { schema: DISCARD_SCHEMA, label: 'release', phase: 'Train' })
  return { outcome: 'trained-only', metrics: train ? train.metrics : null, regression: train ? train.regression_flag : null }
}

phase('Scout')
const scouts = await parallel(AREAS.map((a) => () => agent(scoutPrompt(a), { schema: PROPOSAL_SCHEMA, label: 'scout:' + a.key, phase: 'Scout', agentType: 'Explore' })))
const proposals = scouts.filter(Boolean).map((r) => r.proposal).filter(Boolean)
log('SCOUT -> ' + proposals.length + ' OOS-backed proposal(s)')
if (!proposals.length) {
  await agent(releasePrompt('no OOS-backed ML proposals this cycle'), { schema: DISCARD_SCHEMA, label: 'release', phase: 'Scout' })
  return { outcome: 'no-proposals', metrics: train.metrics }
}

phase('Judge')
const verdict = await agent(judgePrompt(proposals), { schema: JUDGE_SCHEMA, label: 'judge', phase: 'Judge' })
if (!verdict || !verdict.ship || !verdict.chosen) {
  log('JUDGE -> ship nothing: ' + (verdict ? verdict.rationale : 'judge failed'))
  await agent(releasePrompt('judge chose nothing: ' + (verdict ? verdict.rationale : 'judge failed')), { schema: DISCARD_SCHEMA, label: 'release', phase: 'Judge' })
  return { outcome: 'judge-nothing', reason: verdict ? verdict.rationale : 'judge failed', proposals }
}
log('JUDGE -> chosen: ' + (verdict.chosen.title || '(untitled)'))

phase('Build')
const build = await agent(buildPrompt(verdict.chosen), { schema: BUILD_SCHEMA, label: 'build+retrain', phase: 'Build' })
if (!build || !build.built) {
  log('BUILD -> not built: ' + (build ? build.reason : 'build failed') + ' (incumbent=' + (build && build.incumbent_skill) + ' challenger=' + (build && build.challenger_skill) + ')')
  await agent(releasePrompt('build/challenger failed: ' + (build ? build.reason : 'build failed')), { schema: DISCARD_SCHEMA, label: 'release', phase: 'Build' })
  return { outcome: 'build-failed', reason: build ? build.reason : 'build failed', chosen: verdict.chosen }
}
log('BUILD -> committed ' + build.commit + ' (incumbent ' + build.incumbent_skill + ' -> challenger ' + build.challenger_skill + ')')

phase('Attack')
const reviews = (await parallel([1, 2, 3].map((i) => () => agent(attackPrompt(i), { schema: ATTACK_SCHEMA, label: 'attack:' + i, phase: 'Attack', agentType: 'Explore' })))).filter(Boolean)
const violation = reviews.some((v) => v.leakage_or_safety_violation)
const passVotes = reviews.filter((v) => v.refuted === false).length
const survives = !violation && passVotes >= 2 && reviews.length >= 2
log('ATTACK -> passVotes=' + passVotes + '/' + reviews.length + ' violation=' + violation + ' -> ' + (survives ? 'SURVIVES' : 'KILLED'))

phase('Ship')
if (!survives) {
  const why = violation ? 'leakage/era-hygiene/safety violation' : ('failed review (' + passVotes + '/' + reviews.length + ')')
  await agent(releasePrompt('adversarial review killed it: ' + why + '; commit ' + build.commit), { schema: DISCARD_SCHEMA, label: 'release', phase: 'Ship' })
  return { outcome: 'review-killed', reason: why, build, reviews }
}
const ship = await agent(shipPrompt(verdict.chosen), { schema: SHIP_SCHEMA, label: 'ship', phase: 'Ship' })
await agent(releasePrompt('post-ship lock release'), { schema: DISCARD_SCHEMA, label: 'release', phase: 'Ship' })
log('SHIP -> ' + (ship && ship.shipped ? 'SHIPPED ' + ship.commit + ' (' + ship.champion_challenger + ')' : 'rolled back: ' + (ship ? ship.reason : 'ship failed')))
return { outcome: ship && ship.shipped ? 'shipped' : 'rolled-back', ship, build, chosen: verdict.chosen }
