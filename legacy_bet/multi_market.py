from __future__ import annotations
import json, math, os, time
from dataclasses import dataclass
from pathlib import Path
from statistics import median


def _f(v):
    try:
        x=float(v)
        return x if math.isfinite(x) and x>1.0 else None
    except (TypeError,ValueError): return None

def _devig2(a,b):
    ia,ib=1/a,1/b; s=ia+ib; return ia/s,ib/s

def _pois(lam,k): return math.exp(-lam)*(lam**k)/math.factorial(k)

def score_probs(lh,la,max_goals=10,rho=-0.08):
    h=d=a=0.0; over25=0.0; btts=0.0
    for i in range(max_goals+1):
      pi=_pois(lh,i)
      for j in range(max_goals+1):
        p=pi*_pois(la,j)
        if i<=1 and j<=1:
          if i==0 and j==0: p*=1-lh*la*rho
          elif i==0 and j==1: p*=1+lh*rho
          elif i==1 and j==0: p*=1+la*rho
          else: p*=1-rho
        if i>j:h+=p
        elif i==j:d+=p
        else:a+=p
        if i+j>=3: over25+=p
        if i>0 and j>0: btts+=p
    s=h+d+a
    return (h/s,d/s,a/s,over25,btts)

@dataclass
class MultiMarketEstimate:
    home_probability: float; draw_probability: float; away_probability: float
    lambda_home: float; lambda_away: float
    confidence: float; markets_used: int; disagreement: float
    movement: float; source: str

class MultiMarketGoalEngine:
    """Infers a coherent goal distribution from every market actually available.
    No xG/injury/lineup value is fabricated: absent inputs are simply omitted.
    """
    OU_PAIRS=(("Avg>2.5","Avg<2.5"),("B365>2.5","B365<2.5"),("P>2.5","P<2.5"),("Max>2.5","Max<2.5"))
    BTTS_PAIRS=(("AvgBTTSY","AvgBTTSN"),("B365BTTSY","B365BTTSN"),("BTTSY","BTTSN"))
    def __init__(self,snapshot_path="data/market_snapshots.json"):
        self.path=Path(snapshot_path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.snapshots=self._load()
    def _load(self):
        try:return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:return {}
    def _save(self):
        try:self.path.write_text(json.dumps(self.snapshots,ensure_ascii=False),encoding="utf-8")
        except Exception:pass
    @staticmethod
    def _pair(row,pairs):
        vals=[]
        for x,y in pairs:
            a,b=_f(row.get(x)),_f(row.get(y))
            if a and b: vals.append(_devig2(a,b)[0])
        return median(vals) if vals else None
    def infer(self,key,row,market_quote,model_lambdas=None):
        if market_quote is None:return None
        target=(market_quote.home_probability,market_quote.draw_probability,market_quote.away_probability)
        ou=self._pair(row,self.OU_PAIRS) if row else None
        btts=self._pair(row,self.BTTS_PAIRS) if row else None
        best=None
        # Pure-python grid + refinement: stable and dependency-free.
        for step,span in ((0.10,None),(0.025,0.18)):
            if best is None:
                hs=[0.20+i*step for i in range(45)]; aas=[0.15+i*step for i in range(40)]
            else:
                bh,ba=best[1],best[2]
                hs=[max(.15,bh-span)+i*step for i in range(int(2*span/step)+1)]
                aas=[max(.12,ba-span)+i*step for i in range(int(2*span/step)+1)]
            cand=None
            for lh in hs:
              for la in aas:
                p=score_probs(lh,la)
                loss=4.0*sum((p[i]-target[i])**2 for i in range(3))
                used=1
                if ou is not None: loss+=2.4*(p[3]-ou)**2; used+=1
                if btts is not None: loss+=1.8*(p[4]-btts)**2; used+=1
                if model_lambdas:
                    loss+=0.035*((lh-model_lambdas[0])**2+(la-model_lambdas[1])**2)
                if cand is None or loss<cand[0]: cand=(loss,lh,la,p,used)
            best=cand
        loss,lh,la,p,used=best
        disagreement=min(1.0,math.sqrt(max(0.0,loss)))
        # More independent books + more markets + internal coherence => higher confidence.
        bookq=min(1.0,max(0.25,market_quote.books/5))
        confidence=max(.35,min(.97,.48+.18*bookq+.10*(used-1)-.20*disagreement))
        now=time.time(); hist=self.snapshots.setdefault(key,[])
        current=[target[0],target[1],target[2]]
        movement=0.0
        if hist:
            prev=hist[-1].get("p",current); movement=sum(abs(current[i]-prev[i]) for i in range(3))/2
        if not hist or now-hist[-1].get("t",0)>=300:
            hist.append({"t":now,"p":current,"lh":lh,"la":la}); self.snapshots[key]=hist[-72:]; self._save()
        return MultiMarketEstimate(p[0],p[1],p[2],lh,la,confidence,used,disagreement,movement,"multi-market goal model")
