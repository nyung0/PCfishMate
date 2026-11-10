using System.Diagnostics;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using BepInEx;
using BepInEx.Configuration;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;

[BepInPlugin("pcfish.helper.internal", "PC Fish Internal Breeding", "0.3.0")]
public sealed class Plugin : BasePlugin
{
    private const BindingFlags Members = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
    private static Plugin instance = null!;
    private static object? data, ui, breed, home, merge;
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private ConfigEntry<bool> enabled = null!;
    private ConfigEntry<int> interval = null!;
    private ConfigEntry<string> grades = null!;
    private double nextPoll, nextBreed, nextOpen, waitUntil;
    private double nextConfig;
    private DateTime configStamp;
    private int heartsBefore, fishBefore;
    private bool waiting;
    private double nextUpgrade;
    private int? upgradeFrom;
    private double upgradeDeadline;
    private bool upgradePopup;
    private HashSet<string>? mergePending;
    private double mergeDeadline, nextMerge;
    private string mergeSummary = "";
    private string mergeMessage = "";
    private static readonly string[] RarityNames = { "기본", "일반", "고급", "희귀", "전설", "신화" };
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
            Hook("NN.PF.UI.Home.UIHome", "OnEnable", nameof(CaptureHome)),
            Hook("NN.PF.UI.Merge.UIMerge", "OnEnable", nameof(CaptureMerge)),
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
    private static void CaptureHome(object __instance) => home = __instance;
    private static void CaptureMerge(object __instance) => merge = __instance;

    private bool previousF7;
    private bool previousF8;
    private double nextBridge;
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
        }
        if (plugin.clock.Elapsed.TotalSeconds >= plugin.nextBridge)
        {
            plugin.nextBridge = plugin.clock.Elapsed.TotalSeconds + 0.25;
            plugin.PollBridge();
        }
        bool f7 = GetAsyncKeyState(0x76) < 0;
        if (f7 && !plugin.previousF7 && plugin.command is null)
        {
            plugin.enabled.Value = !plugin.enabled.Value;
            plugin.Record(plugin.enabled.Value ? "자동화 시작" : "자동화 중지", "F7 · 교배");
        }
        plugin.previousF7 = f7;
        bool f8 = GetAsyncKeyState(0x77) < 0;
        if (f8)
        {
            if (!plugin.previousF8 && (plugin.enabled.Value || plugin.bridgeEnabled))
                plugin.Record("자동화 중지", "F8");
            plugin.enabled.Value = false;
            plugin.blockedRun = plugin.bridgeRun;
            plugin.bridgeEnabled = false;
        }
        plugin.previousF8 = f8;
        if (!plugin.enabled.Value && !plugin.bridgeEnabled && !plugin.waiting
            && plugin.upgradeFrom is null && !plugin.upgradePopup && plugin.mergePending is null)
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
            plugin.upgradeFrom = null;
            plugin.upgradePopup = false;
            plugin.mergePending = null;
            plugin.Record("오류", plugin.message);
        }
    }

    private sealed record Command(string RunId, bool Enabled, int Interval, int[]? Rarities,
        bool? Breed, bool TankUpgrade, bool StarsFirst, bool SameBreedRarity, bool AutoMerge, MergeOptions? Merge);
    private sealed record MergeOptions(bool SameRarity, bool ZeroBreeds, bool ExcludePlaced,
        bool ExcludeSeason, bool ExcludeSeasonMaterials, int[]? Rarities, string[]? SeasonIds,
        string[]? SeasonMaterials, bool? SameStars);
    private Command? command;

    private void Record(string kind, string detail, string?[]? parents = null, string? child = null)
    {
        var entry = JsonSerializer.Serialize(new {
            Time = DateTimeOffset.Now, Kind = kind, Message = detail, Parents = parents, Child = child,
        });
        Log.LogInfo($"{kind} · {detail}");
        try
        {
            Directory.CreateDirectory(bridge);
            File.AppendAllText(Path.Combine(bridge, "events.jsonl"), entry + Environment.NewLine);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            Log.LogWarning($"Automation history unavailable: {error.Message}");
        }
    }

    private Command? ReadCommand()
    {
        var path = Path.Combine(bridge, "command.json");
        return DateTime.UtcNow - File.GetLastWriteTimeUtc(path) < TimeSpan.FromSeconds(5)
            ? JsonSerializer.Deserialize<Command>(File.ReadAllText(path))
            : null;
    }

    private void PollBridge()
    {
        try
        {
            Directory.CreateDirectory(bridge);
            command = ReadCommand();
            if (command is not null && enabled.Value)
                enabled.Value = false;
            bridgeRun = command?.RunId;
            bool requested = command is { Enabled: true } && bridgeRun != blockedRun;
            if (requested != bridgeEnabled)
                Record(requested ? "자동화 시작" : "자동화 중지", "컨트롤러");
            bridgeEnabled = requested;
            var status = Path.Combine(bridge, "status.json");
            var temporary = status + ".tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(new {
                Version = 3, Running = enabled.Value || bridgeEnabled, Message = message,
                MergeMessage = bridgeEnabled && command?.AutoMerge == true ? mergeMessage : "",
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

        if (mergePending is not null)
        {
            FinishMerge(fish, count, now);
            return;
        }
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
            Record("교배 완료", message, parentVariants, child);
        }
        if (TryUpgrade(now))
            return;
        if (!enabled.Value && !bridgeEnabled)
            return;
        if (TryMerge(fish, count, now))
            return;
        if (bridgeEnabled && command?.Breed == false)
            return;
        if (hearts <= 0)
        {
            message = "하트 회복 대기 중";
            return;
        }
        if (now < nextBreed)
            return;

        if (bridgeEnabled && command?.Rarities is not { Length: > 0 })
        {
            message = "교배에 사용할 희귀도를 선택하세요";
            return;
        }
        var candidates = Candidates(fish, count);
        var parents = bridgeEnabled && command?.SameBreedRarity == true
            ? candidates.GroupBy(item => item.Rarity).Select(group => group.Take(2).ToArray())
                .FirstOrDefault(pair => pair.Length == 2) ?? Array.Empty<(string Id, int Rarity, int Stars)>()
            : candidates.Take(2).ToArray();
        if (parents.Length != 2)
        {
            message = bridgeEnabled && command?.SameBreedRarity == true
                ? "교배 가능한 같은 희귀도 2마리 대기 중" : "교배 가능한 서로 다른 개체 2마리 대기 중";
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
                Record("교배 건너뜀", message);
                nextBreed = now + Interval;
                return;
            }
        }
        selected = Read(breed, "parentFishList");
        if (selected is null || !new HashSet<object?> { Item(selected, 0), Item(selected, 1) }
            .SetEquals(parents.Select(parent => (object?)parent.Id)))
            throw new InvalidOperationException("The game did not retain the selected parent IDs.");
        // Read the current selection again before spending a heart.
        if (bridgeEnabled)
        {
            var latest = ReadCommand();
            var allowed = latest?.Rarities;
            if (latest is null || !latest.Enabled || latest.Breed == false || allowed is null
                || latest.RunId != bridgeRun || parents.Any(parent => !allowed.Contains(parent.Rarity))
                || (latest.SameBreedRarity && parents[0].Rarity != parents[1].Rarity))
            {
                Call(breed, "ResetParentFishList");
                message = "교배 설정이 변경되어 다시 확인 중";
                return;
            }
        }
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
        Call(breed, "Breed");
    }

    private static bool MergeEligible(object item, MergeOptions options, int grade, DateTimeOffset now)
    {
        if (Read(item, "Id") is not string { Length: > 0 } ||
            Read(item, "Level") is not int rarity || rarity is < 1 or > 5 ||
            options.Rarities?.Contains(rarity) != true ||
            Read(item, "Grade") is not int stars || stars is < 1 or > 5 ||
            (options.SameStars != false && stars != grade) ||
            Read(item, "IsLocked") is true ||
            (options.ExcludePlaced && Read(item, "IsPlaced") is true) ||
            (options.ZeroBreeds && Read(item, "BreedCount") is not 0) ||
            Read(item, "Type") is not string species)
            return false;
        if ((options.ExcludeSeason && (options.SeasonIds is not { Length: > 0 } ||
                options.SeasonIds.Contains(species))) ||
            (options.ExcludeSeasonMaterials && (options.SeasonMaterials is not { Length: > 0 } ||
                options.SeasonMaterials.Contains($"{species}:{stars}"))))
            return false;
        return Read(item, "BreedNextDatetime") is not string { Length: > 0 } date ||
            (DateTimeOffset.TryParse(date, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var ready)
             && ready <= now);
    }

    private static string[] MergeSlots(object screen)
    {
        var list = Read(screen, "mergeFishList") ?? throw new InvalidOperationException("합성 슬롯을 읽지 못했습니다");
        int size = list is Array array ? array.Length : (int)Read(list, "Length")!;
        return Enumerable.Range(0, size).Select(i => Item(list, i)).OfType<string>()
            .Where(id => id.Length > 0).ToArray();
    }

    private bool TryMerge(object fish, int count, double now)
    {
        if (!bridgeEnabled || command is not { AutoMerge: true, Merge: { } options } || now < nextMerge)
            return false;
        if (merge is null || Read(merge, "mergeType")?.ToString() != "Standard"
            || Read(merge, "grade") is not int grade || grade is < 1 or > 5)
        {
            mergeMessage = "게임의 일반 합성에서 별 등급을 선택하세요";
            return false;
        }
        if (ui is null || Read(ui, "isProcessing") is not false)
            return false;
        var utc = DateTimeOffset.UtcNow;
        var candidates = Enumerable.Range(0, count).Select(i => Item(fish, i))
            .OfType<object>().Where(item => MergeEligible(item, options, grade, utc))
            .DistinctBy(item => (string)Read(item, "Id")!)
            .OrderBy(item => (int)Read(item, "Level")!).ToArray();
        var groups = candidates.GroupBy(item => (int)Read(item, "Level")!).ToArray();
        var batch = options.SameRarity
            ? groups.FirstOrDefault(group => group.Count() >= 10)?.Take(10).ToArray()
            : candidates.Take(10).ToArray();
        if (batch?.Length != 10)
        {
            int available = options.SameRarity ? groups.Select(group => group.Count()).DefaultIfEmpty().Max()
                                               : candidates.Length;
            string grades = options.SameStars != false ? $"{grade}성" : "별 등급 혼합";
            mergeMessage = $"{grades} 재료 {available}/10 · " +
                (options.SameRarity ? "같은 희귀도" : "허용 희귀도 전체");
            return false;
        }
        if (!Active(merge))
        {
            mergeMessage = "재료 10/10 · 합성 탭 여는 중";
            nextMerge = now + 1;
            if (Read(ui, "viewWindow") is object window && Read(window, "activeInHierarchy") is false)
                Call(ui, "OpenWindow");
            Call(ui, "ChangeTabMenu", 3);
            return true;
        }
        if (Read(merge, "viewMergeType") is object picker && Read(picker, "activeInHierarchy") is true)
            return true;
        if (MergeSlots(merge).Length != 0)
        {
            mergeMessage = "기존 합성 슬롯이 비기를 기다리는 중";
            return true;
        }
        var ids = batch.Select(item => (string)Read(item, "Id")!).ToHashSet();
        foreach (var id in ids)
        {
            if (Call(merge, "SetParent", id) is true)
                continue;
            Call(merge, "ResetParent");
            nextMerge = now + Interval;
            mergeMessage = options.SameStars == false
                ? "게임이 재료 조합을 거절했습니다 · 일반 합성의 별 등급을 확인하세요"
                : "게임이 합성 재료 등록을 거절했습니다";
            return true;
        }
        var latest = ReadCommand();
        var currentOptions = latest?.Merge;
        if (latest is not { Enabled: true, AutoMerge: true } || latest.RunId != bridgeRun ||
            currentOptions is null || batch.Any(item => !MergeEligible(item, currentOptions, grade, DateTimeOffset.UtcNow)) ||
            (currentOptions.SameRarity && batch.Select(item => Read(item, "Level")).Distinct().Count() != 1) ||
            !ids.SetEquals(MergeSlots(merge)))
        {
            Call(merge, "ResetParent");
            return true;
        }
        mergeSummary = string.Join(" / ", batch.GroupBy(item =>
                (Stars: (int)Read(item, "Grade")!, Rarity: (int)Read(item, "Level")!))
            .OrderBy(group => group.Key.Stars).ThenBy(group => group.Key.Rarity)
            .Select(group => $"{group.Key.Stars}성 {RarityNames[group.Key.Rarity]} {group.Count()}마리"));
        mergePending = ids;
        mergeDeadline = now + 30;
        nextMerge = now + Interval;
        message = "합성 결과 확인 중";
        mergeMessage = message;
        Call(merge, "Merge");
        return true;
    }

    private void FinishMerge(object fish, int count, double now)
    {
        bool remaining = Enumerable.Range(0, count).Select(i => Item(fish, i))
            .Any(item => item is not null && Read(item, "Id") is string id && mergePending!.Contains(id));
        if (!remaining)
        {
            if (merge is not null && Read(merge, "viewResultFx") is object effect &&
                Read(effect, "activeInHierarchy") is true && now < mergeDeadline)
                return;
            mergePending = null;
            if (merge is not null && Read(merge, "uiFishResult") is object result && Active(result))
                Call(result, "Confirm");
            message = "게임에서 합성 완료 확인";
            mergeMessage = message;
            Record("합성 완료", mergeSummary);
        }
        else if (now >= mergeDeadline)
        {
            throw new InvalidOperationException("게임에서 합성 완료를 확인하지 못했습니다. 합성 화면을 확인하세요.");
        }
    }

    private bool TryUpgrade(double now)
    {
        if (upgradeFrom is int previousLevel)
        {
            int level = (int)Read(data!, "tankLevel")!;
            if (level > previousLevel)
            {
                upgradeFrom = null;
                upgradePopup = true;
                nextUpgrade = now + 1;
                message = $"수조 레벨업 · Lv.{previousLevel} → Lv.{level}";
                Record("수조 레벨업", message);
            }
            else if (now >= upgradeDeadline)
            {
                upgradeFrom = null;
                blockedRun = bridgeRun;
                bridgeEnabled = false;
                message = "게임에서 수조 레벨업을 확인하지 못했습니다";
                Record("오류", message);
            }
            return true;
        }
        if (upgradePopup)
        {
            if (now < nextUpgrade)
                return true;
            upgradePopup = false;
            if (home is not null && Read(home, "uiLevelUp") is object popup && Active(popup))
                Call(popup, "Ok");
            return true;
        }
        if (!bridgeEnabled || command?.TankUpgrade != true || now < nextUpgrade
            || data is null || Call(data, "IsCheckTankLevelUp") is not true)
            return false;
        if (ui is null || Read(ui, "isProcessing") is not false)
            return true;
        if (home is null || !Active(home))
        {
            nextUpgrade = now + 1;
            if (Read(ui, "viewWindow") is object window && Read(window, "activeInHierarchy") is false)
                Call(ui, "OpenWindow");
            Call(ui, "ChangeTabMenu", 0);
            return true;
        }
        upgradeFrom = (int)Read(data, "tankLevel")!;
        upgradeDeadline = now + 15;
        Call(home, "TankLevelUp");
        return true;
    }

    private IEnumerable<(string Id, int Rarity, int Stars)> Candidates(object fish, int count)
    {
        var allowed = bridgeEnabled && command is not null
            ? (command.Rarities ?? Array.Empty<int>()).ToHashSet()
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
        var ordered = bridgeEnabled && command?.StarsFirst == true
            ? candidates.OrderByDescending(item => item.Stars).ThenByDescending(item => item.Rarity)
            : candidates.OrderByDescending(item => item.Rarity).ThenByDescending(item => item.Stars);
        return ordered.DistinctBy(item => item.Id);
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
