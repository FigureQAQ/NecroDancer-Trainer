local callbacks = {}
local validEvents = {
	frame = true,
	objectCurrency = true,
	levelComplete = true,
	gameStateLevel = true,
	objectTakeDamage = true,
	objectDealDamage = true,
	objectCheckAbility = true,
	objectComputeDigStrength = true,
	priceTagCheck = true,
}
local validOrders = {
	levelComplete = {
		collectItems = true,
		dad = true,
		pendingDelays = true,
		speedrunTimer = true,
		unlocks = true,
		ensemble = true,
		multiChar = true,
		stats = true,
		deathless = true,
		resetPlayers = true,
		cutscene = true,
		winScreen = true,
		nextLevel = true,
		notifyHost = true,
	},
	objectComputeDigStrength = {
		charge = true,
		confusion = true,
		fear = true,
	},
}
local registeredOptions = {}
local config = table.concat({
	"keepCoinsOnTransition=1",
	"freeShop=1",
	"forceBeat=1",
	"invincible=1",
	"oneHit=1",
	"digAll=1",
	"coinsMultiplier=4",
	"coinsRequestId=2",
	"coinsValue=777",
	"maxHealthValue=9",
	"visionRadius=12",
	"levelRequestId=0",
	"itemRequestId=4",
	"characterRequestId=0",
	"itemRequest=weapon_obsidian_warhammer",
}, "\n")
local players = {}
local function register(eventName, id, options, callback)
	assert(validEvents[eventName], "invalid game event registered: " .. eventName)
	local eventOrders = validOrders[eventName]
	if eventOrders then
		assert(type(options.order) == "string", "missing ordering key for " .. eventName)
		assert(eventOrders[options.order], "invalid ordering key for " .. eventName .. ": " .. tostring(options.order))
	end
	registeredOptions[eventName] = registeredOptions[eventName] or {}
	registeredOptions[eventName][id] = options
	callbacks[eventName] = callbacks[eventName] or {}
	callbacks[eventName][id] = callback
end
local event = setmetatable({}, {
	__index = function(_, eventName)
		return {add = function(...) register(eventName, ...) end}
	end,
})
package.preload["necro.event.Event"] = function() return event end
package.preload["system.game.FileIO"] = function()
	return {readFileToString = function() return config end}
end
package.preload["system.game.Entities"] = function()
	return {
		entitiesWithComponents = function()
			local i = 0
			return function()
				i = i + 1
				return players[i]
			end
		end,
	}
end
local goldWrites = 0
local Currency = {
	Type = {GOLD = "gold"},
	get = function(entity) return entity.gold end,
	set = function(entity, _, value)
		goldWrites = goldWrites + 1
		entity.gold = value
		return true
	end,
}
package.preload["necro.game.item.Currency"] = function() return Currency end
local increased = 0
package.preload["necro.game.character.Health"] = function()
	return {
		increaseMaxHealth = function(entity, amount, healer)
			assert(type(amount) == "number" and healer == nil, "Health API argument order is wrong")
			increased = increased + amount
			entity.health.maxHealth = entity.health.maxHealth + amount
		end,
	}
end
package.preload["necro.game.system.Ability"] = function()
	return {
		Flag = {
			CHECK_BEAT_DELAY = 2,
			unmask = function(flags, mask)
				return math.floor(flags / mask) % 2 == 1 and flags - mask or flags
			end,
		},
	}
end
package.preload["necro.game.tile.Dig"] = function()
	return {Strength = {UNBREAKABLE = 99}}
end
local grantedItem = nil
package.preload["necro.game.item.Inventory"] = function()
	return {
		grant = function(itemType, holder)
			assert(type(itemType) == "string" and holder and holder.playableCharacter,
				"Inventory.grant argument order is wrong")
			grantedItem = itemType
			return true
		end,
	}
end
local rhythmSettings = {}
package.preload["necro.game.character.Rhythm"] = function()
	return {
		getRhythmSettings = function(playerID)
			rhythmSettings[playerID] = rhythmSettings[playerID] or {ignoreRhythm = false}
			return rhythmSettings[playerID]
		end,
	}
end
package.preload["necro.game.character.CharacterSwitch"] = function()
	return {
		perform = function(entity, characterType)
			assert(entity and entity.playableCharacter and type(characterType) == "string",
				"CharacterSwitch.perform argument order is wrong")
			entity.characterType = characterType
			return true
		end,
	}
end
local visionUpdates = 0
package.preload["necro.game.vision.Vision"] = function()
	return {LIGHT_TILE_SIZE = 256, queueFieldOfViewUpdate = function() visionUpdates = visionUpdates + 1 end}
end
package.preload["necro.game.vision.LightSource"] = function()
	return {setRadius = function(entity, inner, outer)
		assert(type(inner) == "number" and type(outer) == "number")
		entity.lightSourceRadial.innerRadius = inner
		entity.lightSourceRadial.outerRadius = outer
	end}
end
package.preload["core.Logger"] = function() error("not installed") end
package.preload["necro.debug.DebugCommands"] = function() error("not installed") end
package.preload["necro.game.system.Progression"] = function() error("not installed") end
dofile("mod/NecroDancerTrainer.lua")

local digOptions = registeredOptions.objectComputeDigStrength.trainerDigAll
assert(digOptions.order == "charge", "dig-all uses an invalid ordering key")
assert(digOptions.sequence == 1000, "dig-all must run after the base dig-strength handlers")
local transitionOptions = registeredOptions.levelComplete.trainerCaptureTransitionCoins
assert(transitionOptions.order == "collectItems", "coin snapshot must run before level-completion handlers")
assert(transitionOptions.sequence == -1000, "coin snapshot must precede the collectItems phase")

local function tick(count)
	for _ = 1, count do callbacks.frame.trainerConfig() end
end
local first = {
	id = 101, controllable = {playerID = 1}, playableCharacter = true, gold = 5,
	health = {health = 3, maxHealth = 5}, visionRadial = {radius = 4},
}
local second = {
	id = 202, controllable = {playerID = 2}, playableCharacter = true, gold = 8,
	health = {health = 4, maxHealth = 6},
	visionRaycast = {active = true},
	lightSourceRadial = {innerRadius = 320, outerRadius = 768},
}
local missingMaxHealth = {
	id = 303, controllable = {playerID = 3}, playableCharacter = true, gold = 2,
	health = setmetatable({health = 2}, {
		__index = function(_, key)
			if key == "maxHealth" then error("missing ECS component") end
		end,
	}),
}
players = {first, second, missingMaxHealth}

-- A persisted request is not replayed merely by loading the mod.
tick(15)
assert(first.gold == 5 and second.gold == 8, "old coin request replayed")
assert(first.health.maxHealth == 9 and second.health.maxHealth == 9, "max health not applied")
assert(increased == 7, "health API did not add the expected max health")
assert(first.visionRadial.radius == 12, "vision radius was not applied")
assert(second.lightSourceRadial.innerRadius == 12 * 256, "player light radius was not applied")
assert(visionUpdates > 0, "vision update was not queued")
assert(rhythmSettings[1].ignoreRhythm and rhythmSettings[2].ignoreRhythm,
	"force beat did not enable ignoreRhythm for all players")
assert(grantedItem == nil, "persisted item request replayed")
assert(callbacks.tileVisionRadius == nil and callbacks.objectVisionRadius == nil,
	"unsupported vision events must not be registered")

local pickup = {entity = first, currency = "gold", item = {}, difference = 5}
callbacks.objectCurrency.trainerCoinMultiplier(pickup)
assert(pickup.difference == 20, "pickup multiplier was not applied to gold")
for _, change in ipairs({
	{entity = first, currency = "gold", difference = 5},
	{entity = first, currency = "gold", item = {}, difference = -5},
	{entity = first, currency = "diamond", item = {}, difference = 5},
	{entity = {health = {}}, currency = "gold", item = {}, difference = 5},
}) do
	local before = change.difference
	callbacks.objectCurrency.trainerCoinMultiplier(change)
	assert(change.difference == before, "multiplier affected a non-player pickup or direct currency change")
end

local purchase = {multiplier = 1, effectiveCost = 25}
callbacks.priceTagCheck.trainerFreeShop(purchase)
assert(purchase.multiplier == 0, "shop price multiplier was not zeroed")

local incoming = {entity = first, damage = 2}
callbacks.objectTakeDamage.trainerInvincible(incoming)
assert(incoming.damage == 0, "invincibility did not cancel incoming damage")

local outgoing = {entity = first, target = {health = {health = 3}}, damage = 1}
callbacks.objectDealDamage.trainerDamage(outgoing)
assert(outgoing.damage == 999999, "one-hit damage was not applied")

local ability = {flags = 6}
callbacks.objectCheckAbility.trainerForceBeat(ability)
assert(ability.flags == 4, "beat-delay flag was not removed")

config = config .. "\nforceBeat=0"
tick(15)
assert(not rhythmSettings[1].ignoreRhythm and not rhythmSettings[2].ignoreRhythm,
	"disabling force beat did not restore regular rhythm")
config = config .. "\nforceBeat=1"
tick(15)

local dig = {strength = 1}
callbacks.objectComputeDigStrength.trainerDigAll(dig)
assert(dig.strength == 99, "unbreakable dig strength was not applied")

-- A new UI request applies once and does not replay on subsequent frames.
config = config .. "\ncoinsRequestId=3"
tick(15)
assert(first.gold == 777 and second.gold == 777, "new coin request was not applied")
local writesAfterRequest = goldWrites
tick(15)
assert(goldWrites == writesAfterRequest, "coin request repeated")

config = config .. "\nitemRequestId=5"
tick(15)
assert(grantedItem == "weapon_obsidian_warhammer", "item request was not applied")

config = config .. "\ncharacterRequest=Cadence\ncharacterRequestId=1"
tick(15)
assert(first.characterType == "Cadence", "character request was not applied")

first.gold, second.gold = 12, 34
players = {first, second}
callbacks.levelComplete.trainerCaptureTransitionCoins()
first.gold, second.gold = 0, 0
players = {second, first}
callbacks.gameStateLevel.trainerRestoreTransitionCoins()
tick(2)
assert(first.gold == 12 and second.gold == 34, "coins restored to the wrong player")

config = config .. "\nvisionRadius=0"
tick(15)
assert(first.visionRadial.radius == 4, "disabling vision did not restore radial vision")
assert(second.lightSourceRadial.innerRadius == 320
	and second.lightSourceRadial.outerRadius == 768, "disabling vision did not restore player light")

print("trainer runtime tests passed")
