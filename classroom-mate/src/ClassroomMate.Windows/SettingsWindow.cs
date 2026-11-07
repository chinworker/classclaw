using ClassroomMate.Core;

namespace ClassroomMate.Windows;

sealed class SettingsWindow : Form
{
    sealed record Option(string? Value, string Name)
    {
        public override string ToString() => Name;
    }

    readonly Label status = new() { AutoSize = true, MaximumSize = new Size(560, 0) };
    readonly Label feedback = new() { AutoSize = true, MaximumSize = new Size(560, 0) };
    readonly ComboBox camera = Picker();
    readonly ComboBox microphone = Picker();
    readonly ComboBox speaker = Picker();
    readonly ComboBox display = Picker();

    public SettingsWindow(string server, bool localHttp, string message, Func<string, string, bool, Task> pair,
        Func<Preferences, Task> save, Func<Preferences> preferences,
        Func<CancellationToken, Task<UpdateRelease>> checkUpdate, Func<CancellationToken, Task> installUpdate)
    {
        Text = "Classroom Mate · 设置";
        Icon = Program.AppIcon;
        ClientSize = new Size(640, 580);
        MinimumSize = new Size(590, 510);
        StartPosition = FormStartPosition.CenterScreen;
        Font = new Font("Microsoft YaHei UI", 10);
        AutoScaleMode = AutoScaleMode.Dpi;
        var tabs = new TabControl { Dock = DockStyle.Fill };
        var connection = Panel();
        var devices = Panel();
        tabs.TabPages.Add(new TabPage("服务器与配对") { Controls = { connection } });
        tabs.TabPages.Add(new TabPage("音视频与播放设备") { Controls = { devices } });
        tabs.TabPages.Add(new TabPage("版本更新") { Controls = { UpdatePanel(checkUpdate, installUpdate) } });
        Controls.Add(tabs);

        var address = new TextBox { Width = 490, Text = server, PlaceholderText = Program.DefaultServer };
        var code = new TextBox { Width = 230, PlaceholderText = "网页生成的 4 位数字码", MaxLength = 9 };
        var development = new CheckBox { Text = "开发模式（仅本机 HTTP）", AutoSize = true, Checked = localHttp };
        var startup = new CheckBox { Text = "当前用户登录 Windows 后启动到托盘", AutoSize = true, Checked = Storage.StartupEnabled };
        startup.Click += (_, _) =>
        {
            try { Storage.StartupEnabled = startup.Checked; }
            catch { startup.Checked = Storage.StartupEnabled; status.Text = "无法更改登录启动项，请检查用户权限。"; }
        };
        var pairButton = new Button { Text = "配对教室终端", Width = 160, Height = 38 };
        pairButton.Click += async (_, _) =>
        {
            pairButton.Enabled = false;
            try { await pair(address.Text, code.Text, development.Checked); code.Clear(); startup.Checked = Storage.StartupEnabled; }
            catch (TerminalException error) { SetStatus(error.Message); }
            catch { SetStatus("连接失败，请检查服务器地址、证书、网络和配对码。"); }
            finally { pairButton.Enabled = true; }
        };
        connection.Controls.AddRange([Label("服务器根地址"), address, Label("一次性配对码"), code, development,
            pairButton, startup, status]);

        var detect = new Button { Text = "刷新设备", Width = 130, Height = 38 };
        var saveButton = new Button { Text = "保存设备选择", Width = 160, Height = 38 };
        var actions = new FlowLayoutPanel { AutoSize = true, WrapContents = false };
        actions.Controls.AddRange([detect, saveButton]);
        detect.Click += (_, _) => RefreshDevices(ReadSelection());
        saveButton.Click += async (_, _) =>
        {
            saveButton.Enabled = false;
            try
            {
                var next = ReadSelection();
                bool captureChanged = next.CameraIdentifier != preferences().CameraIdentifier ||
                    next.MicrophoneIdentifier != preferences().MicrophoneIdentifier;
                await save(next);
                feedback.Text = captureChanged
                    ? "已保存。主界面预览与网页监控会切换到新来源。"
                    : "设备选择已保存。";
            }
            catch (TerminalException error) { feedback.Text = error.Message; }
            catch { feedback.Text = "无法保存设备设置，请检查本机应用数据目录。"; }
            finally { saveButton.Enabled = true; }
        };
        devices.Controls.AddRange([
            Label("视频来源"), camera, Label("现场声音来源"), microphone,
            Label("播报扬声器"), speaker, Label("广播显示屏"), display,
            actions, feedback, Label("主界面显示所选摄像头。网页打开实时监控时按需传输画面及可用声音。")
        ]);
        tabs.SelectedIndexChanged += (_, _) =>
        {
            if (tabs.SelectedIndex == 1 && camera.Items.Count == 0) RefreshDevices(preferences());
        };
        SetStatus(message);
    }

    static ComboBox Picker() => new() { Width = 490, DropDownStyle = ComboBoxStyle.DropDownList };
    static Label Label(string text) => new() { Text = text, AutoSize = true, Margin = new Padding(3, 8, 3, 2) };
    static FlowLayoutPanel Panel() => new()
    {
        Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false,
        AutoScroll = true, Padding = new Padding(20, 12, 20, 12)
    };

    FlowLayoutPanel UpdatePanel(Func<CancellationToken, Task<UpdateRelease>> check, Func<CancellationToken, Task> install)
    {
        var panel = Panel();
        var info = new Label { AutoSize = true, MaximumSize = new Size(550, 0) };
        var checkButton = new Button { Text = "检查更新", Width = 130, Height = 38 };
        var installButton = new Button { Text = "下载并安装", Width = 145, Height = 38, Enabled = false };
        checkButton.Click += async (_, _) =>
        {
            checkButton.Enabled = installButton.Enabled = false;
            info.Text = "正在从 ClassClaw 服务器检查更新…";
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(15));
            try
            {
                var release = await check(timeout.Token);
                if (IsDisposed) return;
                installButton.Enabled = release.Version > Program.BuildVersion;
                info.Text = installButton.Enabled ? $"发现新版 {release.Version}。" : "当前已是最新版。";
            }
            catch { if (!IsDisposed) info.Text = "无法检查更新，请确认网络连接后重试。"; }
            finally { if (!IsDisposed) checkButton.Enabled = true; }
        };
        installButton.Click += async (_, _) =>
        {
            checkButton.Enabled = installButton.Enabled = false;
            info.Text = "正在下载安装包。校验通过后，终端会退出并打开安装向导。";
            using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(10));
            try { await install(timeout.Token); }
            catch (TerminalException error) { if (!IsDisposed) info.Text = error.Message; }
            catch { if (!IsDisposed) info.Text = "无法下载安装包，请检查网络或磁盘空间后重试。"; }
            finally { if (!IsDisposed) checkButton.Enabled = true; }
        };
        var buttons = new FlowLayoutPanel { AutoSize = true, WrapContents = false };
        buttons.Controls.AddRange([checkButton, installButton]);
        panel.Controls.AddRange([Label($"当前版本 {Program.Version}"), buttons, info]);
        return panel;
    }

    static string? Value(ComboBox box) => (box.SelectedItem as Option)?.Value;
    Preferences ReadSelection() => new(Value(display) ?? "", Value(speaker) ?? "", Value(camera), Value(microphone));

    void RefreshDevices(Preferences selected)
    {
        try
        {
            Fill(camera, Devices.Cameras().Select(row => new Option(row.Identifier, row.Name)), selected.CameraIdentifier, true);
            Fill(microphone, Devices.Microphones().Select(row => new Option(row.Identifier, row.Name)), selected.MicrophoneIdentifier, true);
            Fill(speaker, Devices.Speakers().Select(row => new Option(row.Name, row.Name)), selected.AudioOutputName, false);
            Fill(display, Screen.AllScreens.Take(20).Select(screen => new Option(screen.DeviceName,
                $"{(screen.Primary ? "主显示屏" : "外接显示屏")} · {screen.Bounds.Width}×{screen.Bounds.Height} · {screen.DeviceName}")),
                selected.DisplayName, false);
            feedback.Text = "已刷新本机设备。";
        }
        catch { feedback.Text = "设备检测失败，请检查 Windows 设备管理器及摄像头、麦克风权限。"; }
    }

    static void Fill(ComboBox box, IEnumerable<Option> available, string? selected, bool capture)
    {
        var options = new List<Option> { new("", "不选择") };
        if (capture && selected is null) options.Add(new(null, "未设置（如有旧登记则沿用）"));
        options.AddRange(available);
        if (!options.Any(item => item.Value == selected))
            options.Add(new(selected, $"已保存的设备当前不可用 · {selected}"));
        box.Items.Clear();
        box.Items.AddRange(options.Cast<object>().ToArray());
        box.SelectedItem = options.First(item => item.Value == selected);
    }

    public void SetStatus(string message) => status.Text = message;
}
