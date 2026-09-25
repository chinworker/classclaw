using System.Text.Json.Nodes;
using ClassroomMate.Core;

namespace ClassroomMate.Windows;

public sealed class Platform : IPlatform
{
    readonly Control ui;
    readonly Storage storage;
    readonly Audio audio = new();
    Preferences preferences;
    BroadcastWindow? window;
    public Media? Media { get; set; }
    public Action<string>? Cleared { get; set; }
    public bool SessionLocked { get; set; }
    public Platform(Control ui, Storage storage)
    {
        this.ui = ui; this.storage = storage; preferences = storage.LoadPreferences();
        if (!string.IsNullOrEmpty(preferences.AudioOutputName)) try { audio.Select(preferences.AudioOutputName); } catch { }
    }
    public Task Invoke(Action action)
    {
        if (!ui.InvokeRequired) { action(); return Task.CompletedTask; }
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        ui.BeginInvoke(new Action(() => { try { action(); done.SetResult(); } catch (Exception e) { done.SetException(e); } }));
        return done.Task;
    }
    public JsonObject Report()
    {
        var cameras = Devices.Cameras(); var microphones = Devices.Microphones(); var speakers = Devices.Speakers();
        var (volume, mute) = audio.Read();
        var chinese = audio.HasChineseVoice;
        JsonArray Items(InputDevice[] items) => new(items.Select(d => (JsonNode)new JsonObject { ["identifier"] = d.Identifier, ["name"] = d.Name }).ToArray());
        return new JsonObject
        {
            ["capabilities"] = new JsonObject { ["display"] = !SessionLocked, ["speak"] = chinese, ["chinese_tts"] = chinese,
                ["volume_control"] = speakers.Length > 0, ["capture"] = ClassroomMate.Windows.Media.Available,
                ["displays"] = new JsonArray(Devices.Screens().Select(s => (JsonNode)JsonValue.Create(s)!).ToArray()) },
            ["inventory"] = new JsonObject { ["cameras"] = Items(cameras), ["microphones"] = Items(microphones), ["speakers"] = Items(speakers) },
            ["display_name"] = preferences.DisplayName, ["audio_output_name"] = preferences.AudioOutputName,
            ["volume_level"] = volume, ["muted"] = mute, ["camera_state"] = Media?.Report()
        };
    }
    public async Task Configure(JsonObject payload)
    {
        var display = payload.Text("display_name", preferences.DisplayName);
        var speaker = payload.Text("audio_output_name", preferences.AudioOutputName);
        if (display.Length > 0 && !Devices.Screens().Contains(display)) throw new TerminalException("DEVICE_OUTPUT_UNAVAILABLE", "所选屏幕不可用");
        if (speaker.Length > 0 && !Devices.Speakers().Any(s => s.Name == speaker)) throw new TerminalException("DEVICE_OUTPUT_UNAVAILABLE", "所选扬声器不可用");
        var next = new Preferences(display, speaker);
        storage.SavePreferences(next);
        if (speaker.Length > 0) audio.Select(speaker);
        preferences = next; await Task.CompletedTask;
    }
    public Task Show(string id, string text, string? screen, CancellationToken token) => Invoke(() =>
    {
        token.ThrowIfCancellationRequested();
        if (SessionLocked) throw new TerminalException("SCREEN_LOCKED", "屏幕已锁定，无法显示");
        var target = Screen.AllScreens.SingleOrDefault(s => s.DeviceName == (string.IsNullOrEmpty(screen) ? preferences.DisplayName : screen))
            ?? throw new TerminalException("DEVICE_OUTPUT_UNAVAILABLE", "请在网页选择教室显示屏");
        if (window is not null) { window.SuppressCloseCallback = true; window.Close(); window.Dispose(); }
        window = new BroadcastWindow(id, text, target.Bounds);
        var created = window;
        window.FormClosed += (_, _) => { if (!created.SuppressCloseCallback) Cleared?.Invoke(id); };
        window.Show(); window.Refresh();
    });
    public Task Clear(string id) => Invoke(() =>
    {
        if (window?.CommandId != id) return;
        window.SuppressCloseCallback = true; window.Close(); window.Dispose(); window = null;
    });
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
        CommandId = id; Text = "ClassClaw 点名广播"; StartPosition = FormStartPosition.Manual;
        FormBorderStyle = FormBorderStyle.None; Bounds = bounds; BackColor = Color.FromArgb(20, 31, 48);
        ShowInTaskbar = true; KeyPreview = true; AutoScaleMode = AutoScaleMode.Dpi;
        var close = new Button { Text = "关闭显示 ×", Dock = DockStyle.Bottom, Height = 52, FlatStyle = FlatStyle.Flat,
            ForeColor = Color.White, BackColor = Color.FromArgb(38, 55, 77) };
        close.Click += (_, _) => Close();
        var label = new Label { Text = text, Dock = DockStyle.Fill, ForeColor = Color.White, Padding = new Padding(50),
            TextAlign = ContentAlignment.MiddleCenter, Font = new Font("Microsoft YaHei UI", text.Length > 160 ? 30 : text.Length > 80 ? 42 : 56, FontStyle.Bold) };
        Controls.Add(label); Controls.Add(close);
        void Fit()
        {
            var area = new Size(Math.Max(100, label.ClientSize.Width - 100), Math.Max(80, label.ClientSize.Height - 100));
            for (int points = 56; points >= 12; points -= 2)
            {
                var candidate = new Font("Microsoft YaHei UI", points, FontStyle.Bold);
                var measured = TextRenderer.MeasureText(text, candidate, new Size(area.Width, int.MaxValue), TextFormatFlags.WordBreak);
                if (measured.Height <= area.Height || points == 12)
                { var previous = label.Font; label.Font = candidate; previous.Dispose(); break; }
                candidate.Dispose();
            }
        }
        Shown += (_, _) => Fit(); Resize += (_, _) => Fit();
        KeyDown += (_, e) => { if (e.KeyCode == Keys.Escape) Close(); };
        // Normal foreground window, never an always-on-top or locked-screen overlay.
    }
}
