from __future__ import annotations
import hashlib, re, unicodedata

NOISE = {'fc','cf','ac','afc','ssc','sc','as','us','calcio','club','football','futbol','balompie'}
ALIASES = {'internazionalemilano':'inter','internazionalemilan':'inter','intermilan':'inter','parmacalcio':'parma','realbetisbalompie':'realbetis'}

def norm_text(value: object) -> str:
    s = unicodedata.normalize('NFKD', str(value or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+',' ',s).strip()

def team_key(value: object) -> str:
    toks=[]
    for t in norm_text(value).split():
        if t in NOISE or re.fullmatch(r'(18|19|20)\d{2}', t): continue
        if t == 'milano': t='milan'
        if t == 'internazionale': t='inter'
        toks.append(t)
    key=''.join(toks)
    return ALIASES.get(key,key)

def event_fingerprint(event: dict) -> str:
    typ = norm_text(event.get('type') or event.get('event_type') or 'event')
    detail = norm_text(event.get('detail'))
    # Period snapshots are state, not repeated timeline events: minute intentionally omitted.
    if typ in {'period score','period_score','halftime score','half time score'}:
        raw=f"{typ}|{detail}"
    else:
        clock=norm_text(event.get('clock')).replace(' ','')
        raw=f"{typ}|{clock}|{detail}"
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:24]
