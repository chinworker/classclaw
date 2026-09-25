using System.Text.Json.Nodes;

namespace ClassroomMate.Core;

public interface IPlatform
{
    Task Show(string id, string text, string? screen, CancellationToken token);
    Task Clear(string id);
    Task Speak(string text, CancellationToken token);
    Task Configure(JsonObject payload);
    Task SetVolume(JsonObject payload, int ceiling);
    Task<IDisposable?> BroadcastVolume(int? volume, int ceiling);
    Task CameraCommand(Command command);
}

public sealed class Executor(IPlatform platform, Ledger ledger, ServerClock clock)
{
    sealed class Active(string id, CancellationToken token) : IDisposable
    {
        public string Id = id;
        public CancellationTokenSource Speech = CancellationTokenSource.CreateLinkedTokenSource(token);
        public volatile bool Cleared;
        public volatile bool Stopped;
        public void Dispose() => Speech.Dispose();
    }
    readonly object gate = new();
    Active? active;
    public int VolumeCeiling { get; set; } = 100;
    public void ClearFromWindow(string id) { lock (gate) if (active?.Id == id) active.Cleared = true; }

    public async Task Handle(JsonObject message, Func<Receipt, Task> send, CancellationToken connection)
    {
        Command command;
        try { command = Command.Parse(message); }
        catch (Exception e) when (e is TerminalException or System.Text.Json.JsonException or InvalidOperationException or FormatException)
        {
            var id = message.Text("command_id");
            if (id.Length is > 0 and <= 36) await send(new(id, "failed", ErrorCode: "PROTOCOL_ERROR", ErrorMessage: "拒绝未知或无效命令"));
            return;
        }
        Receipt? duplicate;
        try { duplicate = ledger.Reserve(command, clock.UtcNow); }
        catch { await send(new(command.Id, "failed", ErrorCode: "LEDGER_UNAVAILABLE", ErrorMessage: "无法可靠保存去重标记，未执行")); return; }
        if (duplicate is not null) { await send(duplicate); return; }
        var remaining = clock.Remaining(command.ExpiresAt);
        if (remaining <= TimeSpan.Zero || remaining > TimeSpan.FromHours(2))
        { await Finish(new(command.Id, "failed", ErrorCode: "COMMAND_EXPIRED", ErrorMessage: "命令已过期或期限无效"), send); return; }
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(connection);
        timeout.CancelAfter(remaining);
        var token = timeout.Token;
        try
        {
            await send(new(command.Id, "started"));
            token.ThrowIfCancellationRequested();
            switch (command.Kind)
            {
                case "broadcast.show": case "device.test_display": case "device.test_speak":
                    await Broadcast(command, send, token); return;
                case "broadcast.stop": case "broadcast.clear":
                    var target = command.Payload.Text("target_command_id");
                    lock (gate)
                    {
                        if (active?.Id == target)
                        {
                            if (command.Kind == "broadcast.stop") { active.Stopped = true; active.Speech.Cancel(); }
                            else active.Cleared = true;
                        }
                    }
                    if (command.Kind == "broadcast.clear") await platform.Clear(target);
                    break;
                case "volume.set":
                    lock (gate) if (active is not null && command.Payload.Flag("restore_after_broadcast"))
                        throw new TerminalException("BUSY", "临时音量只可在下一次广播开始前设置");
                    await platform.SetVolume(command.Payload, VolumeCeiling); break;
                case "device.configure":
                    lock (gate) if (active is not null) throw new TerminalException("BUSY", "正在播报，请结束后再更换输出设备");
                    await platform.Configure(command.Payload); break;
                case "camera.start": case "camera.stop": await platform.CameraCommand(command); break;
            }
            await Finish(new(command.Id, "succeeded"), send);
        }
        catch (OperationCanceledException) { await Finish(new(command.Id, "unknown", ErrorCode: "INTERRUPTED", ErrorMessage: "连接中断或命令到期，未重播"), send); }
        catch (TerminalException e) { await Finish(new(command.Id, "failed", ErrorCode: e.Code, ErrorMessage: e.Message), send); }
        catch { await Finish(new(command.Id, "failed", ErrorCode: "DEVICE_ERROR", ErrorMessage: "设备执行失败，请在托盘检查设备"), send); }
    }

    async Task Broadcast(Command command, Func<Receipt, Task> send, CancellationToken token)
    {
        Active run;
        lock (gate)
        {
            if (active is not null) throw new TerminalException("BUSY", "已有广播正在执行；本次未排队");
            active = run = new(command.Id, token);
        }
        var payload = command.Payload;
        bool displayEnabled = command.Kind != "device.test_speak", speechEnabled = command.Kind != "device.test_display";
        var texts = command.Kind == "broadcast.show" ? ((JsonArray)payload["segments"]!).Select(r => ((JsonObject)r!).Text("text")).ToArray()
            : new[] { payload.Text("text") };
        int seconds = command.Kind == "broadcast.show" ? payload.Number("display_seconds") : 3;
        int repeats = command.Kind == "broadcast.show" ? payload.Number("repeat_count") : 1;
        int gap = payload.Number("gap_seconds");
        string display = displayEnabled ? "pending" : "unavailable", speak = speechEnabled ? "pending" : "skipped";
        IDisposable? volume = null;
        bool displayFailed = false, speechFailed = false;
        Receipt outcome = new(command.Id, "failed", display, speak);
        try
        {
            int minimumSegmentSeconds = Math.Max(displayEnabled ? seconds : 0, speechEnabled ? (repeats - 1) * gap : 0);
            if (TimeSpan.FromSeconds(texts.Length * minimumSegmentSeconds) >= clock.Remaining(command.ExpiresAt))
                throw new TerminalException("COMMAND_TOO_SHORT", "剩余有效期不足以完成显示或重复播报间隔，未执行");
            if (command.Kind == "broadcast.show") volume = await platform.BroadcastVolume(payload["volume"]?.GetValue<int>(), VolumeCeiling);
            foreach (var text in texts)
            {
                token.ThrowIfCancellationRequested();
                if (displayEnabled && !run.Cleared)
                {
                    try { await platform.Show(command.Id, text, payload.Text("target_screen"), token); if (!displayFailed) display = "shown"; }
                    catch (TerminalException) { display = "unavailable"; displayFailed = true; }
                }
                var hold = Task.Delay(displayEnabled ? TimeSpan.FromSeconds(seconds) : TimeSpan.Zero, token);
                if (speechEnabled && !run.Stopped)
                {
                    try
                    {
                        for (var i = 0; i < repeats; i++)
                        {
                            await platform.Speak(text, run.Speech.Token);
                            if (i < repeats - 1) await Task.Delay(TimeSpan.FromSeconds(gap), run.Speech.Token);
                        }
                        if (!speechFailed) speak = "spoken";
                    }
                    catch (OperationCanceledException) when (!token.IsCancellationRequested) { speak = "skipped"; }
                    catch (TerminalException) { speak = "unavailable"; speechFailed = true; }
                }
                await hold;
            }
            if (run.Cleared) display = "cleared";
            if (run.Stopped) speak = "skipped";
            bool failed = displayFailed || speechFailed;
            outcome = new(command.Id, failed ? "failed" : "succeeded", display, speak,
                failed ? "OUTPUT_UNAVAILABLE" : "", failed ? "显示或中文语音不可用，请检查终端" : "");
        }
        catch (OperationCanceledException)
        { outcome = new(command.Id, "unknown", display, speak == "spoken" && texts.Length == 1 ? speak : "skipped", "INTERRUPTED", "广播中断，未重播"); }
        catch (TerminalException e) { outcome = new(command.Id, "failed", display, speak, e.Code, e.Message); }
        catch { outcome = new(command.Id, "failed", display, speak, "DEVICE_ERROR", "设备执行失败，请在托盘检查设备"); }
        finally
        {
            try { await platform.Clear(command.Id); }
            catch { outcome = outcome with { State = outcome.State == "unknown" ? "unknown" : "failed", ErrorCode = "DISPLAY_CLEAR_FAILED", ErrorMessage = "显示窗口未正常清理，请检查终端" }; }
            try { volume?.Dispose(); }
            catch
            {
                outcome = outcome with { State = outcome.State == "unknown" ? "unknown" : "failed", ErrorCode = "VOLUME_RESTORE_FAILED", ErrorMessage = "音量恢复失败，请检查终端" };
            }
            finally { lock (gate) { active = null; run.Dispose(); } }
        }
        await Finish(outcome, send);
    }
    async Task Finish(Receipt receipt, Func<Receipt, Task> send)
    {
        ledger.Complete(receipt);
        try { await send(receipt); } catch (Exception e) when (e is IOException or OperationCanceledException or System.Net.WebSockets.WebSocketException) { }
    }
}
