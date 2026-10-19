local event = require "necro.event.Event"
local fileIO = require "system.game.FileIO"
local entities = require "system.game.Entities"

local CONFIG_PATH = "mods/NecroDancerTrainer/config.txt"
local config = {
	invincible = false,
	oneHit = false,
	doubleDamage = false,
	infiniteCoins = false,
}
local tickCounter = 0

local function readConfig()
	local text = fileIO.readFileToString(CONFIG_PATH, 4096)
	if type(text) ~= "string" then
		return
	end

	local function enabled(key)
		return text:find(key .. "=1", 1, true) ~= nil
	end

	config.invincible = enabled("invincible")
	config.oneHit = enabled("oneHit")
	config.doubleDamage = enabled("doubleDamage")
	config.infiniteCoins = enabled("infiniteCoins")
end

local function isPlayer(entity)
	return entity and entity.playableCharacter
end

local function isEnemy(entity, attacker)
	return entity
		and entity.health
		and isPlayer(attacker)
		and not isPlayer(entity)
end

local function refreshPlayerCurrency()
	if not config.infiniteCoins then
		return
	end

	for entity in entities.entitiesWithComponents {"playableCharacter", "currency"} do
		if entity.currency.goldCounter then
			entity.currency.goldCounter.amount = 999999
		end
	end
end

event.frame.add("trainerConfig", {order = "aggro", sequence = -1000}, function()
	tickCounter = tickCounter + 1
	if tickCounter % 30 == 0 then
		readConfig()
		refreshPlayerCurrency()
	end
end)

event.objectTakeDamage.add("trainerDamage", {order = "damage", sequence = -1000}, function(ev)
	local victim = ev.victim or ev.entity
	local attacker = ev.attacker

	if config.invincible and isPlayer(victim) then
		ev.damage = 0
		ev.suppressed = true
		return
	end

	if not isEnemy(victim, attacker) then
		return
	end

	if config.doubleDamage then
		ev.damage = (ev.damage or 0) * 2
	end

	if config.oneHit and victim.health.health then
		ev.damage = math.max(ev.damage or 0, victim.health.health)
	end
end)
