-- A tiny stand-in for Balatro + LÖVE, just enough of the globals, card areas
-- and G.FUNCS for the real OMGI mod to run against under plain LuaJIT.
-- Game rules are simplified; "animations" are blocking events that take a
-- few frames, so the link's busy/stable handling is exercised too.
--
-- usage: luajit fake_balatro.lua <src dir> <port> [seconds]

local SRC, PORT, SECONDS = arg[1], tonumber(arg[2]), tonumber(arg[3] or "60")
local packed = io.open(SRC .. "/omgi/init.lua") ~= nil
if packed then
  -- SRC is an unpacked, patched game.love: same search path LÖVE uses.
  package.path = SRC .. "/?.lua;" .. SRC .. "/?/init.lua;" .. package.path
else
  package.path = table.concat({
    SRC .. "/omgi/runtimes/love/?.lua",
    SRC .. "/omgi/runtimes/love/?/init.lua",
    SRC .. "/omgi/adapters/balatro/lua/?.lua",
    package.path,
  }, ";")
  package.preload["omgi.config"] = function() return { port = PORT } end
end

local socket = require("socket")

-- Engine bits -------------------------------------------------------------

love = { timer = { getTime = socket.gettime } }

local Event = {}
local function add_event(frames, fn, blocking)
  table.insert(G.E_MANAGER.queues.base, { frames = frames, func = fn, blocking = blocking })
end

local Card = {}
Card.__index = Card
local next_id = 0
local function card(t)
  next_id = next_id + 1
  t.sort_id = next_id
  t.facing = t.facing or "front"
  t.ability = t.ability or { set = "Default" }
  t.config = t.config or { center = { key = "c_base" } }
  return setmetatable(t, Card)
end
function Card:get_chip_bonus() return self.base and self.base.nominal or 0 end
function Card:can_use_consumeable() return self.ability.consumeable ~= nil end
function Card:can_sell_card() return true end
function Card:generate_UIBox_ability_table()
  if self.ability.set == "Joker" then
    return { main = { { { n = 1, config = { text = "+" .. self.ability.mult } }, { n = 1, config = { text = " Mult" } } } } }
  end
  return { main = {} }
end

local Area = {}
Area.__index = Area
local function area(limit, hl)
  return setmetatable({ cards = {}, highlighted = {}, config = { card_limit = limit, highlighted_limit = hl } }, Area)
end
function Area:unhighlight_all()
  for _, c in ipairs(self.cards) do c.highlighted = false end
  self.highlighted = {}
end
function Area:add_to_highlighted(c)
  c.highlighted = true
  table.insert(self.highlighted, c)
end
function Area:remove(c)
  for i, x in ipairs(self.cards) do if x == c then table.remove(self.cards, i) break end end
  c.area = nil
end
function Area:emplace(c)
  table.insert(self.cards, c)
  c.area = self
end
function Area:set_ranks() end
function Area:align_cards() end

local function ui(id_map)
  return { get_UIE_by_ID = function(_, id) return id_map[id] end }
end

-- Game ----------------------------------------------------------------------

G = {
  VERSION = "fake-1.0",
  STATES = { SELECTING_HAND = 1, HAND_PLAYED = 2, DRAW_TO_HAND = 3, GAME_OVER = 4, SHOP = 5,
    PLAY_TAROT = 6, BLIND_SELECT = 7, ROUND_EVAL = 8, TAROT_PACK = 9, PLANET_PACK = 10,
    MENU = 11, TUTORIAL = 12, SPLASH = 13, SANDBOX = 14, SPECTRAL_PACK = 15, DEMO_CTA = 16,
    STANDARD_PACK = 17, BUFFOON_PACK = 18, NEW_ROUND = 19 },
  E_MANAGER = { queues = { base = {} } },
  CONTROLLER = { locked = false },
  FUNCS = {},
  P_BLINDS = {
    bl_small = { key = "bl_small", name = "Small Blind", mult = 1, dollars = 3 },
    bl_big = { key = "bl_big", name = "Big Blind", mult = 1.5, dollars = 4 },
    bl_hook = { key = "bl_hook", name = "The Hook", mult = 2, dollars = 5 },
  },
  P_TAGS = { tag_economy = { key = "tag_economy", name = "Economy Tag" } },
  P_CENTERS = {
    b_red = { key = "b_red", set = "Back", name = "Red Deck", unlocked = true },
    b_blue = { key = "b_blue", set = "Back", name = "Blue Deck", unlocked = false },
  },
  P_CENTER_POOLS = { Back = {} },
  localization = { descriptions = {
    Blind = { bl_hook = { name = "The Hook", text = { "Discards {C:red}2{} random", "cards per hand played" } },
      bl_small = { name = "Small Blind", text = {} }, bl_big = { name = "Big Blind", text = {} } },
    Tag = { tag_economy = { name = "Economy Tag", text = { "Doubles your money", "{C:inactive}(Max of {C:money}$40{})" } } },
    Joker = { j_joker = { name = "Joker", text = { "{C:mult}+#1#{} Mult" } } },
  } },
  SETTINGS = { profile = 1 },
}
G.P_CENTER_POOLS.Back = { G.P_CENTERS.b_red, G.P_CENTERS.b_blue }
G.STATE = G.STATES.MENU

function get_blind_amount(ante) return 300 * ante end
function get_deck_win_stake() return 1 end

local RANKS = { { "2", 2 }, { "3", 3 }, { "4", 4 }, { "5", 5 }, { "6", 6 }, { "7", 7 }, { "8", 8 },
  { "9", 9 }, { "10", 10 }, { "Jack", 10 }, { "Queen", 10 }, { "King", 10 }, { "Ace", 11 } }
local SUITS = { "Spades", "Hearts", "Clubs", "Diamonds" }

local function joker(mult, cost)
  return card({ ability = { set = "Joker", name = "Joker", mult = mult }, config = { center = { key = "j_joker", name = "Joker" } },
    cost = cost, sell_cost = math.floor(cost / 2) })
end

local function draw_to_hand()
  while #G.hand.cards < G.hand.config.card_limit and #G.deck.cards > 0 do
    local c = table.remove(G.deck.cards, 1)
    G.hand:emplace(c)
  end
end

local function to_blind_select()
  G.STATE = G.STATES.BLIND_SELECT
  G.blind_select = {}
  local function opt(kind)
    return ui({ select_blind_button = { config = { ref_table = G.P_BLINDS[G.GAME.round_resets.blind_choices[kind]] } } })
  end
  G.blind_select_opts = { small = opt("Small"), big = opt("Big"), boss = opt("Boss") }
end

function G.FUNCS.start_run(_, args)
  add_event(3, function()
    G.GAME = {
      dollars = 4, bankrupt_at = 0, round = 0, stake = args.stake, chips = 0,
      selected_back = { name = "Red Deck" },
      round_resets = { ante = 1, blind_choices = { Small = "bl_small", Big = "bl_big", Boss = "bl_hook" },
        blind_states = { Small = "Select", Big = "Upcoming", Boss = "Upcoming" },
        blind_tags = { Small = "tag_economy", Big = "tag_economy" } },
      blind_on_deck = "Small",
      current_round = { hands_left = 4, discards_left = 3, reroll_cost = 5, free_rerolls = 0 },
      starting_params = { ante_scaling = 1 },
      hands = {
        ["High Card"] = { level = 1, chips = 5, mult = 1, played = 0, visible = true, order = 12 },
        ["Pair"] = { level = 2, chips = 25, mult = 3, played = 0, visible = true, order = 11 },
      },
      used_vouchers = {}, tags = {}, blind = { name = "" },
    }
    G.hand, G.deck, G.jokers, G.consumeables = area(8, 5), area(52), area(5), area(2)
    G.playing_cards = {}
    for _, s in ipairs(SUITS) do
      for _, r in ipairs(RANKS) do
        local c = card({ base = { value = r[1], suit = s, nominal = r[2] } })
        G.deck:emplace(c)
        table.insert(G.playing_cards, c)
      end
    end
    G.consumeables:emplace(card({ ability = { set = "Planet", consumeable = {} }, config = { center = { key = "c_mercury", name = "Mercury" } }, sell_cost = 1 }))
    G.consumeables.cards[1].area = G.consumeables
    to_blind_select()
  end)
end

function G.FUNCS.select_blind(e)
  local proto = e.config.ref_table
  add_event(4, function()
    G.blind_select, G.blind_select_opts = nil, nil
    G.GAME.round_resets.blind_states[G.GAME.blind_on_deck] = "Current"
    G.GAME.blind = { name = proto.name, chips = get_blind_amount(G.GAME.round_resets.ante) * proto.mult,
      boss = proto.key == "bl_hook", config = { blind = proto } }
    G.GAME.chips = 0
    G.GAME.round = G.GAME.round + 1
    G.STATE = G.STATES.SELECTING_HAND
    draw_to_hand()
  end)
end

function G.FUNCS.skip_blind(e)
  assert(e.UIBox, "skip_blind needs the blind's UIBox")
  add_event(2, function()
    G.GAME.round_resets.blind_states[G.GAME.blind_on_deck] = "Skipped"
    G.GAME.blind_on_deck = G.GAME.blind_on_deck == "Small" and "Big" or "Boss"
    table.insert(G.GAME.tags, { key = "tag_economy", name = "Economy Tag" })
  end)
end

function G.FUNCS.get_poker_hand_info(cards)
  local seen = {}
  for _, c in ipairs(cards) do
    if seen[c.base.value] then return "Pair", nil, {}, { seen[c.base.value], c } end
    seen[c.base.value] = c
  end
  return "High Card", nil, {}, { cards[1] }
end

function G.FUNCS.play_cards_from_highlighted()
  G.STATE = G.STATES.HAND_PLAYED
  local played = G.hand.highlighted
  add_event(5, function()
    local score = 0
    for _, c in ipairs(played) do score = score + c.base.nominal end
    G.GAME.chips = G.GAME.chips + score * 10
    G.GAME.current_round.hands_left = G.GAME.current_round.hands_left - 1
    for _, c in ipairs(played) do G.hand:remove(c) end
    G.hand.highlighted = {}
    if G.GAME.chips >= G.GAME.blind.chips then
      G.STATE = G.STATES.ROUND_EVAL
      G.round_eval = ui({ cash_out_button = { config = {} } })
      for _, c in ipairs(G.hand.cards) do G.deck:emplace(c) end
      G.hand.cards = {}
    elseif G.GAME.current_round.hands_left <= 0 then
      G.STATE = G.STATES.GAME_OVER
    else
      draw_to_hand()
      G.STATE = G.STATES.SELECTING_HAND
    end
  end)
end

function G.FUNCS.discard_cards_from_highlighted()
  local cards = G.hand.highlighted
  add_event(3, function()
    for _, c in ipairs(cards) do G.hand:remove(c) end
    G.hand.highlighted = {}
    G.GAME.current_round.discards_left = G.GAME.current_round.discards_left - 1
    draw_to_hand()
  end)
end

function G.FUNCS.sort_hand_suit() table.sort(G.hand.cards, function(a, b) return a.base.suit < b.base.suit end) end
function G.FUNCS.sort_hand_value() table.sort(G.hand.cards, function(a, b) return a.base.nominal > b.base.nominal end) end

function G.FUNCS.cash_out(e)
  assert(e and e.config, "cash_out needs a button")
  add_event(3, function()
    G.round_eval = nil
    G.GAME.dollars = G.GAME.dollars + 5
    G.STATE = G.STATES.SHOP
    G.shop = {}
    G.shop_jokers, G.shop_vouchers, G.shop_booster = area(2), area(1), area(2)
    G.shop_jokers:emplace(joker(4, 5))
    G.shop_jokers:emplace(joker(8, 50))
  end)
end

function G.FUNCS.check_for_buy_space() return #G.jokers.cards < G.jokers.config.card_limit end

function G.FUNCS.buy_from_shop(e)
  local c = e.config.ref_table
  add_event(2, function()
    G.GAME.dollars = G.GAME.dollars - c.cost
    c.area:remove(c)
    G.jokers:emplace(c)
  end)
end

function G.FUNCS.sell_card(e)
  local c = e.config.ref_table
  add_event(2, function()
    G.GAME.dollars = G.GAME.dollars + (c.sell_cost or 0)
    c.area:remove(c)
  end)
end

function G.FUNCS.use_card(e)
  local c = e.config.ref_table
  add_event(2, function()
    if c.ability.set == "Planet" then
      G.GAME.hands["High Card"].level = G.GAME.hands["High Card"].level + 1
    end
    c.area:remove(c)
  end)
end

function G.FUNCS.reroll_shop()
  G.GAME.dollars = G.GAME.dollars - G.GAME.current_round.reroll_cost
end

function G.FUNCS.toggle_shop()
  add_event(3, function()
    G.shop, G.shop_jokers, G.shop_vouchers, G.shop_booster = nil, nil, nil, nil
    G.GAME.blind_on_deck = G.GAME.blind_on_deck == "Small" and "Big" or "Boss"
    G.GAME.round_resets.blind_states[G.GAME.blind_on_deck] = "Select"
    to_blind_select()
  end)
end

function G.FUNCS.exit_overlay_menu() G.OVERLAY_MENU = nil end

-- Main loop ---------------------------------------------------------------

function love.update(dt)
  local q = G.E_MANAGER.queues.base
  local ev = q[1]
  if ev then
    ev.frames = ev.frames - 1
    if ev.frames <= 0 then
      table.remove(q, 1)
      ev.func()
    end
  end
end

-- Hooks the mod in exactly as the patched main.lua does.
local ok, err = pcall(function() require("omgi").start("balatro") end)
if not ok then print("[omgi] failed to start: " .. tostring(err)) os.exit(1) end
if not require("omgi").server then os.exit(1) end

print("READY")
io.stdout:flush()
local deadline = socket.gettime() + SECONDS
while socket.gettime() < deadline do
  love.update(1 / 60)
  socket.sleep(1 / 120)
end
