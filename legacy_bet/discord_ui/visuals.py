from __future__ import annotations
from io import BytesIO
from pathlib import Path
import re
import json
import unicodedata
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
LOGO_DIR = ROOT / 'assets' / 'team_logos'
BG=(9,10,13); PANEL=(18,19,23); GOLD=(214,173,77); TEXT=(245,245,242); MUTED=(155,158,166); GREEN=(67,190,112); RED=(210,75,75); ORANGE=(232,151,58)

def _font(size:int,bold:bool=False):
    candidates=['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf']
    for p in candidates:
        if Path(p).exists(): return ImageFont.truetype(p,size)
    return ImageFont.load_default()

def _slug(s:str)->str:
    raw=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode('ascii').lower().replace('&',' and ')
    raw=re.sub(r'\b(fc|afc|cf|ac|sc|as|ssc|calcio|football club|club de futbol)\b',' ',raw)
    return re.sub(r'[^a-z0-9]+','-',raw).strip('-')

def _logo_index():
    try:
        return json.loads((LOGO_DIR/'index.json').read_text(encoding='utf-8')).get('aliases',{})
    except Exception:
        return {}

def _logo_mapping(team: str, idx: dict[str, str]) -> str | None:
    key = _slug(team)
    if key in idx:
        return idx[key]
    # Same conservative provider aliases as the synchronizer, imported lazily to
    # keep image rendering usable even if network dependencies are unavailable.
    try:
        from .team_logos import PROVIDER_ALIASES, normalize_name
        canonical = PROVIDER_ALIASES.get(key)
        if canonical:
            direct = idx.get(normalize_name(canonical))
            if direct:
                return direct
            # Older index files may not contain the explicit alias yet, but the
            # canonical cached filename can still be used safely.
            for ext in ('png','webp','jpg','jpeg'):
                p = LOGO_DIR / f'{canonical}.{ext}'
                if p.exists():
                    return p.name
    except Exception:
        pass
    return None

def _logo(team:str,size=132):
    idx=_logo_index(); mapped=_logo_mapping(team, idx)
    candidates=[LOGO_DIR/mapped] if mapped else []
    candidates += [LOGO_DIR/f'{_slug(team)}.{ext}' for ext in ('png','webp','jpg','jpeg')]
    for p in candidates:
        if p.exists():
            try:
                im=Image.open(p).convert('RGBA'); im.thumbnail((size,size),Image.Resampling.LANCZOS); return im
            except Exception: pass
    im=Image.new('RGBA',(size,size),(0,0,0,0)); d=ImageDraw.Draw(im); d.ellipse((3,3,size-3,size-3),fill=PANEL,outline=GOLD,width=4)
    initials=''.join(w[0] for w in re.findall(r'[A-Za-z0-9]+',team)[:3]).upper()[:3] or '?'
    f=_font(34,True); box=d.textbbox((0,0),initials,font=f); d.text(((size-(box[2]-box[0]))/2,(size-(box[3]-box[1]))/2-4),initials,font=f,fill=TEXT)
    return im

def _center(draw,y,text,font,fill=TEXT,width=1200):
    b=draw.textbbox((0,0),text,font=font); draw.text(((width-(b[2]-b[0]))/2,y),text,font=font,fill=fill)

def match_card(match:dict,state='prematch')->BytesIO:
    # V45: ultra-clean matchup banner. The surrounding Discord UI already carries
    # date, competition, status and odds, so the image only identifies the fixture.
    W,H=1200,300
    im=Image.new('RGB',(W,H),BG)
    d=ImageDraw.Draw(im)
    d.rounded_rectangle((20,20,W-20,H-20),26,fill=PANEL,outline=GOLD,width=3)

    home=str(match.get('home_team') or 'Domicile')
    away=str(match.get('away_team') or 'Extérieur')

    # Oversized crests, intentionally the only visual information besides VS.
    logo_size=220
    hl=_logo(home,logo_size)
    al=_logo(away,logo_size)

    # Keep each crest in its own half, with generous breathing room around VS.
    hy=(H-hl.height)//2
    ay=(H-al.height)//2
    hx=285-(hl.width//2)
    ax=915-(al.width//2)
    im.paste(hl,(hx,hy),hl)
    im.paste(al,(ax,ay),al)

    vs='VS'
    vsf=_font(52,True)
    vb=d.textbbox((0,0),vs,font=vsf)
    d.text(((W-(vb[2]-vb[0]))/2,(H-(vb[3]-vb[1]))/2-8),vs,font=vsf,fill=GOLD)

    out=BytesIO()
    im.save(out,'PNG',optimize=True)
    out.seek(0)
    return out

def _centered_box_compact(d,box,label,value):
    x1,y1,x2,y2=box; f1=_font(13,True); f2=_font(18,True)
    for text,f,y,c in ((label,f1,y1+7,MUTED),(value,f2,y1+29,TEXT)):
        b=d.textbbox((0,0),text,font=f); d.text(((x1+x2-(b[2]-b[0]))/2,y),text,font=f,fill=c)

def _centered_box(d,box,label,value):
    x1,y1,x2,y2=box; f1=_font(18,True); f2=_font(25,True)
    for text,f,y,c in ((label,f1,y1+12,MUTED),(value,f2,y1+43,TEXT)):
        b=d.textbbox((0,0),text,font=f); d.text(((x1+x2-(b[2]-b[0]))/2,y),text,font=f,fill=c)
