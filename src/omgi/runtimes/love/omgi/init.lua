-- OMGI runtime for LÖVE games.
--
-- The patcher appends `require("omgi").start("<adapter>")` to the game's
-- main.lua. start() wraps love.update so the server is polled every frame on
-- the main thread; nothing here may ever take the game down, so every call
-- into the server or the adapter is guarded.
--
-- Adapter contract (omgi/adapters/<name>.lua returns a table):
--   info()                 -> { id, name, version }
--   snapshot()             -> { phase = "...", stable = bool, ... }  full state
--   phase()                -> phase, stable    (cheap; polled every frame)
--   actions()              -> list of { name, description, params = {...} }
--   act(name, params)      -> result table | nil, err { code, message }
--   query(name, params)    -> result table | nil, err       (optional)
--   queries()              -> list like actions()            (optional)

local json = require("omgi.json")
local Server = require("omgi.server")

local M = {
  PROTOCOL = 1,
  VERSION = "0.1.0",
}

local function load_config()
  local ok, cfg = pcall(require, "omgi.config")
  if ok and type(cfg) == "table" then return cfg end
  return {}
end

local function err(code, message)
  return nil, { code = code, message = message }
end

local function with_actions(adapter, snap)
  snap.actions = snap.stable and adapter.actions() or json.array()
  return snap
end

function M.handle(client, msg)
  local adapter, cfg = M.adapter, M.config
  local method = msg.method
  local params = type(msg.params) == "table" and msg.params or {}

  if method == "hello" then
    if cfg.token and cfg.token ~= "" and params.token ~= cfg.token then
      return err("unauthorized", "wrong or missing token")
    end
    client.authed = true
    client.subscribed = params.subscribe and true or false
    local methods = { "hello", "ping", "state", "actions", "act" }
    if adapter.query then
      methods[#methods + 1] = "queries"
      methods[#methods + 1] = "query"
    end
    return {
      protocol = M.PROTOCOL,
      game = adapter.info(),
      runtime = { name = "love", version = M.VERSION },
      methods = json.array(methods),
    }
  end

  if cfg.token and cfg.token ~= "" and not client.authed then
    return err("unauthorized", "send hello with the token first")
  end

  if method == "ping" then
    return { pong = true, frame = M.frame }
  elseif method == "state" then
    return with_actions(adapter, adapter.snapshot())
  elseif method == "actions" then
    local phase, stable = adapter.phase()
    return { phase = phase, stable = stable, actions = stable and adapter.actions() or json.array() }
  elseif method == "act" then
    if type(params.action) ~= "string" then
      return err("bad_params", "act needs params.action")
    end
    local _, stable = adapter.phase()
    if not stable and not params.force then
      return err("busy", "the game is mid-animation; wait for stable=true and try again")
    end
    local args = type(params.args) == "table" and params.args or {}
    return adapter.act(params.action, args)
  elseif method == "queries" and adapter.queries then
    return { queries = adapter.queries() }
  elseif method == "query" and adapter.query then
    if type(params.query) ~= "string" then
      return err("bad_params", "query needs params.query")
    end
    return adapter.query(params.query, type(params.args) == "table" and params.args or {})
  end
  return err("unknown_method", "no method '" .. tostring(method) .. "'")
end

function M.tick()
  M.frame = M.frame + 1
  M.server:poll()
  local phase, stable = M.adapter.phase()
  local sig = tostring(phase) .. "|" .. tostring(stable)
  if sig ~= M.last_sig then
    M.last_sig = sig
    M.server:broadcast(
      { event = "phase", data = { phase = phase, stable = stable, frame = M.frame } },
      function(c) return c.subscribed end
    )
  end
end

local function log(...)
  print("[omgi] " .. table.concat({ ... }, " "))
end

function M.start(adapter_name, opts)
  if M.server then return M end
  opts = opts or {}
  local cfg = load_config()
  for k, v in pairs(opts) do cfg[k] = v end
  M.config = cfg
  M.frame = 0

  local ok, adapter = pcall(require, "omgi.adapters." .. adapter_name)
  if not ok then
    log("adapter '" .. adapter_name .. "' failed to load: " .. tostring(adapter))
    return nil
  end
  M.adapter = adapter

  local port = cfg.port or 47800
  local server, serr = Server.new({
    host = cfg.host or "127.0.0.1",
    port = port,
    handler = M.handle,
    socket = cfg.socket,
  })
  if not server then
    log(serr)
    return nil
  end
  M.server = server
  log("listening on " .. (cfg.host or "127.0.0.1") .. ":" .. port .. " for " .. adapter_name)

  local failures = 0
  local function guarded_tick()
    if failures >= 50 then return end
    local tok, terr = pcall(M.tick)
    if not tok then
      failures = failures + 1
      log("tick error (" .. failures .. "): " .. tostring(terr))
      if failures >= 50 then log("too many errors; link disabled until restart") end
    end
  end

  local love = rawget(_G, "love")
  if love and not cfg.no_hook then
    local original = love.update
    love.update = function(...)
      if original then original(...) end
      guarded_tick()
    end
  end
  M.step = guarded_tick
  return M
end

return M
