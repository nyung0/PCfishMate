using System.Diagnostics;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using BepInEx;
using BepInEx.Configuration;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;

[BepInPlugin("pcfish.helper.internal", "PC Fish Internal Breeding", "0.1.0")]
public sealed class Plugin : BasePlugin
{
    private const BindingFlags Members = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
    private static Plugin instance = null!;
    private static object? data, ui, breed;
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private ConfigEntry<bool> enabled = null!;
    private ConfigEntry<int> interval = null!;
    private ConfigEntry<string> grades = null!;
    private double nextPoll, nextBreed, nextOpen, waitUntil;
    private double nextConfig;
    private DateTime configStamp;
    private int heartsBefore, fishBefore;
    private bool waiting;
    private readonly HashSet<string> fishIdsBefore = new();
    private string?[] parentVariants = Array.Empty<string?>();
    private string message = "게임 연결됨 · 대기 중";
    private string? bridgeRun, blockedRun;
    private readonly string bridge = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PCFishHelper", "internal");
    private bool bridgeEnabled;

    [DllImport("user32.dll")]
    private static extern short GetAsyncKeyState(int key);

    public override void Load()
    {
        instance = this;
        enabled = Config.Bind("Breeding", "Enabled", false, "Allow the plugin to spend breeding hearts. F8 stops it.");
        enabled.Value = false;
        interval = Config.Bind("Breeding", "IntervalSeconds", 10, "Minimum seconds between breeding calls.");
        grades = Config.Bind("Breeding", "AllowedRarities", "0,1,2,3,4,5",
            "0 basic, 1 common, 2 uncommon, 3 rare, 4 legendary, 5 mythical.");
        configStamp = File.GetLastWriteTimeUtc(Config.ConfigFilePath);
        var harmony = new Harmony("pcfish.helper.internal");
        var hooks = new[]
        {
            Hook("NN.PF.Core.Managers.GameDataManager", "Start", nameof(CaptureData)),
            Hook("NN.PF.Core.Managers.UIManager", "Start", nameof(CaptureUi)),
            Hook("NN.PF.Core.Managers.GameManager", "Update", nameof(Tick)),
        };
        foreach (var (target, postfix) in hooks)
            harmony.Patch(target, postfix: new HarmonyMethod(postfix));
        Log.LogInfo("Internal breeding hooks loaded. F7 starts or stops; F8 stops.");
    }

    private static (MethodInfo, MethodInfo) Hook(string type, string method, string postfix)
    {
        var gameType = Assembly.Load("Assembly-CSharp").GetType(type, throwOnError: true)!;
        return (Method(gameType, method, Type.EmptyTypes),
            typeof(Plugin).GetMethod(postfix, BindingFlags.Static | BindingFlags.NonPublic)!);
    }

    private static void CaptureData(object __instance) => data = __instance;
    private static void CaptureUi(object __instance) => ui = __instance;

    private bool previousF7;
    private bool sawUpdate;

    private static void Tick(object __instance)
    {
        var plugin = instance;
        if (!plugin.sawUpdate)
        {
            plugin.sawUpdate = true;
            plugin.Log.LogInfo("Game main-thread update verified.");
        }
        if (plugin.clock.Elapsed.TotalSeconds >= plugin.nextConfig)
        {
            plugin.nextConfig = plugin.clock.Elapsed.TotalSeconds + 1;
            var stamp = File.GetLastWriteTimeUtc(plugin.Config.ConfigFilePath);
            if (stamp != plugin.configStamp)
            {
                plugin.configStamp = stamp;
                plugin.Config.Reload();
                plugin.Log.LogInfo($"Config reloaded; breeding enabled: {plugin.enabled.Value}.");
            }
            plugin.PollBridge();
        }
        bool f7 = GetAsyncKeyState(0x76) < 0;
        if (f7 && !plugin.previousF7)
        {
            plugin.enabled.Value = !plugin.enabled.Value;
            plugin.Log.LogInfo(plugin.enabled.Value ? "Automatic breeding started." : "Automatic breeding stopped.");
        }
        plugin.previousF7 = f7;
        if (GetAsyncKeyState(0x77) < 0)
        {
            plugin.enabled.Value = false;
            plugin.blockedRun = plugin.bridgeRun;
            plugin.bridgeEnabled = false;
        }
        if (!plugin.enabled.Value && !plugin.bridgeEnabled && !plugin.waiting)
            return;
        try
        {
            plugin.Run(__instance);
        }
        catch (Exception error)
        {
            plugin.enabled.Value = false;
            plugin.blockedRun = plugin.bridgeRun;
            plugin.bridgeEnabled = false;
            plugin.message = error.GetBaseException().Message;
            plugin.waiting = false;
            plugin.Log.LogError($"Breeding stopped: {error.GetBaseException().Message}");
        }
    }

    private sealed record Command(string RunId, bool Enabled, int Interval, int[] Rarities);
    private Command? command;

    private void PollBridge()
    {
        try
        {
            Directory.CreateDirectory(bridge);
            var path = Path.Combine(bridge, "command.json");
            command = DateTime.UtcNow - File.GetLastWriteTimeUtc(path) < TimeSpan.FromSeconds(5)
                ? JsonSerializer.Deserialize<Command>(File.ReadAllText(path))
                : null;
            bridgeRun = command?.RunId;
            bool requested = command is { Enabled: true } && bridgeRun != blockedRun;
            if (requested != bridgeEnabled)
                Log.LogInfo(requested ? "Controller started automation." : "Controller stopped automation.");
            bridgeEnabled = requested;
            var status = Path.Combine(bridge, "status.json");
            var temporary = status + ".tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(new {
                Running = enabled.Value || bridgeEnabled, Message = message,
            }));
            File.Move(temporary, status, overwrite: true);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException)
        {
            bridgeEnabled = false;
        }
    }

    private int Interval => Math.Clamp(bridgeEnabled && command is not null ? command.Interval : interval.Value, 1, 3600);

    private void Run(object gameManager)
    {
        double now = clock.Elapsed.TotalSeconds;
        if (now < nextPoll)
            return;
        nextPoll = now + 0.25;
        if (data is null)
            throw new InvalidOperationException("The game data manager did not initialize.");
        breed = Read(gameManager, "uiBreed");
        int hearts = (int)Read(data, "breedCount")!;
        var fish = Read(data, "fishList");
        if (fish is null)
            return;
        int count = (int)Read(fish, "Count")!;

        if (waiting)
        {
            var added = Enumerable.Range(0, count).Select(i => Item(fish, i))
                .Where(item => item is not null && Read(item, "Id") is string id && !fishIdsBefore.Contains(id))
                .Take(2).ToArray();
            if (hearts >= heartsBefore && count <= fishBefore)
            {
                if (now >= waitUntil)
                    throw new InvalidOperationException("The game did not confirm the previous breeding call.");
                return;
            }
            string? child = null;
            if (breed is not null && Active(breed))
            {
                var result = Read(breed, "uiFishResult");
                if (result is not null && Active(result))
                {
                    if (Read(result, "fish") is object resultFish)
                        child = Read(resultFish, "Fish") as string;
                    Call(result, "Confirm");
                }
                else if (Read(breed, "viewResultFx") is object effect && Read(effect, "activeInHierarchy") is true)
                {
                    if (now >= waitUntil)
                        throw new InvalidOperationException("The breeding result animation did not close.");
                    return;
                }
            }
            if (child is null && added.Length == 0 && now < waitUntil)
                return;
            if (child is null && added.Length == 1)
                child = Read(added[0]!, "Fish") as string;
            waiting = false;
            message = "게임에서 교배 완료 확인";
            Log.LogInfo("The game confirmed breeding.");
            Log.LogInfo("BREED_RESULT " + JsonSerializer.Serialize(new { Parents = parentVariants, Child = child }));
        }
        if (!enabled.Value && !bridgeEnabled)
            return;
        if (hearts <= 0)
        {
            message = "하트 회복 대기 중";
            return;
        }
        if (now < nextBreed)
            return;

        var parents = Candidates(fish, count).Take(2).ToArray();
        if (parents.Length != 2)
        {
            message = "교배 가능한 서로 다른 개체 2마리 대기 중";
            return;
        }
        if (breed is null || !Active(breed))
        {
            if (ui is not null && now >= nextOpen && Read(ui, "isProcessing") is false)
            {
                nextOpen = now + 2;
                if (Read(ui, "viewWindow") is object window && Read(window, "activeInHierarchy") is false)
                    Call(ui, "OpenWindow");
                Call(ui, "ChangeTabMenu", 2);
                Log.LogInfo("Requested the game's breeding tab.");
            }
            return;
        }

        var selected = Read(breed, "parentFishList");
        if (selected is null || Enumerable.Range(0, 2).Any(i => Item(selected, i) is string { Length: > 0 }))
        {
            message = "부모 슬롯이 비기를 기다리는 중";
            return;
        }
        foreach (var parent in parents)
        {
            if (Call(breed, "SetParent", parent.Id) is not true)
            {
                Call(breed, "ResetParentFishList");
                message = "게임이 부모 지정을 거절했습니다";
                Log.LogWarning(message);
                nextBreed = now + Interval;
                return;
            }
        }
        selected = Read(breed, "parentFishList");
        if (selected is null || !new HashSet<object?> { Item(selected, 0), Item(selected, 1) }
            .SetEquals(parents.Select(parent => (object?)parent.Id)))
            throw new InvalidOperationException("The game did not retain the selected parent IDs.");
        heartsBefore = hearts;
        fishBefore = count;
        fishIdsBefore.Clear();
        var variants = new Dictionary<string, string?>();
        for (int i = 0; i < count; i++)
        {
            if (Item(fish, i) is object item && Read(item, "Id") is string id)
            {
                fishIdsBefore.Add(id);
                if (parents.Any(parent => parent.Id == id))
                    variants[id] = Read(item, "Fish") as string;
            }
        }
        parentVariants = parents.Select(parent => variants.GetValueOrDefault(parent.Id)).ToArray();
        waiting = true;
        waitUntil = now + 20;
        nextBreed = now + Interval;
        message = "교배 결과 확인 중";
        Log.LogInfo($"Invoking breed with {hearts} hearts available.");
        Call(breed, "Breed");
    }

    private IEnumerable<(string Id, int Rarity, int Stars)> Candidates(object fish, int count)
    {
        var allowed = bridgeEnabled && command is not null
            ? command.Rarities.ToHashSet()
            : grades.Value.Split(',').Select(value => int.TryParse(value, out int rank) ? rank : -1).ToHashSet();
        var now = DateTimeOffset.UtcNow;
        var candidates = new List<(string Id, int Rarity, int Stars)>();
        for (int i = 0; i < count; i++)
        {
            var item = Item(fish, i);
            if (item is null || Read(item, "Id") is not string { Length: > 0 } id ||
                Read(item, "IsLocked") is true)
                continue;
            int rarity = (int)Read(item, "Level")!;
            int stars = (int)Read(item, "Grade")!;
            if (!allowed.Contains(rarity))
                continue;
            if (rarity != 0 && (int)Read(item, "BreedCount")! <= 0)
                continue;
            if (Read(item, "BreedNextDatetime") is string { Length: > 0 } date &&
                (!DateTimeOffset.TryParse(date, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var ready)
                 || ready > now))
                continue;
            candidates.Add((id, rarity, stars));
        }
        return candidates.OrderByDescending(item => item.Rarity).ThenByDescending(item => item.Stars)
            .DistinctBy(item => item.Id);
    }

    private static bool Active(object behaviour) =>
        Read(behaviour, "gameObject") is object go && Read(go, "activeInHierarchy") is true;

    private static object? Read(object obj, string name)
    {
        var type = obj.GetType();
        var property = type.GetProperty(name, Members);
        if (property is not null)
            return property.GetValue(obj);
        var field = type.GetField(name, Members) ?? throw new MissingMemberException(type.FullName, name);
        return field.GetValue(obj);
    }

    private static object? Item(object list, int index) => list is Array array ? array.GetValue(index) :
        Method(list.GetType(), "get_Item", new[] { typeof(int) }).Invoke(list, new object[] { index });

    private static object? Call(object obj, string name, params object[] args) =>
        Method(obj.GetType(), name, args.Select(arg => arg.GetType()).ToArray()).Invoke(obj, args);

    private static MethodInfo Method(Type type, string name, Type[] types) =>
        type.GetMethod(name, Members, null, types, null) ?? throw new MissingMethodException(type.FullName, name);
}
