-- SPDX-License-Identifier: MIT
-- The DSP parent is hidden from clients, but remains visible to WirePlumber.
-- Exclude only that parent; ordinary selection and owner history choose among
-- the remaining outputs, including the protected speaker filter.
SimpleEventHook {
  name = "default-nodes/j713-protected",
  before = {
    "default-nodes/find-selected-default-node",
    "default-nodes/find-stored-default-node",
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
    local selectable = {}
    for _, props in ipairs (available:parse ()) do
      if props ["node.name"] ~= "alsa_output.j713.RawSpeakers" or
          props ["media.class"] ~= "Audio/Sink" then
        table.insert (selectable, Json.Object (props))
      end
    end
    event:set_data ("available-nodes", Json.Array (selectable))
  end,
}:register ()
