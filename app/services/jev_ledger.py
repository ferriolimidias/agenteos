"""Bounded, tenant-prefixed Redis operational ledger and atomic spending guard."""
from __future__ import annotations
import json
import os
import time
from datetime import datetime, timezone
import redis.asyncio as redis

RETENTION_SECONDS = 7 * 86400
MAX_RUNS = 500
# Documented model ceiling 64k input tokens, price USD .042 / million.
ATTEMPT_NANO_USD = 2_688_000
RESERVE = """
local existing = redis.call('GET', KEYS[1])
if existing then return {'duplicate',existing} end
redis.call('ZREMRANGEBYSCORE',KEYS[3],'-inf',ARGV[1])
if redis.call('ZCARD',KEYS[3]) >= tonumber(ARGV[3]) then return {'concurrency_limited',''} end
local spent=tonumber(redis.call('GET',KEYS[2]) or '0')
if spent+tonumber(ARGV[4])>tonumber(ARGV[5]) then return {'budget_exceeded',''} end
redis.call('INCRBY',KEYS[2],ARGV[4]); redis.call('EXPIRE',KEYS[2],172800)
redis.call('SET',KEYS[1],ARGV[6],'EX',ARGV[7]); redis.call('ZADD',KEYS[3],ARGV[2],ARGV[6]); redis.call('EXPIRE',KEYS[3],120)
return {'reserved',ARGV[6]}
"""
RETRY_RESERVE = """
local spent=tonumber(redis.call('GET',KEYS[1]) or '0')
if spent+tonumber(ARGV[1])>tonumber(ARGV[2]) then return 0 end
redis.call('INCRBY',KEYS[1],ARGV[1]); redis.call('EXPIRE',KEYS[1],172800);return 1
"""
SAVE = """
redis.call('SET',KEYS[1],ARGV[1],'EX',ARGV[2]); redis.call('LPUSH',KEYS[2],ARGV[3])
local removed=redis.call('LRANGE',KEYS[2],ARGV[4],-1)
for _,id in ipairs(removed) do redis.call('DEL',ARGV[5]..id) end
redis.call('LTRIM',KEYS[2],0,tonumber(ARGV[4])-1); redis.call('EXPIRE',KEYS[2],ARGV[2])
return 1
"""


class LedgerUnavailable(Exception):
    pass


class JevLedger:
    def __init__(self, client, tenant):
        self.client = client
        self.prefix = f'jev:a06:{tenant}:'

    def key(self, suffix):
        return self.prefix + suffix

    def budget_key(self):
        return self.key('reserved:' + datetime.now(timezone.utc).strftime('%Y%m%d'))

    async def execute(self, method, *args, **kwargs):
        try:
            return await getattr(self.client, method)(*args, **kwargs)
        except Exception as exc:
            raise LedgerUnavailable('redis_unavailable') from exc

    async def reserve(self, digest, run_id, daily_nano, *, cost=ATTEMPT_NANO_USD):
        now = time.time()
        result = await self.execute('eval', RESERVE, 3, self.key('dedup:' + digest), self.budget_key(), self.key('active'),
                                    now, now + 90, 2, cost, daily_nano, run_id, RETENTION_SECONDS)
        return result[0], result[1]

    async def retry_reserve(self, daily_nano):
        return bool(await self.execute('eval', RETRY_RESERVE, 1, self.budget_key(), ATTEMPT_NANO_USD, daily_nano))

    async def release(self, run_id):
        await self.execute('zrem', self.key('active'), run_id)

    async def save(self, run):
        await self.execute('eval', SAVE, 2, self.key('run:' + run['id']), self.key('runs'),
                           json.dumps(run, ensure_ascii=False, allow_nan=False), RETENTION_SECONDS, run['id'], MAX_RUNS, self.key('run:'))

    async def get(self, run_id):
        raw = await self.execute('get', self.key('run:' + run_id))
        return json.loads(raw) if raw else None

    async def runs(self):
        ids = await self.execute('lrange', self.key('runs'), 0, MAX_RUNS - 1)
        if not ids:
            return []
        values = await self.execute('mget', [self.key('run:' + item) for item in ids])
        return [json.loads(item) for item in values if item]

    async def reserved(self):
        return int(await self.execute('get', self.budget_key()) or 0) / 1_000_000_000


def new_client():
    return redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379/0'), decode_responses=True,
                          socket_connect_timeout=1, socket_timeout=2, max_connections=10)
