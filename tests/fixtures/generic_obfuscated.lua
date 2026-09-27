-- Synthetic sample. No real obfuscator produced this; it has the *shape* of a
-- flattened loader (single-letter locals, string-built identifiers, a
-- loadstring'd chunk, a state loop) so the generic sandbox tracer has something
-- to do. Used by tests/smoke.py to verify result headers and the trace path.
do
	local s = string
	local c = s.char
	local t = {}
	for i = 1, 6 do
		t[i] = c(108 + i)
	end
	local name = s.reverse(table.concat(t))
	local fn = loadstring or load
	local chunk = fn("return " .. #name .. " * 7")
	local n = chunk()
	local out = {}
	out[#out + 1] = name
	out[#out + 1] = tostring(n)
	local state = 0
	while state < 3 do
		if state == 0 then
			out[#out + 1] = "zero"
			state = state + 1
		elseif state == 1 then
			out[#out + 1] = "one"
			state = state + 1
		else
			out[#out + 1] = "done"
			state = 3
		end
	end
	local Players = game:GetService("Players")
	out[#out + 1] = Players and "service-ok" or "no-service"
	print(table.concat(out, ","))
end
