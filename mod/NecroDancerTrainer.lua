local event = require "necro.event.Event"
local fileIO = require "system.game.FileIO"
local entities = require "system.game.Entities"
local okLogger, Logger = pcall(require, "core.Logger")
local okDebug, DebugCommands = pcall(require, "necro.debug.DebugCommands")
local okProgression, Progression = pcall(require, "necro.game.system.Progression")
local okInventory, Inventory = pcall(require, "necro.game.item.Inventory")
local okCurrency, Currency = pcall(require, "necro.game.item.Currency")
local okCharacterSwitch, CharacterSwitch = pcall(require, "necro.game.character.CharacterSwitch")
local okAbility, Ability = pcall(require, "necro.game.system.Ability")
local okRhythm, Rhythm = pcall(require, "necro.game.character.Rhythm")
local okHealth, Health = pcall(require, "necro.game.character.Health")
local okDig, Dig = pcall(require, "necro.game.tile.Dig")
local okVision, Vision = pcall(require, "necro.game.vision.Vision")
local okLightSource, LightSource = pcall(require, "necro.game.vision.LightSource")

local CONFIG_PATHS = {
	"user_data_transient://mods/NecroDancerTrainer/config.txt",
	"user_data://mods/NecroDancerTrainer/config.txt",
	"mods/NecroDancerTrainer/config.txt",
}
local CONFIG_SIZE = 16384
local config = {
	forceBeat = false,
	invincible = false,
	oneHit = false,
	doubleDamage = false,
	freeShop = false,
	digAll = false,
	unlockAll = false,
	keepCoinsOnTransition = false,
	maxHealthValue = 0,
	coinsValue = 999999,
	coinsMultiplier = 2,
	visionRadius = 0,
	coinsRequestId = 0,
	levelRequestId = 0,
	itemRequestId = 0,
	characterRequestId = 0,
	levelRequest = 0,
	itemRequest = "",
	characterRequest = "",
	requestId = 0,
}

local tickCounter = 0
local lastRequestId = -1
local lastCoinsRequestId = nil
local lastLevelRequestId = nil
local lastItemRequestId = nil
local lastCharacterRequestId = nil
local lastConfigText = ""
local configReported = false
local healthReported = false
local beatReported = false
local beatUnavailableReported = false
local rhythmReported = false
local multiplierReported = false
local digReported = false
local unlockAllApplied = false
local pendingCoinRestore = nil
local coinRestoreDelay = 0
local coinRestoreAttempts = 0
local lastKnownCoinAmounts = nil
local unpackArgs = table.unpack or unpack
local logger = nil
if okLogger and type(Logger.createLoggerForScript) == "function" then
	logger = Logger.createLoggerForScript()
end

local function report(message)
	if logger and type(logger.info) == "function" then
		logger.info(message)
	else
		print(message)
	end
end

local function addEvent(name, id, options, handler)
	local lookupOK, group = pcall(function() return event[name] end)
	if not lookupOK or not group or type(group.add) ~= "function" then
		report(string.format("[NecroDancerTrainer] event unavailable: %s", tostring(name)))
		return false
	end
	local ok, err = pcall(group.add, id, options or {}, handler)
	if not ok then
		report(string.format("[NecroDancerTrainer] event registration failed: %s (%s)", tostring(name), tostring(err)))
	end
	return ok
end

local function numberValue(value, default)
	local number = tonumber(value)
	if number == nil or number ~= number or number == math.huge or number == -math.huge then
		return default
	end
	return number
end

local function readConfigText()
	for _, path in ipairs(CONFIG_PATHS) do
		local success, text = pcall(fileIO.readFileToString, path, CONFIG_SIZE)
		if success and type(text) == "string" and #text > 0 then
			return text, path
		end
	end
	return nil, nil
end

local function enabled(values, primary, legacy)
	if values[primary] ~= nil then
		return values[primary] == "1" or values[primary] == "true"
	end
	return legacy and (values[legacy] == "1" or values[legacy] == "true") or false
end

local function readConfig()
	local text, source = readConfigText()
	if type(text) ~= "string" or text == lastConfigText then
		return
	end
	lastConfigText = text

	local values = {}
	for key, value in text:gmatch("([%w_]+)=([^\r\n]*)") do
		values[key] = value
	end

	config.forceBeat = enabled(values, "forceBeat", "skipMissedBeat")
	config.invincible = enabled(values, "invincible")
	config.oneHit = enabled(values, "oneHit")
	config.doubleDamage = enabled(values, "doubleDamage")
	config.freeShop = enabled(values, "freeShop", "infiniteCoins")
	config.digAll = enabled(values, "digAll")
	config.unlockAll = enabled(values, "unlockAll")
	config.keepCoinsOnTransition = enabled(values, "keepCoinsOnTransition")
	config.maxHealthValue = math.max(0, math.floor(numberValue(
		values.maxHealthValue,
		numberValue(values.healthValue, 0)
	)))
	config.coinsValue = math.max(0, math.floor(numberValue(values.coinsValue, 999999)))
	config.coinsMultiplier = math.max(1, math.min(100, numberValue(
		values.coinsMultiplier,
		numberValue(values.speed, 2)
	)))
	config.visionRadius = math.max(0, math.min(100, math.floor(numberValue(
		values.visionRadius,
		numberValue(values.visionMultiplier, numberValue(values.vision, 0))
	))))
	config.coinsRequestId = math.max(0, math.floor(numberValue(values.coinsRequestId, 0)))
	config.levelRequestId = math.max(0, math.floor(numberValue(
		values.levelRequestId, values.requestId and numberValue(values.requestId, 0) or 0
	)))
	config.itemRequestId = math.max(0, math.floor(numberValue(
		values.itemRequestId, values.requestId and numberValue(values.requestId, 0) or 0
	)))
	config.characterRequestId = math.max(0, math.floor(numberValue(
		values.characterRequestId, values.requestId and numberValue(values.requestId, 0) or 0
	)))
	config.levelRequest = math.max(0, math.floor(numberValue(values.levelRequest, 0)))
	config.itemRequest = values.itemRequest or ""
	config.characterRequest = values.characterRequest or ""
	config.requestId = math.max(0, math.floor(numberValue(values.requestId, 0)))

	if not configReported then
		report(string.format(
			"[NecroDancerTrainer] config loaded from %s (forceBeat=%s invincible=%s freeShop=%s digAll=%s coinsMultiplier=%s visionRadius=%s)",
			source or "unknown",
			tostring(config.forceBeat),
			tostring(config.invincible),
			tostring(config.freeShop),
			tostring(config.digAll),
			tostring(config.coinsMultiplier),
			tostring(config.visionRadius)
		))
		configReported = true
	end
end

readConfig()
lastCoinsRequestId = config.coinsRequestId
lastLevelRequestId = config.levelRequestId
lastItemRequestId = config.itemRequestId
lastCharacterRequestId = config.characterRequestId
report("[NecroDancerTrainer] script initialized")

local function isPlayer(entity)
	return entity and entity.playableCharacter
end

local function isLivingEnemy(entity)
	return entity and entity.health and not isPlayer(entity)
end

local function players()
	return entities.entitiesWithComponents {"playableCharacter"}
end

local function callDebug(name, ...)
	if not okDebug then
		return false
	end
	local lookupOK, command = pcall(function()
		return DebugCommands[name]
	end)
	if not lookupOK or type(command) ~= "function" then
		return false
	end
	local args = {...}
	local ok, result = pcall(function()
		return command(unpackArgs(args))
	end)
	return ok and result ~= false
end

local function setExisting(target, field, value)
	if not target then
		return false
	end
	local readOK, current = pcall(function()
		return target[field]
	end)
	if not readOK or current == nil then
		return false
	end
	return pcall(function()
		target[field] = value
	end)
end

local function component(entity, name)
	if not entity then
		return nil
	end
	local ok, value = pcall(function()
		return entity[name]
	end)
	if ok then
		return value
	end
	return nil
end

local function field(target, name)
	if not target then
		return nil
	end
	local ok, value = pcall(function()
		return target[name]
	end)
	return ok and value or nil
end

local function applyMaxHealth()
	if config.maxHealthValue <= 0 then
		return
	end
	for entity in players() do
		local health = component(entity, "health")
		local currentHealth = field(health, "health")
		local currentMax = field(health, "maxHealth")
		if currentMax == nil and okHealth and type(Health.getMaxHealth) == "function" then
			local ok, value = pcall(Health.getMaxHealth, entity)
			if ok then currentMax = value end
		end
		local writeOK = false
		if health and okHealth and type(Health.increaseMaxHealth) == "function"
			and type(currentMax) == "number" and config.maxHealthValue > currentMax then
			writeOK = pcall(Health.increaseMaxHealth, entity, config.maxHealthValue - currentMax)
		end
		if not writeOK and health and currentMax ~= nil then
			writeOK = pcall(function() health.maxHealth = config.maxHealthValue end)
		end
		local appliedMax = field(health, "maxHealth")
		if appliedMax == nil and okHealth and type(Health.getMaxHealth) == "function" then
			local ok, value = pcall(Health.getMaxHealth, entity)
			if ok then appliedMax = value end
		end
		if type(currentHealth) == "number" and currentHealth > config.maxHealthValue then
			pcall(function() health.health = config.maxHealthValue end)
		end
		if not healthReported and health then
			report(string.format(
				"[NecroDancerTrainer] max health requested=%d previous=%s applied=%s health=%s",
				config.maxHealthValue,
				tostring(currentMax),
				tostring(appliedMax),
				tostring(field(health, "health"))
			))
			healthReported = true
		end
	end
end

local visionReported = false
local visionOverrides = {}
local function applyVisionRadius()
	if not okVision then
		return
	end
	local applied = false
	local seen = {}
	for entity in players() do
		local key = field(entity, "id") or entity
		seen[key] = true
		local saved = visionOverrides[key]
		local radial = component(entity, "visionRadial")
		local light = component(entity, "lightSourceRadial")
		if config.visionRadius > 0 then
			if not saved then
				saved = {
					radius = field(radial, "radius"),
					inner = field(light, "innerRadius"),
					outer = field(light, "outerRadius"),
				}
				visionOverrides[key] = saved
			end
			if type(saved.radius) == "number" and field(radial, "radius") ~= config.visionRadius then
				applied = setExisting(radial, "radius", config.visionRadius) or applied
			end
			-- Players normally use raycast vision and radial light, not visionRadial.
			local unit = Vision.LIGHT_TILE_SIZE
			if type(saved.inner) == "number" and type(saved.outer) == "number"
				and type(unit) == "number" and okLightSource then
				local inner = config.visionRadius * unit
				local outer = math.floor(inner * 1.25)
				if field(light, "innerRadius") ~= inner or field(light, "outerRadius") ~= outer then
					applied = pcall(LightSource.setRadius, entity, inner, outer) or applied
				end
			end
		elseif saved then
			if saved.radius ~= nil then
				applied = setExisting(radial, "radius", saved.radius) or applied
			end
			if saved.inner ~= nil and saved.outer ~= nil and okLightSource then
				applied = pcall(LightSource.setRadius, entity, saved.inner, saved.outer) or applied
			end
			visionOverrides[key] = nil
		end
	end
	for key in pairs(visionOverrides) do
		if not seen[key] then visionOverrides[key] = nil end
	end
	if applied and type(Vision.queueFieldOfViewUpdate) == "function" then
		pcall(Vision.queueFieldOfViewUpdate)
	end
	if applied and config.visionRadius > 0 and not visionReported then
		report(string.format(
			"[NecroDancerTrainer] vision radius override applied=%s",
			tostring(config.visionRadius)
		))
		visionReported = true
	end
end

local function playerKey(entity, fallback)
	local controllable = component(entity, "controllable")
	local id = controllable and controllable.playerID
	if type(id) == "number" or type(id) == "string" then
		return tostring(id)
	end
	if type(entity) == "table" and entity.id ~= nil then
		return tostring(entity.id)
	end
	return tostring(fallback)
end

local function snapshotCoins()
	if not config.keepCoinsOnTransition or not okCurrency
		or type(Currency.get) ~= "function" or not Currency.Type then
		return nil
	end
	local amounts = {}
	local count = 0
	for entity in players() do
		local success, amount = pcall(Currency.get, entity, Currency.Type.GOLD)
		if success and type(amount) == "number" then
			count = count + 1
			amounts[playerKey(entity, count)] = amount
		end
	end
	return count > 0 and amounts or nil
end

local function captureTransitionCoins()
	pendingCoinRestore = snapshotCoins() or lastKnownCoinAmounts
	coinRestoreDelay = pendingCoinRestore and 2 or 0
	coinRestoreAttempts = 0
end

local function restoreTransitionCoins()
	if not config.keepCoinsOnTransition or not pendingCoinRestore
		or not okCurrency or type(Currency.set) ~= "function" or not Currency.Type then
		pendingCoinRestore = nil
		coinRestoreDelay = 0
		coinRestoreAttempts = 0
		return
	end
	coinRestoreAttempts = coinRestoreAttempts + 1
	local restored = 0
	local count = 0
	for entity in players() do
		count = count + 1
		local key = playerKey(entity, count)
		local amount = pendingCoinRestore[key]
		if amount ~= nil then
			local ok, result = pcall(Currency.set, entity, Currency.Type.GOLD, amount)
			if ok and result ~= false then
				restored = restored + 1
				pendingCoinRestore[key] = nil
			end
		end
	end
	if restored == 0 then
		if coinRestoreAttempts < 15 then
			coinRestoreDelay = 1
		else
			pendingCoinRestore = nil
			coinRestoreDelay = 0
			coinRestoreAttempts = 0
		end
		return
	end
	local remaining = next(pendingCoinRestore) ~= nil
	if remaining and coinRestoreAttempts < 15 then
		coinRestoreDelay = 1
	else
		pendingCoinRestore = nil
		coinRestoreDelay = 0
		coinRestoreAttempts = 0
	end
end

local function applyCoinsValue()
	if config.coinsRequestId == lastCoinsRequestId then
		return
	end
	local applied = false
	if okCurrency and type(Currency.set) == "function" and Currency.Type then
		for entity in players() do
			local ok, result = pcall(Currency.set, entity, Currency.Type.GOLD, config.coinsValue)
			if ok and result ~= false then
				applied = true
			end
		end
	end
	if applied then
		lastCoinsRequestId = config.coinsRequestId
	end
end


local characterNames = {
	"Cadence", "Aria", "Melody", "Dorian", "Eli", "Monk", "Coda", "Bolt",
	"Bard", "Nocturna", "Diamond", "Mary", "Tempo", "Dove", "Reaper",
	"Chaunter", "Klarinetta", "Suzu", "Kento", "Beastmaster", "Merlin",
	"Goldman_Goldman", "Coldsteel_Coldsteel",
}

local function unlockOne(unlockType, name)
	if not okProgression or type(Progression.setUnlocked) ~= "function" or unlockType == nil then
		return false
	end
	return pcall(Progression.setUnlocked, unlockType, name, true)
end

local function applyUnlockAll()
	if not config.unlockAll then
		unlockAllApplied = false
		return
	end
	if unlockAllApplied then
		return
	end
	local applied = false
	if okProgression and Progression.UnlockType then
		local types = Progression.UnlockType
		local function unlockKnownNames(unlockType)
			if type(Progression.getAllUnlocks) ~= "function" or unlockType == nil then
				return
			end
			local ok, all = pcall(Progression.getAllUnlocks, unlockType)
			if not ok or type(all) ~= "table" then
				return
			end
			for key, value in pairs(all) do
				local name = nil
				if type(key) == "string" then
					name = key
				elseif type(value) == "string" then
					name = value
				end
				if name and name ~= "" then
					applied = unlockOne(unlockType, name) or applied
				end
			end
		end

		for depth = 2, 4 do
			local depthOK, depthType = pcall(Progression.getDepthUnlockType, depth)
			if depthOK and depthType ~= nil then
				for _, name in ipairs(characterNames) do
					applied = unlockOne(depthType, name) or applied
				end
				unlockKnownNames(depthType)
			end
		end
		if types.PLAYABLE_CHARACTER ~= nil then
			for _, name in ipairs(characterNames) do
				applied = unlockOne(types.PLAYABLE_CHARACTER, name) or applied
			end
			unlockKnownNames(types.PLAYABLE_CHARACTER)
		end
		for _, key in ipairs({"LOBBY_NPC", "EXTRA_MODE"}) do
			if types[key] ~= nil then
				unlockKnownNames(types[key])
			end
		end
		if types.EXTRA_MODE ~= nil then
			for _, name in ipairs({"ZONE_2", "ZONE_3", "ZONE_4", "HARDMODE", "AMPLIFIED"}) do
				applied = unlockOne(types.EXTRA_MODE, name) or applied
			end
		end
	end
	if applied and okProgression and type(Progression.persist) == "function" then
		pcall(Progression.persist)
	end
	unlockAllApplied = true
end

local function applyLevel()
	if config.levelRequest <= 0 or config.levelRequestId == lastLevelRequestId then
		return
	end
	if callDebug("level", config.levelRequest) then
		lastLevelRequestId = config.levelRequestId
	end
end

local function applyItem()
	if config.itemRequest == "" or config.itemRequestId == lastItemRequestId or not okInventory then
		return
	end
	for entity in players() do
		local success = false
		for _, method in ipairs({"grant", "addItem", "give"}) do
			local grant = Inventory[method]
			if type(grant) == "function" then
				local result
				-- Synchrony Inventory.grant uses (itemType, holder); keep
				-- compatibility fallbacks for older API builds.
				success, result = pcall(grant, config.itemRequest, entity)
				success = success and result ~= false
				if not success then
					success, result = pcall(grant, entity, config.itemRequest)
					success = success and result ~= false
				end
				if success then
					break
				end
			end
		end
		if success then
			lastItemRequestId = config.itemRequestId
			report(string.format(
				"[NecroDancerTrainer] item request applied=%s",
				tostring(config.itemRequest)
			))
			break
		end
	end
end

local function applyCharacter()
	if config.characterRequest == "" or config.characterRequestId == lastCharacterRequestId
		or not okCharacterSwitch then
		return
	end
	for _, method in ipairs({"perform", "switch", "change"}) do
		local action = CharacterSwitch[method]
		if type(action) == "function" then
			for entity in players() do
				local success, result = pcall(action, entity, config.characterRequest, {})
				success = success and result ~= false
				if not success then
					success, result = pcall(action, config.characterRequest, entity, {})
					success = success and result ~= false
				end
				if success then
					lastCharacterRequestId = config.characterRequestId
					report(string.format(
						"[NecroDancerTrainer] character request applied=%s",
						tostring(config.characterRequest)
					))
					return
				end
			end
		end
	end
end

local function applyRhythmOverride()
	if not okRhythm or type(Rhythm.getRhythmSettings) ~= "function" then
		return
	end
	for entity in players() do
		local controllable = component(entity, "controllable")
		local playerID = controllable and controllable.playerID
		if playerID ~= nil then
			local ok, settings = pcall(Rhythm.getRhythmSettings, playerID)
			if ok and type(settings) == "table" then
				if settings.ignoreRhythm ~= config.forceBeat then
					settings.ignoreRhythm = config.forceBeat
					if type(settings.updateRhythm) == "function" then
						pcall(settings.updateRhythm)
					end
				end
				if config.forceBeat and not rhythmReported then
					report("[NecroDancerTrainer] player rhythm judgment bypass applied")
					rhythmReported = true
				end
			end
		end
	end
end

local function applyDigAll(ev)
	if not config.digAll then
		return
	end
	if okDig and Dig.Strength and type(Dig.Strength.UNBREAKABLE) == "number" then
		local applied = pcall(function()
			ev.strength = Dig.Strength.UNBREAKABLE
		end)
		if applied and not digReported then
			report(string.format(
				"[NecroDancerTrainer] dig strength override applied=%s",
				tostring(Dig.Strength.UNBREAKABLE)
			))
			digReported = true
		end
	end
end

event.frame.add("trainerConfig", "delayedCasts", function()
	tickCounter = tickCounter + 1
	if config.keepCoinsOnTransition then
		local snapshot = snapshotCoins()
		if snapshot then lastKnownCoinAmounts = snapshot end
	else
		lastKnownCoinAmounts = nil
	end
	if coinRestoreDelay > 0 then
		coinRestoreDelay = coinRestoreDelay - 1
		if coinRestoreDelay == 0 then
			restoreTransitionCoins()
		end
	end
	if tickCounter % 15 ~= 0 then
		applyRhythmOverride()
		return
	end
	readConfig()
	applyRhythmOverride()
	applyMaxHealth()
	applyVisionRadius()
	applyCoinsValue()
	applyUnlockAll()
	applyLevel()
	applyItem()
	applyCharacter()
end)
addEvent("objectCurrency", "trainerCoinMultiplier", {
	order = "multiplier",
	sequence = 1000,
}, function(ev)
	if not ev or not isPlayer(ev.entity) or not okCurrency
		or ev.currency ~= Currency.Type.GOLD or not ev.item
		or type(ev.difference) ~= "number" or ev.difference <= 0 then
		return
	end
	ev.difference = math.floor(ev.difference * config.coinsMultiplier)
	if not multiplierReported then
		report(string.format(
			"[NecroDancerTrainer] currency pickup multiplier applied=%s",
			tostring(config.coinsMultiplier)
		))
		multiplierReported = true
	end
end)
addEvent("levelComplete", "trainerCaptureTransitionCoins", {
	order = "collectItems",
	sequence = -1000,
}, captureTransitionCoins)
addEvent("gameStateLevel", "trainerRestoreTransitionCoins", {
	order = "levelTransition",
	sequence = 1000,
}, function()
	if config.keepCoinsOnTransition and not pendingCoinRestore then
		pendingCoinRestore = lastKnownCoinAmounts
	end
	if config.keepCoinsOnTransition and pendingCoinRestore then
		coinRestoreDelay = 2
	end
end)
addEvent("objectTakeDamage", "trainerInvincible", {order = "health", sequence = -1000}, function(ev)
	local victim = ev.entity or ev.victim or ev.target
	if config.invincible and isPlayer(victim) then
		ev.damage = 0
		setExisting(ev, "remainingDamage", 0)
		setExisting(ev, "suppressed", true)
		setExisting(ev, "cancelled", true)
	end
end)

addEvent("objectDealDamage", "trainerDamage", {order = "baseIncrease", sequence = -1000}, function(ev)
	local attacker = ev.entity or ev.attacker or ev.caster
	local victim = ev.target or ev.victim
	if not isPlayer(attacker) or not isLivingEnemy(victim) then
		return
	end
	if config.oneHit then
		ev.damage = 999999
	elseif config.doubleDamage and ev.damage then
		ev.damage = ev.damage * 2
	end
end)

local function forceBeatAbility(ev)
	if not config.forceBeat or not okAbility or type(ev.flags) ~= "number" then
		return
	end
	local flagsAPI = Ability.Flag
	local flag = flagsAPI and flagsAPI.CHECK_BEAT_DELAY
	local unmask = flagsAPI and flagsAPI.unmask
	if type(flag) ~= "number" or type(unmask) ~= "function" then
		if not beatUnavailableReported then
			report("[NecroDancerTrainer] CHECK_BEAT_DELAY flag is unavailable")
			beatUnavailableReported = true
		end
		return
	end
	local ok, flags = pcall(unmask, ev.flags, flag)
	if ok and type(flags) == "number" and flags ~= ev.flags then
		ev.flags = flags
		if not beatReported then
			report("[NecroDancerTrainer] beat-delay judgment bypass applied")
			beatReported = true
		end
	end
end

addEvent("objectCheckAbility", "trainerForceBeat", {
	order = "beatDelayBypass",
	sequence = -1000,
}, forceBeatAbility)

addEvent("objectComputeDigStrength", "trainerDigAll", {
	order = "charge",
	sequence = 1000,
}, applyDigAll)

addEvent("priceTagCheck", "trainerFreeShop", {order = "currency", sequence = -1000}, function(ev)
	if not config.freeShop or not ev then
		return
	end
	setExisting(ev, "multiplier", 0)
end)
