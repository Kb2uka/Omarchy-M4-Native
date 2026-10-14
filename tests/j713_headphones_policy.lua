-- SPDX-License-Identifier: MIT
-- Run the headphone hook together with the protected-speaker hook, as the Air
-- does, on WirePlumber 0.5.18's actual default-node hooks. The harness is the
-- one in j713_audio_default_policy.lua; only the scenarios differ.
-- Only the event/object-manager/JSON boundaries are substituted; no audio opens.
local root = assert(arg[1])
local fixture = root .. "/tests/fixtures/wireplumber-0.5.18/"
local hooks, metadata_values, stored, writes = {}, {}, {}, {}
local function json(value)
  return { parse = function() return value end, to_string = function() return value end }
end
Json = { Array = json, Object = json, Raw = json }
Constraint = function(value) return value end
EventInterest = function(value) return value end
SimpleEventHook = function(value)
  value.register = function(self) hooks[self.name] = self end
  value.remove = function(self) hooks[self.name] = nil end
  return value
end
Log = { open_topic = function() return setmetatable({}, {
  __index = function() return function() end end }) end }
Settings = { subscribe = function() end, get_boolean = function() return true end }
State = function() return {
  load = function() return stored end,
  save_after_timeout = function() error("selection must not write user history") end,
} end
package.preload["common-utils"] = function() return {
  get_object_manager = function() return { lookup = function() return nil end } end,
} end
package.preload["filter-utils"] = function() return {
  is_filter_smart = function() return false end,
} end
package.preload["linking-utils"] = function() return {
  haveAvailableRoutes = function(props) return props.test_routes ~= false end,
} end
package.preload["node-utils"] = function() return dofile(fixture .. "node-utils.lua") end
local metadata = {
  find = function(_, _, key) return metadata_values[key] end,
  set = function(_, _, key, _, value)
    writes[#writes + 1] = key
    metadata_values[key] = value
  end,
}
local source = { call = function(_, method, kind)
  assert(method == "get-object-manager" and kind == "metadata")
  return { lookup = function() return metadata end }
end }
for _, name in ipairs { "rescan", "find-selected-default-node", "state-default-nodes",
                        "find-best-default-node", "apply-default-node" } do
  dofile(fixture .. name .. ".lua")
end
for i = 2, #arg do dofile(arg[i]) end

-- Order select-default-node hooks by their real before/after declarations.
local selected_hooks, order, pending = {}, {}, {}
for name, hook in pairs(hooks) do
  for _, interest in ipairs(hook.interests) do
    for _, constraint in ipairs(interest) do
      if constraint[1] == "event.type" and constraint[3] == "select-default-node" then
        selected_hooks[name], pending[name] = hook, true
      end
    end
  end
end
local function list(value)
  if type(value) == "string" then return {value} end
  return value or {}
end
while next(pending) do
  local progressed = false
  local names = {}
  for name in pairs(pending) do names[#names + 1] = name end
  table.sort(names)
  for _, name in ipairs(names) do
    local blocked = false
    for _, other in ipairs(list(selected_hooks[name].after)) do
      if pending[other] then blocked = true end
    end
    for other in pairs(pending) do
      for _, successor in ipairs(list(selected_hooks[other].before)) do
        if successor == name then blocked = true end
      end
    end
    if not blocked then
      order[#order + 1] = selected_hooks[name]
      pending[name], progressed = nil, true
    end
  end
  assert(progressed, "hook ordering cycle")
end

local RAW, PROTECTED = "alsa_output.j713.RawSpeakers", "audio_effect.j713-protected"
local function node(name, priority, class)
  return { ["node.name"] = name, ["media.class"] = class or "Audio/Sink",
           ["priority.session"] = priority, ["object.serial"] = "10" }
end
local function run(nodes, configured, history, kind)
  metadata_values, writes = {}, {}
  for key in pairs(stored) do stored[key] = nil end
  if configured then metadata_values["default.configured.audio.sink"] = { name = configured } end
  for i, name in ipairs(history or {}) do
    stored["default.configured.audio.sink" .. (i == 1 and "" or "." .. (i - 2))] = name
  end
  local si_om = { iterate = function()
    local i = 0
    return function()
      i = i + 1
      local props = nodes[i]
      if not props then return nil end
      return { get_associated_proxy = function() return {
        properties = props,
        lookup_port = function() return props.test_ports ~= false end,
      } end }
    end
  end }
  -- Actual upstream collection excludes nodes without ports or available routes.
  local collected = collectAvailableNodes(si_om, {}, "in", { "Audio/Sink" })
  local plain = {}
  for _, value in ipairs(collected) do plain[#plain + 1] = value:parse() end
  local data = { ["available-nodes"] = Json.Array(plain) }
  local event = {
    get_properties = function() return { ["default-node.type"] = kind or "audio.sink" } end,
    get_source = function() return source end,
    get_data = function(_, key) return data[key] end,
    set_data = function(_, key, value)
      if key == "available-nodes" then
        local flattened = {}
        for _, v in ipairs(value:parse()) do flattened[#flattened + 1] = v.parse and v:parse() or v end
        value = Json.Array(flattened)
      end
      data[key] = value
    end,
  }
  for _, hook in ipairs(order) do hook.execute(event) end
  assert(metadata_values["default.configured.audio.sink"] == nil or
    metadata_values["default.configured.audio.sink"].name == configured, "changed owner choice")
  for _, key in ipairs(writes) do assert(not key:find("default.configured", 1, true)) end
  return data["selected-node"], metadata_values["default." .. (kind or "audio.sink")], data
end
local failures, passed = 0, 0
local function test(name, check)
  local ok, reason = pcall(check)
  if ok then passed = passed + 1; print("PASS " .. name)
  else failures = failures + 1; print("FAIL " .. name .. ": " .. tostring(reason)) end
end
local function expect(name, expected, nodes, configured, history)
  test(name, function()
    local result, applied = run(nodes, configured, history)
    assert(result == expected, "selected " .. tostring(result) .. ", expected " .. tostring(expected))
    assert((applied and applied.name) == expected, "wrong applied default")
  end)
end
local HP = "alsa_output.j713.Headphones"
local SPEAKERS = { node(RAW, 1000), node(PROTECTED, 0) }
local function with(...)
  local nodes = { table.unpack(SPEAKERS) }
  for _, extra in ipairs { ... } do nodes[#nodes + 1] = extra end
  return nodes
end
local unplugged = node(HP, 0); unplugged.test_routes = false
expect("plugged headphones become the default", HP, with(node(HP, 0)))
expect("headphones win over a configured choice", HP, with(node(HP, 0), node("usb", 0)), "usb")
expect("headphones win over the protected speakers configured", HP, with(node(HP, 0)), PROTECTED)
expect("headphones win over stored history", HP, with(node(HP, 0), node("usb", 0)), nil, {"usb"})
expect("headphones win over a higher-priority sink", HP, with(node(HP, 0), node("usb", 35000)))
expect("unplugged headphones fall back to the speakers", PROTECTED, with(unplugged))
expect("unplugged headphones keep the configured choice", "usb", with(unplugged, node("usb", 0)), "usb")
expect("unplugged headphones keep stored history", "usb", with(unplugged, node("usb", 0)), nil, {"usb"})
expect("raw speakers stay hidden with headphones unplugged", PROTECTED, with(unplugged), RAW)
expect("the jack-less fallback sink is never forced", PROTECTED,
  with(node("alsa_output.platform-j713-headphones.stereo-fallback", 0)))
expect("a similar name is never forced", PROTECTED, with(node(HP .. ".custom", 0)))
test("audio source selection untouched", function()
  local result = run({ node(HP, 0, "Audio/Source"), node("mic", 1000, "Audio/Source") },
    nil, nil, "audio.source")
  assert(result == "mic", "selected " .. tostring(result))
end)
test("headphone node of another media class ignored", function()
  local result = run(with(node(HP, 0, "Audio/Duplex")))
  assert(result == PROTECTED, "selected " .. tostring(result))
end)
print(string.format("%d passed, %d failed", passed, failures))
os.exit(failures == 0 and 0 or 1)
