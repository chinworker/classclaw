using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace ClassroomMate.Core;

public sealed class TerminalException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}

public static class Wire
{
    public static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    public static JsonObject Object(string value) => JsonNode.Parse(value, documentOptions: new() { MaxDepth = 24 }) as JsonObject
        ?? throw new TerminalException("PROTOCOL_ERROR", "消息必须是 JSON 对象");
    public static string Text(this JsonObject value, string key, string fallback = "") => value[key]?.GetValue<string>() ?? fallback;
    public static int Number(this JsonObject value, string key, int fallback = 0) => value[key]?.GetValue<int>() ?? fallback;
    public static bool Flag(this JsonObject value, string key, bool fallback = false) => value[key]?.GetValue<bool>() ?? fallback;
    public static JsonObject Copy(this JsonObject value) => (JsonObject)value.DeepClone();
    public static Uri Server(string input, bool development = false)
    {
        if (!Uri.TryCreate(input.Trim(), UriKind.Absolute, out var uri) || !string.IsNullOrEmpty(uri.UserInfo)
            || !string.IsNullOrEmpty(uri.Query) || !string.IsNullOrEmpty(uri.Fragment) || uri.AbsolutePath != "/"
            || (uri.Scheme != "https" && !(development && uri.Scheme == "http" && uri.IsLoopback)))
            throw new TerminalException("SERVER_URL_INVALID", "填写 HTTPS 服务器根地址；开发模式仅允许本机 HTTP。");
        return uri;
    }
    public static Uri Endpoint(Uri server, string path)
    {
        if (!path.StartsWith("/api/v1/classroom/", StringComparison.Ordinal) || path.Contains('\\') || path.Contains(".."))
            throw new TerminalException("PROTOCOL_ERROR", "非法设备接口路径");
        var uri = new Uri(server, path);
        if (uri.Authority != server.Authority || uri.Scheme != server.Scheme)
            throw new TerminalException("PROTOCOL_ERROR", "接口不能改变服务器地址");
        return uri;
    }
}

public sealed class ServerClock
{
    readonly object gate = new();
    DateTimeOffset anchor = DateTimeOffset.UtcNow;
    long stamp = Stopwatch.GetTimestamp();
    public void Sync(string value)
    {
        if (!DateTimeOffset.TryParse(value, out var date)) throw new TerminalException("PROTOCOL_ERROR", "服务器时间无效");
        lock (gate) { anchor = date; stamp = Stopwatch.GetTimestamp(); }
    }
    public DateTimeOffset UtcNow { get { lock (gate) return anchor + Stopwatch.GetElapsedTime(stamp); } }
    public TimeSpan Remaining(DateTimeOffset expiry) => expiry - UtcNow;
}

public sealed record Command(string Id, string Kind, JsonObject Payload, DateTimeOffset ExpiresAt)
{
    static readonly Dictionary<string, string[]> Fields = new()
    {
        ["broadcast.show"] = ["segments", "display_seconds", "repeat_count", "gap_seconds", "volume", "target_screen"],
        ["broadcast.stop"] = ["target_command_id"], ["broadcast.clear"] = ["target_command_id"],
        ["volume.set"] = ["volume", "mute", "restore_after_broadcast"],
        ["device.configure"] = ["display_name", "audio_output_name"],
        ["device.test_display"] = ["text"], ["device.test_speak"] = ["text"],
        ["camera.start"] = ["camera_id", "config_revision", "identifier", "access_path", "audio"],
        ["camera.stop"] = ["camera_id", "config_revision", "identifier", "access_path", "audio"]
    };
    public static Command Parse(JsonObject message)
    {
        var id = message.Text("command_id"); var kind = message.Text("kind");
        if (id.Length is < 1 or > 36 || !Fields.TryGetValue(kind, out var allowed)
            || message["payload"] is not JsonObject payload || payload.Any(p => !allowed.Contains(p.Key))
            || !DateTimeOffset.TryParse(message.Text("expires_at"), out var expiry))
            throw new TerminalException("PROTOCOL_ERROR", "命令或参数不在白名单内");
        if (payload["volume"] is not null && payload.Number("volume") is < 0 or > 100) Invalid();
        if (kind == "broadcast.show")
        {
            if (payload["segments"] is not JsonArray rows || rows.Count is < 1 or > 60
                || rows.Any(r => r is not JsonObject row || row.Text("text").Length is < 1 or > 400)
                || payload.Number("display_seconds") is < 3 or > 600 || payload.Number("repeat_count") is < 1 or > 10
                || payload.Number("gap_seconds") is < 0 or > 60) Invalid();
        }
        if (kind is "broadcast.stop" or "broadcast.clear" && payload.Text("target_command_id").Length is < 1 or > 36) Invalid();
        if (kind.StartsWith("device.test_") && payload.Text("text").Length is < 1 or > 200) Invalid();
        if (kind.StartsWith("camera.") && (payload.Text("camera_id").Length is < 1 or > 36 || payload.Number("config_revision") < 1)) Invalid();
        return new(id, kind, payload.Copy(), expiry);
    }
    static void Invalid() => throw new TerminalException("PROTOCOL_ERROR", "命令参数超出范围");
}

public sealed record Receipt(string CommandId, string State, string? Display = null, string? Speak = null,
    string ErrorCode = "", string ErrorMessage = "")
{
    public string Type => "command_result";
}
