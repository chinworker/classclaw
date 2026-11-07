using System.Text.Json.Nodes;
using ClassroomMate.Core;

namespace ClassroomMate.Windows;

public sealed class Platform : IPlatform
{
    readonly Control ui;
    readonly Storage storage;
    readonly Audio audio = new();
    volatile Preferences preferences;
    BroadcastWindow? window;
    public Media? Media { get; set; }
    public Action<string>? Cleared { get; set; }
    public volatile bool SessionLocked;
    public Preferences Selection => preferences;
    public Platform(Control ui, Storage storage)
    {
        this.ui = ui; this.storage = storage; preferences = storage.LoadPreferences();
        audio.Select(preferences.AudioOutputName);
    }
    public Task Invoke(Action action)
    {
        if (!ui.InvokeRequired) { action(); return Task.CompletedTask; }
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        ui.BeginInvoke(new Action(() => { try { action(); done.SetResult(); } catch (Exception e) { done.SetException(e); } }));
        return done.Task;
    }
    public JsonObject Report(bool includeLocal = false)
    {
        var cameras = Devices.Cameras(); var microphones = Devices.Microphones(); var speakers = Devices.Speakers();
        var (volume, mute) = audio.Read();
        var chinese = audio.HasChineseVoice;
        var selection = preferences;
        var screens = Devices.Screens();
        bool playbackAvailable = speakers.Any(s => s.Name == selection.AudioOutputName);
        JsonArray Items(InputDevice[] items) => new(items.Select(d => (JsonNode)new JsonObject { ["identifier"] = d.Identifier, ["name"] = d.Name }).ToArray());
        var inventory = new JsonObject { ["cameras"] = Items(cameras), ["microphones"] = Items(microphones), ["speakers"] = Items(speakers) };
        if (includeLocal)
            inventory["local_capture"] = new JsonObject { ["camera"] = selection.CameraIdentifier, ["microphone"] = selection.MicrophoneIdentifier };
        return new JsonObject
        {
            ["capabilities"] = new JsonObject { ["display"] = !SessionLocked && screens.Contains(selection.DisplayName),
                ["speak"] = chinese && playbackAvailable, ["chinese_tts"] = chinese,
                ["volume_control"] = playbackAvailable, ["capture"] = ClassroomMate.Windows.Media.Available,
                ["displays"] = new JsonArray(screens.Select(s => (JsonNode)JsonValue.Create(s)!).ToArray()) },
            ["inventory"] = inventory,
            ["display_name"] = selection.DisplayName, ["audio_output_name"] = selection.AudioOutputName,
            ["volume_level"] = volume, ["muted"] = mute, ["camera_state"] = Media?.Report()
        };
    }
    public Task Configure(JsonObject payload) => Invoke(() => SaveSelection(preferences with
    {
        DisplayName = payload.Text("display_name", preferences.DisplayName),
        AudioOutputName = payload.Text("audio_output_name", preferences.AudioOutputName)
    }));
    public void SaveSelection(Preferences next)
    {
        var previous = preferences;
        if (next.DisplayName != previous.DisplayName && next.DisplayName.Length > 0 && !Devices.Screens().Contains(next.DisplayName))
            throw new TerminalException("DEVICE_OUTPUT_UNAVAILABLE", "所选屏幕不可用");
        if (next.AudioOutputName != previous.AudioOutputName && next.AudioOutputName.Length > 0 && !Devices.Speakers().Any(s => s.Name == next.AudioOutputName))
            throw new TerminalException("DEVICE_OUTPUT_UNAVAILABLE", "所选扬声器不可用");
        if (next.CameraIdentifier != previous.CameraIdentifier && !string.IsNullOrEmpty(next.CameraIdentifier) &&
            !Devices.Cameras().Any(d => d.Identifier == next.CameraIdentifier))
            throw new TerminalException("CAPTURE_DEVICE_MISSING", "所选摄像头不可用");
        if (next.MicrophoneIdentifier != previous.MicrophoneIdentifier && !string.IsNullOrEmpty(next.MicrophoneIdentifier) &&
            !Devices.Microphones().Any(d => d.Identifier == next.MicrophoneIdentifier))
            throw new TerminalException("CAPTURE_DEVICE_MISSING", "所选麦克风不可用");
        storage.SavePreferences(next);
        if (next.AudioOutputName != previous.AudioOutputName) audio.Select(next.AudioOutputName);
        preferences = next;
    }
    public Task Show(string id, string text, string? screen, CancellationToken token) => Invoke(() =>
    {
        token.ThrowIfCancellationRequested();
        if (SessionLocked) throw new TerminalException("SCREEN_LOCKED", "屏幕已锁定，无法显示");
        var target = Screen.AllScreens.SingleOrDefault(s => s.DeviceName == (string.IsNullOrEmpty(screen) ? preferences.DisplayName : screen))
            ?? throw new TerminalException("DEVICE_OUTPUT_UNAVAILABLE", "请在 Classroom Mate 设置中选择教室显示屏");
        CloseBroadcast();
        var created = new BroadcastWindow(id, text, target.Bounds);
        window = created;
        created.FormClosed += (_, _) =>
        {
            if (window == created) window = null;
            if (!created.SuppressCloseCallback) Cleared?.Invoke(id);
        };
        created.Show();
        if (!created.IsDisposed) { created.Activate(); created.Refresh(); }
    });
    public Task Clear(string id) => Invoke(() =>
    {
        if (window?.CommandId == id) CloseBroadcast();
    });
    void CloseBroadcast()
    {
        if (window is not { } previous) return;
        window = null;
        previous.SuppressCloseCallback = true;
        previous.Close();
        previous.Dispose();
    }
    public async Task Speak(string text, CancellationToken token)
    {
        try { await audio.Speak(text, token); }
        catch (OperationCanceledException) { throw; }
        catch (TerminalException) { throw; }
        catch { throw new TerminalException("TTS_FAILED", "中文语音或所选输出设备不可用"); }
    }
    public Task SetVolume(JsonObject payload, int ceiling)
    { audio.Set(payload["volume"]?.GetValue<int>(), payload["mute"]?.GetValue<bool>(), payload.Flag("restore_after_broadcast"), ceiling); return Task.CompletedTask; }
    public Task<IDisposable?> BroadcastVolume(int? volume, int ceiling) => Task.FromResult(audio.Begin(volume, ceiling));
    public Task CameraCommand(Command command) => Media?.CameraCommand(command) ?? Task.CompletedTask;
    public void RestoreVolume() => audio.RestorePending();
}

sealed class BroadcastWindow : Form
{
    public string CommandId { get; }
    public bool SuppressCloseCallback;
    public BroadcastWindow(string id, string text, Rectangle bounds)
    {
        CommandId = id; Text = "ClassClaw 点名广播"; Icon = Program.AppIcon; StartPosition = FormStartPosition.Manual;
        FormBorderStyle = FormBorderStyle.None; Bounds = bounds; BackColor = Color.FromArgb(20, 31, 48);
        TopMost = true; ShowInTaskbar = true; KeyPreview = true; AutoScaleMode = AutoScaleMode.Dpi;
        var close = new Button { Text = "关闭显示 ×", Dock = DockStyle.Bottom, Height = 52, FlatStyle = FlatStyle.Flat,
            ForeColor = Color.White, BackColor = Color.FromArgb(38, 55, 77) };
        close.Click += (_, _) => Close();
        var label = new Label { Text = text, Dock = DockStyle.Fill, ForeColor = Color.White, Padding = new Padding(50),
            TextAlign = ContentAlignment.MiddleCenter, UseMnemonic = false, Font = new Font("Microsoft YaHei UI", 48, FontStyle.Bold) };
        Controls.Add(label); Controls.Add(close);
        void Fit()
        {
            var area = new Size(Math.Max(100, label.ClientSize.Width - 100), Math.Max(80, label.ClientSize.Height - 100));
            for (int points = 120; points >= 12; points -= 2)
            {
                var candidate = new Font("Microsoft YaHei UI", points, FontStyle.Bold);
                var measured = TextRenderer.MeasureText(text, candidate, new Size(area.Width, int.MaxValue), TextFormatFlags.WordBreak | TextFormatFlags.NoPrefix);
                if ((measured.Height <= area.Height && measured.Width <= area.Width) || points == 12)
                { var previous = label.Font; label.Font = candidate; previous.Dispose(); break; }
                candidate.Dispose();
            }
        }
        Shown += (_, _) => Fit(); Resize += (_, _) => Fit();
        Disposed += (_, _) => label.Font.Dispose();
        KeyDown += (_, e) => { if (e.KeyCode == Keys.Escape) Close(); };
    }
}
