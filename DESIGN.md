# DESIGN.md — Dark Fintech Desk (read and obey for every UI change)

Aesthetic anchor: **Bloomberg Terminal meets Linear/Vercel** — cold, dense, restrained, institutional. NOT a consumer app. When unsure, choose denser, more monochrome, more mono-numeric; build hierarchy by de-emphasis (push secondary DOWN), never by bolding everything up.

## Atmosphere
Radial top-wash background + ~3.5% feTurbulence film grain (mix-blend overlay, pointer-events:none). Elevation by **lighter surfaces, never drop-shadows**. Real box-shadow reserved for modals/popovers only.

## Color tokens (CSS vars; never pure #000 or #fff)
```
--bg:#0b0d10        /* canvas */
--card:#121417      /* card / surface-1 */
--card2:#181b21     /* raised / sticky header */
--card3:#1f232a     /* hover / popover */
--ink:rgba(255,255,255,.92)  --muted:rgba(255,255,255,.60)  --faint:rgba(255,255,255,.40)
--line:rgba(255,255,255,.08)  --divider:rgba(255,255,255,.06)
--lit: inset 0 1px 0 rgba(255,255,255,.06)   /* lit top edge on every card */
--accent:#4a9eff    /* active/primary/focus/equity line ONLY — one accent */
--green:#3ecf8e  --red:#f6685e  /* P&L ONLY, equal perceived weight, always + sign + ▲▼ + 12% tint chip */
```
Color is **never decorative**. Multi-series chart legends may use distinct hues (data-justified); UI chrome uses one accent.

## Type (the #1 quality signal)
- Display / hero / logo: **Clash Display** 600/700 (Fontshare).
- UI / body / labels: **General Sans** 400/500/600 (Fontshare).
- ALL numbers (prices, P&L, %, timestamps, equity hero, table figures): **JetBrains Mono**, `font-variant-numeric: tabular-nums slashed-zero`, right-aligned in columns.
- Tiny labels: 11px, UPPERCASE, `letter-spacing:.08em`, weight 600, `--muted`.
- Hero number: mono, 40–56px, `letter-spacing:-.03em`, line-height 1.1.
- **Never** Inter / Roboto / Arial / Space Grotesk / system-only.

## Spacing & shape
4 / 8 / 12 / 16 / 24 / 32 / 48 / 64. Card padding 16–24px; section gaps 32–48px. Radius 8–14px; nested radius = outer − padding.

## Components
- **Card:** `border:1px solid var(--line)`, `box-shadow:var(--lit)`, radius 14px, no drop shadow.
- **Table:** 32–36px rows; hairline row-lines only (no zebra, no vertical gridlines); sticky `--card2` header with uppercase tracked labels; row hover `rgba(255,255,255,.03)`; per-row actions fade in on hover; figures tabular + right-aligned.
- **Chart (equity = the hero):** 1.5–2px glowing stroke, vertical gradient area fill fading .22→0, horizontal-only gridlines at 4% white, no axis box, 50%-opacity tabular axis labels, glowing last-point dot, recolor green/red by sign, stroke draw-in on load.
- **Icons:** Lucide/Phosphor, 1.5px stroke, 16px, `--muted` default. Never emoji-as-icons.

## Motion (spend the budget on first paint)
- **One** staggered page-load cascade: `@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}`, `animation-delay: calc(var(--i)*60ms)`, ease `cubic-bezier(.22,1,.36,1)`.
- Custom `:focus-visible` ring (`0 0 0 1px accent, 0 0 0 4px rgba(74,158,255,.25)`).
- Flash-on-tick for live P&L (`@keyframes flashUp`).
- Always `@media(prefers-reduced-motion:reduce){*{animation:none;transition:none}}`.

## DON'Ts (Reddit-mined AI "tells")
No Inter/Roboto/Space Grotesk · no purple/indigo or gradient hero text · no proportional figures on money · no drop-shadowed cards (use lightness) · no zebra tables / full gridlines · no emoji icons · no more than one accent · no pure #000/#fff · no centered-hero+three-equal-cards · no `rounded-2xl` everywhere.

## Counterintuitive law
MORE specificity (exact hex/px in the *brief*) makes AI output WORSE. Brief by **principle + named reference + an avoid-list**, then let the model pick exact values.

_Source: 71-search design-research synthesis, 2026-06-22 (Anthropic frontend-aesthetics cookbook; Vercel/Linear/Stripe design systems; r/web_design + JCarterJohnson/vibecoded-design-tells; OKLCH dark-UI guides)._
