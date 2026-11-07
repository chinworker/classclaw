using System.Net.Http.Json;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace ClassroomMate.Core;

public sealed record Pairing(string Server, string DeviceId, string ClassId, string Credential, bool Development = false)
{
    public static Pairing FromResponse(Uri server, JsonObject result, bool development = false)
    {
        const string error = "配对响应不完整或协议不兼容";
        try
        {
            var pairing = new Pairing(server.ToString(), result.Text("device_id"), result.Text("class_id"), result.Text("credential"), development);
            if (result.Number("protocol_version") != 1 || pairing.DeviceId.Length is < 1 or > 36 || pairing.ClassId.Length is < 1 or > 36
                || pairing.Credential.Length is < 8 or > 200 || !DateTimeOffset.TryParse(result.Text("server_time"), out _))
                throw new TerminalException("PROTOCOL_ERROR", error);
            return pairing;
        }
        catch (Exception exception) when (exception is InvalidOperationException or FormatException)
        { throw new TerminalException("PROTOCOL_ERROR", error); }
    }
}
public sealed class DeviceApi(HttpMessageHandler? handler = null) : IDisposable
{
    readonly HttpClient http = CreateClient(handler ?? new HttpClientHandler { AllowAutoRedirect = false });
    readonly HttpClient loopback = CreateClient(new HttpClientHandler { AllowAutoRedirect = false, UseProxy = false });

    static HttpClient CreateClient(HttpMessageHandler handler) => new(handler)
    {
        Timeout = TimeSpan.FromSeconds(20), MaxResponseContentBufferSize = 65536,
    };

    public async Task<JsonObject> Post(Uri server, string path, JsonObject body, CancellationToken token)
    {
        try
        {
            var client = server.Scheme == "http" && server.IsLoopback ? loopback : http;
            using var response = await client.PostAsJsonAsync(Wire.Endpoint(server, path), body, token);
            var status = (int)response.StatusCode;
            if (!string.Equals(response.Content.Headers.ContentType?.MediaType, "application/json", StringComparison.OrdinalIgnoreCase))
                throw new TerminalException($"HTTP_{status}", $"服务器返回了无法识别的响应（HTTP {status}）。");
            var data = Wire.Object(await response.Content.ReadAsStringAsync(token));
            if (!response.IsSuccessStatusCode || !data.Flag("success"))
            {
                var error = data["error"] as JsonObject;
                var requestId = Guid.TryParse(data.Text("request_id"), out var id) ? id : (Guid?)null;
                throw new TerminalException(error?.Text("code") ?? $"HTTP_{status}",
                    error?.Text("message") ?? $"服务器请求失败（HTTP {status}）。", requestId);
            }
            return (data["data"] as JsonObject)?.Copy() ?? throw new TerminalException("PROTOCOL_ERROR", "服务器响应缺少配对结果。");
        }
        catch (OperationCanceledException) when (!token.IsCancellationRequested)
        { throw new TerminalException("NETWORK_TIMEOUT", "连接服务器超时，请检查教室电脑的网络或代理。"); }
        catch (HttpRequestException error) { throw NetworkError(error); }
        catch (JsonException) { throw new TerminalException("PROTOCOL_ERROR", "服务器返回的配对响应格式无效。"); }
    }

    internal static TerminalException NetworkError(HttpRequestException error) => error.HttpRequestError switch
    {
        HttpRequestError.NameResolutionError => new("DNS_ERROR", "无法解析服务器地址，请检查网络和 DNS。"),
        HttpRequestError.SecureConnectionError => new("TLS_ERROR", "无法验证服务器的 HTTPS 连接，请检查 Windows 日期、时间及证书信任。"),
        HttpRequestError.ProxyTunnelError => new("PROXY_ERROR", "系统代理无法连接服务器，请检查 Windows 代理设置。"),
        HttpRequestError.ConnectionError => new("NETWORK_CONNECTION_ERROR", "教室电脑无法连接服务器，请检查网络或代理。"),
        _ => new("HTTP_TRANSPORT_ERROR", "与服务器通信中断，请稍后重试。"),
    };

    public void Dispose() { http.Dispose(); loopback.Dispose(); }
}

public sealed class Connection(Pairing pairing, ServerClock clock, Executor executor,
    Func<JsonObject> report, Func<JsonObject, Task> media, Func<Task> stopMedia, Action<string> status,
    string appVersion = "0.1.0", Action<string>? diagnostic = null)
{
    readonly SemaphoreSlim sendGate = new(1, 1);
    readonly System.Threading.Channels.Channel<bool> reportChanges = System.Threading.Channels.Channel.CreateBounded<bool>(1);
    public void RequestReport() => reportChanges.Writer.TryWrite(true);
    public async Task Run(CancellationToken lifetime)
    {
        int failures = 0;
        while (!lifetime.IsCancellationRequested)
        {
            using var socket = new ClientWebSocket();
            socket.Options.KeepAliveInterval = TimeSpan.FromSeconds(15);
            using var connection = CancellationTokenSource.CreateLinkedTokenSource(lifetime);
            var tasks = new List<Task>();
            try
            {
                status("正在连接服务器…");
                var server = Wire.Server(pairing.Server, pairing.Development);
                socket.Options.Proxy = server.Scheme == "https" ? HttpClient.DefaultProxy : null;
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
                (hello["inventory"] as JsonObject)?.Remove("local_capture");
                hello["type"] = "hello"; hello["device_id"] = pairing.DeviceId; hello["credential"] = pairing.Credential;
                hello["protocol_version"] = 1; hello["app_version"] = appVersion; hello["os_version"] = Environment.OSVersion.VersionString;
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
                bool localSelection = welcome.Flag("local_device_selection");
                failures = 0; status("已连接 · 等待网页操作");
                tasks.Add(Task.Run(async () =>
                {
                    try
                    {
                        while (!connection.IsCancellationRequested)
                        {
                            var heartbeat = report(); heartbeat["type"] = "heartbeat";
                            if (!localSelection) (heartbeat["inventory"] as JsonObject)?.Remove("local_capture");
                            await Send(heartbeat);
                            using var next = CancellationTokenSource.CreateLinkedTokenSource(connection.Token);
                            next.CancelAfter(TimeSpan.FromSeconds(interval));
                            try { await reportChanges.Reader.ReadAsync(next.Token); }
                            catch (OperationCanceledException) when (!connection.IsCancellationRequested) { }
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
            { diagnostic?.Invoke(e.Code); status($"已停止连接：{e.Code}；请在网页复核后重新配对或手动恢复"); return; }
            catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { break; }
            catch (Exception error)
            {
                diagnostic?.Invoke(error.InnerException is HttpRequestException request
                    ? DeviceApi.NetworkError(request).Code : error.GetType().Name);
                status("连接中断 · 已停止当前执行，稍后重连");
            }
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
