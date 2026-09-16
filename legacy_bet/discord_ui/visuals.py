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
    # V42: dense premium market card. Larger crests/text, structured zones, no dead space.
    W,H=1200,390; im=Image.new('RGB',(W,H),BG); d=ImageDraw.Draw(im)
    d.rounded_rectangle((20,20,W-20,H-20),26,fill=PANEL,outline=GOLD,width=3)

    competition=str(match.get('competition_name') or 'SPORTSBOOK').upper()
    state_label={'live':'● LIVE','won':'✓ GAGNÉ','lost':'✕ PERDU','selected':'◆ SÉLECTION','prematch':'PRÉ-MATCH'}.get(state,state.upper())
    sf=GREEN if state=='won' else RED if state=='lost' else ORANGE if state=='live' else GOLD
    d.text((48,42),'ODDIUM • MARKET',font=_font(25,True),fill=GOLD)
    d.text((48,78),competition[:42],font=_font(19,True),fill=TEXT)
    stf=_font(18,True); bb=d.textbbox((0,0),state_label,font=stf); d.text((W-48-(bb[2]-bb[0]),48),state_label,font=stf,fill=sf)
    d.line((48,108,W-48,108),fill=(52,53,58),width=2)

    home=str(match.get('home_team') or 'Domicile'); away=str(match.get('away_team') or 'Extérieur')
    # Large, balanced club crests.
    logo_size=142
    hl=_logo(home,logo_size); al=_logo(away,logo_size)
    hx,ay=92,124; ax=W-92-logo_size
    im.paste(hl,(hx,ay),hl); im.paste(al,(ax,ay),al)

    # Team names sit close to their crest instead of floating in empty space.
    namef=_font(28,True)
    home_txt=home[:22]; away_txt=away[:22]
    d.text((hx+logo_size+24,150),home_txt,font=namef,fill=TEXT)
    ab=d.textbbox((0,0),away_txt,font=namef)
    d.text((ax-24-(ab[2]-ab[0]),150),away_txt,font=namef,fill=TEXT)

    _center(d,132,'VS',_font(25,True),GOLD,W)
    when=str(match.get('commence_time') or '').replace('T',' ')[:16]
    _center(d,174,when,_font(18,True),MUTED,W)

    # Clear 1/N/2 market rail: larger typography and wider boxes.
    d.line((48,282,W-48,282),fill=(52,53,58),width=2)
    odds=[('1 • DOMICILE',match.get('home_odd')),('N • NUL',match.get('draw_odd')),('2 • EXTÉRIEUR',match.get('away_odd'))]
    box_w=250; gap=26; total=box_w*3+gap*2; start=(W-total)//2
    for i,(lab,val) in enumerate(odds):
        x=start+i*(box_w+gap)
        d.rounded_rectangle((x,300,x+box_w,354),13,fill=BG,outline=(74,76,83),width=2)
        value=f'{float(val):.2f}' if val else '—'
        lf=_font(15,True); vf=_font(23,True)
        d.text((x+18,318),lab,font=lf,fill=MUTED)
        vb=d.textbbox((0,0),value,font=vf); d.text((x+box_w-18-(vb[2]-vb[0]),312),value,font=vf,fill=TEXT)

    out=BytesIO(); im.save(out,'PNG',optimize=True); out.seek(0); return out

def _centered_box_compact(d,box,label,value):
    x1,y1,x2,y2=box; f1=_font(13,True); f2=_font(18,True)
    for text,f,y,c in ((label,f1,y1+7,MUTED),(value,f2,y1+29,TEXT)):
        b=d.textbbox((0,0),text,font=f); d.text(((x1+x2-(b[2]-b[0]))/2,y),text,font=f,fill=c)

def _centered_box(d,box,label,value):
    x1,y1,x2,y2=box; f1=_font(18,True); f2=_font(25,True)
    for text,f,y,c in ((label,f1,y1+12,MUTED),(value,f2,y1+43,TEXT)):
        b=d.textbbox((0,0),text,font=f); d.text(((x1+x2-(b[2]-b[0]))/2,y),text,font=f,fill=c)
