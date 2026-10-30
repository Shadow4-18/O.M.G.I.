-- Small JSON encoder/decoder for the OMGI runtime (Lua 5.1 / LuaJIT).
--
-- Tables are objects unless they're a non-empty sequence or were made with
-- json.array(); use json.array{} for lists that may be empty so they encode
-- as [] instead of {}. Non-finite numbers encode as null.

local json = {}

local ARRAY = {}
local array_mt = { __jsontype = ARRAY }

function json.array(t)
  return setmetatable(t or {}, array_mt)
end

local function is_array(t)
  local mt = getmetatable(t)
  if mt and mt.__jsontype == ARRAY then return true end
  local n = #t
  if n == 0 then return false end
  local count = 0
  for k in pairs(t) do
    if type(k) ~= "number" or k < 1 or k > n or k % 1 ~= 0 then return false end
    count = count + 1
  end
  return count == n
end

local escapes = {
  ['"'] = '\\"', ["\\"] = "\\\\", ["\b"] = "\\b", ["\f"] = "\\f",
  ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t",
}

local function encode_string(s)
  return '"' .. s:gsub('[%c"\\]', function(c)
    return escapes[c] or string.format("\\u%04x", c:byte())
  end) .. '"'
end

local function encode_number(n)
  if n ~= n or n == math.huge or n == -math.huge then return "null" end
  if n % 1 == 0 and n > -1e15 and n < 1e15 then
    return string.format("%d", n)
  end
  return string.format("%.14g", n)
end

local encode_value

local function encode_table(t, stack, out)
  if stack[t] then error("json: circular reference") end
  stack[t] = true
  if is_array(t) then
    out[#out + 1] = "["
    for i = 1, #t do
      if i > 1 then out[#out + 1] = "," end
      encode_value(t[i], stack, out)
    end
    out[#out + 1] = "]"
  else
    local keys = {}
    for k, v in pairs(t) do
      if type(v) ~= "function" and type(v) ~= "userdata" and type(v) ~= "thread" then
        keys[#keys + 1] = tostring(k)
      end
    end
    table.sort(keys)
    out[#out + 1] = "{"
    for i, k in ipairs(keys) do
      if i > 1 then out[#out + 1] = "," end
      out[#out + 1] = encode_string(k)
      out[#out + 1] = ":"
      local v = t[k]
      if v == nil then v = t[tonumber(k)] end
      encode_value(v, stack, out)
    end
    out[#out + 1] = "}"
  end
  stack[t] = nil
end

encode_value = function(v, stack, out)
  local tv = type(v)
  if v == nil then out[#out + 1] = "null"
  elseif tv == "boolean" then out[#out + 1] = v and "true" or "false"
  elseif tv == "number" then out[#out + 1] = encode_number(v)
  elseif tv == "string" then out[#out + 1] = encode_string(v)
  elseif tv == "table" then encode_table(v, stack, out)
  else out[#out + 1] = "null" end
end

function json.encode(v)
  local out = {}
  encode_value(v, {}, out)
  return table.concat(out)
end

-- Decoder ------------------------------------------------------------------

local function decode_error(s, i, msg)
  error(string.format("json: %s at position %d", msg, i), 0)
end

local function skip_ws(s, i)
  return s:find("[^ \t\r\n]", i) or #s + 1
end

local decode_value

local function codepoint_to_utf8(n)
  if n < 0x80 then return string.char(n) end
  if n < 0x800 then
    return string.char(0xC0 + math.floor(n / 0x40), 0x80 + n % 0x40)
  end
  if n < 0x10000 then
    return string.char(0xE0 + math.floor(n / 0x1000),
      0x80 + math.floor(n / 0x40) % 0x40, 0x80 + n % 0x40)
  end
  return string.char(0xF0 + math.floor(n / 0x40000),
    0x80 + math.floor(n / 0x1000) % 0x40,
    0x80 + math.floor(n / 0x40) % 0x40, 0x80 + n % 0x40)
end

local function decode_string(s, i)
  local out = {}
  local j = i + 1
  while true do
    local c = s:sub(j, j)
    if c == "" then decode_error(s, i, "unterminated string") end
    if c == '"' then return table.concat(out), j + 1 end
    if c == "\\" then
      local e = s:sub(j + 1, j + 1)
      local simple = ({ b = "\b", f = "\f", n = "\n", r = "\r", t = "\t",
        ['"'] = '"', ["\\"] = "\\", ["/"] = "/" })[e]
      if simple then
        out[#out + 1] = simple
        j = j + 2
      elseif e == "u" then
        local hex = s:sub(j + 2, j + 5)
        local n = tonumber(hex, 16)
        if not n then decode_error(s, j, "bad \\u escape") end
        j = j + 6
        if n >= 0xD800 and n <= 0xDBFF and s:sub(j, j + 1) == "\\u" then
          local lo = tonumber(s:sub(j + 2, j + 5), 16)
          if lo and lo >= 0xDC00 and lo <= 0xDFFF then
            n = 0x10000 + (n - 0xD800) * 0x400 + (lo - 0xDC00)
            j = j + 6
          end
        end
        out[#out + 1] = codepoint_to_utf8(n)
      else
        decode_error(s, j, "bad escape")
      end
    else
      local k = s:find('["\\]', j) or #s + 1
      out[#out + 1] = s:sub(j, k - 1)
      j = k
    end
  end
end

decode_value = function(s, i)
  i = skip_ws(s, i)
  local c = s:sub(i, i)
  if c == "{" then
    local obj = {}
    i = skip_ws(s, i + 1)
    if s:sub(i, i) == "}" then return obj, i + 1 end
    while true do
      i = skip_ws(s, i)
      if s:sub(i, i) ~= '"' then decode_error(s, i, "expected key") end
      local k
      k, i = decode_string(s, i)
      i = skip_ws(s, i)
      if s:sub(i, i) ~= ":" then decode_error(s, i, "expected ':'") end
      obj[k], i = decode_value(s, i + 1)
      i = skip_ws(s, i)
      local d = s:sub(i, i)
      if d == "}" then return obj, i + 1 end
      if d ~= "," then decode_error(s, i, "expected ',' or '}'") end
      i = i + 1
    end
  elseif c == "[" then
    local arr = json.array()
    i = skip_ws(s, i + 1)
    if s:sub(i, i) == "]" then return arr, i + 1 end
    while true do
      arr[#arr + 1], i = decode_value(s, i)
      i = skip_ws(s, i)
      local d = s:sub(i, i)
      if d == "]" then return arr, i + 1 end
      if d ~= "," then decode_error(s, i, "expected ',' or ']'") end
      i = i + 1
    end
  elseif c == '"' then
    return decode_string(s, i)
  elseif s:sub(i, i + 3) == "true" then return true, i + 4
  elseif s:sub(i, i + 4) == "false" then return false, i + 5
  elseif s:sub(i, i + 3) == "null" then return nil, i + 4
  else
    local num = s:match("^-?%d+%.?%d*[eE]?[-+]?%d*", i)
    if not num or num == "" then decode_error(s, i, "unexpected character") end
    return tonumber(num), i + #num
  end
end

function json.decode(s)
  local v, i = decode_value(s, 1)
  i = skip_ws(s, i)
  if i <= #s then decode_error(s, i, "trailing data") end
  return v
end

return json
