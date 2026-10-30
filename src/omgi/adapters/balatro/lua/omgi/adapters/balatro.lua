-- OMGI adapter for Balatro (LÖVE).
--
-- Reads the run straight out of the game's globals (G, G.GAME, card areas)
-- and performs actions by calling the same G.FUNCS handlers the on-screen
-- buttons call, so the game applies its own rules, animations and saves.
--
-- Fair play: only what the player could see on screen is reported. Face-down
-- cards stay hidden and the draw order of the deck is never exposed (the
-- remaining deck's contents are, since the game shows them in deck view).
--
-- Card positions in params are 1-based, left to right, as on screen.

local json = require("omgi.json")
local A = {}

-- Helpers -------------------------------------------------------------------

local SUIT_SYMBOL = { Hearts = "♥", Diamonds = "♦", Clubs = "♣", Spades = "♠" }
local RANK_SHORT = { Jack = "J", Queen = "Q", King = "K", Ace = "A" }

local PACK_PHASES = {
  tarot_pack = true, planet_pack = true, spectral_pack = true,
  standard_pack = true, buffoon_pack = true,
}

local IDLE_GRACE_SECONDS = 8

local function now()
  local love = rawget(_G, "love")
  if love and love.timer and love.timer.getTime then return love.timer.getTime() end
  return os.clock()
end

local function fail(code, message)
  return nil, { code = code, message = message }
end

local function loc_entry(set, key)
  local loc = G.localization and G.localization.descriptions
  return loc and loc[set] and loc[set][key]
end

local function strip_markup(s)
  return (s:gsub("{.-}", ""):gsub("%s+", " "):gsub("^ ", ""):gsub(" $", ""))
end

local function loc_template(set, key)
  local d = loc_entry(set, key)
  if not d or type(d.text) ~= "table" then return nil end
  local parts = {}
  for _, line in ipairs(d.text) do
    if type(line) == "string" then parts[#parts + 1] = line end
  end
  return strip_markup(table.concat(parts, " "))
end

local function center_name(center, set)
  if not center then return nil end
  local d = loc_entry(set, center.key)
  return (d and d.name) or center.name or center.key
end

-- Pull the rendered text out of a card's tooltip UI definition. That's the
-- game's own description with the numbers filled in ("+4 Mult", "X1.5 Mult").
local function collect_text(node, out, depth)
  if type(node) ~= "table" or depth > 12 then return end
  local cfg = node.config
  if type(cfg) == "table" then
    if type(cfg.text) == "string" then out[#out + 1] = cfg.text end
    local obj = cfg.object
    if type(obj) == "table" and type(obj.config) == "table" then
      local s = obj.config.string
      if type(s) == "table" then s = s[1] end
      if type(s) == "table" then s = s.string end
      if type(s) == "string" then out[#out + 1] = s end
    end
  end
  if type(node.nodes) == "table" then
    for _, n in ipairs(node.nodes) do collect_text(n, out, depth + 1) end
  elseif not node.n and not cfg then
    for _, n in ipairs(node) do collect_text(n, out, depth + 1) end
  end
end

local function card_description(card)
  local ok, ui = pcall(function() return card:generate_UIBox_ability_table() end)
  if ok and type(ui) == "table" and type(ui.main) == "table" then
    local lines = {}
    for _, line in ipairs(ui.main) do
      local out = {}
      collect_text(line, out, 0)
      local text = table.concat(out)
      if text:find("%S") then lines[#lines + 1] = text end
    end
    if #lines > 0 then return strip_markup(table.concat(lines, " ")) end
  end
  local center = card.config and card.config.center
  if center then return loc_template(card.ability and card.ability.set or "", center.key) end
  return nil
end

local function edition_of(card)
  local e = card.edition
  if type(e) ~= "table" then return nil end
  if e.type then return e.type end
  for _, k in ipairs({ "negative", "polychrome", "holo", "foil" }) do
    if e[k] then return k end
  end
  return nil
end

local function stickers_of(card)
  local ab = card.ability or {}
  local s = json.array()
  if ab.eternal then s[#s + 1] = "eternal" end
  if ab.perishable then
    s[#s + 1] = "perishable" .. (ab.perish_tally and (" (" .. ab.perish_tally .. " rounds left)") or "")
  end
  if ab.rental then s[#s + 1] = "rental" end
  return s
end

local function face_down(card)
  return card.facing == "back"
end

local function playing_card(card, i)
  if face_down(card) then
    return { index = i, face_down = true, selected = card.highlighted and true or false }
  end
  local base = card.base or {}
  local center = card.config and card.config.center
  local c = {
    index = i,
    id = card.sort_id,
    rank = base.value,
    suit = base.suit,
    label = (RANK_SHORT[base.value] or tostring(base.value)) .. (SUIT_SYMBOL[base.suit] or tostring(base.suit)),
    selected = card.highlighted and true or false,
    debuffed = card.debuff and true or false,
    edition = edition_of(card),
    seal = card.seal,
  }
  if center and center.key ~= "c_base" then
    c.enhancement = center_name(center, "Enhanced")
  end
  local ok, chips = pcall(function() return card:get_chip_bonus() end)
  if ok and type(chips) == "number" then c.chips = chips end
  if c.enhancement or c.edition or c.seal then c.description = card_description(card) end
  return c
end

local function shop_cost(card)
  return card.cost or 0
end

local function can_afford(cost)
  local g = G.GAME
  return cost <= 0 or (g.dollars - (g.bankrupt_at or 0)) >= cost
end

-- Jokers, consumables, shop items, vouchers, packs.
local function item_card(card, i, priced)
  local ab = card.ability or {}
  local center = card.config and card.config.center
  if ab.set == "Default" or ab.set == "Enhanced" then
    local c = playing_card(card, i)
    c.kind = "Playing card"
    if priced then c.cost = shop_cost(card); c.affordable = can_afford(c.cost) end
    return c
  end
  local c = {
    index = i,
    id = card.sort_id,
    key = center and center.key,
    name = center_name(center, ab.set) or ab.name,
    kind = ab.set,
    edition = edition_of(card),
    stickers = stickers_of(card),
    debuffed = card.debuff and true or false,
    description = card_description(card),
  }
  if card.sell_cost then c.sell_value = card.sell_cost end
  if priced then
    c.cost = shop_cost(card)
    c.affordable = can_afford(c.cost)
  end
  return c
end

local function area_cards(area, fn, priced)
  local out = json.array()
  if area and area.cards then
    for i, card in ipairs(area.cards) do out[#out + 1] = fn(card, i, priced) end
  end
  return out
end

local function phase_name()
  if not G or not G.STATE or not G.STATES then return "loading" end
  for name, v in pairs(G.STATES) do
    if v == G.STATE then return name:lower() end
  end
  return "unknown"
end

local function blocking_events()
  local q = G.E_MANAGER and G.E_MANAGER.queues and G.E_MANAGER.queues.base
  if type(q) ~= "table" then return 0 end
  local n = 0
  for _, ev in ipairs(q) do
    if type(ev) == "table" and ev.blocking ~= false and not ev.complete then n = n + 1 end
  end
  return n
end

local function ui_ready(phase)
  if phase == "selecting_hand" then
    return G.hand and G.hand.cards and #G.hand.cards > 0
  elseif phase == "blind_select" then
    return G.blind_select ~= nil and G.blind_select_opts ~= nil
  elseif phase == "shop" then
    return G.shop ~= nil and G.shop_jokers ~= nil
  elseif phase == "round_eval" then
    return G.round_eval ~= nil and G.round_eval:get_UIE_by_ID("cash_out_button") ~= nil
  elseif PACK_PHASES[phase] then
    return G.pack_cards ~= nil and G.pack_cards.cards ~= nil and #G.pack_cards.cards > 0
  elseif phase == "menu" or phase == "game_over" then
    return true
  end
  return false
end

-- Stability: the game is waiting for input (no animation or event chain
-- running). If only queued events hold it back but nothing has changed for a
-- few seconds, treat it as idle so a lingering event can't lock the link.
local idle = { sig = nil, since = 0 }

local function stability()
  local phase = phase_name()
  if phase == "loading" or phase == "unknown" then return phase, false, phase end
  if G.CONTROLLER and G.CONTROLLER.locked then return phase, false, "controls locked" end
  if not G.OVERLAY_MENU and not ui_ready(phase) then return phase, false, "screen setting up" end
  local events = blocking_events()
  if events > 0 then
    local sig = phase .. ":" .. events
    if idle.sig ~= sig then idle.sig, idle.since = sig, now() end
    if now() - idle.since < IDLE_GRACE_SECONDS then
      return phase, false, events .. " event(s) running"
    end
  else
    idle.sig = nil
  end
  return phase, true, nil
end

-- Snapshot ------------------------------------------------------------------

local function run_summary()
  local g = G.GAME
  local cr = g.current_round or {}
  local rr = g.round_resets or {}
  return {
    ante = rr.ante,
    round = g.round,
    dollars = g.dollars,
    hands_left = cr.hands_left,
    discards_left = cr.discards_left,
    hand_size = G.hand and G.hand.config and G.hand.config.card_limit,
    max_selected = G.hand and G.hand.config and G.hand.config.highlighted_limit,
    joker_slots = G.jokers and G.jokers.config and G.jokers.config.card_limit,
    consumable_slots = G.consumeables and G.consumeables.config and G.consumeables.config.card_limit,
    deck_remaining = G.deck and G.deck.cards and #G.deck.cards,
    deck_total = G.playing_cards and #G.playing_cards,
    stake = g.stake,
    deck = g.selected_back and g.selected_back.name,
    won = g.won and true or false,
  }
end

local function blind_info(kind)
  local rr = G.GAME.round_resets
  local key = rr.blind_choices and rr.blind_choices[kind]
  local proto = key and G.P_BLINDS and G.P_BLINDS[key]
  local b = { kind = kind, key = key, status = rr.blind_states and rr.blind_states[kind] }
  if proto then
    b.name = center_name(proto, "Blind")
    b.description = loc_template("Blind", key)
    b.reward = proto.dollars
    local ok, amount = pcall(function()
      return get_blind_amount(rr.ante) * proto.mult * G.GAME.starting_params.ante_scaling
    end)
    if ok then b.chips_needed = amount end
  end
  local tag = rr.blind_tags and rr.blind_tags[kind]
  if tag and kind ~= "Boss" then
    local tproto = G.P_TAGS and G.P_TAGS[tag]
    b.skip_tag = { key = tag, name = center_name(tproto or { key = tag }, "Tag"), description = loc_template("Tag", tag) }
  end
  return b
end

local function current_blind()
  local b = G.GAME.blind
  if not b or not b.name or b.name == "" then return nil end
  local key = b.config and b.config.blind and b.config.blind.key
  return {
    name = (key and center_name(b.config.blind, "Blind")) or b.name,
    key = key,
    boss = b.boss and true or false,
    chips_needed = b.chips,
    description = key and loc_template("Blind", key) or nil,
    disabled = b.disabled and true or false,
  }
end

local function hand_levels()
  local out = json.array()
  for name, h in pairs(G.GAME.hands or {}) do
    if h.visible then
      out[#out + 1] = { name = name, level = h.level, chips = h.chips, mult = h.mult, played = h.played, order = h.order }
    end
  end
  table.sort(out, function(a, b) return (a.order or 0) < (b.order or 0) end)
  for _, h in ipairs(out) do h.order = nil end
  return out
end

local function deck_counts()
  local suits, ranks = {}, {}
  for _, card in ipairs((G.deck and G.deck.cards) or {}) do
    local base = card.base or {}
    if base.suit then suits[base.suit] = (suits[base.suit] or 0) + 1 end
    if base.value then ranks[base.value] = (ranks[base.value] or 0) + 1 end
  end
  return { suits = suits, ranks = ranks }
end

local function tags_held()
  local out = json.array()
  for _, t in ipairs(G.GAME.tags or {}) do
    out[#out + 1] = { key = t.key, name = t.name or (t.key and center_name({ key = t.key }, "Tag")) }
  end
  return out
end

local function vouchers_owned()
  local out = json.array()
  for key, v in pairs(G.GAME.used_vouchers or {}) do
    if v then out[#out + 1] = center_name(G.P_CENTERS and G.P_CENTERS[key] or { key = key }, "Voucher") end
  end
  table.sort(out)
  return out
end

function A.info()
  return { id = "balatro", name = "Balatro", version = G and G.VERSION or nil }
end

function A.phase()
  local phase, stable = stability()
  return phase, stable
end

function A.snapshot()
  local phase, stable, reason = stability()
  local s = { phase = phase, stable = stable, waiting_on = reason, overlay = G and G.OVERLAY_MENU ~= nil or false }
  if not G or not G.GAME or phase == "menu" or phase == "loading" or phase == "splash" then
    return s
  end

  s.run = run_summary()
  s.jokers = area_cards(G.jokers, item_card)
  s.consumables = area_cards(G.consumeables, item_card)
  s.hand = area_cards(G.hand, playing_card)
  s.hand_levels = hand_levels()
  s.deck_remaining = deck_counts()
  s.tags = tags_held()
  s.vouchers = vouchers_owned()

  if phase == "blind_select" then
    s.on_deck = G.GAME.blind_on_deck
    s.blinds = json.array({ blind_info("Small"), blind_info("Big"), blind_info("Boss") })
  else
    s.blind = current_blind()
    s.score = G.GAME.chips
  end

  if phase == "shop" then
    local cr = G.GAME.current_round or {}
    s.shop = {
      reroll_cost = cr.reroll_cost,
      free_rerolls = cr.free_rerolls,
      cards = area_cards(G.shop_jokers, item_card, true),
      vouchers = area_cards(G.shop_vouchers, item_card, true),
      packs = area_cards(G.shop_booster, item_card, true),
    }
  end

  if PACK_PHASES[phase] then
    s.pack = {
      kind = phase,
      picks_left = G.GAME.pack_choices,
      cards = area_cards(G.pack_cards, item_card),
    }
  end

  if phase == "selecting_hand" and G.hand and G.hand.highlighted and #G.hand.highlighted > 0 then
    local ok, info = pcall(A.hand_info_for, G.hand.highlighted)
    if ok then s.selected_hand = info end
  end
  return s
end

-- Actions -------------------------------------------------------------------

local CARDS = {
  type = "array", items = { type = "integer" },
  description = "Hand card positions, 1-based, left to right",
}

local function action(name, description, properties, required)
  return {
    name = name,
    description = description,
    params = { type = "object", properties = properties or {}, required = json.array(required or {}) },
  }
end

local function unlocked_decks()
  local out = json.array()
  for _, back in ipairs((G.P_CENTER_POOLS and G.P_CENTER_POOLS.Back) or {}) do
    if back.unlocked ~= false then out[#out + 1] = back.key end
  end
  return out
end

local save_checked_at = -math.huge

local function saved_run()
  if G.SAVED_GAME then return G.SAVED_GAME end
  if now() - save_checked_at < 5 then return nil end  -- don't hit the disk every request
  save_checked_at = now()
  local ok, saved = pcall(function()
    local raw = get_compressed(G.SETTINGS.profile .. "/save.jkr")
    return raw and STR_UNPACK(raw)
  end)
  if ok and saved then G.SAVED_GAME = saved end
  return G.SAVED_GAME
end

function A.actions()
  local phase = phase_name()
  local list = json.array()
  local function add(a) list[#list + 1] = a end

  if G.OVERLAY_MENU then
    add(action("close_menu", "Close the menu or popup covering the game"))
    if phase ~= "game_over" then return list end
  end

  if phase == "menu" or phase == "game_over" then
    add(action("start_run", "Start a new run", {
      deck = { type = "string", enum = unlocked_decks(), description = "Deck key (default: last used)" },
      stake = { type = "integer", minimum = 1, maximum = 8, description = "Stake, 1 = White (default)" },
      seed = { type = "string", description = "Optional seed" },
    }))
    if phase == "menu" and saved_run() then
      add(action("continue_run", "Continue the saved run"))
    end
    return list
  end

  local function shared_round_actions()
    if G.consumeables and #G.consumeables.cards > 0 then
      add(action("use_consumable", "Use a Tarot/Planet/Spectral card you hold; some need hand cards as targets", {
        index = { type = "integer", description = "Consumable position, 1-based" },
        cards = CARDS,
      }, { "index" }))
      add(action("sell_consumable", "Sell a consumable for money", {
        index = { type = "integer", description = "Consumable position, 1-based" },
      }, { "index" }))
    end
    if G.jokers and #G.jokers.cards > 0 then
      add(action("sell_joker", "Sell a joker for money", {
        index = { type = "integer", description = "Joker position, 1-based" },
      }, { "index" }))
      if #G.jokers.cards > 1 then
        add(action("move_joker", "Reorder jokers (they trigger left to right)", {
          from = { type = "integer" }, to = { type = "integer" },
        }, { "from", "to" }))
      end
    end
  end

  if phase == "blind_select" then
    add(action("select_blind", "Play the blind that's up next (" .. tostring(G.GAME.blind_on_deck) .. ")"))
    if G.GAME.blind_on_deck ~= "Boss" then
      add(action("skip_blind", "Skip this blind and take its tag"))
    end
    shared_round_actions()
  elseif phase == "selecting_hand" then
    add(action("play_hand", "Play 1-" .. tostring(G.hand.config.highlighted_limit) .. " cards from your hand", { cards = CARDS }, { "cards" }))
    if (G.GAME.current_round.discards_left or 0) > 0 then
      add(action("discard", "Discard cards and draw replacements", { cards = CARDS }, { "cards" }))
    end
    add(action("sort_hand", "Sort the hand", { by = { type = "string", enum = json.array({ "rank", "suit" }) } }, { "by" }))
    shared_round_actions()
  elseif phase == "round_eval" then
    add(action("cash_out", "Collect the round's money and go to the shop"))
    shared_round_actions()
  elseif phase == "shop" then
    add(action("buy", "Buy a card from the shop's card row", { index = { type = "integer" } }, { "index" }))
    if G.shop_vouchers and #G.shop_vouchers.cards > 0 then
      add(action("redeem_voucher", "Buy and redeem a voucher", { index = { type = "integer" } }, { "index" }))
    end
    if G.shop_booster and #G.shop_booster.cards > 0 then
      add(action("open_pack", "Buy and open a booster pack", { index = { type = "integer" } }, { "index" }))
    end
    add(action("reroll", "Reroll the shop's card row"))
    add(action("leave_shop", "Leave the shop and go to the next blind"))
    shared_round_actions()
  elseif PACK_PHASES[phase] then
    add(action("pick", "Take a card from the pack; Tarot/Spectral cards may need hand cards as targets", {
      index = { type = "integer", description = "Pack card position, 1-based" },
      cards = CARDS,
    }, { "index" }))
    add(action("skip_pack", "Skip the rest of this pack"))
  end
  return list
end

local function int_param(args, key, max, label)
  local v = args[key]
  if type(v) ~= "number" or v % 1 ~= 0 then return nil, (label or key) .. " must be a whole number" end
  if v < 1 or v > max then return nil, string.format("%s %d is out of range (1-%d)", label or key, v, max) end
  return v
end

local function card_list(args, area, min_n, max_n)
  local list = args.cards
  if list == nil then list = {} end
  if type(list) ~= "table" then return nil, "cards must be a list of positions" end
  if #list < min_n or #list > max_n then
    return nil, string.format("pick %d-%d cards (got %d)", min_n, max_n, #list)
  end
  local seen, out = {}, {}
  for _, i in ipairs(list) do
    if type(i) ~= "number" or i % 1 ~= 0 or i < 1 or i > #area.cards then
      return nil, string.format("card %s is out of range (1-%d)", tostring(i), #area.cards)
    end
    if seen[i] then return nil, "card " .. i .. " listed twice" end
    seen[i] = true
    out[#out + 1] = area.cards[i]
  end
  return out
end

local function highlight(area, cards)
  area:unhighlight_all()
  for _, c in ipairs(cards) do area:add_to_highlighted(c, true) end
end

local function labels(cards)
  local out = json.array()
  for _, c in ipairs(cards) do
    out[#out + 1] = face_down(c) and "face-down card" or playing_card(c, 0).label
  end
  return out
end

local function use_card(card, targets)
  if targets and #targets > 0 then highlight(G.hand, targets)
  elseif G.hand and G.hand.highlighted and #G.hand.highlighted > 0 then G.hand:unhighlight_all() end
  if card.can_use_consumeable and card.ability.consumeable and not card:can_use_consumeable() then
    return fail("not_allowed", "that card can't be used right now (check how many hand cards it needs)")
  end
  G.FUNCS.use_card({ config = { ref_table = card } })
  return true
end

local function sell(area, args, what)
  local i, e = int_param(args, "index", #area.cards, what)
  if not i then return fail("bad_params", e) end
  local card = area.cards[i]
  if card.ability and card.ability.eternal then return fail("not_allowed", "that card is Eternal and can't be sold") end
  if card.can_sell_card and not card:can_sell_card() then return fail("not_allowed", "that card can't be sold right now") end
  G.FUNCS.sell_card({ config = { ref_table = card } })
  return { ok = true, message = "Sold " .. tostring(item_card(card, i).name) .. " for $" .. tostring(card.sell_cost) }
end

local handlers = {}

function handlers.close_menu()
  if not G.OVERLAY_MENU then return fail("not_allowed", "no menu is open") end
  G.FUNCS.exit_overlay_menu()
  return { ok = true }
end

function handlers.start_run(args)
  local deck = args.deck
  if deck ~= nil then
    local center = G.P_CENTERS and G.P_CENTERS[deck]
    if not center or center.set ~= "Back" then return fail("bad_params", "unknown deck '" .. tostring(deck) .. "'") end
    if center.unlocked == false then return fail("not_allowed", "that deck isn't unlocked") end
  end
  local stake = args.stake or 1
  if type(stake) ~= "number" or stake % 1 ~= 0 or stake < 1 or stake > 8 then
    return fail("bad_params", "stake must be 1-8")
  end
  if stake > 1 then
    local vb = G.GAME and G.GAME.viewed_back
    local key = deck or (vb and vb.effect and vb.effect.center and vb.effect.center.key)
    local ok, won = pcall(get_deck_win_stake, key)
    if ok and type(won) == "number" and stake > won + 1 then
      return fail("not_allowed", "that stake isn't unlocked for this deck yet (highest: " .. (won + 1) .. ")")
    end
  end
  if G.OVERLAY_MENU then G.FUNCS.exit_overlay_menu() end
  if deck ~= nil and G.GAME and G.GAME.viewed_back then
    G.GAME.viewed_back:change_to(G.P_CENTERS[deck])
  end
  local seed = args.seed
  if seed ~= nil and type(seed) ~= "string" then seed = tostring(seed) end
  G.FUNCS.start_run(nil, { stake = stake, seed = seed })
  return { ok = true, message = "Starting a run" }
end

function handlers.continue_run()
  local saved = saved_run()
  if not saved then return fail("not_allowed", "there's no saved run") end
  G.FUNCS.start_run(nil, { savetext = saved })
  return { ok = true }
end

function handlers.select_blind()
  local kind = G.GAME.blind_on_deck
  local opts = G.blind_select_opts and kind and G.blind_select_opts[kind:lower()]
  local button = opts and opts:get_UIE_by_ID("select_blind_button")
  if not button then
    local key = G.GAME.round_resets.blind_choices[kind]
    button = { config = { ref_table = G.P_BLINDS[key] }, UIBox = opts }
  end
  G.FUNCS.select_blind(button)
  return { ok = true, message = "Selected the " .. tostring(kind) .. " blind" }
end

function handlers.skip_blind()
  local kind = G.GAME.blind_on_deck
  if kind == "Boss" then return fail("not_allowed", "the Boss blind can't be skipped") end
  local opts = G.blind_select_opts and G.blind_select_opts[kind:lower()]
  if not opts then return fail("failed", "blind choice isn't on screen") end
  G.FUNCS.skip_blind({ UIBox = opts, config = {} })
  return { ok = true, message = "Skipped the " .. kind .. " blind" }
end

function handlers.play_hand(args)
  local cards, e = card_list(args, G.hand, 1, G.hand.config.highlighted_limit or 5)
  if not cards then return fail("bad_params", e) end
  if (G.GAME.current_round.hands_left or 0) <= 0 then return fail("not_allowed", "no hands left") end
  highlight(G.hand, cards)
  local info = A.hand_info_for(G.hand.highlighted)
  G.FUNCS.play_cards_from_highlighted(nil)
  return { ok = true, played = labels(cards), hand = info and info.hand }
end

function handlers.discard(args)
  local cards, e = card_list(args, G.hand, 1, G.hand.config.highlighted_limit or 5)
  if not cards then return fail("bad_params", e) end
  if (G.GAME.current_round.discards_left or 0) <= 0 then return fail("not_allowed", "no discards left") end
  highlight(G.hand, cards)
  G.FUNCS.discard_cards_from_highlighted(nil)
  return { ok = true, discarded = labels(cards) }
end

function handlers.sort_hand(args)
  if args.by == "suit" then G.FUNCS.sort_hand_suit()
  elseif args.by == "rank" then G.FUNCS.sort_hand_value()
  else return fail("bad_params", "by must be 'rank' or 'suit'") end
  return { ok = true }
end

function handlers.use_consumable(args)
  local i, e = int_param(args, "index", #G.consumeables.cards, "consumable")
  if not i then return fail("bad_params", e) end
  local targets = {}
  if args.cards and #args.cards > 0 then
    local err
    targets, err = card_list(args, G.hand, 1, #G.hand.cards)
    if not targets then return fail("bad_params", err) end
  end
  local card = G.consumeables.cards[i]
  local name = item_card(card, i).name
  local ok, err = use_card(card, targets)
  if not ok then return nil, err end
  return { ok = true, message = "Used " .. tostring(name) }
end

function handlers.sell_consumable(args) return sell(G.consumeables, args, "consumable") end
function handlers.sell_joker(args) return sell(G.jokers, args, "joker") end

function handlers.move_joker(args)
  local n = #G.jokers.cards
  local from, e1 = int_param(args, "from", n)
  if not from then return fail("bad_params", e1) end
  local to, e2 = int_param(args, "to", n)
  if not to then return fail("bad_params", e2) end
  local card = table.remove(G.jokers.cards, from)
  table.insert(G.jokers.cards, to, card)
  if G.jokers.set_ranks then G.jokers:set_ranks() end
  if G.jokers.align_cards then G.jokers:align_cards() end
  return { ok = true }
end

function handlers.cash_out()
  local button = G.round_eval and G.round_eval:get_UIE_by_ID("cash_out_button")
  G.FUNCS.cash_out(button or { config = {} })
  return { ok = true }
end

local function buy_from(area, args, what, via_use)
  local i, e = int_param(args, "index", #area.cards, what)
  if not i then return fail("bad_params", e) end
  local card = area.cards[i]
  local cost = shop_cost(card)
  if not can_afford(cost) then
    return fail("not_allowed", string.format("can't afford it ($%s, you have $%s)", tostring(cost), tostring(G.GAME.dollars)))
  end
  local name = item_card(card, i).name
  if via_use then
    G.FUNCS.use_card({ config = { ref_table = card } })
  else
    if G.FUNCS.check_for_buy_space and not G.FUNCS.check_for_buy_space(card) then
      return fail("not_allowed", "no room for it; sell something first")
    end
    G.FUNCS.buy_from_shop({ config = { ref_table = card } })
  end
  return { ok = true, message = "Bought " .. tostring(name) .. " for $" .. tostring(cost) }
end

function handlers.buy(args) return buy_from(G.shop_jokers, args, "shop card", false) end
function handlers.redeem_voucher(args) return buy_from(G.shop_vouchers, args, "voucher", true) end
function handlers.open_pack(args) return buy_from(G.shop_booster, args, "pack", true) end

function handlers.reroll()
  local cr = G.GAME.current_round
  if (cr.free_rerolls or 0) <= 0 and not can_afford(cr.reroll_cost or 0) then
    return fail("not_allowed", "can't afford a reroll ($" .. tostring(cr.reroll_cost) .. ")")
  end
  G.FUNCS.reroll_shop(nil)
  return { ok = true }
end

function handlers.leave_shop()
  G.FUNCS.toggle_shop(nil)
  return { ok = true }
end

function handlers.pick(args)
  local i, e = int_param(args, "index", #G.pack_cards.cards, "pack card")
  if not i then return fail("bad_params", e) end
  local card = G.pack_cards.cards[i]
  local set = card.ability and card.ability.set
  if set == "Joker" and G.jokers and #G.jokers.cards >= G.jokers.config.card_limit and edition_of(card) ~= "negative" then
    return fail("not_allowed", "joker slots are full; sell a joker or skip")
  end
  local targets = {}
  if args.cards and #args.cards > 0 then
    local err
    targets, err = card_list(args, G.hand, 1, #G.hand.cards)
    if not targets then return fail("bad_params", err) end
  end
  local name = item_card(card, i).name
  local ok, err = use_card(card, targets)
  if not ok then return nil, err end
  return { ok = true, message = "Took " .. tostring(name) }
end

function handlers.skip_pack()
  G.FUNCS.skip_booster({ config = {} })
  return { ok = true }
end

function A.act(name, args)
  local allowed = false
  for _, a in ipairs(A.actions()) do
    if a.name == name then allowed = true break end
  end
  if not allowed then
    return fail("invalid_action", "'" .. tostring(name) .. "' isn't available in phase " .. phase_name())
  end
  return handlers[name](args)
end

-- Queries (read-only) ------------------------------------------------------

function A.hand_info_for(cards)
  if not cards or #cards == 0 then return nil end
  local text, _, poker_hands, scoring = G.FUNCS.get_poker_hand_info(cards)
  if not text or text == "NULL" then return nil end
  local h = G.GAME.hands[text] or {}
  local info = { hand = text, level = h.level, chips = h.chips, mult = h.mult }
  if h.chips and h.mult then info.base_score = h.chips * h.mult end
  local scoring_idx = json.array()
  for _, sc in ipairs(scoring or {}) do
    for i, c in ipairs(G.hand.cards) do
      if c == sc then scoring_idx[#scoring_idx + 1] = i end
    end
  end
  info.scoring_cards = scoring_idx
  if G.GAME.blind and G.GAME.blind.debuff_hand then
    local ok, blocked = pcall(G.GAME.blind.debuff_hand, G.GAME.blind, cards, poker_hands, text, true)
    if ok then info.blocked_by_boss = blocked and true or false end
  end
  return info
end

local query_handlers = {}

function query_handlers.hand_info(args)
  if phase_name() ~= "selecting_hand" then return fail("not_allowed", "only while choosing a hand") end
  local cards, e = card_list(args, G.hand, 1, G.hand.config.highlighted_limit or 5)
  if not cards then return fail("bad_params", e) end
  for _, c in ipairs(cards) do
    if face_down(c) then return fail("not_allowed", "can't evaluate face-down cards") end
  end
  return A.hand_info_for(cards) or { hand = nil }
end

function query_handlers.deck()
  local out = json.array()
  for _, card in ipairs((G.deck and G.deck.cards) or {}) do out[#out + 1] = playing_card(card, #out + 1) end
  table.sort(out, function(a, b)
    if a.suit ~= b.suit then return tostring(a.suit) < tostring(b.suit) end
    return (tonumber(a.id) or 0) < (tonumber(b.id) or 0)
  end)
  for i, c in ipairs(out) do c.index = nil; c.selected = nil; c.id = nil; out[i] = c end
  return { remaining = out, note = "sorted by suit; draw order is not revealed" }
end

function A.queries()
  return json.array({
    action("hand_info", "What poker hand some hand cards make and its base score, without playing them", { cards = CARDS }, { "cards" }),
    action("deck", "Every card left in your draw pile (sorted, not in draw order)"),
  })
end

function A.query(name, args)
  local fn = query_handlers[name]
  if not fn then return fail("unknown_query", "no query '" .. tostring(name) .. "'") end
  if G == nil or G.GAME == nil then return fail("not_allowed", "no run in progress") end
  return fn(args)
end

return A
