-- SPDX-License-Identifier: MIT
-- omarchy-m4-headphones. While a headset is plugged in, the headphone sink
-- is the default output and, for a 4-pole headset, its microphone the
-- default input, even over a device the user picked (like macOS). On unplug
-- the UCM route ("Headphone Jack", "Headset Mic Jack") becomes unavailable,
-- rescan drops the node from available-nodes, and the normal selection falls
-- back. A 3-pole plug never turns the mic route on, so the input stays as it
-- was. Runs after find-selected (configured default, priority 30000+) and
-- find-stored; 40000 makes find-best skip, and apply writes the result.
local jack = {
  ["audio.sink"] = { name = "alsa_output.j713.Headphones", class = "Audio/Sink" },
  ["audio.source"] = { name = "alsa_input.j713.Headset", class = "Audio/Source" },
}

SimpleEventHook {
  name = "default-nodes/j713-headphones",
  after = {
    "default-nodes/find-selected-default-node",
    "default-nodes/find-stored-default-node",
  },
  before = {
    "default-nodes/find-best-default-node",
    "default-nodes/apply-default-node",
  },
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "select-default-node" },
    },
  },
  execute = function (event)
    local node = jack [event:get_properties () ["default-node.type"]]
    if not node then
      return
    end
    local available = event:get_data ("available-nodes")
    if not available then
      return
    end
    for _, props in ipairs (available:parse ()) do
      if props ["node.name"] == node.name and props ["media.class"] == node.class then
        event:set_data ("selected-node-priority", 40000)
        event:set_data ("selected-node", props ["node.name"])
        return
      end
    end
  end,
}:register ()
