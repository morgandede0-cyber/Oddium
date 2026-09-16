from __future__ import annotations

import hashlib, json, time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _stable(obj: Any) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _event_key(ev: dict[str, Any]) -> str:
    keep = {k: ev.get(k) for k in ("id","type","minute","team","count","period","score","player","player_in","player_out") if ev.get(k) is not None}
    return _stable(keep)


def _num(v):
    try: return int(v) if v is not None else None
    except (TypeError, ValueError): return None


@dataclass
class FixtureBrain:
    fixture_id: int
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    revision: int = 0
    snapshot_hash: str = ""
    status: str = ""
    minute: int | None = None
    score: tuple[Any, Any] = (None, None)
    seen_events: set[str] = field(default_factory=set)
    changes: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=200))
    pending_corrections: dict[str, dict[str, Any]] = field(default_factory=dict)
    anomalies: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=100))


class FiveDollarIntelligence:
    """Conservative self-healing state layer above 5Dollar.

    Rules: never estimate a football fact; repair only from a previously confirmed
    5Dollar value. Suspicious regressions are quarantined and accepted only when
    repeated by 5Dollar on two consecutive snapshots (provider correction/VAR).
    """
    TERMINAL = {"finished", "full", "ft", "cancelled", "canceled", "postponed", "abandoned"}

    def __init__(self, state_path: str | Path | None = None):
        self.fixtures: dict[int, FixtureBrain] = {}
        self.metrics = defaultdict(int)
        self.state_path = Path(state_path) if state_path else None
        if self.state_path: self._load()

    @staticmethod
    def _minute(item: dict[str, Any]) -> int | None:
        for v in (item.get("minute"), item.get("elapsed"), item.get("match_minute"), item.get("live_minute"), item.get("status_code")):
            try:
                n = int(str(v).replace("'", "").strip())
                if 0 <= n <= 130: return n
            except (TypeError, ValueError): pass
        return None

    @staticmethod
    def _score(item: dict[str, Any]) -> tuple[Any, Any]:
        g = item.get("goals") or {}
        return _num(g.get("home")), _num(g.get("away"))

    def _anomaly(self, brain: FixtureBrain, kind: str, incoming: Any, kept: Any) -> None:
        row={"at":time.time(),"type":kind,"incoming":incoming,"kept":kept}
        brain.anomalies.append(row); self.metrics["anomalies"] += 1

    def _confirm_or_hold(self, brain: FixtureBrain, key: str, incoming: Any, current: Any) -> tuple[Any, bool]:
        """Hold first suspicious correction; accept if provider repeats it next poll."""
        token=_stable(incoming)
        pending=brain.pending_corrections.get(key)
        if pending and pending.get("token") == token:
            pending["count"] += 1
            if pending["count"] >= 2:
                brain.pending_corrections.pop(key, None); self.metrics["provider_corrections_accepted"] += 1
                return incoming, True
        else:
            brain.pending_corrections[key]={"token":token,"count":1,"value":incoming,"at":time.time()}
        self._anomaly(brain, key, incoming, current); self.metrics["self_heals"] += 1
        return current, False

    def heal(self, item: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return a safe snapshot. Only previously confirmed provider facts may fill/repair fields."""
        out=dict(item)
        try: fid=int(out["id"])
        except (KeyError,TypeError,ValueError): return out, []
        brain=self.fixtures.setdefault(fid, FixtureBrain(fid)); repairs=[]
        minute=self._minute(out); score=self._score(out); status=str(out.get("status") or "").lower()

        if brain.revision:
            # Missing clock during an active phase: retain last confirmed provider clock.
            if minute is None and brain.minute is not None and status not in self.TERMINAL:
                out["minute"]=brain.minute; repairs.append({"field":"minute","reason":"missing","value":brain.minute}); self.metrics["self_heals"] += 1
            elif minute is not None and brain.minute is not None and minute < brain.minute:
                safe, accepted=self._confirm_or_hold(brain,"clock_regression",minute,brain.minute)
                if not accepted: out["minute"]=safe; out["status_code"]=str(safe); repairs.append({"field":"minute","reason":"regression","value":safe})

            # Goals can be corrected by VAR. First regression is quarantined; a repeated correction wins.
            oldh,olda=brain.score; newh,newa=score
            if None not in (oldh,olda,newh,newa) and (newh < oldh or newa < olda):
                safe,accepted=self._confirm_or_hold(brain,"score_regression",score,brain.score)
                if not accepted:
                    goals=dict(out.get("goals") or {}); goals["home"],goals["away"]=safe; out["goals"]=goals
                    repairs.append({"field":"score","reason":"regression_quarantine","value":safe})

            # A terminal match must not flip back to live from one isolated bad response.
            if brain.status.lower() in self.TERMINAL and status and status not in self.TERMINAL:
                safe,accepted=self._confirm_or_hold(brain,"terminal_status_regression",status,brain.status)
                if not accepted: out["status"]=safe; repairs.append({"field":"status","reason":"terminal_regression","value":safe})
        return out, repairs

    def ingest(self, item: dict[str, Any]) -> dict[str, Any]:
        try: fid=int(item["id"])
        except (KeyError,TypeError,ValueError): return {"revision":0,"new_events":[],"changes":[],"repairs":[]}
        brain=self.fixtures.setdefault(fid, FixtureBrain(fid)); brain.last_seen=time.time()
        safe,repairs=self.heal(item)
        status=str(safe.get("status") or ""); minute=self._minute(safe); score=self._score(safe)
        events=safe.get("events") or []
        if isinstance(events,dict): events=events.get("events") or []
        new_events=[]
        for ev in events if isinstance(events,list) else []:
            if not isinstance(ev,dict): continue
            k=_event_key(ev)
            if k not in brain.seen_events: brain.seen_events.add(k); new_events.append(ev)
        changes=[]
        if brain.revision:
            if status and status != brain.status: changes.append({"type":"status","from":brain.status,"to":status})
            if score != brain.score: changes.append({"type":"score","from":brain.score,"to":score})
            if minute is not None and minute != brain.minute: changes.append({"type":"clock","from":brain.minute,"to":minute})
        snap_hash=_stable(safe)
        if snap_hash != brain.snapshot_hash: brain.revision+=1; brain.snapshot_hash=snap_hash
        brain.status=status or brain.status; brain.minute=minute if minute is not None else brain.minute; brain.score=score
        for c in changes: brain.changes.append({"at":time.time(),**c})
        self.metrics["snapshots"]+=1; self.metrics["new_events"]+=len(new_events); self.metrics["changes"]+=len(changes)
        if repairs: self.metrics["snapshots_repaired"]+=1
        self._save()
        return {"revision":brain.revision,"new_events":new_events,"changes":changes,"event_count":len(brain.seen_events),"repairs":repairs,"safe_snapshot":safe}

    def enrich(self, item: dict[str, Any]) -> dict[str, Any]:
        meta=self.ingest(item); out=dict(meta.pop("safe_snapshot", item)); out["oddium_intelligence"]=meta; return out

    def cleanup(self, max_age_seconds: int = 86400) -> None:
        now=time.time()
        for fid in [k for k,v in self.fixtures.items() if now-v.last_seen > max_age_seconds]: self.fixtures.pop(fid,None)
        self._save()

    def _save(self):
        if not self.state_path: return
        try:
            self.state_path.parent.mkdir(parents=True,exist_ok=True)
            data={str(fid):{"first_seen":b.first_seen,"last_seen":b.last_seen,"revision":b.revision,"snapshot_hash":b.snapshot_hash,"status":b.status,"minute":b.minute,"score":list(b.score),"seen_events":list(b.seen_events),"changes":list(b.changes),"anomalies":list(b.anomalies),"pending_corrections":b.pending_corrections} for fid,b in self.fixtures.items()}
            tmp=self.state_path.with_suffix(".tmp"); tmp.write_text(json.dumps(data,ensure_ascii=False,default=str),encoding="utf-8"); tmp.replace(self.state_path)
        except Exception: self.metrics["state_save_errors"]+=1

    def _load(self):
        try:
            if not self.state_path.exists(): return
            data=json.loads(self.state_path.read_text(encoding="utf-8"))
            for k,v in data.items():
                b=FixtureBrain(int(k)); b.first_seen=v.get("first_seen",time.time()); b.last_seen=v.get("last_seen",time.time()); b.revision=v.get("revision",0); b.snapshot_hash=v.get("snapshot_hash",""); b.status=v.get("status",""); b.minute=v.get("minute"); b.score=tuple(v.get("score",[None,None])); b.seen_events=set(v.get("seen_events",[])); b.changes=deque(v.get("changes",[]),maxlen=200); b.anomalies=deque(v.get("anomalies",[]),maxlen=100); b.pending_corrections=v.get("pending_corrections",{}); self.fixtures[b.fixture_id]=b
            self.metrics["state_restores"]+=1
        except Exception: self.metrics["state_load_errors"]+=1

    def diagnostics(self) -> dict[str, Any]:
        recent=sum(len(b.anomalies) for b in self.fixtures.values())
        return {"tracked_fixtures":len(self.fixtures),"recent_anomalies":recent,"self_healing":True,"correction_policy":"quarantine_then_confirm",**dict(self.metrics)}


    def fixture_quality(self, fixture_id: int) -> dict[str, Any]:
        b=self.fixtures.get(int(fixture_id))
        if not b: return {"known":False,"quality":0}
        age=max(0.0,time.time()-b.last_seen); score=100
        if age>30: score-=20
        if age>90: score-=30
        if b.minute is None and b.status.lower() not in self.TERMINAL: score-=15
        if b.pending_corrections: score-=20
        score-=min(25,len(b.anomalies)*2)
        return {"known":True,"quality":max(0,score),"age_s":round(age,1),"pending_corrections":len(b.pending_corrections),"anomalies":len(b.anomalies),"revision":b.revision}

    def rollback_snapshot(self, fixture_id: int) -> dict[str, Any] | None:
        """Expose last confirmed facts for safe operational recovery; never synthesizes data."""
        b=self.fixtures.get(int(fixture_id))
        if not b: return None
        return {"id":b.fixture_id,"status":b.status,"minute":b.minute,"goals":{"home":b.score[0],"away":b.score[1]},"revision":b.revision}

    @staticmethod
    def analyse_team_history(rows: list[dict[str, Any]], team_id: int, limit: int = 10) -> dict[str, Any]:
        games=[]
        for r in rows:
            if str(r.get("status")) != "finished": continue
            teams=r.get("teams") or {}; home=teams.get("home") or {}; away=teams.get("away") or {}
            try: is_home=int(home.get("id"))==int(team_id); is_away=int(away.get("id"))==int(team_id)
            except (TypeError,ValueError): continue
            if not (is_home or is_away): continue
            g=r.get("goals") or {}; gf=g.get("home") if is_home else g.get("away"); ga=g.get("away") if is_home else g.get("home")
            try: gf=float(gf); ga=float(ga)
            except (TypeError,ValueError): continue
            games.append((gf,ga,r))
            if len(games)>=limit: break
        n=len(games)
        if not n:return {"games":0}
        wins=sum(gf>ga for gf,ga,_ in games); draws=sum(gf==ga for gf,ga,_ in games); gf=sum(x[0] for x in games); ga=sum(x[1] for x in games)
        return {"games":n,"wins":wins,"draws":draws,"losses":n-wins-draws,"points":wins*3+draws,"goals_for_avg":round(gf/n,2),"goals_against_avg":round(ga/n,2),"btts_pct":round(100*sum(a>0 and b>0 for a,b,_ in games)/n,1),"over_2_5_pct":round(100*sum(a+b>2.5 for a,b,_ in games)/n,1)}
