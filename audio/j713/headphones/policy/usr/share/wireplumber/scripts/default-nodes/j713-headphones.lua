-- SPDX-License-Identifier: MIT
-- omarchy-m4-headphones. While a headset is plugged in, the headphone sink
-- is the default output, even over a sink the user picked (like macOS). On
-- unplug its UCM route ("Headphone Jack") becomes unavailable, rescan drops
-- the node from available-nodes, and the normal selection falls back.
-- Runs after find-selected (configured default, priority 30000+) and
-- find-stored; 40000 makes find-best skip, and apply writes the result.
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
    if event:get_properties () ["default-node.type"] ~= "audio.sink" then
      return
    end
    local available = event:get_data ("available-nodes")
    if not available then
      return
    end
    for _, props in ipairs (available:parse ()) do
      if props ["node.name"] == "alsa_output.j713.Headphones" and
          props ["media.class"] == "Audio/Sink" then
        event:set_data ("selected-node-priority", 40000)
        event:set_data ("selected-node", props ["node.name"])
        return
      end
    end
  end,
}:register ()
