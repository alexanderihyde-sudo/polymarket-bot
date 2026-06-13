#!/bin/bash
S=$(curl -s --max-time 10 http://localhost:8765/api/state)
[ -z "$S" ] && echo "ALERT bot down" && exit 0
echo "$S" | python3 -c "
import json,sys
s=json.load(sys.stdin); a=s['account']
import pathlib
_p=pathlib.Path('.last_settled'); _prev=int(_p.read_text()) if _p.exists() else s['settled_total']
_new=s['settled_total']-_prev; _p.write_text(str(s['settled_total']))
alerts=[]
if a['total'] < 0.97*a['starting_cash']: alerts.append('drawdown>3%')
for k,v in s['learning'].items():
    if isinstance(v,dict) and v.get('multiplier')==0 and v.get('settled',0)>0: alerts.append(f'{k} paused')
# live-value watch: flag positions bleeding badly before they settle.
# explore (hold-for-label \$1 stakes) and arbitrage (locked payouts,
# mids read 0) are EXCLUDED by design; dollar floor stops penny alarms.
bleeders=[p for p in s['positions'] if p['cost']>0 and p['pnl']/p['cost'] < -0.25
          and p.get('strategy') not in ('explore','arbitrage') and p['pnl'] < -1.0]
if bleeders: alerts.append(f\"{len(bleeders)} positions down >25%: \" + '; '.join(p['name'][:30]+f\" {p['pnl']:+.2f}\" for p in bleeders[:3]))
worst=min(s['positions'], key=lambda p:p['pnl'], default=None)
w=f\" worst {worst['name'][:25]} {worst['pnl']:+.2f}\" if worst else ''
pre='ALERT ' if alerts else ''
print(f\"{pre}total \${a['total']} live {a['unrealized_pnl']:+.2f} realized {a['realized_pnl']:+.2f} open {len(s['positions'])}{f' settles+{_new}' if _new>0 else ''}{w} {';'.join(alerts)}\")"
