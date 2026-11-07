using System.Diagnostics;
using ClassroomMate.Core;
using Microsoft.Win32;
using System.Text.Json.Nodes;

namespace ClassroomMate.Windows;

static class Program
{
    public const string DefaultServer = "https://claw.dickhead.top";
    public static readonly Version BuildVersion = typeof(Program).Assembly.GetName().Version!;
    public static readonly string Version = BuildVersion.ToString();
    public static readonly Icon AppIcon = Icon.ExtractAssociatedIcon(Environment.ProcessPath!) ?? SystemIcons.Application;

    [STAThread]
    static void Main(string[] args)
    {
        var name = "Local\\ClassClaw.ClassroomMate." + Devices.StableId(Environment.UserName)[..12];
        try
        {
            using var singleton = new Mutex(true, name, out var created);
            using var show = new EventWaitHandle(false, EventResetMode.AutoReset, name + ".Show");
            bool trayOnly = args.Contains("--tray");
            if (!created) { if (!trayOnly) show.Set(); return; }
            ApplicationConfiguration.Initialize();
            var tray = new Tray(trayOnly, show);
            Application.Run(tray);
            if (tray.UpdateInstaller is string installer)
            {
                singleton.ReleaseMutex();
                OpenInstaller(installer);
            }
        }
        catch
        {
            MessageBox.Show("无法安全启动。请确认当前账号可写入应用数据目录，并退出已用其他权限启动的终端。",
                "Classroom Mate · 启动失败");
        }
    }

    static void OpenInstaller(string installer)
    {
        try { Process.Start(new ProcessStartInfo(installer) { UseShellExecute = true }); }
        catch (System.ComponentModel.Win32Exception)
        {
            MessageBox.Show("安装程序未启动。可以重新打开 Classroom Mate 并再次下载安装。", "Classroom Mate · 版本更新");
        }
    }
}

sealed class Tray : ApplicationContext
{
    readonly Form dispatcher = new();
    readonly NotifyIcon icon;
    readonly ToolStripMenuItem pause;
    readonly Storage storage = new();
    readonly DeviceApi api = new();
    readonly UpdateClient updates = new(new Uri(Program.DefaultServer));
    readonly Platform platform;
    readonly Media media;
    bool capturePaused;
    readonly ServerClock clock = new();
    readonly Executor executor;
    Pairing? pairing;
    CancellationTokenSource? running;
    Task? connectionTask;
    SettingsWindow? settings;
    Dashboard? dashboard;
    readonly RegisteredWaitHandle showRequest;
    Connection? connection;
    CancellationTokenSource? localTest;
    Task? localTestTask;
    string mediaStatus = "未在采集音视频";
    string status = "未配对 · 请从网页获取配对码";
    bool exiting;
    bool changing;
    readonly SemaphoreSlim stopGate = new(1, 1);
    public string? UpdateInstaller { get; private set; }
    public Tray(bool trayOnly, EventWaitHandle show)
    {
        _ = dispatcher.Handle;
        platform = new(dispatcher, storage);
        media = new Media(clock, SetMediaStatus, () => platform.Selection);
        platform.Media = media;
        executor = new(platform, new Ledger(storage.LedgerPath), clock);
        platform.Cleared = executor.ClearFromWindow;
        var menu = new ContextMenuStrip();
        menu.Items.Add("打开主界面", null, (_, _) => ShowDashboard());
        menu.Items.Add("设置", null, (_, _) => ShowSettings());
        pause = new ToolStripMenuItem("暂停连接与采集", null, async (_, _) => await Toggle());
        menu.Items.Add(pause);
        menu.Items.Add("退出", null, async (_, _) => await Shutdown());
        icon = new NotifyIcon { Icon = Program.AppIcon, Text = "Classroom Mate", ContextMenuStrip = menu, Visible = true };
        icon.DoubleClick += (_, _) => ShowDashboard();
        pairing = storage.LoadPairing();
        SystemEvents.SessionSwitch += SessionChanged;
        SystemEvents.PowerModeChanged += PowerChanged;
        showRequest = ThreadPool.RegisterWaitForSingleObject(show, (_, _) => Post(ShowDashboard), null, Timeout.Infinite, false);
        if (pairing is not null) Start();
        if (!trayOnly) ShowDashboard();
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
    void Post(Action action)
    {
        if (dispatcher.IsDisposed || exiting) return;
        try { dispatcher.BeginInvoke(() => { if (!exiting) action(); }); }
        catch (InvalidOperationException) { }
    }
    void SetStatus(string value) => Post(() =>
    {
        status = value;
        icon.Text = value.Length > 55 ? value[..55] : value;
        UpdateDashboard();
        settings?.SetStatus(value);
    });
    void SetMediaStatus(string value) => Post(() =>
    {
        mediaStatus = value;
        UpdateDashboard();
    });
    void UpdateDashboard() => dashboard?.UpdateStatus(status, pairing?.Server ?? Program.DefaultServer, pairing is not null,
        running is not null && connectionTask?.IsCompleted != true, mediaStatus);
    void Start()
    {
        if (pairing is null || running is not null || exiting || platform.SessionLocked) return;
        running = new();
        capturePaused = false;
        SetPreview(dashboard?.PreviewVisible == true);
        var source = pairing;
        var client = new Connection(source, clock, executor, () => platform.Report(includeLocal: true),
            state => media.Apply(source, state), media.StopRemote, SetStatus, Program.Version);
        connection = client;
        var token = running.Token;
        connectionTask = Task.Run(() => client.Run(token));
        pause.Text = "暂停连接与采集";
        UpdateDashboard();
    }
    async Task Stop()
    {
        await stopGate.WaitAsync();
        try
        {
            localTest?.Cancel();
            running?.Cancel();
            capturePaused = true;
            media.SetPreview(false);
            dashboard?.ClearPreview();
            if (localTestTask is not null) await localTestTask;
            if (connectionTask is not null) try { await connectionTask; } catch { }
            await media.Stop();
            connection = null; connectionTask = null; running?.Dispose(); running = null;
            platform.RestoreVolume(); pause.Text = "恢复连接";
            mediaStatus = "未在采集音视频";
            UpdateDashboard();
        }
        finally { stopGate.Release(); }
    }
    async Task Toggle()
    {
        if (changing) return; changing = true;
        try
        {
            if (pairing is null) { ShowSettings(); return; }
            if (platform.SessionLocked) { SetStatus("会话已锁定 · 解锁后再恢复"); return; }
            if (running is null || connectionTask?.IsCompleted == true)
            { if (running is not null) await Stop(); Start(); }
            else { await Stop(); SetStatus("已暂停 · 无采集和播报"); }
        }
        finally { changing = false; }
    }
    void SetPreview(bool visible) =>
        media.SetPreview(visible && !exiting && !platform.SessionLocked && (!capturePaused || pairing is null));

    void ShowDashboard()
    {
        if (dashboard is null || dashboard.IsDisposed)
        {
            dashboard = new Dashboard(ShowSettings, Toggle, OpenWeb, TestOutput,
                () => platform.SessionLocked ? null : media.Preview, SetPreview);
            dashboard.FormClosing += (_, e) => { if (!exiting) { e.Cancel = true; dashboard.Hide(); } };
        }
        UpdateDashboard();
        dashboard.Show();
        if (dashboard.WindowState == FormWindowState.Minimized) dashboard.WindowState = FormWindowState.Normal;
        dashboard.Activate();
        SetPreview(dashboard.PreviewVisible);
    }
    void OpenWeb()
    {
        var url = new Uri(Wire.Server(pairing?.Server ?? Program.DefaultServer, pairing?.Development ?? false), "/app/");
        Process.Start(new ProcessStartInfo(url.ToString()) { UseShellExecute = true });
    }
    async Task<Receipt> TestOutput(string kind)
    {
        if (platform.SessionLocked || exiting) throw new TerminalException("SCREEN_LOCKED", "请在解锁后的会话中测试");
        if (localTest is not null) throw new TerminalException("BUSY", "本机测试正在进行");
        using var operation = new CancellationTokenSource();
        localTest = operation;
        try
        {
            var command = new JsonObject { ["type"] = "command", ["command_id"] = Guid.NewGuid().ToString(),
                ["kind"] = kind, ["expires_at"] = clock.UtcNow.AddSeconds(45).ToString("O"),
                ["payload"] = new JsonObject { ["text"] = "Classroom Mate 教室终端测试。" } };
            Receipt? result = null;
            var execution = executor.Handle(command, receipt => { result = receipt; return Task.CompletedTask; }, operation.Token);
            localTestTask = execution;
            await execution;
            return result ?? throw new TerminalException("DEVICE_ERROR", "本机测试未完成");
        }
        finally { localTest = null; localTestTask = null; }
    }
    async Task SaveSelection(Preferences selection)
    {
        await executor.ConfigureOutput(() => { platform.SaveSelection(selection); return Task.CompletedTask; });
        connection?.RequestReport();
    }
    void ShowSettings()
    {
        if (settings is null || settings.IsDisposed)
        {
            settings = new SettingsWindow(pairing?.Server ?? Program.DefaultServer, pairing?.Development ?? false,
                status, Pair, SaveSelection, () => platform.Selection, updates.Latest, InstallUpdate);
            settings.FormClosing += (_, e) => { if (!exiting) { e.Cancel = true; settings.Hide(); } };
        }
        settings.Show(); settings.Activate();
    }
    async Task InstallUpdate(CancellationToken cancellation)
    {
        if (changing || exiting) throw new TerminalException("BUSY", "终端正在切换连接，请稍后重试。");
        changing = true;
        try
        {
            var release = await updates.Latest(cancellation);
            if (release.Version <= Program.BuildVersion)
                throw new TerminalException("UP_TO_DATE", "当前已是最新版。");
            var installer = await updates.Download(release, Path.Combine(Storage.Root, "updates"), cancellation);
            cancellation.ThrowIfCancellationRequested();
            if (exiting) return;
            await Shutdown(installer);
        }
        finally { changing = false; }
    }
    async Task Pair(string url, string code, bool development)
    {
        if (changing) throw new TerminalException("BUSY", "正在切换连接，请稍后重试");
        changing = true;
        try
        {
            url = url.Trim();
            if (!url.Contains("://", StringComparison.Ordinal)) url = "https://" + url;
            var server = Wire.Server(url, development);
            var report = platform.Report();
            var result = await api.Post(server, "/api/v1/classroom/device/pair", new JsonObject {
                ["pairing_code"] = code.Trim(), ["protocol_version"] = 1, ["app_version"] = Program.Version,
                ["os_version"] = Environment.OSVersion.VersionString, ["device_name"] = Environment.MachineName,
                ["capabilities"] = report["capabilities"]!.DeepClone(), ["inventory"] = report["inventory"]!.DeepClone()
            }, CancellationToken.None);
            var next = Pairing.FromResponse(server, result, development);
            try { storage.SavePairing(next); }
            catch { throw new TerminalException("PAIR_SAVE_FAILED", "设备已兑换但未能加密保存。请在网页撤销设备、修复本机目录权限后重新配对。"); }
            await Stop();
            pairing = next; clock.Sync(result.Text("server_time"));
            bool startupFailed = false;
            try { if (!Storage.StartupEnabled) Storage.StartupEnabled = true; }
            catch (Exception exception) when (exception is UnauthorizedAccessException or System.Security.SecurityException or IOException)
            { startupFailed = true; }
            Start();
            SetStatus(startupFailed ? "配对成功 · 正在连接；无法启用登录自启，请检查本用户注册表权限" : "配对成功 · 正在连接");
        }
        finally { changing = false; }
    }
    async Task Shutdown(string? installer = null)
    {
        if (exiting) return; exiting = true;
        icon.Visible = false;
        SystemEvents.SessionSwitch -= SessionChanged; SystemEvents.PowerModeChanged -= PowerChanged;
        showRequest.Unregister(null);
        await Stop();
        await media.DisposeAsync();
        settings?.Dispose(); dashboard?.Dispose(); icon.Dispose(); api.Dispose(); updates.Dispose(); dispatcher.Dispose();
        UpdateInstaller = installer;
        ExitThread();
    }
}
