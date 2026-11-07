using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using ClassroomMate.Core;

namespace ClassroomMate.Windows;

public sealed class Media : IAsyncDisposable
{
    sealed class Worker(Process process, string key, IntPtr job, bool localOnly)
    {
        public Process Process = process; public string Key = key; public IntPtr Job = job;
        public volatile string State = "starting"; public volatile string Code = "";
        public volatile byte[]? Preview;
        public bool LocalOnly { get; } = localOnly;
    }
    Pairing? pairing;
    readonly ServerClock clock;
    readonly Action<string> status;
    readonly Func<Preferences> selection;
    readonly object gate = new();
    readonly Dictionary<string, Worker> workers = [];
    readonly Dictionary<string, string> failures = [];
    readonly CancellationTokenSource lifetime = new();
    readonly SemaphoreSlim serial = new(1, 1);
    readonly Task loop;
    JsonObject? desired;
    DateTimeOffset until;
    JsonObject? cameraState;
    string lastCaptureStatus = "";
    volatile Worker? videoWorker;
    string? previewFailure;
    volatile bool previewRequested;
    public byte[]? Preview
    {
        get
        {
            lock (gate)
            {
                if (!previewRequested || videoWorker is not { } worker || worker.State == "failed") return null;
                if (worker.LocalOnly)
                    return worker.Key == PreviewKey(selection().CameraIdentifier) ? worker.Preview : null;
                if (desired?.Flag("enabled") != true || !desired.Flag("video") || until <= clock.UtcNow ||
                    desired["camera"] is not JsonObject camera || worker.Key != CaptureKey(camera, "video") ||
                    !CameraMatches(camera, selection())) return null;
                return worker.Preview;
            }
        }
    }
    public void SetPreview(bool enabled)
    {
        previewRequested = enabled;
        if (!enabled && videoWorker is { } worker) worker.Preview = null;
    }
    public static bool Available => File.Exists(Path.Combine(AppContext.BaseDirectory, "runtime", "python.exe"))
        && File.Exists(Path.Combine(AppContext.BaseDirectory, "media", "publisher.py"));
    public Media(ServerClock clock, Action<string> status, Func<Preferences> selection)
    { this.clock = clock; this.status = status; this.selection = selection; loop = Task.Run(Loop); }
    public JsonObject? Report() { lock (gate) return cameraState?.Copy(); }
    public Task Apply(Pairing source, JsonObject state)
    {
        lock (gate)
        {
            pairing = source;
            desired = state.Copy();
            if (!DateTimeOffset.TryParse(state.Text("valid_until"), out until)) until = DateTimeOffset.MinValue;
        }
        return Task.CompletedTask;
    }
    public Task StopRemote() => StopCapture(includeLocal: false);
    public Task Stop() => StopCapture(includeLocal: true);

    async Task StopCapture(bool includeLocal)
    {
        if (includeLocal) SetPreview(false);
        await serial.WaitAsync();
        try
        {
            lock (gate) { pairing = null; desired = null; until = DateTimeOffset.MinValue; }
            await StopWorkers(includeLocal);
        }
        finally { serial.Release(); }
    }
    public async Task CameraCommand(Command command)
    {
        if (command.Kind == "camera.start") return; // Never grants acquisition authority.
        await serial.WaitAsync();
        try
        {
            lock (gate)
            {
                if (desired?["camera"] is not JsonObject camera || camera.Text("camera_id") != command.Payload.Text("camera_id")
                    || camera.Number("config_revision") != command.Payload.Number("config_revision")) return;
                desired = null; until = DateTimeOffset.MinValue;
            }
            await StopWorkers(includeLocal: false);
        }
        finally { serial.Release(); }
    }
    async Task StopWorkers(bool includeLocal)
    {
        // Remove each reference before disposal: a failed stop must not leave a disposed
        // Process in the registry or prevent the other authorized track from stopping.
        foreach (var track in workers.Keys.ToArray())
        {
            var worker = workers[track];
            if (!includeLocal && worker.LocalOnly) continue;
            await RemoveWorker(track);
        }
        failures.Clear();
        if (includeLocal) previewFailure = null;
        lock (gate) cameraState = null;
        lastCaptureStatus = "";
    }
    async Task Loop()
    {
        while (!lifetime.IsCancellationRequested)
        {
            await serial.WaitAsync();
            try { await Reconcile(); }
            catch { status("媒体采集不可用 · 请检查设备与部署"); }
            finally { serial.Release(); }
            try { await Task.Delay(500, lifetime.Token); } catch (OperationCanceledException) { break; }
        }
    }
    static string CaptureKey(JsonObject camera, string track) =>
        $"{camera.Text("camera_id")}:{camera.Number("config_revision")}:{track}";

    static bool CameraMatches(JsonObject camera, Preferences local) =>
        camera.Text("source_kind") != "windows_device" || local.CameraIdentifier is null || local.CameraIdentifier == camera.Text("identifier");

    static string PreviewKey(string? identifier) => "preview:" + identifier;

    async Task RemoveWorker(string track)
    {
        if (track == "video") videoWorker = null;
        if (workers.Remove(track, out var worker)) await Stop(worker);
    }

    async Task Reconcile()
    {
        JsonObject? state; DateTimeOffset expiry; Pairing? source;
        lock (gate) { state = desired?.Copy(); expiry = until; source = pairing; }
        var camera = state?["camera"] as JsonObject;
        var local = selection();
        bool enabled = source is not null && state?.Flag("enabled") == true && expiry > clock.UtcNow
            && camera is not null && camera.Text("access_path") != "server_direct" && CameraMatches(camera, local);
        bool localPreview = previewRequested && !string.IsNullOrEmpty(local.CameraIdentifier);
        string localKey = PreviewKey(local.CameraIdentifier);
        if (!localPreview) previewFailure = null;
        bool microphoneMatches = camera?.Text("source_kind") != "windows_device" || local.MicrophoneIdentifier is null
            || local.MicrophoneIdentifier == camera.Text("microphone_identifier");

        foreach (var track in new[] { "video", "audio" })
        {
            bool remote = enabled && state!.Flag(track) && (track == "video" || microphoneMatches);
            var remoteKey = remote ? CaptureKey(camera!, track) : "";
            if (!remote) failures.Remove(track);
            if (workers.TryGetValue(track, out var current) && (current.State == "failed" || current.Process.HasExited))
            {
                if (current.LocalOnly) previewFailure = current.Key;
                else if (current.Key == remoteKey) failures[track] = remoteKey;
                await RemoveWorker(track);
            }
            bool publish = remote && failures.GetValueOrDefault(track) != remoteKey;
            bool preview = !publish && track == "video" && localPreview && previewFailure != localKey;
            var key = publish ? remoteKey : preview ? localKey : "";
            if (workers.TryGetValue(track, out var old) && old.Key != key) await RemoveWorker(track);
            if (!publish && !preview) continue;

            var workerExpiry = preview ? clock.UtcNow.AddSeconds(3) : expiry;
            if (!workers.TryGetValue(track, out var worker))
            {
                var selectedCamera = preview ? new JsonObject
                { ["source_kind"] = "windows_device", ["identifier"] = local.CameraIdentifier } : camera!;
                try
                {
                    if (!Available) throw new IOException("媒体运行库不可用");
                    worker = Start(publish ? source : null, state, selectedCamera, track, key, workerExpiry);
                    workers[track] = worker;
                    if (track == "video") videoWorker = worker;
                }
                catch
                {
                    if (preview) previewFailure = key;
                    else failures[track] = key;
                    continue;
                }
            }
            try
            {
                await worker.Process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(new
                {
                    valid_seconds = Math.Max(0, (workerExpiry - clock.UtcNow).TotalSeconds),
                    preview = track == "video" && previewRequested
                }));
            }
            catch (IOException) { worker.State = "failed"; worker.Code = "MEDIA_PROCESS_EXITED"; }
        }

        bool videoConnected = workers.TryGetValue("video", out var video) && !video.LocalOnly && video.State == "connected";
        bool audioConnected = workers.TryGetValue("audio", out var audio) && audio.State == "connected";
        string captureState = failures.Count > 0 && !videoConnected ? "failed" : videoConnected || audioConnected ? "connected" : "idle";
        lock (gate) cameraState = camera is null ? null : new JsonObject
        {
            ["camera_id"] = camera.Text("camera_id"), ["config_revision"] = camera.Number("config_revision"),
            ["identifier"] = camera.Text("identifier"), ["state"] = captureState, ["audio_capable"] = camera.Flag("audio_capable"),
            ["error"] = captureState == "failed" ? "CAPTURE_DEVICE_UNAVAILABLE" : ""
        };
        string text = videoConnected ? audioConnected ? "正在向网页传输画面和声音" : "正在向网页传输画面"
            : audioConnected ? "正在向网页传输声音"
            : video?.LocalOnly == true && video.Preview is not null ? "本机摄像头预览"
            : video is not null ? "正在打开摄像头…"
            : localPreview && previewFailure == localKey ? "无法打开摄像头 · 请检查占用及 Windows 摄像头权限"
            : failures.Count > 0 ? "远程采集失败 · 请检查设备与网络" : "未在采集音视频";
        if (text != lastCaptureStatus) { lastCaptureStatus = text; status(text); }
    }

    Worker Start(Pairing? source, JsonObject? state, JsonObject camera, string track, string key, DateTimeOffset expiry)
    {
        var path = (state?["publish"] as JsonObject)?.Text(track) ?? "";
        if (source is not null) Wire.Endpoint(Wire.Server(source.Server, source.Development), path);
        string? native = null;
        if (camera.Text("source_kind") == "windows_device")
        {
            var id = camera.Text(track == "video" ? "identifier" : "microphone_identifier");
            native = (track == "video" ? Devices.Cameras() : Devices.Microphones()).SingleOrDefault(d => d.Identifier == id)?.NativeName
                ?? throw new TerminalException("CAPTURE_DEVICE_MISSING", "所选采集设备不可用");
        }
        var info = new ProcessStartInfo(Path.Combine(AppContext.BaseDirectory, "runtime", "python.exe"))
        { UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new System.Text.UTF8Encoding(false), StandardOutputEncoding = System.Text.Encoding.UTF8 };
        info.ArgumentList.Add("-I"); info.ArgumentList.Add("-B");
        info.ArgumentList.Add(Path.Combine(AppContext.BaseDirectory, "media", "publisher.py"));
        var process = Process.Start(info) ?? throw new IOException("无法启动媒体组件");
        IntPtr job;
        try { job = ProcessJob.Attach(process); }
        catch { process.Kill(true); process.Dispose(); throw; }
        var worker = new Worker(process, key, job, source is null);
        // No credentials, SDP, camera URLs or native library stderr enter logs.
        _ = Task.Run(async () => { try { while (await process.StandardError.ReadLineAsync() is not null) { } } catch { } });
        _ = Task.Run(async () =>
        {
            try
            {
                while (await process.StandardOutput.ReadLineAsync() is { } line)
                {
                    const string prefix = "preview:";
                    if (track == "video" && line.StartsWith(prefix, StringComparison.Ordinal))
                    {
                        if (previewRequested && line.Length <= 65536)
                            try { worker.Preview = Convert.FromBase64String(line[prefix.Length..]); }
                            catch (FormatException) { }
                        continue;
                    }
                    if (line.Length > 2048) continue;
                    var message = Wire.Object(line);
                    worker.State = message.Text("state", "failed"); worker.Code = message.Text("code");
                    if (worker.State == "failed") status("媒体采集失败 · " + worker.Code);
                }
            }
            catch { worker.State = "failed"; worker.Code = "MEDIA_PROCESS_EXITED"; }
        });
        var init = new JsonObject { ["server"] = source?.Server, ["device_id"] = source?.DeviceId, ["credential"] = source?.Credential,
            ["development"] = source?.Development, ["track"] = track, ["offer_path"] = path, ["preview_only"] = source is null,
            ["camera"] = camera.DeepClone(), ["native_device"] = native,
            ["ice_servers"] = state?["ice_servers"]?.DeepClone(), ["valid_seconds"] = Math.Max(0, (expiry - clock.UtcNow).TotalSeconds),
            ["preview"] = track == "video" && previewRequested };
        try { process.StandardInput.WriteLine(init.ToJsonString()); process.StandardInput.Flush(); }
        catch { ProcessJob.Close(job); process.Dispose(); throw; }
        return worker;
    }
    static async Task Stop(Worker worker)
    {
        try
        {
            if (!worker.Process.HasExited)
            {
                worker.Process.StandardInput.Close();
                try { await worker.Process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(2)); }
                catch (TimeoutException) { worker.Process.Kill(true); await worker.Process.WaitForExitAsync(); }
            }
        }
        catch (Exception e) when (e is IOException or InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            // The job handle still owns every child. Closing it below forces termination
            // even if stdin or the process handle became unavailable during graceful stop.
        }
        finally { ProcessJob.Close(worker.Job); worker.Process.Dispose(); }
    }
    public async ValueTask DisposeAsync() { lifetime.Cancel(); await loop; await Stop(); lifetime.Dispose(); serial.Dispose(); }
}

// Child capture ends even if the tray process is terminated unexpectedly.
static class ProcessJob
{
    [StructLayout(LayoutKind.Sequential)] struct Basic { public long PerProcess, PerJob; public uint Flags; public UIntPtr Min, Max; public uint Active; public UIntPtr Affinity; public uint Priority, Scheduling; }
    [StructLayout(LayoutKind.Sequential)] struct Io { public ulong A, B, C, D, E, F; }
    [StructLayout(LayoutKind.Sequential)] struct Extended { public Basic Basic; public Io Io; public UIntPtr ProcessMemory, JobMemory, PeakProcess, PeakJob; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr attributes, string? name);
    [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job, int type, ref Extended value, int size);
    [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    public static IntPtr Attach(Process process)
    {
        var job = CreateJobObject(IntPtr.Zero, null);
        var value = new Extended { Basic = new Basic { Flags = 0x2000 } };
        if (job == IntPtr.Zero || !SetInformationJobObject(job, 9, ref value, Marshal.SizeOf<Extended>()) || !AssignProcessToJobObject(job, process.Handle))
        { if (job != IntPtr.Zero) CloseHandle(job); throw new IOException("无法绑定媒体进程生命周期"); }
        return job;
    }
    public static void Close(IntPtr job) { if (job != IntPtr.Zero) CloseHandle(job); }
}
