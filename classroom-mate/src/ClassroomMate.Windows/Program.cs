using ClassroomMate.Core;
using Microsoft.Win32;
using System.Text.Json.Nodes;

namespace ClassroomMate.Windows;

static class Program
{
    [STAThread]
    static void Main()
    {
        using var singleton = new Mutex(true, "Local\\ClassClaw.ClassroomMate." + Devices.StableId(Environment.UserName)[..12], out var created);
        if (!created) { MessageBox.Show("Classroom Mate 已在托盘运行。", "ClassClaw"); return; }
        ApplicationConfiguration.Initialize();
        try { Application.Run(new Tray()); }
        catch { MessageBox.Show("无法安全启动。请检查本用户应用数据目录权限；如果凭据或去重账本损坏，请先在网页撤销设备后重新配置。", "Classroom Mate"); }
    }
}

sealed class Tray : ApplicationContext
{
    readonly Form dispatcher = new();
    readonly NotifyIcon icon;
    readonly ToolStripMenuItem pause;
    readonly Storage storage = new();
    readonly DeviceApi api = new();
    readonly Platform platform;
    readonly ServerClock clock = new();
    Executor executor;
    Pairing? pairing;
    CancellationTokenSource? running;
    Task? connectionTask;
    SettingsWindow? settings;
    string status = "未配对 · 请从网页获取配对码";
    bool exiting;
    bool changing;
    readonly SemaphoreSlim stopGate = new(1, 1);
    public Tray()
    {
        _ = dispatcher.Handle;
        platform = new(dispatcher, storage);
        executor = new(platform, new Ledger(storage.LedgerPath), clock);
        platform.Cleared = executor.ClearFromWindow;
        var menu = new ContextMenuStrip();
        menu.Items.Add("设置与设备状态", null, (_, _) => ShowSettings());
        pause = new ToolStripMenuItem("暂停连接与采集", null, async (_, _) => await Toggle());
        menu.Items.Add(pause);
        menu.Items.Add("退出", null, async (_, _) => await Shutdown());
        icon = new NotifyIcon { Icon = SystemIcons.Application, Text = "Classroom Mate", ContextMenuStrip = menu, Visible = true };
        icon.DoubleClick += (_, _) => ShowSettings();
        pairing = storage.LoadPairing();
        SystemEvents.SessionSwitch += SessionChanged;
        SystemEvents.PowerModeChanged += PowerChanged;
        if (pairing is null) ShowSettings(); else Start();
    }
    async void SessionChanged(object sender, SessionSwitchEventArgs e)
    {
        if (e.Reason is SessionSwitchReason.SessionLock or SessionSwitchReason.SessionLogoff or SessionSwitchReason.ConsoleDisconnect or SessionSwitchReason.RemoteDisconnect)
        {
            platform.SessionLocked = true;
            await platform.Invoke(() => _ = SuspendForSession());
        }
        else if (e.Reason is SessionSwitchReason.SessionUnlock or SessionSwitchReason.ConsoleConnect or SessionSwitchReason.RemoteConnect)
            platform.SessionLocked = false; // Resume explicitly from tray; no surprise playback after login.
    }
    async void PowerChanged(object sender, PowerModeChangedEventArgs e)
    { if (e.Mode == PowerModes.Suspend) await platform.Invoke(() => _ = SuspendForSession()); }
    async Task SuspendForSession() { await Stop(); SetStatus("已暂停：会话锁定或系统休眠，请在托盘恢复"); }
    void SetStatus(string value)
    {
        if (dispatcher.IsDisposed) return;
        dispatcher.BeginInvoke(new Action(() =>
        {
            status = value; icon.Text = value.Length > 55 ? value[..55] : value;
            settings?.SetStatus(value);
        }));
    }
    void Start()
    {
        if (pairing is null || running is not null || exiting || platform.SessionLocked) return;
        running = new();
        platform.Media = new Media(pairing, clock, SetStatus);
        var connection = new Connection(pairing, clock, executor, platform.Report, platform.Media.Apply, platform.Media.Stop, SetStatus);
        connectionTask = Task.Run(() => connection.Run(running.Token));
        pause.Text = "暂停连接与采集";
    }
    async Task Stop()
    {
        await stopGate.WaitAsync();
        try
        {
            running?.Cancel();
            if (connectionTask is not null) try { await connectionTask; } catch { }
            if (platform.Media is not null) await platform.Media.DisposeAsync();
            platform.Media = null; connectionTask = null; running?.Dispose(); running = null;
            platform.RestoreVolume(); pause.Text = "恢复连接";
        }
        finally { stopGate.Release(); }
    }
    async Task Toggle()
    {
        if (changing) return; changing = true;
        try
        {
            if (running is null) Start();
            else { await Stop(); SetStatus("已暂停 · 无采集和播报"); }
        }
        finally { changing = false; }
    }
    void ShowSettings()
    {
        if (settings is null || settings.IsDisposed)
        {
            settings = new SettingsWindow(pairing?.Server ?? "", status, Pair, platform.Report);
            settings.FormClosing += (_, e) => { if (!exiting) { e.Cancel = true; settings.Hide(); } };
        }
        settings.Show(); settings.Activate();
    }
    async Task Pair(string url, string code, bool development)
    {
        if (changing) throw new TerminalException("BUSY", "正在切换连接，请稍后重试");
        changing = true;
        try
        {
            await Stop();
            var server = Wire.Server(url, development);
            var report = platform.Report();
            var result = await api.Post(server, "/api/v1/classroom/device/pair", new JsonObject {
                ["pairing_code"] = code.Trim(), ["protocol_version"] = 1, ["app_version"] = "0.1.0",
                ["os_version"] = Environment.OSVersion.VersionString, ["device_name"] = Environment.MachineName,
                ["capabilities"] = report["capabilities"]!.DeepClone(), ["inventory"] = report["inventory"]!.DeepClone()
            }, CancellationToken.None);
            var next = new Pairing(server.ToString(), result.Text("device_id"), result.Text("class_id"), result.Text("credential"), development);
            if (next.DeviceId.Length is < 1 or > 36 || next.Credential.Length < 8) throw new TerminalException("PROTOCOL_ERROR", "配对响应不完整");
            try { storage.SavePairing(next); }
            catch { throw new TerminalException("PAIR_SAVE_FAILED", "设备已兑换但未能加密保存。请在网页撤销设备、修复本机目录权限后重新配对。"); }
            pairing = next; clock.Sync(result.Text("server_time"));
            if (!Storage.StartupEnabled) Storage.StartupEnabled = true;
            Start(); SetStatus("配对成功 · 正在连接");
        }
        finally { changing = false; }
    }
    async Task Shutdown()
    {
        if (exiting) return; exiting = true;
        icon.Visible = false;
        SystemEvents.SessionSwitch -= SessionChanged; SystemEvents.PowerModeChanged -= PowerChanged;
        await Stop();
        settings?.Dispose(); icon.Dispose(); api.Dispose(); dispatcher.Dispose(); ExitThread();
    }
}

sealed class SettingsWindow : Form
{
    readonly Label status = new() { AutoSize = true, MaximumSize = new Size(590, 0), Padding = new Padding(0, 8, 0, 12) };
    public SettingsWindow(string server, string message, Func<string, string, bool, Task> pair, Func<JsonObject> report)
    {
        Text = "Classroom Mate · ClassClaw 教室终端"; ClientSize = new(650, 600); MinimumSize = new(600, 560);
        StartPosition = FormStartPosition.CenterScreen; Font = new Font("Microsoft YaHei UI", 10); AutoScaleMode = AutoScaleMode.Dpi;
        var panel = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoScroll = true, Padding = new Padding(24) };
        var address = new TextBox { Width = 570, Text = server, PlaceholderText = "https://class.example.com" };
        var code = new TextBox { Width = 280, PlaceholderText = "网页中的一次性配对码", MaxLength = 32 };
        var development = new CheckBox { Text = "开发模式（仅本机 HTTP）", AutoSize = true };
        var startup = new CheckBox { Text = "当前用户登录 Windows 后启动到托盘", AutoSize = true, Checked = Storage.StartupEnabled };
        startup.CheckedChanged += (_, _) => { try { Storage.StartupEnabled = startup.Checked; } catch { SetStatus("无法更改登录启动项，请检查用户权限"); } };
        var button = new Button { Text = "配对教室终端", Width = 170, Height = 40 };
        button.Click += async (_, _) =>
        {
            button.Enabled = false;
            try { await pair(address.Text, code.Text, development.Checked); code.Clear(); startup.Checked = Storage.StartupEnabled; SetStatus("配对成功。请在网页选择屏幕、音箱和摄像头。"); }
            catch (TerminalException e) { SetStatus(e.Message); }
            catch { SetStatus("连接失败，请检查服务器地址、证书、网络和配对码。"); }
            finally { button.Enabled = true; }
        };
        var inventory = new TextBox { Multiline = true, ReadOnly = true, Width = 570, Height = 200, ScrollBars = ScrollBars.Vertical };
        var refresh = new Button { Text = "检测设备", Width = 170, Height = 36 };
        refresh.Click += (_, _) =>
        {
            try
            {
                var data = report();
                inventory.Text = System.Text.Json.JsonSerializer.Serialize(new { capabilities = data["capabilities"], inventory = data["inventory"] }, new System.Text.Json.JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping });
            }
            catch { inventory.Text = "设备检测失败，请检查 Windows 设备管理器与摄像头/麦克风权限。"; }
        };
        panel.Controls.AddRange([new Label { Text = "Classroom Mate", Font = new Font(Font, FontStyle.Bold), AutoSize = true }, status,
            new Label { Text = "服务器根地址", AutoSize = true }, address, code, development, button, startup, refresh, inventory,
            new Label { Text = "关闭此窗口继续驻留托盘。暂停/退出可停止连接与采集。\n摄像头与音箱由网页选择；这里只检测设备。", AutoSize = true }]);
        Controls.Add(panel); SetStatus(message);
    }
    public void SetStatus(string value) => status.Text = value;
}
