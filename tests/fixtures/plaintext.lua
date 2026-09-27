-- a normal, readable script: nothing to undo
local Players = game:GetService("Players")
local player = Players.LocalPlayer

local function greet(who)
    return "hello, " .. who
end

print(greet(player.Name))
