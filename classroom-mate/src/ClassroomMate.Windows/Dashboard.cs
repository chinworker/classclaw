using ClassroomMate.Core;

namespace ClassroomMate.Windows;

sealed class Dashboard : Form
{
    readonly Label connection = new() { AutoSize = true, Font = new Font("Microsoft YaHei UI", 13, FontStyle.Bold) };
    readonly Label server = new() { AutoSize = true, ForeColor = Color.FromArgb(73, 91, 112) };
    readonly Label capture = new() { AutoSize = true, ForeColor = Color.FromArgb(73, 91, 112) };
    readonly Label feedback = new() { AutoSize = true };
    readonly Button toggle = ActionButton("设置并配对");
    readonly PictureBox picture = new() { Dock = DockStyle.Fill, SizeMode = PictureBoxSizeMode.Zoom, BackColor = Color.FromArgb(19, 34, 52) };
    readonly Label empty = new()
    {
        Text = "等待本机摄像头画面\n请在设置中选择视频来源",
        ForeColor = Color.FromArgb(204, 220, 238), BackColor = Color.FromArgb(19, 34, 52),
        Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter
    };
    readonly System.Windows.Forms.Timer refresh = new() { Interval = 350 };
    byte[]? lastFrame;
    bool paired;
    public bool PreviewVisible => Visible && WindowState != FormWindowState.Minimized;

    public Dashboard(Action settings, Func<Task> toggleConnection, Action web, Func<string, Task<Receipt>> test,
        Func<byte[]?> preview, Action<bool> requestPreview)
    {
        Text = "Classroom Mate";
        Icon = Program.AppIcon;
        Font = new Font("Microsoft YaHei UI", 10);
        AutoScaleMode = AutoScaleMode.Dpi;
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(800, 560);
        MinimumSize = new Size(650, 470);
        BackColor = Color.FromArgb(241, 245, 251);

        var body = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 4, Padding = new Padding(20) };
        body.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        body.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        body.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        body.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        var heading = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false };
        heading.Controls.AddRange([
            new Label { Text = "CLASSROOM MATE  /  教室终端", AutoSize = true, ForeColor = Color.FromArgb(0, 106, 114) },
            connection, server
        ]);
        body.Controls.Add(heading, 0, 0);

        var actions = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, Margin = new Padding(0, 10, 0, 10) };
        var setup = ActionButton("设置");
        setup.Click += (_, _) => settings();
        var portal = ActionButton("打开网页");
        portal.Click += (_, _) => { try { web(); } catch { feedback.Text = "无法打开网页，请检查系统默认浏览器。"; } };
        toggle.Click += async (_, _) =>
        {
            toggle.Enabled = false;
            try { if (paired) await toggleConnection(); else settings(); }
            finally { toggle.Enabled = true; }
        };
        Button Test(string title, string kind)
        {
            var button = ActionButton(title);
            button.Click += async (_, _) =>
            {
                button.Enabled = false;
                feedback.Text = "正在进行本机测试…";
                try
                {
                    var result = await test(kind);
                    feedback.Text = result.State == "succeeded" ? "本机测试完成" : result.ErrorMessage;
                }
                catch (TerminalException error) { feedback.Text = error.Message; }
                catch { feedback.Text = "本机测试失败，请检查设备和设置。"; }
                finally { button.Enabled = true; }
            };
            return button;
        }
        actions.Controls.AddRange([toggle, setup, portal, Test("显示测试", "device.test_display"), Test("播报测试", "device.test_speak")]);
        body.Controls.Add(actions, 0, 1);

        var frame = new Panel { Dock = DockStyle.Fill, Margin = Padding.Empty };
        frame.Controls.Add(picture);
        frame.Controls.Add(empty);
        empty.BringToFront();
        body.Controls.Add(frame, 0, 2);
        var footer = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false };
        footer.Controls.AddRange([capture, feedback]);
        body.Controls.Add(footer, 0, 3);
        Controls.Add(body);

        refresh.Tick += (_, _) => ShowFrame(preview());
        void SyncPreview()
        {
            bool enabled = PreviewVisible;
            requestPreview(enabled);
            refresh.Enabled = enabled;
            if (!enabled) ClearPreview();
        }
        VisibleChanged += (_, _) => SyncPreview();
        Resize += (_, _) => SyncPreview();
        Disposed += (_, _) => { refresh.Dispose(); picture.Image?.Dispose(); };
    }

    static Button ActionButton(string text) => new() { Text = text, AutoSize = true, Height = 36, Padding = new Padding(8, 0, 8, 0) };

    public void UpdateStatus(string status, string address, bool isPaired, bool running, string media)
    {
        paired = isPaired;
        connection.Text = status;
        server.Text = address;
        capture.Text = "监控预览 · " + media;
        toggle.Text = !paired ? "设置并配对" : running ? "暂停连接" : "恢复连接";
    }

    public void ClearPreview() => ShowFrame(null);

    void ShowFrame(byte[]? frame)
    {
        if (ReferenceEquals(frame, lastFrame)) return;
        lastFrame = frame;
        Image? image = null;
        if (frame is not null)
        {
            try
            {
                using var stream = new MemoryStream(frame, writable: false);
                using var decoded = Image.FromStream(stream);
                image = new Bitmap(decoded);
            }
            catch (ArgumentException) { }
        }
        var previous = picture.Image;
        picture.Image = image;
        empty.Visible = image is null;
        previous?.Dispose();
    }
}
