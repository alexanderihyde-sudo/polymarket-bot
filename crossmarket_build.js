export const meta = {
  name: 'crossmarket-weave',
  description: 'Build crossmarket.py (Kalshi/PredictIt/Manifold + Odds-API connectors, strict matcher, de-vig consensus, scorecard) + a shadow loop, and weave consensus/divergence into the brain as OOS-gated features + a by_crossmarket attribution bucket. Tested + adversarially verified + rollback.',
  phases: [ { title: 'Lock' }, { title: 'Build' }, { title: 'Verify' }, { title: 'Ship' } ],
}

const RULES = [
  'You are adding CROSS-MARKET data (other prediction markets + sportsbook consensus) to the PAPER-TRADING bot at ~/polymarket-bot. Live daemon: http://127.0.0.1:8765. Git repo; HEAD is last known-good.',
  'PHILOSOPHY: cross-market divergence is informative but trap-laden (different resolution rules, timing, vig, event-matching errors). So it enters the decision path as a SIGNAL that earns sizing weight ONLY through the brain\'s existing OOS-gated credibility — never as blind divergence trading. Day one it is effectively neutral; it ramps up only where it measurably predicts.',
  '',
  'HARD RULES — never violate:',
  '- PAPER ONLY. Never touch real money / wallet / .env / .envtab. The Odds-API key is read from os.environ via .env which YOU NEVER read or print; if ODDS_API_KEY is absent the sportsbook source is simply skipped.',
  '- NO FUTURE-DATA LEAKAGE: cross-market prices are read at/ before decision time; never use post-entry or resolution info as a feature.',
  '- The bot must behave IDENTICALLY on markets with NO cross-market match (the common case): the new brain features default neutral/None -> global model unchanged where there is no cross-market data. Prove this with a regression test.',
  '- ERA HYGIENE: any cross-market learner respects dead_cohort().',
  '- Network calls must be governed/timeout-bounded/fail-silent (reuse the bot\'s _governor + caching patterns); a dead external API must NEVER stall or crash the daemon.',
  '- Shadow-scored until proven: the consensus earns trading influence only via the brain\'s OOS/credibility gate + a by_crossmarket attribution bucket. One coherent, fully-tested increment; when in doubt, present.',
].join('\n')
const R = (b) => RULES + '\n\n=== YOUR TASK ===\n' + b

const GATE_SCHEMA = { type:'object', additionalProperties:false, required:['clear','deferred','reason'], properties:{ clear:{type:'boolean'}, deferred:{type:'boolean'}, reason:{type:'string'} } }
const BUILD_SCHEMA = { type:'object', additionalProperties:false, required:['built','reason'],
  properties:{ built:{type:'boolean'}, reason:{type:'string'}, commit:{type:'string'}, files:{type:'array',items:{type:'string'}},
    test_summary:{type:'string'}, live_data_proof:{type:'string', description:'real probabilities pulled from Kalshi/Manifold/PredictIt during the build'},
    regression_proof:{type:'string', description:'how a test proves brain behavior is identical when cross-market context is absent'}, diffstat:{type:'string'} } }
const REVIEW_SCHEMA = { type:'object', additionalProperties:false, required:['refuted','violation','reasoning'], properties:{ refuted:{type:'boolean'}, violation:{type:'boolean'}, reasoning:{type:'string'} } }
const SHIP_SCHEMA = { type:'object', additionalProperties:false, required:['shipped','reason'], properties:{ shipped:{type:'boolean'}, rolled_back:{type:'boolean'}, reason:{type:'string'}, commit:{type:'string'}, health_after:{type:'string'} } }
const REL_SCHEMA = { type:'object', additionalProperties:false, required:['clean','reason'], properties:{ clean:{type:'boolean'}, reason:{type:'string'} } }

const DESIGN = [
  'IMPLEMENT this as ONE coherent increment. Study the existing sportsedge.py + its shadow loop in bot.py (sportsedge_loop, SPORTSEDGE state, /api/health.sportsedge, the import + thread start) and MIRROR that proven shadow-instrument pattern — crossmarket is the same shape.',
  '',
  'A) NEW FILE crossmarket.py (pure python, import only stdlib + maybe ml; like sportsedge.py):',
  '  - CONNECTORS (each timeout-bounded, fail-silent, returns []/{} on any error): ',
  '    kalshi_markets(): GET https://api.elections.kalshi.com/trade-api/v2/markets?limit=...&status=open (paginate via cursor); keep markets that have real prices (yes_bid/yes_ask/last_price not None and volume>0); implied p = last_price/100 or mid(yes_bid,yes_ask)/100.',
  '    predictit_all(): GET https://www.predictit.org/api/marketdata/all/ ; each contract lastTradePrice is an implied p.',
  '    manifold_markets(): GET https://api.manifold.markets/v0/markets?limit=... ; binary markets carry probability directly. FLAG manifold as PLAY-MONEY (lower reliability weight).',
  '    odds_api(): only if os.environ.get("ODDS_API_KEY"); GET https://api.the-odds-api.com/v4/sports/{sport}/odds?apiKey=...&regions=us&markets=h2h ; de-vig the two-way moneyline to implied probs. If no key -> return [] (skipped).',
  '    During the build, ACTUALLY CALL kalshi/manifold/predictit with curl/python and paste a few real (question, implied_p) into live_data_proof. Refine field parsing against the real responses.',
  '  - STRICT MATCHER match_event(pm_market, cross_pool): like sportsedge.join_event — require strong entity-token overlap + same resolution date/type + 2-outcome where applicable; REJECT/abstain on futures/props/wrong-day/ambiguous/non-overlapping. Most PM markets will have NO match — that is correct.',
  '  - CONSENSUS: for a matched PM market, combine the cross-source implied probs into consensus_p, weighted by source reliability (Kalshi real-money highest, PredictIt mid, Manifold play-money lowest, OddsAPI per-source). divergence = pm_p - consensus_p.',
  '  - SCORECARD grade(preds): Brier(consensus) vs Brier(pm market price) + CLV + n + a promotion verdict (e.g. >=15 graded, consensus beats market Brier, positive CLV). Defaults to "no data"/no-promotion.',
  '  - self_test(): planted-data assertions (matcher accepts a true match, rejects futures/wrong-day; consensus weighting; grade math). Must pass via `python3 crossmarket.py`.',
  '',
  'B) WIRE INTO bot.py (additive + gated, mirroring sportsedge wiring):',
  '  - import crossmarket; load CROSSMARKET state (crossmarket_model.json) like SPORTSEDGE.',
  '  - crossmarket_loop() daemon thread (sleep ~180s then loop, like sportsedge_loop): fetch sources, match against current open PM markets, compute consensus, append shadow preds, grade resolved ones, atomic_write ONLY crossmarket_model.json. Add a /api/health "crossmarket" field {n, matched, verdict, updated}. CLI `elif cmd == "crossmarket":` runs one pass + prints the scorecard.',
  '  - ENTRY CONTEXT: where an opportunity\'s context dict is built (high_prob/news/daytrade scans), attach ctx["xmkt_consensus"] and ctx["xmkt_divergence"] = crossmarket.lookup(market) (None if no match — the common case).',
  '  - BRAIN FEATURE (the weave): add cross-market features to _brain_x (e.g. divergence, and consensus-minus-price), defaulting to 0/neutral when xmkt context is None. This puts the signal INTO the model; the brain\'s existing CV/credibility/skill_factor gating decides its weight — a non-predictive feature gets ~0 weight, and where xmkt is None the feature is 0 so the global path is unchanged.',
  '  - ATTRIBUTION: add a by_crossmarket bucket (group settled pnl by divergence sign/strength) so its real edge is measured, like by_oracle/by_smart_money.',
  '',
  'C) REGRESSION GUARD (learn from the per-category kill): add a test asserting that with NO cross-market context (xmkt fields None / feature 0), _brain_x and brain_adjust produce IDENTICAL output to before, and that brain_train cv_skill on a no-xmkt fixture is unchanged. This is the key proof the weave cannot regress the common path.',
].join('\n')

const releasePrompt = (note) => R([
  'Close out cleanly. Reason: ' + note,
  '1. If `cd ~/polymarket-bot && git log -1 --format=%s` starts with "FEATURE:" and NOT shipped this run, discard: `git reset --hard HEAD~1`. Else leave HEAD. (crossmarket.py may be a new untracked file — if not shipping, also `rm -f crossmarket.py crossmarket_model.json`.)',
  '2. `rm -f .autopilot_pause && git checkout -- . 2>/dev/null`; `git status --short` empty (besides any intentionally-kept new files only if shipped).',
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
  'Refresh lock + fence: `cd ~/polymarket-bot && echo "feature:$(date +%s)" > .autopilot_lock && touch .autopilot_pause`.',
  '', DESIGN, '',
  'Then run `python3 crossmarket.py` (self-test) && `python3 bot.py test` && `python3 tests.py` && `python3 chartml.py` && `python3 ml.py`. Also run `python3 bot.py crossmarket` once to prove the live pass works (it will match few/zero markets — that is correct). If any suite red OR the regression test fails OR the daemon path is not fail-silent: `git checkout -- . && rm -f crossmarket.py crossmarket_model.json .autopilot_pause`, built=false. Else `git add -A && git commit -m "FEATURE: cross-market consensus (Kalshi/PredictIt/Manifold) shadow + OOS-gated brain weave"`, `rm -f .autopilot_pause`. Return built, commit, files, test_summary, live_data_proof, regression_proof, diffstat.',
  'NEVER read/print .env. Never weaken a test to pass.',
].join('\n')), { schema: BUILD_SCHEMA, label:'build', phase:'Build' })
if (!build || !build.built) {
  log('BUILD -> not built: ' + (build?build.reason:'failed'))
  await agent(releasePrompt('build failed: '+(build?build.reason:'failed')), {schema:REL_SCHEMA,label:'release',phase:'Build'})
  return { outcome:'build-failed', reason: build?build.reason:'failed' }
}
log('BUILD -> ' + build.commit + ' | live data: ' + (build.live_data_proof||'').slice(0,120))

phase('Verify')
const reviews = (await parallel([1,2,3].map(i => () => agent(R([
  'Hostile reviewer #'+i+'. Inspect the committed change: `cd ~/polymarket-bot && git show HEAD` (and read crossmarket.py).',
  'REFUTE it. violation=true (instant veto) if ANY: the brain/global path is NOT identical when cross-market context is absent (regression on the common path); future-data leakage; a dead/slow external API can stall or crash the daemon (not fail-silent/timeout-bounded); .env read/printed; cross-market given trading influence OUTSIDE the brain\'s OOS/credibility gate (blind divergence trading); era-hygiene break. Verify the regression test actually proves the no-match path is unchanged, and that the matcher abstains on non-overlapping events. Default refuted=true unless it clearly holds. Return {refuted, violation, reasoning}.',
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
  '3. Verify :8765/api/health ok+balanced AND the new "crossmarket" field present; exactly one bot (`pgrep -f "bot.py paper"|wc -l`==2).',
  '4. If BAD: `git reset --hard HEAD~1`, restart same way, sleep 12, re-verify reverted. shipped=false, rolled_back=true.',
  '5. Once healthy: `rm -f .autopilot_pause`. Append dated QUANT_LOG.md entry (what shipped, sources wired, gated-weave, shadow-scored, test tally, rollback HEAD~1), commit ONLY that file, `git push origin master 2>&1|tail -1||true`.',
  '6. Release lock: `grep -q "^feature:" .autopilot_lock 2>/dev/null && rm -f .autopilot_lock || true`.',
  'Return {shipped, rolled_back, reason, commit, health_after}.',
].join('\n')), { schema: SHIP_SCHEMA, label:'ship', phase:'Ship' })
log('SHIP -> ' + (ship && ship.shipped ? 'SHIPPED ' + ship.commit : 'rolled back / ' + (ship?ship.reason:'failed')))
return { outcome: ship && ship.shipped ? 'shipped' : 'rolled-back', ship, build }
