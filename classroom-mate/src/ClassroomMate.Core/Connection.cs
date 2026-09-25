using System.Net.Http.Json;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace ClassroomMate.Core;

public sealed record Pairing(string Server, string DeviceId, string ClassId, string Credential, bool Development = false);
public sealed class DeviceApi : IDisposable
{
    readonly HttpClient http = new(new HttpClientHandler { AllowAutoRedirect = false, UseProxy = false }) { Timeout = TimeSpan.FromSeconds(30) };
    public async Task<JsonObject> Post(Uri server, string path, JsonObject body, CancellationToken token)
    {
        using var response = await http.PostAsJsonAsync(Wire.Endpoint(server, path), body, token);
        var data = Wire.Object(await response.Content.ReadAsStringAsync(token));
        if (!response.IsSuccessStatusCode || !data.Flag("success"))
        {
            var error = data["error"] as JsonObject;
            throw new TerminalException(error?.Text("code") ?? "HTTP_ERROR", error?.Text("message") ?? "服务器请求失败");
        }
        return (data["data"] as JsonObject)?.Copy() ?? throw new TerminalException("PROTOCOL_ERROR", "响应缺少 data");
    }
    public void Dispose() => http.Dispose();
}

public sealed class Connection(Pairing pairing, ServerClock clock, Executor executor,
    Func<JsonObject> report, Func<JsonObject, Task> media, Func<Task> stopMedia, Action<string> status)
{
    readonly SemaphoreSlim sendGate = new(1, 1);
    public async Task Run(CancellationToken lifetime)
    {
        int failures = 0;
        while (!lifetime.IsCancellationRequested)
        {
            using var socket = new ClientWebSocket();
            socket.Options.KeepAliveInterval = TimeSpan.FromSeconds(15);
            socket.Options.Proxy = null;
            using var connection = CancellationTokenSource.CreateLinkedTokenSource(lifetime);
            var tasks = new List<Task>();
            try
            {
                status("正在连接服务器…");
                var server = Wire.Server(pairing.Server, pairing.Development);
                var uri = new UriBuilder(Wire.Endpoint(server, "/api/v1/classroom/device-channel")) { Scheme = server.Scheme == "https" ? "wss" : "ws" };
                using (var connectTimeout = CancellationTokenSource.CreateLinkedTokenSource(lifetime))
                { connectTimeout.CancelAfter(TimeSpan.FromSeconds(20)); await socket.ConnectAsync(uri.Uri, connectTimeout.Token); }
                async Task Send(object value)
                {
                    var bytes = JsonSerializer.SerializeToUtf8Bytes(value, Wire.Json);
                    await sendGate.WaitAsync(connection.Token);
                    try { await socket.SendAsync(bytes.AsMemory(), WebSocketMessageType.Text, true, connection.Token); }
                    finally { sendGate.Release(); }
                }
                var hello = report();
                hello["type"] = "hello"; hello["device_id"] = pairing.DeviceId; hello["credential"] = pairing.Credential;
                hello["protocol_version"] = 1; hello["app_version"] = "0.1.0"; hello["os_version"] = Environment.OSVersion.VersionString;
                hello.Remove("camera_state");
                await Send(hello);
                JsonObject welcome;
                using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(connection.Token))
                { deadline.CancelAfter(TimeSpan.FromSeconds(20)); welcome = await Receive(socket, deadline.Token); }
                Error(welcome);
                if (welcome.Text("type") != "welcome" || welcome.Number("protocol_version") != 1)
                    throw new TerminalException("DEVICE_PROTOCOL_UNSUPPORTED", "服务器协议版本不兼容");
                clock.Sync(welcome.Text("server_time"));
                await ApplyMedia(welcome["media"] as JsonObject);
                int interval = Math.Clamp(welcome.Number("heartbeat_interval_seconds", 30), 1, 300);
                failures = 0; status("已连接 · 等待网页操作");
                tasks.Add(Task.Run(async () =>
                {
                    try
                    {
                        while (!connection.IsCancellationRequested)
                        {
                            await Task.Delay(TimeSpan.FromSeconds(interval), connection.Token);
                            var heartbeat = report(); heartbeat["type"] = "heartbeat";
                            await Send(heartbeat);
                        }
                    }
                    catch { connection.Cancel(); }
                }, CancellationToken.None));
                while (!connection.IsCancellationRequested)
                {
                    JsonObject message;
                    using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(connection.Token))
                    { deadline.CancelAfter(TimeSpan.FromSeconds(interval * 3 + 10)); message = await Receive(socket, deadline.Token); }
                    Error(message);
                    switch (message.Text("type"))
                    {
                        case "heartbeat_ack": clock.Sync(message.Text("server_time")); await ApplyMedia(message["media"] as JsonObject); break;
                        case "media_state": await ApplyMedia(message); break;
                        case "command":
                            tasks.RemoveAll(t => t.IsCompletedSuccessfully);
                            tasks.Add(executor.Handle(message, receipt => Send(receipt), connection.Token));
                            break;
                    }
                    if (tasks.Any(t => t.IsFaulted)) throw new IOException("命令处理失败");
                }
            }
            catch (TerminalException e) when (e.Code is "DEVICE_UNAUTHORIZED" or "DEVICE_REVOKED" or "DEVICE_PROTOCOL_UNSUPPORTED" or "PROTOCOL_ERROR")
            { status($"已停止连接：{e.Code}；请在网页复核后重新配对或手动恢复"); return; }
            catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { break; }
            catch { status("连接中断 · 已停止当前执行，稍后重连"); }
            finally
            {
                connection.Cancel(); socket.Abort();
                await stopMedia();
                try { await Task.WhenAll(tasks); } catch { }
            }
            failures = Math.Min(failures + 1, 6);
            try { await Task.Delay(TimeSpan.FromSeconds(Math.Pow(2, failures) + Random.Shared.NextDouble()), lifetime); }
            catch (OperationCanceledException) { break; }
        }
    }
    async Task ApplyMedia(JsonObject? state)
    {
        if (state is null) return;
        executor.VolumeCeiling = Math.Clamp(state.Number("volume_ceiling", 100), 1, 100);
        await media(state.Copy());
    }
    static void Error(JsonObject message)
    {
        if (message.Text("type") == "error") throw new TerminalException(message.Text("code"), "服务器拒绝设备请求");
    }
    public static async Task<JsonObject> Receive(ClientWebSocket socket, CancellationToken token)
    {
        var buffer = new byte[8192]; using var memory = new MemoryStream();
        while (true)
        {
            var result = await socket.ReceiveAsync(buffer.AsMemory(), token);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                if ((int?)socket.CloseStatus is 4400 or 4401) throw new TerminalException((int?)socket.CloseStatus == 4401 ? "DEVICE_REVOKED" : "PROTOCOL_ERROR", "服务器关闭了控制连接");
                throw new IOException("连接已关闭");
            }
            if (result.MessageType != WebSocketMessageType.Text || memory.Length + result.Count > 262144)
                throw new TerminalException("PROTOCOL_ERROR", "消息大小或类型无效");
            memory.Write(buffer, 0, result.Count);
            if (result.EndOfMessage) return Wire.Object(Encoding.UTF8.GetString(memory.ToArray()));
        }
    }
}
