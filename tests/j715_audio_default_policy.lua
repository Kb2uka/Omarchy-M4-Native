-- SPDX-License-Identifier: MIT
-- Reuse the retained upstream pipeline harness, with exact J715 node names.
local root = assert(arg[1])
local file = assert(io.open(root .. "/tests/j713_audio_default_policy.lua", "r"))
local harness = file:read("*a")
file:close()
harness = harness:gsub("j713", "j715"):gsub("j714", "j713")
-- Exercise a feedback source through upstream sink collection, not a fake rule.
local marker = 'print(string.format("%d passed, %d failed", passed, failures))'
local feedback = [=[
test("feedback source excluded by upstream sink rescan", function()
  local feedback_name = "alsa_input.j715.feedback"
  local nodes = {node(RAW, 1000), node(PROTECTED, 0),
                 node(feedback_name, 9000, "Audio/Source")}
  local collected = {}
  local manager = { iterate = function(_, interest)
    -- The actual WpObjectManager implements this constraint; model that boundary.
    local constraint, allowed, index = interest[1], {}, 0
    assert(constraint[1] == "media.class" and constraint[2] == "c")
    for i = 3, #constraint do allowed[constraint[i]] = true end
    return function()
      while index < #nodes do
        index = index + 1
        local props = nodes[index]
        if allowed[props["media.class"]] then
          return { get_associated_proxy = function() return {
            properties = props, lookup_port = function() return true end,
          } end }
        end
      end
    end
  end }
  EventDispatcher = { push_event = function(event)
    collected[event.kind] = event.available
  end }
  local rescan_source = { call = function(_, method, name, _, props)
    if method == "get-object-manager" then
      return name == "session-item" and manager or {}
    end
    assert(method == "create-event" and name == "select-default-node")
    return { kind = props["default-node.type"], set_data = function(self, key, value)
      assert(key == "available-nodes"); self.available = value:parse()
    end }
  end }
  hooks["default-nodes/rescan"].execute({
    get_source = function() return rescan_source end,
  })
  assert(#collected["audio.sink"] == 2)
  for _, props in ipairs(collected["audio.sink"]) do
    assert(props:parse()["node.name"] ~= feedback_name)
  end
  -- The fixture did contain a real candidate, not an absent-input tautology.
  assert(#collected["audio.source"] == 3)
end)
]=]
local start = assert(harness:find(marker, 1, true))
harness = harness:sub(1, start - 1) .. feedback .. harness:sub(start)
assert(load(harness, "@j715-retained-upstream-harness"))()
