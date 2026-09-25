using ClassroomMate.Core;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;
using System.Net.WebSockets;
using System.Text.Json;
using System.Text.Json.Nodes;

static class SocketTest
{
    public static async Task Run(string directory)
    {
        var builder = WebApplication.CreateBuilder(); builder.Logging.ClearProviders(); builder.WebHost.UseUrls("http://127.0.0.1:0");
        await using var app = builder.Build(); app.UseWebSockets();
        var helloSeen = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var receiptSeen = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        app.Map("/api/v1/classroom/device-channel", async context =>
        {
            using var socket = await context.WebSockets.AcceptWebSocketAsync();
            async Task Send(object data) => await socket.SendAsync(JsonSerializer.SerializeToUtf8Bytes(data, Wire.Json), WebSocketMessageType.Text, true, context.RequestAborted);
            async Task<JsonObject> Read()
            {
                var bytes = new byte[262144]; var result = await socket.ReceiveAsync(bytes, context.RequestAborted);
                return Wire.Object(System.Text.Encoding.UTF8.GetString(bytes, 0, result.Count));
            }
            var hello = await Read();
            if (hello.Text("device_id") != "device-test" || hello.Text("credential") != "not-a-real-secret") throw new Exception("bad hello");
            helloSeen.TrySetResult();
            await Send(new { type = "welcome", protocol_version = 1, server_time = DateTimeOffset.UtcNow, heartbeat_interval_seconds = 1 });
            var message = new { type = "command", command_id = "socket-once", kind = "device.test_speak",
                payload = new { text = "合成协议测试" }, expires_at = DateTimeOffset.UtcNow.AddMinutes(1) };
            await Send(message);
            int completed = 0;
            while (completed < 2)
            {
                var received = await Read();
                if (received.Text("type") == "heartbeat") await Send(new { type = "heartbeat_ack", server_time = DateTimeOffset.UtcNow });
                if (received.Text("type") == "command_result" && received.Text("state") == "succeeded")
                { completed++; if (completed == 1) await Send(message); }
            }
            receiptSeen.TrySetResult();
            await Send(new { type = "error", code = "DEVICE_REVOKED", message = "test" });
        });
        await app.StartAsync();
        var fake = new Fake(); var clock = new ServerClock(); var statuses = new List<string>();
        var executor = new Executor(fake, new Ledger(Path.Combine(directory, "socket.json")), clock);
        var connection = new Connection(new(app.Urls.Single(), "device-test", "class-test", "not-a-real-secret", true), clock, executor,
            () => new JsonObject(), _ => Task.CompletedTask, () => Task.CompletedTask, s => statuses.Add(s));
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        await connection.Run(deadline.Token);
        if (!helloSeen.Task.IsCompletedSuccessfully || !receiptSeen.Task.IsCompletedSuccessfully || fake.Spoken != 1
            || !statuses.Any(s => s.Contains("DEVICE_REVOKED"))) throw new Exception("WebSocket integration failed");
        await app.StopAsync();
    }
}
