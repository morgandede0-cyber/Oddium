from __future__ import annotations
import json, time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

class FiveDollarGuardian:
    """Operational safety layer: circuit breaker, schema watch, audit and recovery.
    It never invents football facts; it only protects transport/cache/state operations.
    """
    def __init__(self, root: str | Path):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.audit_path=self.root/'guardian_audit.jsonl'; self.schema_path=self.root/'known_schema.json'
        self.failures=defaultdict(int); self.open_until=defaultdict(float); self.metrics=defaultdict(int)
        self.known_schema=self._load_json(self.schema_path,{})
        self.last_success=0.0; self.last_failure=0.0; self.recent=deque(maxlen=100)
    @staticmethod
    def _load_json(path, default):
        try: return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
        except Exception: return default
    def audit(self,event:str,**data):
        row={'at':time.time(),'event':event,**data}; self.recent.append(row)
        try:
            with self.audit_path.open('a',encoding='utf-8') as f: f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
        except Exception: self.metrics['audit_write_errors']+=1
    def allow(self, group:str)->bool:
        if time.time() < self.open_until[group]: self.metrics['circuit_rejections']+=1; return False
        return True
    def success(self,group:str):
        if self.failures[group]: self.audit('circuit_recovered',group=group,failures=self.failures[group])
        self.failures[group]=0; self.open_until[group]=0; self.last_success=time.time(); self.metrics['successes']+=1
    def failure(self,group:str,reason:str):
        self.last_failure=time.time(); self.failures[group]+=1; self.metrics['failures']+=1
        # transient provider failures: trip only after 3 consecutive failures, short cooldown.
        if self.failures[group]>=3:
            delay=min(60, 10*(self.failures[group]-2)); self.open_until[group]=time.time()+delay
            self.metrics['circuit_trips']+=1; self.audit('circuit_open',group=group,seconds=delay,reason=reason)
    def schema_watch(self,path:str,payload:Any):
        if not isinstance(payload,dict): return []
        key=path; keys=sorted(payload.keys()); old=set(self.known_schema.get(key,[])); new=[k for k in keys if k not in old]
        if new:
            self.known_schema[key]=sorted(old|set(keys)); self.metrics['new_schema_fields']+=len(new)
            self.audit('schema_discovery',path=path,fields=new)
            try:
                tmp=self.schema_path.with_suffix('.tmp'); tmp.write_text(json.dumps(self.known_schema,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(self.schema_path)
            except Exception: self.metrics['schema_save_errors']+=1
        return new
    def health(self)->dict[str,Any]:
        now=time.time(); opened={k:round(v-now,1) for k,v in self.open_until.items() if v>now}
        return {'healthy':not opened,'open_circuits':opened,'last_success_age_s':round(now-self.last_success,1) if self.last_success else None,
                'last_failure_age_s':round(now-self.last_failure,1) if self.last_failure else None,'audit':str(self.audit_path),**dict(self.metrics)}
