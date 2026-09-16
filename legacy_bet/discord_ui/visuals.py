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

def _logo(team:str,size=132):
    idx=_logo_index(); mapped=idx.get(_slug(team))
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
    # Compact sportsbook strip: logos are the visual focus, with no large empty canvas.
    W,H=1200,300; im=Image.new('RGB',(W,H),BG); d=ImageDraw.Draw(im)
    d.rounded_rectangle((24,24,W-24,H-24),24,fill=PANEL,outline=GOLD,width=3)
    d.text((52,44),'ODDIUM • MARKET',font=_font(24,True),fill=GOLD)
    competition=str(match.get('competition_name') or 'SPORTSBOOK').upper()
    d.text((52,78),competition[:42],font=_font(16,True),fill=MUTED)
    state_label={'live':'● LIVE','won':'✓ GAGNÉ','lost':'✕ PERDU','selected':'◆ SÉLECTION','prematch':'PRÉ-MATCH'}.get(state,state.upper())
    sf=GREEN if state=='won' else RED if state=='lost' else ORANGE if state=='live' else GOLD
    stf=_font(16,True); bb=d.textbbox((0,0),state_label,font=stf); d.text((W-52-(bb[2]-bb[0]),50),state_label,font=stf,fill=sf)

    home=str(match.get('home_team') or 'Domicile'); away=str(match.get('away_team') or 'Extérieur')
    hl=_logo(home,86); al=_logo(away,86)
    im.paste(hl,(90,120),hl); im.paste(al,(W-176,120),al)
    namef=_font(20,True)
    d.text((195,132),home[:24],font=namef,fill=TEXT)
    ab=d.textbbox((0,0),away[:24],font=namef); d.text((W-195-(ab[2]-ab[0]),132),away[:24],font=namef,fill=TEXT)

    _center(d,112,'VS',_font(22,True),GOLD,W)
    when=str(match.get('commence_time') or '').replace('T',' ')[:16]
    _center(d,146,when,_font(14),MUTED,W)

    odds=[('1',match.get('home_odd')),('N',match.get('draw_odd')),('2',match.get('away_odd'))]
    xs=[414,536,658]
    for x,(lab,val) in zip(xs,odds):
        d.rounded_rectangle((x,185,x+104,246),12,fill=BG,outline=(62,64,70),width=2)
        _centered_box_compact(d,(x,185,x+104,246),lab,f'{float(val):.2f}' if val else '—')
    d.text((52,H-42),'5DOLLAR • BET365',font=_font(12,True),fill=MUTED)
    tag='ODDIUM SPORTSBOOK'; tf=_font(12,True); tb=d.textbbox((0,0),tag,font=tf); d.text((W-52-(tb[2]-tb[0]),H-42),tag,font=tf,fill=GOLD)
    out=BytesIO(); im.save(out,'PNG',optimize=True); out.seek(0); return out

def _centered_box_compact(d,box,label,value):
    x1,y1,x2,y2=box; f1=_font(13,True); f2=_font(18,True)
    for text,f,y,c in ((label,f1,y1+7,MUTED),(value,f2,y1+29,TEXT)):
        b=d.textbbox((0,0),text,font=f); d.text(((x1+x2-(b[2]-b[0]))/2,y),text,font=f,fill=c)

def _centered_box(d,box,label,value):
    x1,y1,x2,y2=box; f1=_font(18,True); f2=_font(25,True)
    for text,f,y,c in ((label,f1,y1+12,MUTED),(value,f2,y1+43,TEXT)):
        b=d.textbbox((0,0),text,font=f); d.text(((x1+x2-(b[2]-b[0]))/2,y),text,font=f,fill=c)

def betslip_card(legs:list,total:float,state='building')->BytesIO:
    W,H=900,max(430,260+len(legs)*76); im=Image.new('RGB',(W,H),BG); d=ImageDraw.Draw(im)
    d.rounded_rectangle((25,25,W-25,H-25),26,fill=PANEL,outline=GOLD,width=3)
    d.text((55,52),'ODDIUM',font=_font(28,True),fill=GOLD); d.text((55,92),'BET SLIP',font=_font(18,True),fill=MUTED)
    d.text((W-235,58),f'{len(legs):02d} SÉLECTIONS',font=_font(18,True),fill=TEXT)
    y=145
    for i,l in enumerate(legs[:10],1):
        pick={'HOME':l.get('home'),'DRAW':'Match nul','AWAY':l.get('away')}.get(l.get('selection'),'—')
        d.text((58,y),f'{i:02d}  {l.get("home","?")} — {l.get("away","?")}'[:55],font=_font(17,True),fill=TEXT)
        d.text((88,y+29),f'{pick}   @ {float(l.get("odd") or 0):.2f}',font=_font(16),fill=GOLD); y+=72
    d.line((55,H-120,W-55,H-120),fill=(55,57,62),width=2)
    d.text((55,H-92),'COTE TOTALE',font=_font(17,True),fill=MUTED); val=f'{total:.2f}'; b=d.textbbox((0,0),val,font=_font(32,True)); d.text((W-55-(b[2]-b[0]),H-101),val,font=_font(32,True),fill=GOLD)
    out=BytesIO(); im.save(out,'PNG',optimize=True); out.seek(0); return out
