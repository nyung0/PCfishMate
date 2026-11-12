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
    private static object? data, ui, breed, home, merge, mergePicker;
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
    private HashSet<string>? mergeFishBefore;
    private double mergeDeadline, nextMerge;
    private double mergeSetupUntil;
    private double mergeSetupDeadline;
    private string mergeSummary = "";
    private string mergeMessage = "";
    private static readonly string[] RarityNames = { "기본", "일반", "고급", "희귀", "전설", "신화" };
    private readonly HashSet<string> fishIdsBefore = new();
    private string?[] parentVariants = Array.Empty<string?>();
    private string message = "게임 연결됨 · 대기 중";
    private string? bridgeRun, blockedRun, lastStartedRun;
    private readonly string bridge = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PCFishHelper", "internal");
    private bool bridgeEnabled;
    private bool mergeHooksReady, upgradeHookReady;
    private bool mergeFailed, breedFailed, upgradeFailed;
    private string mergeFailure = "", breedFailure = "", upgradeFailure = "";
    private bool bridgeReadFailed;
    private bool mergeSlotsOwned, breedSlotsOwned;
    private (int Max, long Interval)? heartConfig;

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
        upgradeHookReady = TryHook(harmony, "수조 레벨업", "NN.PF.UI.Home.UIHome", nameof(CaptureHome));
        mergeHooksReady = TryHook(harmony, "합성", "NN.PF.UI.Merge.UIMerge", nameof(CaptureMerge))
            && TryHook(harmony, "합성", "NN.PF.UI.Merge.UIMergeType", nameof(CaptureMergePicker));
        Log.LogInfo("Internal breeding hooks loaded. F7 starts or stops; F8 stops.");
    }

    private bool TryHook(Harmony harmony, string feature, string type, string postfix)
    {
        try
        {
            var (target, handler) = Hook(type, "OnEnable", postfix);
            harmony.Patch(target, postfix: new HarmonyMethod(handler));
            return true;
        }
        catch (Exception error)
        {
            Log.LogWarning($"{feature} 호환성 오류 · {type}.OnEnable · {error.GetBaseException().Message}");
            return false;
        }
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
    private static void CaptureMergePicker(object __instance) => mergePicker = __instance;

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
            try
            {
                var stamp = File.GetLastWriteTimeUtc(plugin.Config.ConfigFilePath);
                if (stamp != plugin.configStamp)
                {
                    plugin.configStamp = stamp;
                    plugin.Config.Reload();
                    plugin.Log.LogInfo($"Config reloaded; breeding enabled: {plugin.enabled.Value}.");
                }
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException)
            {
                plugin.Log.LogWarning($"게임 모듈 설정 읽기 실패 · {error.Message}");
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
            if (plugin.enabled.Value)
                plugin.breedFailed = false;
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
            plugin.mergeFishBefore = null;
            plugin.Record("오류", plugin.message);
        }
    }

    private sealed record Command(string RunId, bool Enabled, int Interval, int[]? Rarities,
        bool? Breed, bool TankUpgrade, bool StarsFirst, bool SameBreedRarity, bool AutoMerge, MergeOptions? Merge,
        int[]? BreedStars, bool SameBreedStars);
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
            if (command is { Enabled: true } && command.RunId != lastStartedRun && command.RunId != blockedRun)
            {
                lastStartedRun = command.RunId;
                breedFailed = mergeFailed = upgradeFailed = false;
                breedFailure = mergeFailure = upgradeFailure = "";
                mergeSetupDeadline = 0;
            }
            if (command is not null && enabled.Value)
                enabled.Value = false;
            bridgeRun = command?.RunId;
            bool requested = command is { Enabled: true } && bridgeRun != blockedRun;
            if (requested != bridgeEnabled)
                Record(requested ? "자동화 시작" : "자동화 중지", "컨트롤러");
            bridgeEnabled = requested;
            var hearts = ReadHeartStatus();
            var status = Path.Combine(bridge, "status.json");
            var temporary = status + ".tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(new {
                Version = 3,
                Running = bridgeEnabled
                    ? (command?.Breed != false && !breedFailed)
                      || (command?.AutoMerge == true && mergeHooksReady && !mergeFailed)
                      || (command?.TankUpgrade == true && upgradeHookReady && !upgradeFailed)
                    : enabled.Value && !breedFailed,
                Message = message,
                HeartCount = hearts.Count,
                HeartMax = hearts.Max,
                NextHeartAt = hearts.NextAt,
                MergeMessage = bridgeEnabled && command?.AutoMerge == true ? mergeMessage : "",
                FeatureErrors = new[] {
                    command?.Breed != false ? breedFailure : "",
                    command?.AutoMerge == true ? mergeHooksReady ? mergeFailure : "합성 · 이 게임 버전과 호환되지 않습니다" : "",
                    command?.TankUpgrade == true ? upgradeHookReady ? upgradeFailure : "수조 레벨업 · 이 게임 버전과 호환되지 않습니다" : "",
                }.Where(error => error.Length > 0),
            }));
            File.Move(temporary, status, overwrite: true);
            bridgeReadFailed = false;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException)
        {
            bridgeEnabled = false;
            if (!bridgeReadFailed)
                Log.LogWarning($"컨트롤러 연결 오류 · {error.GetBaseException().Message}");
            bridgeReadFailed = true;
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
        {
            message = "게임 데이터 초기화 대기 중";
            return;
        }

        if (mergePending is not null && RunMerge(now))
            return;
        if (waiting)
        {
            RunBreed(gameManager, now);
            return;
        }
        if (upgradeHookReady && !upgradeFailed)
        {
            try
            {
                if (TryUpgrade(now))
                    return;
            }
            catch (Exception error)
            {
                upgradeFailed = true;
                upgradeFrom = null;
                upgradePopup = false;
                upgradeFailure = ReportFailure("수조 레벨업", error);
            }
        }
        if (!enabled.Value && !bridgeEnabled)
            return;
        if (mergeHooksReady && !mergeFailed && bridgeEnabled && command?.AutoMerge == true && RunMerge(now))
            return;
        if (!breedFailed && (bridgeEnabled ? command?.Breed != false : enabled.Value))
            RunBreed(gameManager, now);
    }

    private string ReportFailure(string feature, Exception error)
    {
        var cause = error.GetBaseException();
        string detail = $"{feature} 자동화 중지 · {cause.GetType().Name}: {cause.Message}";
        Record("오류", detail);
        return detail;
    }

    private void ClearOwnedSlots(object? screen, string method, string feature)
    {
        try
        {
            if (screen is not null && Active(screen))
                Call(screen, method);
        }
        catch (Exception error)
        {
            Log.LogWarning($"{feature} 슬롯 정리 실패 · {error.GetBaseException().Message}");
        }
    }

    private bool RunMerge(double now)
    {
        try
        {
            var fish = Read(data!, "fishList");
            if (fish is null)
                return false;
            int count = (int)Read(fish, "Count")!;
            if (mergePending is null)
                return TryMerge(fish, count, now);
            FinishMerge(fish, count, now);
            return true;
        }
        catch (Exception error)
        {
            if (mergeSlotsOwned)
                ClearOwnedSlots(merge, "ResetParent", "합성");
            mergeSlotsOwned = false;
            mergeFailed = true;
            mergePending = null;
            mergeFishBefore = null;
            mergeFailure = ReportFailure("합성", error);
            mergeMessage = "";
            return false;
        }
    }

    private void RunBreed(object gameManager, double now)
    {
        try
        {
            BreedStep(gameManager, now);
        }
        catch (Exception error)
        {
            if (breedSlotsOwned)
                ClearOwnedSlots(breed, "ResetParentFishList", "교배");
            breedSlotsOwned = false;
            breedFailed = true;
            waiting = false;
            breedFailure = ReportFailure("교배", error);
        }
    }

    private void BreedStep(object gameManager, double now)
    {
        breed = Read(gameManager, "uiBreed");
        int hearts = (int)Read(data!, "breedCount")!;
        var fish = Read(data!, "fishList");
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
            Record("교배 완료", message, parentVariants, child);
            return;
        }
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
        bool sameRarity = bridgeEnabled && command?.SameBreedRarity == true;
        bool sameStars = bridgeEnabled && command?.SameBreedStars == true;
        var parents = candidates.GroupBy(item => (sameRarity ? item.Rarity : 0, sameStars ? item.Stars : 0))
            .Select(group => group.Take(2).ToArray()).FirstOrDefault(pair => pair.Length == 2);
        if (parents is null)
        {
            message = "교배 조건에 맞는 부모 2마리 대기 중";
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
        breedSlotsOwned = true;
        foreach (var parent in parents)
        {
            if (Call(breed, "SetParent", parent.Id) is not true)
            {
                Call(breed, "ResetParentFishList");
                breedSlotsOwned = false;
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
                || latest.RunId != bridgeRun || parents.Any(parent => !allowed.Contains(parent.Rarity)
                    || latest.BreedStars?.Contains(parent.Stars) == false)
                || (latest.SameBreedRarity && parents[0].Rarity != parents[1].Rarity)
                || (latest.SameBreedStars && parents[0].Stars != parents[1].Stars))
            {
                Call(breed, "ResetParentFishList");
                breedSlotsOwned = false;
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
        breedSlotsOwned = false;
        Call(breed, "Breed");
    }

    private static bool MergeEligible(object item, MergeOptions options, int grade, DateTimeOffset now)
    {
        if (Read(item, "Id") is not string { Length: > 0 } ||
            Read(item, "Level") is not int rarity || rarity is < 1 or > 5 ||
            options.Rarities?.Contains(rarity) != true ||
            Read(item, "Grade") is not int stars || stars is < 1 or > 5 ||
            (options.SameStars != false && grade != 0 && stars != grade) ||
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
        if (now < mergeSetupUntil)
            return true;
        if (ui is null || Read(ui, "isProcessing") is not false)
            return false;
        var utc = DateTimeOffset.UtcNow;
        var candidates = Enumerable.Range(0, count).Select(i => Item(fish, i))
            .OfType<object>().Where(item => MergeEligible(item, options, 0, utc))
            .DistinctBy(item => (string)Read(item, "Id")!)
            .OrderBy(item => (int)Read(item, "Level")!).ToArray();
        var groups = candidates.GroupBy(item => (
            Stars: options.SameStars != false ? (int)Read(item, "Grade")! : 0,
            Rarity: options.SameRarity ? (int)Read(item, "Level")! : 0)).ToArray();
        var batch = groups.FirstOrDefault(group => group.Count() >= 10)?.Take(10).ToArray();
        if (batch?.Length != 10)
        {
            mergeSetupDeadline = 0;
            int available = groups.Select(group => group.Count()).DefaultIfEmpty().Max();
            string grades = options.SameStars != false ? "같은 별 등급" : "별 등급 혼합";
            mergeMessage = $"{grades} 재료 {available}/10 · " +
                (options.SameRarity ? "같은 희귀도" : "허용 희귀도 전체");
            return false;
        }
        if (merge is null || !Active(merge))
        {
            CheckMergeSetupDeadline(now);
            mergeMessage = "재료 10/10 · 합성 탭 여는 중";
            mergeSetupUntil = now + 1;
            if (Read(ui, "viewWindow") is object window && Read(window, "activeInHierarchy") is false)
                Call(ui, "OpenWindow");
            Call(ui, "ChangeTabMenu", 3);
            return true;
        }
        if (MergeSlots(merge).Length != 0)
        {
            mergeSetupDeadline = 0;
            mergeMessage = "기존 합성 슬롯이 비기를 기다리는 중";
            return true;
        }
        int grade = (int)Read(batch[0], "Grade")!;
        var pickerOpen = Read(merge, "viewMergeType") is object picker &&
            Read(picker, "activeInHierarchy") is true;
        if (pickerOpen || Read(merge, "mergeType")?.ToString() != "Standard" ||
            Read(merge, "grade") is not int currentGrade || currentGrade != grade)
        {
            CheckMergeSetupDeadline(now);
            if (!pickerOpen)
                Call(merge, "OpenMergeType");
            var selection = mergePicker;
            if (selection is null || !Active(selection))
            {
                mergeMessage = "일반 합성 설정 창을 여는 중";
                mergeSetupUntil = now + 1;
                return true;
            }
            var type = Read(selection, "mergeType")?.GetType()
                ?? throw new InvalidOperationException("합성 유형을 확인할 수 없습니다.");
            Write(selection, "mergeType", Enum.Parse(type, "Standard"));
            Write(selection, "grade", grade);
            Call(selection, "Ok");
            mergeMessage = $"일반 합성 · {grade}성 선택 중";
            mergeSetupUntil = now + 1;
            return true;
        }
        mergeSetupDeadline = 0;
        var ids = batch.Select(item => (string)Read(item, "Id")!).ToHashSet();
        mergeSlotsOwned = true;
        foreach (var id in ids)
        {
            if (Call(merge, "SetParent", id) is true)
                continue;
            Call(merge, "ResetParent");
            mergeSlotsOwned = false;
            nextMerge = now + Interval;
            mergeMessage = options.SameStars == false
                ? "게임이 혼합 재료 등록을 거절했습니다"
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
            mergeSlotsOwned = false;
            return true;
        }
        mergeSummary = string.Join(" / ", batch.GroupBy(item =>
                (Stars: (int)Read(item, "Grade")!, Rarity: (int)Read(item, "Level")!))
            .OrderBy(group => group.Key.Stars).ThenBy(group => group.Key.Rarity)
            .Select(group => $"{group.Key.Stars}성 {RarityNames[group.Key.Rarity]} {group.Count()}마리"));
        mergePending = ids;
        mergeFishBefore = Enumerable.Range(0, count).Select(i => Item(fish, i))
            .OfType<object>().Select(item => Read(item, "Id")).OfType<string>().ToHashSet();
        mergeDeadline = now + 30;
        nextMerge = now + Interval;
        message = "합성 결과 확인 중";
        mergeMessage = message;
        mergeSlotsOwned = false;
        Call(merge, "Merge");
        return true;
    }

    private void CheckMergeSetupDeadline(double now)
    {
        if (mergeSetupDeadline == 0)
            mergeSetupDeadline = now + 10;
        else if (now >= mergeSetupDeadline)
            throw new InvalidOperationException("일반 합성 화면 또는 별 등급 전환을 확인하지 못했습니다.");
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
            var result = merge is not null ? Read(merge, "uiFishResult") : null;
            bool resultOpen = result is not null && Active(result);
            string? child = resultOpen && Read(result!, "fish") is object resultFish
                ? Read(resultFish, "Fish") as string : null;
            if (child is null && mergeFishBefore is not null)
            {
                var added = Enumerable.Range(0, count).Select(i => Item(fish, i))
                    .OfType<object>().Where(item => Read(item, "Id") is string id && !mergeFishBefore.Contains(id))
                    .Take(2).ToArray();
                if (added.Length == 1)
                    child = Read(added[0], "Fish") as string;
            }
            if (child is null && now < mergeDeadline)
                return;
            mergePending = null;
            mergeFishBefore = null;
            if (resultOpen)
                Call(result!, "Confirm");
            message = "게임에서 합성 완료 확인";
            mergeMessage = message;
            Record("합성 완료", mergeSummary, child: child);
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
                throw new InvalidOperationException("게임에서 수조 레벨업을 확인하지 못했습니다.");
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
            if (!allowed.Contains(rarity) || (bridgeEnabled && command?.BreedStars?.Contains(stars) == false))
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

    private (int? Count, int? Max, long? NextAt) ReadHeartStatus()
    {
        if (data is null)
            return (null, null, null);

        int count;
        try
        {
            if (Read(data, "breedCount") is not int current || current < 0)
                return (null, null, null);
            count = current;
        }
        catch (Exception)
        {
            return (null, null, null);
        }

        try
        {
            if (heartConfig is null)
            {
                var type = data.GetType().Assembly.GetType("NN.PF.Core.GameConfig", throwOnError: true)!;
                const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
                object? ConfigValue(string name) =>
                    type.GetProperty(name, flags)?.GetValue(null) ?? type.GetField(name, flags)?.GetValue(null);
                var maximum = Convert.ToInt32(ConfigValue("CF_BREED_MAX_COUNT"), CultureInfo.InvariantCulture);
                var seconds = Convert.ToInt64(ConfigValue("CF_BREED_COUNT_INTERVAL"), CultureInfo.InvariantCulture);
                if (maximum <= 0 || seconds <= 0)
                    return (count, null, null);
                heartConfig = (maximum, seconds);
            }
            var (max, interval) = heartConfig.Value;
            if (count >= max)
                return (count, max, null);

            var stamp = Convert.ToInt64(Read(data, "breedTimestamp"), CultureInfo.InvariantCulture);
            long? next = stamp > 0 ? checked(stamp + interval) : null;
            return (count, max, next);
        }
        catch (Exception)
        {
            return (count, heartConfig?.Max, null);
        }
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

    private static void Write(object obj, string name, object value)
    {
        var type = obj.GetType();
        var property = type.GetProperty(name, Members);
        if (property is not null)
            property.SetValue(obj, value);
        else
            (type.GetField(name, Members) ?? throw new MissingMemberException(type.FullName, name))
                .SetValue(obj, value);
    }

    private static object? Call(object obj, string name, params object[] args) =>
        Method(obj.GetType(), name, args.Select(arg => arg.GetType()).ToArray()).Invoke(obj, args);

    private static MethodInfo Method(Type type, string name, Type[] types) =>
        type.GetMethod(name, Members, null, types, null) ?? throw new MissingMethodException(type.FullName, name);
}
