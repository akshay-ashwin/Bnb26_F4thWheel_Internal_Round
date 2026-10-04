-- Multi-bucket token bucket: check every bucket first, spend only if all of them allow.
-- A rejected request therefore consumes nothing, from any bucket. One round trip per request.
-- The clock is Redis TIME, so every API worker sees the same time.
--
-- KEYS[1] cooldown key   KEYS[2] violation counter   KEYS[3] slow-poll marker
-- KEYS[4..3+n] bucket keys, most specific first (L3, then L2, then L1)
-- ARGV[1] n   ARGV[2] check cooldown (0/1)   ARGV[3] violations before cooldown
-- ARGV[4] violation window ms   ARGV[5] cooldown ms   ARGV[6] slow marker ttl ms
-- then 6 values per bucket: rate_per_s, burst, cost, counts_violation (0/1),
--                           marks_slow (0/1), layer number
-- Returns {allowed (0/1), layer number (0 if allowed), retry_after_ms}

local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local n = tonumber(ARGV[1])

if ARGV[2] == '1' then
  local ttl = redis.call('PTTL', KEYS[1])
  if ttl > 0 then
    return {0, 2, ttl}
  end
end

local tokens = {}
local deny_layer, deny_wait = 0, 0
local violation, slow = false, false

for i = 1, n do
  local o = 6 + (i - 1) * 6
  local rate = tonumber(ARGV[o + 1])
  local burst = tonumber(ARGV[o + 2])
  local cost = tonumber(ARGV[o + 3])
  local st = redis.call('HMGET', KEYS[3 + i], 't', 'ts')
  local tk, ts = tonumber(st[1]), tonumber(st[2])
  if tk == nil or ts == nil then
    tk, ts = burst, now
  end
  tk = math.min(burst, tk + math.max(0, now - ts) * rate / 1000)
  tokens[i] = tk
  if tk < cost then
    local wait = math.ceil((cost - tk) * 1000 / rate)
    if wait > deny_wait then deny_wait = wait end
    if deny_layer == 0 then deny_layer = tonumber(ARGV[o + 6]) end
    if ARGV[o + 4] == '1' then violation = true end
    if ARGV[o + 5] == '1' then slow = true end
  end
end

if deny_layer > 0 then
  if violation then
    local v = redis.call('INCR', KEYS[2])
    if v == 1 then redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[4])) end
    if v >= tonumber(ARGV[3]) then
      redis.call('SET', KEYS[1], '1', 'PX', tonumber(ARGV[5]))
      redis.call('DEL', KEYS[2])
    end
  end
  if slow then
    redis.call('SET', KEYS[3], '1', 'PX', tonumber(ARGV[6]))
  end
  return {0, deny_layer, deny_wait}
end

for i = 1, n do
  local o = 6 + (i - 1) * 6
  local rate = tonumber(ARGV[o + 1])
  local burst = tonumber(ARGV[o + 2])
  local cost = tonumber(ARGV[o + 3])
  redis.call('HSET', KEYS[3 + i], 't', tokens[i] - cost, 'ts', now)
  redis.call('PEXPIRE', KEYS[3 + i], math.ceil(2 * burst * 1000 / rate))
end
return {1, 0, 0}
