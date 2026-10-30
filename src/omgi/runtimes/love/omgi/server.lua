-- Non-blocking line-delimited JSON server, polled once per frame from the
-- game's update loop so every request runs on the game's main thread.

local json = require("omgi.json")

local Server = {}
Server.__index = Server

local MAX_LINE = 1024 * 1024

function Server.new(opts)
  local socket = opts.socket or require("socket")
  local self = setmetatable({
    socket = socket,
    host = opts.host or "127.0.0.1",
    port = opts.port,
    handler = opts.handler,       -- function(client, msg) -> result | nil, err
    on_log = opts.log or print,
    clients = {},
  }, Server)
  local srv, err = socket.bind(self.host, self.port)
  if not srv then
    return nil, string.format("can't listen on %s:%s (%s)", self.host, tostring(self.port), tostring(err))
  end
  srv:settimeout(0)
  self.listener = srv
  return self
end

function Server:log(...)
  self.on_log("[omgi] " .. table.concat({ ... }, " "))
end

function Server:_accept()
  while true do
    local conn = self.listener:accept()
    if not conn then return end
    conn:settimeout(0)
    pcall(function() conn:setoption("tcp-nodelay", true) end)
    self.clients[#self.clients + 1] = { conn = conn, inbuf = "", outbuf = "", authed = false }
  end
end

function Server:send(client, msg)
  client.outbuf = client.outbuf .. json.encode(msg) .. "\n"
end

function Server:broadcast(msg, filter)
  for _, c in ipairs(self.clients) do
    if not filter or filter(c) then self:send(c, msg) end
  end
end

function Server:_flush(client)
  while #client.outbuf > 0 do
    local sent, err, partial = client.conn:send(client.outbuf)
    local n = sent or partial or 0
    if n > 0 then client.outbuf = client.outbuf:sub(n + 1) end
    if not sent then
      return err ~= "closed"   -- timeout: try again next frame
    end
  end
  return true
end

function Server:_handle_line(client, line)
  local ok, msg = pcall(json.decode, line)
  if not ok or type(msg) ~= "table" then
    self:send(client, { error = { code = "bad_request", message = "invalid JSON" } })
    return
  end
  local ok2, result, err = pcall(self.handler, client, msg)
  if not ok2 then
    self:log("handler error:", tostring(result))
    self:send(client, { id = msg.id, error = { code = "internal", message = tostring(result) } })
  elseif err then
    self:send(client, { id = msg.id, error = err })
  else
    if result == nil then result = {} end
    self:send(client, { id = msg.id, result = result })
  end
end

function Server:_read(client)
  while true do
    local data, err, partial = client.conn:receive(8192)
    local chunk = data or partial
    if chunk and #chunk > 0 then client.inbuf = client.inbuf .. chunk end
    if not data then
      if err == "closed" then return false end
      break
    end
  end
  while true do
    local nl = client.inbuf:find("\n", 1, true)
    if not nl then break end
    local line = client.inbuf:sub(1, nl - 1)
    client.inbuf = client.inbuf:sub(nl + 1)
    if line:find("%S") then self:_handle_line(client, line) end
  end
  if #client.inbuf > MAX_LINE then
    self:log("dropping client: request too large")
    return false
  end
  return true
end

function Server:poll()
  self:_accept()
  local keep = {}
  for _, c in ipairs(self.clients) do
    local alive = self:_read(c) and self:_flush(c)
    if alive then
      keep[#keep + 1] = c
    else
      pcall(function() c.conn:close() end)
    end
  end
  self.clients = keep
end

function Server:close()
  for _, c in ipairs(self.clients) do pcall(function() c.conn:close() end) end
  self.clients = {}
  if self.listener then self.listener:close() end
end

return Server
