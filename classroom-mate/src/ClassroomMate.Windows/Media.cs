using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using ClassroomMate.Core;

namespace ClassroomMate.Windows;

public sealed class Media : IAsyncDisposable
{
    sealed class Worker(Process process, string key, IntPtr job)
    {
        public Process Process = process; public string Key = key; public IntPtr Job = job;
        public volatile string State = "starting"; public volatile string Code = "";
    }
    readonly Pairing pairing;
    readonly ServerClock clock;
    readonly Action<string> status;
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
    public static bool Available => File.Exists(Path.Combine(AppContext.BaseDirectory, "runtime", "python.exe"))
        && File.Exists(Path.Combine(AppContext.BaseDirectory, "media", "publisher.py"));
    public Media(Pairing pairing, ServerClock clock, Action<string> status)
    { this.pairing = pairing; this.clock = clock; this.status = status; loop = Loop(); }
    public JsonObject? Report() { lock (gate) return cameraState?.Copy(); }
    public Task Apply(JsonObject state)
    {
        lock (gate)
        {
            desired = state.Copy();
            if (!DateTimeOffset.TryParse(state.Text("valid_until"), out until)) until = DateTimeOffset.MinValue;
        }
        return Task.CompletedTask;
    }
    public async Task Stop()
    {
        lock (gate) { desired = null; until = DateTimeOffset.MinValue; }
        await serial.WaitAsync();
        try { await StopAll(); }
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
            await StopAll();
        }
        finally { serial.Release(); }
    }
    async Task StopAll()
    {
        // Remove each reference before disposal: a failed stop must not leave a disposed
        // Process in the registry or prevent the other authorized track from stopping.
        foreach (var track in workers.Keys.ToArray())
        {
            var worker = workers[track]; workers.Remove(track);
            await Stop(worker);
        }
        failures.Clear();
        lock (gate) { if (cameraState is not null) cameraState["state"] = "stopped"; }
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
    async Task Reconcile()
    {
        JsonObject? state; DateTimeOffset expiry;
        lock (gate) { state = desired?.Copy(); expiry = until; }
        var camera = state?["camera"] as JsonObject;
        bool enabled = state?.Flag("enabled") == true && expiry > clock.UtcNow && camera is not null && camera.Text("access_path") != "server_direct";
        foreach (var track in new[] { "video", "audio" })
        {
            string key = camera is null ? "" : $"{camera.Text("camera_id")}:{camera.Number("config_revision")}:{track}";
            bool wanted = enabled && state!.Flag(track);
            if (workers.TryGetValue(track, out var old) && (!wanted || old.Key != key))
            { workers.Remove(track); await Stop(old); }
            if (!wanted) { failures.Remove(track); continue; }
            if (failures.TryGetValue(track, out var failureKey) && failureKey == key) continue;
            if (!Available) { failures[track] = key; continue; }
            if (!workers.TryGetValue(track, out var worker))
            {
                try { worker = Start(state!, camera!, track, key, expiry); }
                catch { failures[track] = key; status("采集设备不可用 · 请检测设备后重新观看"); continue; }
                workers[track] = worker;
                failures.Remove(track);
            }
            if (worker.State == "failed" || worker.Process.HasExited)
            {
                workers.Remove(track); failures[track] = key;
                await Stop(worker);
                continue;
            }
            // Failed acquisition stays failed until the viewer restarts, avoiding camera-open retry storms.
            if (!worker.Process.HasExited)
                try { await worker.Process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(new { valid_seconds = Math.Max(0, (expiry - clock.UtcNow).TotalSeconds) })); }
                catch (IOException) { worker.State = "failed"; worker.Code = "MEDIA_PROCESS_EXITED"; }
        }
        if (camera is not null)
        {
            string captureState = !enabled ? "idle" : workers.TryGetValue("video", out var video) ?
                video.Process.HasExited ? "failed" : video.State == "connected" ? "connected" : video.State == "failed" ? "failed" : "idle" : "idle";
            var failed = workers.Values.FirstOrDefault(w => w.State == "failed" || w.Process.HasExited);
            if (failed is not null || failures.Count > 0) captureState = "failed";
            lock (gate) cameraState = new JsonObject { ["camera_id"] = camera.Text("camera_id"), ["config_revision"] = camera.Number("config_revision"),
                ["identifier"] = camera.Text("identifier"), ["state"] = captureState, ["audio_capable"] = camera.Flag("audio_capable"),
                ["error"] = failed?.Code ?? (failures.Count > 0 ? "CAPTURE_DEVICE_UNAVAILABLE" : "") };
            string text = captureState == "connected" ? (workers.TryGetValue("audio", out var audio) && audio.State == "connected"
                ? "正在采集视频和现场声音" : "正在采集视频 · 未采集现场声音") : captureState == "failed" ? "媒体采集失败 · 请检查设备" : "媒体采集已停止或正在连接";
            if (text != lastCaptureStatus) { lastCaptureStatus = text; status(text); }
        }
    }
    Worker Start(JsonObject state, JsonObject camera, string track, string key, DateTimeOffset expiry)
    {
        var path = (state["publish"] as JsonObject)?.Text(track) ?? "";
        Wire.Endpoint(Wire.Server(pairing.Server, pairing.Development), path);
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
        info.ArgumentList.Add("-I"); info.ArgumentList.Add(Path.Combine(AppContext.BaseDirectory, "media", "publisher.py"));
        var process = Process.Start(info) ?? throw new IOException("无法启动媒体组件");
        IntPtr job;
        try { job = ProcessJob.Attach(process); }
        catch { process.Kill(true); process.Dispose(); throw; }
        var worker = new Worker(process, key, job);
        // No credentials, SDP, camera URLs or native library stderr enter logs.
        _ = Task.Run(async () => { try { while (await process.StandardError.ReadLineAsync() is not null) { } } catch { } });
        _ = Task.Run(async () =>
        {
            try
            {
                while (await process.StandardOutput.ReadLineAsync() is { } line)
                {
                    if (line.Length > 2048) continue;
                    var message = Wire.Object(line);
                    worker.State = message.Text("state", "failed"); worker.Code = message.Text("code");
                    if (worker.State == "failed") status("媒体采集失败 · " + worker.Code);
                }
            }
            catch { worker.State = "failed"; worker.Code = "MEDIA_PROCESS_EXITED"; }
        });
        var init = new JsonObject { ["server"] = pairing.Server, ["device_id"] = pairing.DeviceId, ["credential"] = pairing.Credential,
            ["development"] = pairing.Development, ["track"] = track, ["offer_path"] = path,
            ["camera"] = camera.DeepClone(), ["native_device"] = native,
            ["ice_servers"] = state["ice_servers"]?.DeepClone(), ["valid_seconds"] = Math.Max(0, (expiry - clock.UtcNow).TotalSeconds) };
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
