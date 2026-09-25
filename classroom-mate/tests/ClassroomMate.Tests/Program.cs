using ClassroomMate.Core;
using System.Text.Json.Nodes;

int passed = 0;
async Task Test(string title, Func<Task> test) { await test(); Console.WriteLine("PASS " + title); passed++; }
void Check(bool condition) { if (!condition) throw new Exception("断言失败"); }
JsonObject Message(string id, string kind, JsonObject payload, DateTimeOffset? expiry = null) => new()
{ ["type"] = "command", ["command_id"] = id, ["kind"] = kind, ["payload"] = payload, ["expires_at"] = (expiry ?? DateTimeOffset.UtcNow.AddMinutes(5)).ToString("O") };
JsonObject Broadcast() => new() { ["segments"] = new JsonArray(new JsonObject { ["index"] = 0, ["text"] = "请张三同学现在去扫地。", ["recipients"] = new JsonArray() }), ["display_seconds"] = 3, ["repeat_count"] = 1, ["gap_seconds"] = 0 };
string dir = Path.Combine(Path.GetTempPath(), "classroom-mate-tests-" + Guid.NewGuid()); Directory.CreateDirectory(dir);
try
{
    await Test("服务器地址与接口不允许携带凭据或越域", () =>
    {
        foreach (var url in new[] { "http://example.com", "https://u:p@example.com", "https://example.com/path", "file:///tmp/test" })
        { try { Wire.Server(url); throw new Exception("未拒绝 URL"); } catch (TerminalException) { } }
        Check(Wire.Server("http://127.0.0.1:8000", true).IsLoopback);
        try { Wire.Endpoint(Wire.Server("https://example.com"), "//evil.test/token"); throw new Exception("未拒绝跳转"); } catch (TerminalException) { }
        return Task.CompletedTask;
    });
    await Test("未知动作和命令参数被拒绝", () =>
    {
        foreach (var message in new[] { Message("a", "shell", new()), Message("a", "volume.set", new() { ["volume"] = 200 }), Message("a", "broadcast.stop", new()), Message("a", "device.test_speak", new() { ["text"] = "hello", ["path"] = "cmd.exe" }) })
        { try { Command.Parse(message); throw new Exception("未拒绝非法命令"); } catch (TerminalException) { } }
        return Task.CompletedTask;
    });
    await Test("过期命令不会产生副作用", async () =>
    {
        var fake = new Fake(); var ledger = new Ledger(Path.Combine(dir, "expired.json")); var results = new List<Receipt>();
        var execute = new Executor(fake, ledger, new());
        await execute.Handle(Message("expired", "device.test_speak", new() { ["text"] = "不可播报" }, DateTimeOffset.UtcNow.AddSeconds(-1)), r => { results.Add(r); return Task.CompletedTask; }, default);
        Check(fake.Spoken == 0 && results.Last().ErrorCode == "COMMAND_EXPIRED");
    });
    await Test("重复 ID 不重新执行，账本不保存学生原文", async () =>
    {
        var fake = new Fake(); var path = Path.Combine(dir, "dedup.json"); var ledger = new Ledger(path); var execute = new Executor(fake, ledger, new());
        var message = Message("once", "device.test_speak", new() { ["text"] = "原文姓名张三" });
        await execute.Handle(message, _ => Task.CompletedTask, default);
        await execute.Handle(message, _ => Task.CompletedTask, default);
        Check(fake.Spoken == 1 && !File.ReadAllText(path).Contains("张三"));
    });
    await Test("崩溃中的命令恢复为 unknown，不补播", () =>
    {
        var path = Path.Combine(dir, "crash.json"); var ledger = new Ledger(path);
        ledger.Reserve(Command.Parse(Message("crash", "device.test_speak", new() { ["text"] = "张三" })), DateTimeOffset.UtcNow);
        var recovered = new Ledger(path);
        Check(recovered.Find("crash")?.State == "unknown"); return Task.CompletedTask;
    });
    await Test("单任务广播忙碌时拒绝，不加入队列", async () =>
    {
        var fake = new Fake { HoldSpeech = true }; var execute = new Executor(fake, new Ledger(Path.Combine(dir, "busy.json")), new());
        using var cancel = new CancellationTokenSource(); var results = new List<Receipt>();
        var first = execute.Handle(Message("first", "broadcast.show", Broadcast()), _ => Task.CompletedTask, cancel.Token);
        await fake.Started.Task;
        await execute.Handle(Message("second", "broadcast.show", Broadcast()), r => { results.Add(r); return Task.CompletedTask; }, default);
        Check(results.Last().ErrorCode == "BUSY" && fake.Spoken == 1);
        cancel.Cancel(); await first;
    });
    await Test("清除旧目标不影响当前语音，停止目标只停止对应命令", async () =>
    {
        var fake = new Fake { HoldSpeech = true }; var execute = new Executor(fake, new Ledger(Path.Combine(dir, "target.json")), new());
        using var cancel = new CancellationTokenSource(); var first = execute.Handle(Message("target", "broadcast.show", Broadcast()), _ => Task.CompletedTask, cancel.Token);
        await fake.Started.Task;
        await execute.Handle(Message("oldstop", "broadcast.stop", new() { ["target_command_id"] = "old" }), _ => Task.CompletedTask, default);
        Check(!fake.SpeechCancelled);
        await execute.Handle(Message("stop", "broadcast.stop", new() { ["target_command_id"] = "target" }), _ => Task.CompletedTask, default);
        await Task.Delay(30); Check(fake.SpeechCancelled);
        cancel.Cancel(); await first;
    });
    await Test("断线取消显示和语音，回执不能误报已完成", async () =>
    {
        var fake = new Fake { HoldSpeech = true }; var execute = new Executor(fake, new Ledger(Path.Combine(dir, "cancel.json")), new());
        using var cancel = new CancellationTokenSource(); var results = new List<Receipt>();
        var first = execute.Handle(Message("cancel", "broadcast.show", Broadcast()), r => { results.Add(r); return Task.CompletedTask; }, cancel.Token);
        await fake.Started.Task; cancel.Cancel(); await first;
        Check(results.Last().State == "unknown" && fake.Cleared.Contains("cancel") && fake.SpeechCancelled);
    });
    await Test("服务端时间偏移可用于 TTL", () =>
    {
        var clock = new ServerClock(); clock.Sync("2040-01-01T08:00:00+08:00");
        Check(clock.Remaining(DateTimeOffset.Parse("2040-01-01T08:01:00+08:00")).TotalSeconds is > 58 and <= 60);
        return Task.CompletedTask;
    });
    await Test("无中文语音时仍显示，分别回报结果", async () =>
    {
        var fake = new Fake { NoSpeech = true }; var results = new List<Receipt>();
        var execute = new Executor(fake, new Ledger(Path.Combine(dir, "partial.json")), new());
        await execute.Handle(Message("partial", "broadcast.show", Broadcast()), r => { results.Add(r); return Task.CompletedTask; }, default);
        Check(results.Last() is { State: "failed", Display: "shown", Speak: "unavailable" });
        Check(results.Count(r => r.State != "started") == 1 && fake.Cleared.Contains("partial"));
    });
    await Test("恢复音量异常仍释放活动任务，只发送一次终结回执", async () =>
    {
        var fake = new Fake { RestoreFails = true }; var results = new List<Receipt>();
        var execute = new Executor(fake, new Ledger(Path.Combine(dir, "restore.json")), new());
        await execute.Handle(Message("restore", "broadcast.show", Broadcast()), r => { results.Add(r); return Task.CompletedTask; }, default);
        Check(results.Last().ErrorCode == "VOLUME_RESTORE_FAILED" && results.Count(r => r.State != "started") == 1);
        results.Clear();
        await execute.Handle(Message("afterrestore", "device.test_speak", new() { ["text"] = "测试" }), r => { results.Add(r); return Task.CompletedTask; }, default);
        Check(results.Last().State == "succeeded" && fake.VolumeBegins == 1);
    });
    await Test("进行中的广播拒绝临时音量设置", async () =>
    {
        var fake = new Fake { HoldSpeech = true }; var results = new List<Receipt>();
        var execute = new Executor(fake, new Ledger(Path.Combine(dir, "temporary-volume.json")), new());
        using var cancel = new CancellationTokenSource();
        var first = execute.Handle(Message("playing", "broadcast.show", Broadcast()), _ => Task.CompletedTask, cancel.Token);
        await fake.Started.Task;
        await execute.Handle(Message("volume", "volume.set", new() { ["volume"] = 40, ["restore_after_broadcast"] = true }), r => { results.Add(r); return Task.CompletedTask; }, default);
        Check(results.Last().ErrorCode == "BUSY" && fake.VolumeSets == 0);
        cancel.Cancel(); await first;
    });
    await Test("后续段成功不能覆盖先前显示或语音失败", async () =>
    {
        foreach (bool failDisplay in new[] { true, false })
        {
            var fake = new Fake { FailFirstDisplay = failDisplay, FailFirstSpeech = !failDisplay }; var results = new List<Receipt>();
            var execute = new Executor(fake, new Ledger(Path.Combine(dir, $"segments-{failDisplay}.json")), new());
            var payload = Broadcast();
            ((JsonArray)payload["segments"]!).Add(new JsonObject { ["index"] = 1, ["text"] = "请李四同学现在来办公室。", ["recipients"] = new JsonArray() });
            await execute.Handle(Message("segments", "broadcast.show", payload), r => { results.Add(r); return Task.CompletedTask; }, default);
            var outcome = results.Last();
            Check(fake.Shown == 2 && fake.SpeechAttempts == 2 && outcome.State == "failed");
            Check(failDisplay ? outcome.Display == "unavailable" && outcome.Speak == "spoken"
                : outcome.Display == "shown" && outcome.Speak == "unavailable");
        }
    });
    await Test("固定重复间隔超过有效期时拒绝，不先播报一部分", async () =>
    {
        var fake = new Fake(); var results = new List<Receipt>();
        var execute = new Executor(fake, new Ledger(Path.Combine(dir, "gap-expiry.json")), new());
        var payload = Broadcast(); payload["repeat_count"] = 2; payload["gap_seconds"] = 10;
        await execute.Handle(Message("gap-expiry", "broadcast.show", payload, DateTimeOffset.UtcNow.AddSeconds(5)), r => { results.Add(r); return Task.CompletedTask; }, default);
        Check(results.Last().ErrorCode == "COMMAND_TOO_SHORT" && fake.Shown == 0 && fake.Spoken == 0 && fake.VolumeBegins == 0);
    });
    await Test("真实 WebSocket 鉴权、回执、重复投递与撤销", () => SocketTest.Run(dir));
    Console.WriteLine($"{passed} tests passed");
}
finally { Directory.Delete(dir, true); }

sealed class Fake : IPlatform
{
    public int Spoken, Shown, SpeechAttempts, VolumeBegins, VolumeSets;
    public bool HoldSpeech, NoSpeech, RestoreFails, FailFirstDisplay, FailFirstSpeech;
    public bool SpeechCancelled; public List<string> Cleared = [];
    public TaskCompletionSource Started = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public Task Show(string id, string text, string? screen, CancellationToken token)
    {
        Shown++;
        if (FailFirstDisplay && Shown == 1) throw new TerminalException("DEVICE_OUTPUT_UNAVAILABLE", "屏幕暂时不可用");
        return Task.CompletedTask;
    }
    public Task Clear(string id) { Cleared.Add(id); return Task.CompletedTask; }
    public async Task Speak(string text, CancellationToken token) { SpeechAttempts++; if (NoSpeech || FailFirstSpeech && SpeechAttempts == 1) throw new TerminalException("CHINESE_TTS_UNAVAILABLE", "中文语音不可用"); Spoken++; Started.TrySetResult(); if (HoldSpeech) { try { await Task.Delay(Timeout.Infinite, token); } catch (OperationCanceledException) { SpeechCancelled = true; throw; } } }
    public Task Configure(JsonObject payload) => Task.CompletedTask;
    public Task SetVolume(JsonObject payload, int ceiling) { VolumeSets++; return Task.CompletedTask; }
    public Task<IDisposable?> BroadcastVolume(int? volume, int ceiling) { VolumeBegins++; return Task.FromResult<IDisposable?>(RestoreFails ? new BrokenRestore() : null); }
    public Task CameraCommand(Command command) => Task.CompletedTask;
    sealed class BrokenRestore : IDisposable { public void Dispose() => throw new IOException("模拟音箱拔出"); }
}
