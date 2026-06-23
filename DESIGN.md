# DESIGN.md — DEGEN TERMINAL (read and obey for every UI change)

Synthesized from 713 web searches (2026-06-23). Aesthetic anchor: **Bloomberg-terminal data-density × disciplined neo-brutalism**, on a warm blue near-black ground, with **one acid-lime accent** and **numbers-as-hero** typography. A real quant tool that talks like a self-aware degen. The escape from AI "slop" comes from **structure** (varied container archetypes, a different chart type per panel, numbers-as-hero, elevation-by-lightness, one signature texture) — NOT from palette sprawl or decoration. **The frame is loud; the data is sober.**

## FORBIDDEN (read this list before writing any code)
- **Fonts:** no system-ui/-apple-system, Inter, Geist, Roboto, Helvetica, Arial, Open Sans, Lato, Montserrat, Poppins, or the Space-Grotesk+Instrument-Serif combo.
- **Color:** no indigo/violet/purple of any kind (#6366F1/#4F46E5/#8B5CF6/#635BFF), no from-X-to-Y gradient, no gradient TEXT; no mid-blue accent (#5b8cff/#4a9eff — itself the templated tell).
- **Surfaces:** no #0F172A/#1E293B slate, no neutral-gray #121212/#1A1A1A, no pure #000 (banding/halation), no pure #FFF text.
- **Geometry:** no uniform 12–14px-radius cards, no soft 0.1-opacity blurred drop-shadows, no colored left-border stripe, no identical p-8 padding on everything, **no equal-size card grid (the #1 visual tell)**.
- **Charts:** no single line chart as the only viz, no default line+bar+donut trio, no flat green→transparent area, no skeuomorphic gauges, no candlesticks, no auto-scaled-per-row sparklines, no streamgraph for signed P&L (losses vanish).
- **P&L color:** no traffic-light #00FF00/#FF0000; never encode direction by color alone — always +/- sign **and** ▲/▼ glyph.
- **Motion:** no single global opacity-0+translateY fade-up on everything, no money figures on bouncy springs, no animating box-shadow/filter/width per frame, no confetti per trade, no CRT scanline >~10%.
- **Copy:** no Unlock/Elevate/Empower/Seamless/best-in-class, no "Your portfolio overview." Dry, self-aware terminal voice.
- **When prompting a model:** never "modern AI dashboard / futuristic SaaS / sleek fintech" (pulls purple training defaults). Say "quant terminal / neo-brutalist / Bloomberg-terminal / degen tooling."
- **Don't over-correct into chaos:** never rotate/misalign/illegible NUMBERS, no custom-cursor-everywhere, no parallax-everywhere, no blending 3+ aesthetics into mush.

## Color tokens (author in OKLCH; never pure #000/#fff)
```
--bg0:#0d0d11  --bg1:#131318  --bg2:#181820  --bg3:#1f1f29   /* warm-blue near-black, +4–6% L per step; elevation by LIGHTNESS only */
--ink:#eceaf0  --ink2:#b8b4c4  --ink3:#7e7a8c                /* ≥4.5:1; never #fff */
--accent(acid-lime):#ccff00  hi:#ddff55                      /* active/live-line/the ONE most-important number only, <20% coverage */
--gain(mint):#00d395   --loss(coral):#ff5c5c                 /* P&L ONLY, data not chrome; +sign +▲▼; equity fills 12–18% alpha */
--status(amber):#ffb000                                      /* PAPER / shadow / warn chips only */
--line:rgba(255,255,255,.08) / .16   --lit:inset 0 1px 0 rgba(255,255,255,.06), inset 0 -1px 0 rgba(0,0,0,.4)
```
Strategy/series (data-justified, distinct, NO purple-as-chrome): cyan #5bc8ff · amber #ffb000 · pink #ff7ab6 · mint #00d395 · coral #ff5c5c.

## Type
- **Display / hero number / section heads:** Bricolage Grotesque Variable (NOT the now-templated Clash trio). **Mono** is the safe choice for a constantly-ticking figure (tabular, no reflow).
- **UI / body / labels:** General Sans.
- **Numbers / tickers / timestamps / chrome labels:** JetBrains Mono, `font-variant-numeric: tabular-nums slashed-zero` on EVERY live figure; right-align numeric columns.
- **Hero equity number:** poster-scale `clamp(...)`, the loudest thing on screen, in a hard-offset-shadow brutalist tile. The number IS the chart.
- Terminal chrome labels UPPERCASE mono, tracking tightens as size grows. Self-host variable .woff2, subset Latin, `font-display:swap`.

## Charts — a DIFFERENT type per panel (this IS the de-slopping)
- **Equity (hero):** horizon chart OR glowing neon-stroke area that shades **mint at all-time-highs / coral in drawdown**, $10k baseline, pulsing live endpoint dot, self-draws on mount.
- **Cumulative P&L:** a **waterfall** of signed per-trade steps (NOT a second line, NOT a streamgraph).
- **Daily P&L:** diverging **calendar heatmap** (coral→neutral→mint, intensity=magnitude) — doubles as the streak display.
- **Validation/skill metrics:** **bullet graphs** (value vs threshold band), never gauges.
- **ROI-by-strategy:** horizontal **lollipop/dot** or radial bar (cap ≤7, tail="Other"), never vertical bars or N gauges.
- **Watchlist/positions rows:** shared-y-scale **sparklines**; per-trade distribution (high N) = a **beeswarm**.
- Strip all default axes/gridlines/legends/tooltips; label marks directly. (no-build dashboard: hand-roll in SVG/canvas; lightweight-charts ok for the live line.)

## Container archetypes — give each content type a DIFFERENT frame on ONE 12-col bento (gap 16, tile area = importance, hero 6col×2row)
- **Terminal window** (live status): hairline border, low radius, mono titlebar with traffic-light dots, shell prompt `equity@paper:~$`, blinking █ cursor.
- **Receipt/ledger stub** (settled trades): dashed-perforation mask edge, all-mono figures, a deterministically-rotated "PAPER ONLY" stamp (never re-randomize on refresh).
- **Brutalist hero tile** (the one big number): 2–3px solid border, `box-shadow:6px 6px 0` acid-lime ZERO blur, sharp corners — the opposite of the AI-default blurry shadow. ONE tile only.
- **Hairline bento cells** (KPIs/skill metrics): bare cells, 1px white-alpha hairlines + inner-highlight (`inset 0 1px 0 rgba(255,255,255,.06)` top, `inset 0 -1px 0 rgba(0,0,0,.4)` bottom). Depth from light, not blur.
- **Notched HUD** (open positions): corner brackets + LIVE● dot + mono timestamp.
- At most ONE glass tile (live status only); never glass behind small numbers/tables.

## Motion (vary by element TYPE — one global fade is the tell)
Three spring tokens: SNAPPY (hovers/taps), SMOOTH (ALL money — never wobbles), BOUNCY (new-equity-high only). Signature: every live figure **odometer-rolls** (tabular). Reveals vary by type (wipe/blur/stagger/border-draw); equity self-draws. Hover ≤220ms, press ≤160ms (:active scale .97), cap ~300ms; animate only transform/opacity. Gate everything behind `prefers-reduced-motion` (snap to final, not stuck at 0) and `(hover:hover)`; ship a motion/sound toggle.

## Voice
Dry, self-aware degen-terminal. Empty: "FLAT. holding cash — nothing validated, that's the correct move." Loading: "booting gauntlet…". Own the paper-money framing. Gamify **calibration/streaks**, never bet volume.

## Signature moves (3–6, pick and commit)
numbers-as-hero · shell-prompt terminal chrome + ticker-tape · one acid-lime hard-offset brutalist tile in a field of hairline cells · finance-native chart vocabulary (horizon/waterfall/calendar/bullet/lollipop/sparkline) · one feTurbulence grain token (baseFrequency .65, soft-light, 4–12%) + ≤5% scanline on chrome only · receipt stub with PAPER-ONLY stamp.

_Source: 713-search design-research synthesis, 2026-06-23 (anti-AI-slop · Gen Z aesthetics · data-viz · containers · type · color · motion · Awwwards/Linear/Hyperliquid refs). Supersedes the molten-gold spec._
