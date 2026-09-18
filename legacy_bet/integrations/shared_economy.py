from __future__ import annotations
import asyncio, json, logging
try:
    import psycopg
except Exception:
    psycopg=None
log=logging.getLogger('oddium.economy')
class SharedEconomyError(RuntimeError): pass
class SharedEconomyClient:
    def __init__(self,url:str): self.url=(url or '').strip()
    @property
    def enabled(self): return bool(self.url)
    def _connect(self):
        if not self.enabled: raise SharedEconomyError('ECONOMY_DATABASE_URL absente')
        if psycopg is None: raise SharedEconomyError('psycopg indisponible')
        return psycopg.connect(self.url,autocommit=False)
    def _schema(self,c):
        c.execute('CREATE TABLE IF NOT EXISTS economy_wallets(user_id BIGINT PRIMARY KEY,balance BIGINT NOT NULL DEFAULT 0 CHECK(balance>=0),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())')
        c.execute('CREATE TABLE IF NOT EXISTS economy_transactions(id BIGSERIAL PRIMARY KEY,source TEXT NOT NULL,reference TEXT NOT NULL,user_id BIGINT NOT NULL,amount BIGINT NOT NULL,reason TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),UNIQUE(source,reason,reference))')
        c.execute('CREATE TABLE IF NOT EXISTS economy_events(id BIGSERIAL PRIMARY KEY,source TEXT NOT NULL,event_key TEXT NOT NULL UNIQUE,payload JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),processed_at TIMESTAMPTZ)')
    def _balance(self,uid):
        with self._connect() as c:
            self._schema(c); c.execute('INSERT INTO economy_wallets(user_id,balance) VALUES(%s,0) ON CONFLICT DO NOTHING',(uid,)); r=c.execute('SELECT balance FROM economy_wallets WHERE user_id=%s',(uid,)).fetchone(); c.commit(); return int(r[0])
    async def get_balance(self,user_id:int): return await asyncio.to_thread(self._balance,int(user_id))
    def _mutate(self,uid,amount,reason,reference):
        with self._connect() as c:
            self._schema(c)
            old=c.execute('SELECT 1 FROM economy_transactions WHERE source=%s AND reason=%s AND reference=%s',('ODDIUM',reason,reference)).fetchone()
            if old:
                r=c.execute('SELECT balance FROM economy_wallets WHERE user_id=%s',(uid,)).fetchone(); c.rollback(); return True,int(r[0] if r else 0)
            c.execute('INSERT INTO economy_wallets(user_id,balance) VALUES(%s,0) ON CONFLICT DO NOTHING',(uid,))
            if amount<0: r=c.execute('UPDATE economy_wallets SET balance=balance+%s,updated_at=NOW() WHERE user_id=%s AND balance >= %s RETURNING balance',(amount,uid,-amount)).fetchone()
            else: r=c.execute('UPDATE economy_wallets SET balance=balance+%s,updated_at=NOW() WHERE user_id=%s RETURNING balance',(amount,uid)).fetchone()
            if not r: c.rollback(); return False,self._balance(uid)
            c.execute('INSERT INTO economy_transactions(source,reference,user_id,amount,reason) VALUES(%s,%s,%s,%s,%s)',('ODDIUM',reference,uid,amount,reason)); c.commit(); return True,int(r[0])
    async def mutate(self,user_id:int,amount:int,reason:str,reference:str): return await asyncio.to_thread(self._mutate,int(user_id),int(amount),str(reason),str(reference))
    def _emit(self,event):
        key=str(event.get('event_key') or event.get('reference') or f"{event.get('type','event')}:{event.get('user_id','')}:{event.get('bet_id',event.get('combo_id',''))}")
        with self._connect() as c:
            self._schema(c); c.execute('INSERT INTO economy_events(source,event_key,payload) VALUES(%s,%s,%s::jsonb) ON CONFLICT(event_key) DO NOTHING',('ODDIUM',key,json.dumps(event,ensure_ascii=False))); c.commit()
    async def emit(self,event:dict):
        try: await asyncio.to_thread(self._emit,event)
        except Exception: log.exception('Événement économie commune non publié: %s',event.get('type'))
