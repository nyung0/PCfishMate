using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.RegularExpressions;

internal sealed class LiveFishColors
{
    private sealed record Edit(int X, int Y, int Width, int Height, byte[] Original, byte[] Pixels,
        Dictionary<string, Edit>? Frames);
    private sealed record Document(int Version, string Revision, string Atlas, int AtlasWidth, int AtlasHeight,
        Dictionary<string, Edit> Edits);
    internal sealed record Status(string? Revision = null, bool Applied = false, string? Error = null)
    {
        public bool AnimationFrames => true;
    }

    private static readonly Regex FishId = new(@"\AFS[0-9]{5}_[0-9]{2}_[0-9]{2}_[0-9]{2}\z");
    private readonly string path;
    private readonly Action<string> log;
    private readonly Dictionary<string, Edit> active = new();
    private Dictionary<string, Edit> desiredPatches = new();
    private DateTime stamp;
    private long length = -1;
    private double atlasDeadline, nextAtlasSearch;
    private Document? desired;
    private TextureApi? api;
    private object? atlas;
    private string? atlasName;
    private Status status = new();

    internal LiveFishColors(string path, Action<string> log)
    {
        this.path = path;
        this.log = log;
    }

    // Called by the game's Update hook, where Unity texture operations are valid.
    internal Status Poll(double now)
    {
        try
        {
            var file = new FileInfo(path);
            if (!file.Exists)
                return status;
            if (file.LastWriteTimeUtc != stamp || file.Length != length)
            {
                if (file.Length > 32 * 1024 * 1024)
                    throw new InvalidDataException("색상 설정 파일이 너무 커요.");
                using var input = file.Open(FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                var document = JsonSerializer.Deserialize<Document>(input);
                if (document is null)
                    throw new InvalidDataException("색상 설정을 읽을 수 없어요.");
                desired = document;
                stamp = file.LastWriteTimeUtc;
                length = file.Length;
                atlasDeadline = now + 60;
                nextAtlasSearch = now;
                status = new(desired.Revision);
                desiredPatches = Validate(desired);
            }
            if (desired is null || status.Error is not null)
                return status;
            bool atlasUnloaded = atlas is not null && !api!.Alive(atlas);
            if (status.Applied && !atlasUnloaded)
                return status;
            if (atlasUnloaded)
            {
                status = new(desired.Revision);
                atlas = null;
                active.Clear();
                atlasDeadline = now + 60;
                nextAtlasSearch = now;
            }
            if (active.Count == 0 && desiredPatches.Count == 0)
                return status = new(desired.Revision, true);
            api ??= new TextureApi();
            if (atlasName != desired.Atlas)
            {
                if (active.Count != 0 && atlas is not null && api.Alive(atlas))
                    throw new InvalidDataException("현재 게임의 물고기 이미지와 저장한 색상 버전이 달라요.");
                atlasName = desired.Atlas;
                atlas = null;
                active.Clear();
            }
            if (atlas is null)
            {
                if (now < nextAtlasSearch)
                    return status;
                nextAtlasSearch = now + 1;
                atlas = api.Find(desired.Atlas, desired.AtlasWidth, desired.AtlasHeight);
                if (atlas is null)
                {
                    if (now < atlasDeadline)
                        return status;
                    throw new InvalidOperationException("게임에서 물고기 이미지를 찾지 못했어요.");
                }
            }
            foreach (var (id, previous) in active.ToArray())
            {
                if (desiredPatches.ContainsKey(id))
                    continue;
                api.Copy(atlas, previous, previous.Original);
                active.Remove(id);
            }
            foreach (var (id, edit) in desiredPatches)
            {
                if (active.TryGetValue(id, out var previous) &&
                    previous.X == edit.X && previous.Y == edit.Y &&
                    previous.Width == edit.Width && previous.Height == edit.Height &&
                    previous.Pixels.AsSpan().SequenceEqual(edit.Pixels))
                    continue;
                api.Copy(atlas, edit, edit.Pixels);
                active[id] = edit;
            }
            status = new(desired.Revision, true);
        }
        catch (Exception error)
        {
            var details = error.GetBaseException();
            string message = details is MissingMemberException
                ? "이 게임 버전에서 색상 적용을 지원하지 않아요."
                : details.Message;
            if (status.Error != message)
                log($"색상 적용 실패 · {details}");
            status = new(desired?.Revision, Error: message);
        }
        return status;
    }

    private static Dictionary<string, Edit> Validate(Document document)
    {
        if (document.Version != 1 || !Guid.TryParseExact(document.Revision, "N", out _) ||
            string.IsNullOrEmpty(document.Atlas) || !document.Atlas.Contains("-AtlasFish-", StringComparison.Ordinal) ||
            document.AtlasWidth is < 1 or > 16384 || document.AtlasHeight is < 1 or > 16384 ||
            document.Edits is null || document.Edits.Count > 2048)
            throw new InvalidDataException("색상 설정 형식을 확인할 수 없어요.");
        var patches = new Dictionary<string, Edit>();
        foreach (var (id, edit) in document.Edits)
        {
            if (!FishId.IsMatch(id) || edit is null)
                throw new InvalidDataException($"{id}의 색상 이미지가 올바르지 않아요.");
            Add(id, edit);
            if (edit.Frames is null)
                continue;
            foreach (var (number, frame) in edit.Frames)
            {
                if (number.Length is < 1 or > 2 || number.Any(c => c < '0' || c > '9') ||
                    frame is null || frame.Frames is not null)
                    throw new InvalidDataException($"{id}의 수조 애니메이션이 올바르지 않아요.");
                Add($"{id}_{number}", frame);
            }
        }
        return patches;

        void Add(string id, Edit edit)
        {
            if (patches.Count >= 2048 || edit.Width is < 1 or > 256 || edit.Height is < 1 or > 256 ||
                edit.X < 0 || edit.Y < 0 || edit.X > document.AtlasWidth - edit.Width ||
                edit.Y > document.AtlasHeight - edit.Height ||
                edit.Original?.Length != edit.Width * edit.Height * 4 || edit.Pixels?.Length != edit.Original.Length)
                throw new InvalidDataException($"{id}의 색상 이미지가 올바르지 않아요.");
            for (int i = 3; i < edit.Pixels.Length; i += 4)
                if (edit.Pixels[i] != edit.Original[i])
                    throw new InvalidDataException($"{id}의 투명도가 바뀌었어요.");
            patches.Add(id, edit);
        }
    }

    private sealed class TextureApi
    {
        private const BindingFlags Static = BindingFlags.Public | BindingFlags.Static;
        private readonly Type texture2D;
        private readonly PropertyInfo name, width, height, format, graphicsFormat;
        private readonly ConstructorInfo create;
        private readonly object creationFlags;
        private readonly MethodInfo find, alive, upload, apply, copy, destroy;

        public TextureApi()
        {
            var core = Assembly.Load("UnityEngine.CoreModule");
            var unityObject = core.GetType("UnityEngine.Object", true)!;
            var texture = core.GetType("UnityEngine.Texture", true)!;
            texture2D = core.GetType("UnityEngine.Texture2D", true)!;
            var resources = core.GetType("UnityEngine.Resources", true)!;
            var graphics = core.GetType("UnityEngine.Graphics", true)!;
            var systemInfo = core.GetType("UnityEngine.SystemInfo", true)!;
            var copySupport = Property(systemInfo, "copyTextureSupport");
            var basicSupport = (Enum)Enum.Parse(copySupport.PropertyType, "Basic");
            if (copySupport.GetValue(null) is not Enum supported || !supported.HasFlag(basicSupport))
                throw new NotSupportedException("현재 그래픽 환경에서 실시간 색상 적용을 지원하지 않아요.");
            var flags = core.GetType("UnityEngine.Experimental.Rendering.TextureCreationFlags", true)!;
            name = Property(unityObject, "name");
            width = Property(texture, "width");
            height = Property(texture, "height");
            format = Property(texture2D, "format");
            graphicsFormat = Property(texture, "graphicsFormat");
            create = texture2D.GetConstructor(new[] { typeof(int), typeof(int), graphicsFormat.PropertyType, flags })
                ?? throw new MissingMethodException(texture2D.FullName, ".ctor");
            creationFlags = Enum.ToObject(flags, 0);
            find = (resources.GetMethods(Static).SingleOrDefault(method => method.Name == "FindObjectsOfTypeAll"
                && method.IsGenericMethodDefinition && method.GetParameters().Length == 0)
                ?? throw new MissingMethodException(resources.FullName, "FindObjectsOfTypeAll")).MakeGenericMethod(texture2D);
            alive = Method(unityObject, "op_Implicit", Static, unityObject);
            upload = Method(texture2D, "LoadRawTextureData", BindingFlags.Public | BindingFlags.Instance,
                typeof(IntPtr), typeof(int));
            apply = Method(texture2D, "Apply", BindingFlags.Public | BindingFlags.Instance, typeof(bool), typeof(bool));
            copy = Method(graphics, "CopyTexture", Static, texture, typeof(int), typeof(int), typeof(int), typeof(int),
                typeof(int), typeof(int), texture, typeof(int), typeof(int), typeof(int), typeof(int));
            destroy = Method(unityObject, "Destroy", Static, unityObject);
        }

        private static PropertyInfo Property(Type type, string name) =>
            type.GetProperty(name) ?? throw new MissingMemberException(type.FullName, name);

        private static MethodInfo Method(Type type, string name, BindingFlags flags, params Type[] arguments) =>
            type.GetMethod(name, flags, null, arguments, null) ?? throw new MissingMethodException(type.FullName, name);

        public bool Alive(object texture) => alive.Invoke(null, new[] { texture }) is true;

        public object? Find(string expectedName, int expectedWidth, int expectedHeight)
        {
            if (find.Invoke(null, null) is not System.Collections.IEnumerable textures)
                throw new InvalidOperationException("게임의 물고기 이미지 목록을 읽을 수 없어요.");
            foreach (var texture in textures)
            {
                if (texture is not null && Equals(name.GetValue(texture), expectedName) &&
                    Equals(width.GetValue(texture), expectedWidth) && Equals(height.GetValue(texture), expectedHeight) &&
                    Convert.ToInt32(format.GetValue(texture)) == 4)
                    return texture;
            }
            return null;
        }

        public void Copy(object target, Edit edit, byte[] pixels)
        {
            var patch = create.Invoke(new[] { (object)edit.Width, edit.Height, graphicsFormat.GetValue(target)!, creationFlags });
            try
            {
                var pinned = GCHandle.Alloc(pixels, GCHandleType.Pinned);
                try
                {
                    upload.Invoke(patch, new object[] { pinned.AddrOfPinnedObject(), pixels.Length });
                }
                finally
                {
                    pinned.Free();
                }
                apply.Invoke(patch, new object[] { false, true });
                copy.Invoke(null, new[] { patch, 0, 0, 0, 0, edit.Width, edit.Height, target, 0, 0, edit.X, edit.Y });
            }
            finally
            {
                destroy.Invoke(null, new[] { patch });
            }
        }
    }
}
